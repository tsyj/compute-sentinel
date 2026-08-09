#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_observability v0.1 — 从运行中的 AgentTeams 采集三类可观测数据

赛道技术要求：可观测建议至少覆盖 Trace / Log / Metrics 中的 1-2 类。本工具三类都采：

  Trace    每次工具调用一条 span（时间、Agent、会话、工具、目标、放行决策、理由）
           会话按 Matrix 房间 ID 聚合 —— 一个事故房间 = 一条完整调用链
  Log      Worker 运行时日志（qwenpaw-worker.log）的关键行
  Metrics  工具调用次数、决策分布、每 Agent 活跃度、无沙箱执行占比

数据源是 AgentTeams/QwenPaw 自带的治理审计库 governance/audit.db，
**不是我们自己埋点** —— 也就是说这些证据在框架层面天然存在，无法事后编造。

用法:
    python3 collect_observability.py [--workers sentinel,triage,planner] [--json out.json]
"""
from __future__ import annotations
import argparse, json, shlex, subprocess, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

AUDIT = '/root/agentteams-fs/agents/{w}/.qwenpaw/governance/audit.db'
LOG = '/root/agentteams-fs/agents/{w}/.qwenpaw/logs/qwenpaw-worker.log'

# 需要在报告里高亮的 reason 关键词：它们代表安全边界上的实际状态
SECURITY_FLAGS = {
    'unsandboxed': '⚠ 未沙箱执行',
    'sandbox unavailable': '⚠ 沙箱不可用',
    'deny': '✗ 被拒绝',
    'ask': '? 需确认',
}


def docker(args: list[str]) -> str:
    """经 sg docker 调用，避免依赖当前 shell 的组身份。参数逐个转义，防止引号丢失。"""
    cmd = 'docker ' + ' '.join(shlex.quote(x) for x in args)
    r = subprocess.run(['sg', 'docker', '-c', cmd],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def fetch_spans(worker: str) -> list[dict]:
    """从 worker 容器内直接查审计库（WAL 模式，拷出来会丢数据）。"""
    py = (
        "import sqlite3,json;"
        f"c=sqlite3.connect('{AUDIT.format(w=worker)}');"
        "rows=c.execute('SELECT ts,agent_id,session_id,tool_name,target,decision,reason "
        "FROM audit_events ORDER BY ts').fetchall();"
        "print(json.dumps(rows))"
    )
    out = docker(['exec', f'agentteams-worker-{worker}', 'python3', '-c', py])
    rows = json.loads(out.strip().splitlines()[-1])
    keys = ('ts', 'agent_id', 'session_id', 'tool_name', 'target', 'decision', 'reason')
    return [dict(zip(keys, r), worker=worker) for r in rows]


def log_tail(worker: str, n: int = 400) -> list[str]:
    try:
        out = docker(['exec', f'agentteams-worker-{worker}',
                      'tail', '-n', str(n), LOG.format(w=worker)])
    except Exception:
        return []
    keep = ('error', 'warn', 'fail', 'denied', 'sandbox', 'matrix', 'skill')
    return [l for l in out.splitlines() if any(k in l.lower() for k in keep)]


def ts_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime('%H:%M:%S')


def flags_of(reason: str, decision: str) -> list[str]:
    r = (reason or '').lower()
    out = [v for k, v in SECURITY_FLAGS.items() if k in r]
    if decision and decision.lower() != 'allow':
        out.append(f'✗ decision={decision}')
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='采集 AgentTeams 的 Trace / Log / Metrics')
    ap.add_argument('--workers', default='sentinel,triage,planner')
    ap.add_argument('--json', help='结构化结果输出路径')
    ap.add_argument('--max-target', type=int, default=72, help='trace 中 target 截断长度')
    a = ap.parse_args()

    workers = [w.strip() for w in a.workers.split(',') if w.strip()]
    spans, logs = [], {}
    for w in workers:
        try:
            spans += fetch_spans(w)
        except Exception as e:
            print(f'  [!] {w} 审计库读取失败: {e}', file=sys.stderr)
        logs[w] = log_tail(w)
    spans.sort(key=lambda s: s['ts'])

    if not spans:
        print('未采集到任何 span。确认 Worker 容器在运行。', file=sys.stderr)
        return 1

    # ---------------- Metrics ----------------
    by_worker = Counter(s['worker'] for s in spans)
    by_tool = Counter(s['tool_name'] for s in spans)
    by_decision = Counter(s['decision'] for s in spans)
    sessions = defaultdict(list)
    for s in spans:
        sessions[s['session_id']].append(s)
    unsandboxed = [s for s in spans if 'unsandbox' in (s['reason'] or '').lower()]
    span_ms = spans[-1]['ts'] - spans[0]['ts']

    print('=' * 74)
    print('Metrics')
    print('=' * 74)
    print(f'  span 总数        : {len(spans)}')
    print(f'  时间跨度        : {ts_str(spans[0]["ts"])} → {ts_str(spans[-1]["ts"])}'
          f'  ({span_ms/1000:.0f}s)')
    print(f'  会话数（事故房间）: {len(sessions)}')
    print(f'  每 Agent span    : ' + ', '.join(f'{k}={v}' for k, v in by_worker.most_common()))
    print(f'  工具调用分布     : ' + ', '.join(f'{k}={v}' for k, v in by_tool.most_common()))
    print(f'  放行决策分布     : ' + ', '.join(f'{k}={v}' for k, v in by_decision.most_common()))
    print(f'  ⚠ 无沙箱执行     : {len(unsandboxed)} / {len(spans)}'
          f'  ({100*len(unsandboxed)/len(spans):.0f}%)')

    # ---------------- Trace ----------------
    print()
    print('=' * 74)
    print('Trace（按会话聚合；一个会话 = 一个事故房间）')
    print('=' * 74)
    for sid, ss in sessions.items():
        short = sid.split(':')[0].replace('matrix:', '')
        print(f'\n  会话 {short}  —  {len(ss)} 个 span')
        for s in ss:
            fl = flags_of(s['reason'], s['decision'])
            tgt = (s['target'] or '')[:a.max_target]
            print(f'    {ts_str(s["ts"])}  {s["worker"]:<9} {s["tool_name"]:<6} '
                  f'{s["decision"]:<6} {tgt}')
            if fl:
                print(f'{"":>16}{" ".join(fl)}  ← {s["reason"][:80]}')

    # ---------------- Log ----------------
    print()
    print('=' * 74)
    print('Log（各 Worker 运行时日志中的关键行）')
    print('=' * 74)
    for w, lines in logs.items():
        print(f'  {w}: {len(lines)} 条关键行' + ('' if lines else '（无）'))
        for l in lines[-3:]:
            print(f'    {l[:150]}')

    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(dict(
                collected_at=datetime.now().astimezone().isoformat(),
                metrics=dict(spans=len(spans), sessions=len(sessions),
                             span_seconds=span_ms / 1000,
                             by_worker=dict(by_worker), by_tool=dict(by_tool),
                             by_decision=dict(by_decision),
                             unsandboxed=len(unsandboxed)),
                spans=spans,
                logs={k: v[-40:] for k, v in logs.items()},
            ), f, ensure_ascii=False, indent=2)
        print(f'\n结构化结果已写入 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
