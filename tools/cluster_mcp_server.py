#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cluster-mcp-server v0.1 — 长时算力任务集群适配器的 MCP Server

把 Agent 需要的集群观测能力封装为 MCP 工具，**全部只读**：

  probe_job_progress   三信号联合的进度判定（progress-probe 的后端）
  tail_log             读日志尾部
  stat_outputs         按 glob 统计产物文件的 mtime / size
  sample_resources     采样进程的 CPU / 内存 / 线程数

协议：MCP over stdio，JSON-RPC 2.0，protocolVersion 2024-11-05
实现：**纯标准库**，不引入 mcp SDK —— 便于在任意受限环境部署，也便于审计

设计约束（与 docs/integration-contract.md 一致）：
  · 只读：不提供任何写、删、终止类工具
  · 路径白名单：只允许访问 --allow 指定的根目录之下
  · 全量审计：每次调用写一条 JSON Lines 审计记录
  · 错误不抛栈：统一返回结构化错误，避免把宿主机路径细节泄露给模型

用法:
    python3 cluster_mcp_server.py --allow /data/xinyuan --allow /home/xinyuan --audit audit.jsonl
"""
from __future__ import annotations
import argparse, glob as globlib, json, os, re, subprocess, sys, time
from pathlib import Path

PROTOCOL = "2024-11-05"
SERVER = {"name": "cluster-mcp-server", "version": "0.1.0"}

ALLOW: list[Path] = []
AUDIT: Path | None = None


# ---------------------------------------------------------------- 安全与审计

class ToolError(Exception):
    def __init__(self, code: str, msg: str):
        self.code, self.msg = code, msg
        super().__init__(msg)


def check_path(p: str) -> Path:
    """路径白名单。越界一律拒绝，且不回显白名单内容。"""
    try:
        rp = Path(p).resolve()
    except Exception:
        raise ToolError("BAD_PATH", "路径无法解析")
    for root in ALLOW:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise ToolError("PATH_DENIED", "路径不在允许范围内")


def audit(tool: str, args: dict, decision: str, reason: str = "") -> None:
    if AUDIT is None:
        return
    rec = dict(ts=int(time.time() * 1000), tool=tool, args=args,
               decision=decision, reason=reason)
    try:
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # 审计失败不能影响主流程，但也不静默改变行为


# ---------------------------------------------------------------- workload 适配

ADAPTER_DIR = Path(__file__).resolve().parent.parent / "adapters"
_ADAPTER_CACHE: dict[str, dict] = {}

_FALLBACK = dict(workload="generic", progress_line_regex=".", output_globs=["*"],
                 first_progress_sec=900, stall_sec=1200,
                 notes="适配器文件缺失，已回落到内置兜底值")


def load_adapter(workload: str) -> dict:
    """从 adapters/<workload>.json 加载。文件缺失时回落到兜底并显式标注。"""
    if workload in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[workload]
    f = ADAPTER_DIR / f"{workload}.json"
    try:
        ad = json.loads(f.read_text(encoding="utf-8"))
        ad.setdefault("source", str(f.name))
    except Exception:
        ad = dict(_FALLBACK, source="fallback(内置)", requested=workload)
    _ADAPTER_CACHE[workload] = ad
    return ad


def available_workloads() -> list[str]:
    try:
        return sorted(f.stem for f in ADAPTER_DIR.glob("*.json"))
    except Exception:
        return ["generic"]


def _stat_many(run_dir: Path, patterns: str) -> list[dict]:
    out = []
    for pat in patterns.split("|"):
        for f in sorted(globlib.glob(str(run_dir / pat)))[:200]:
            try:
                st = os.stat(f)
                out.append(dict(path=os.path.basename(f), size=st.st_size,
                                mtime=int(st.st_mtime)))
            except OSError:
                continue
    return out


# ---------------------------------------------------------------- 工具实现

def t_tail_log(path: str, lines: int = 50) -> dict:
    p = check_path(path)
    if not p.is_file():
        raise ToolError("NOT_FOUND", "文件不存在")
    st = p.stat()
    with p.open("rb") as f:
        f.seek(max(0, st.st_size - 64 * 1024))
        tail = f.read().decode("utf-8", "replace").splitlines()[-lines:]
    return dict(path=str(p), size=st.st_size, mtime=int(st.st_mtime), lines=tail)


def t_stat_outputs(run_dir: str, patterns: str = "*") -> dict:
    d = check_path(run_dir)
    if not d.is_dir():
        raise ToolError("NOT_FOUND", "目录不存在")
    files = _stat_many(d, patterns)
    return dict(run_dir=str(d), count=len(files),
                newest_mtime=max((f["mtime"] for f in files), default=0),
                total_size=sum(f["size"] for f in files), files=files[:50])


def t_sample_resources(pattern: str) -> dict:
    """按命令行片段匹配进程，返回 CPU/内存/线程。只读，不发送任何信号。"""
    if not re.fullmatch(r"[\w./=-]{1,120}", pattern or ""):
        raise ToolError("BAD_PATTERN", "匹配串只允许字母数字与 . / _ - = 字符")
    try:
        out = subprocess.run(["ps", "-eo", "user,pid,pgid,pcpu,rss,nlwp,args"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        raise ToolError("PS_FAILED", str(e)[:120])
    procs = []
    for line in out.splitlines()[1:]:
        if pattern in line:
            f = line.split(None, 6)
            if len(f) == 7:
                procs.append(dict(user=f[0], pid=int(f[1]), pgid=int(f[2]),
                                  cpu_pct=float(f[3]), rss_kb=int(f[4]),
                                  threads=int(f[5]), args=f[6][:160]))
    return dict(pattern=pattern, matched=len(procs),
                total_cpu_pct=round(sum(p["cpu_pct"] for p in procs), 1),
                procs=procs[:20])


def t_probe_job_progress(run_dir: str, workload_type: str = "generic",
                         log_path: str = "", prev_snapshot: dict | None = None,
                         proc_pattern: str = "", job_start_ts: int = 0) -> dict:
    """
    三信号联合判定。规则是 OR：任一信号有变化即 RUNNING；
    全部静止且超过该 workload 阈值才 STALLED。采集失败降级 UNKNOWN，绝不误判 DEAD。
    """
    d = check_path(run_dir)
    ad = load_adapter(workload_type)
    prog_re = ad.get("progress_line_regex", ".")
    patterns = "|".join(ad.get("output_globs", ["*"]))
    first_to = int(ad.get("first_progress_sec", 900))
    stall_to = int(ad.get("stall_sec", 1200))
    prev = prev_snapshot or {}
    sig, unavailable = {}, []

    # 信号 A · 日志
    try:
        if log_path:
            lp = check_path(log_path)
            st = lp.stat()
            cur = dict(size=st.st_size, mtime=int(st.st_mtime))
            prev_size = prev.get("log", {}).get("size", -1)
            grew = cur["size"] > prev_size
            # 关键：日志"在长"不等于"有进度"。只统计新增片段里匹配进度行的条数，
            # 一个反复刷同一行错误的作业，size 一直涨但进度行数为 0 —— 那不是 RUNNING。
            prog_hits, tail_txt = 0, ""
            if grew and prev_size >= 0:
                with lp.open("rb") as fh:
                    fh.seek(max(0, prev_size))
                    tail_txt = fh.read(512 * 1024).decode("utf-8", "replace")
                try:
                    prog_hits = len(re.findall(prog_re, tail_txt, re.M))
                except re.error:
                    prog_hits = -1        # 正则无效，退化为只看 size
            has_progress = (prog_hits != 0) if (grew and prev_size >= 0) else grew
            sig["log"] = dict(changed=bool(has_progress), size=cur["size"], mtime=cur["mtime"],
                              grew=bool(grew), progress_lines=prog_hits,
                              detail=(f'size={cur["size"]}'
                                      + (f", 新增 {len(tail_txt)}B 内含进度行 {prog_hits} 条"
                                         if grew and prev_size >= 0 else "")
                                      + (" ← 日志在长但无进度行" if grew and prog_hits == 0 else "")))
        else:
            unavailable.append("log")
            sig["log"] = dict(changed=None, detail="未提供 log_path")
    except ToolError as e:
        unavailable.append("log"); sig["log"] = dict(changed=None, detail=f"{e.code}")

    # 信号 B · 产物文件
    try:
        files = _stat_many(d, patterns)
        newest = max((f["mtime"] for f in files), default=0)
        total = sum(f["size"] for f in files)
        p = prev.get("file", {})
        changed = newest > p.get("newest_mtime", -1) or total > p.get("total_size", -1)
        sig["file"] = dict(changed=bool(changed), newest_mtime=newest, total_size=total,
                           count=len(files),
                           detail=f"{len(files)} 个产物, 最新 mtime={newest}, 合计 {total} B")
    except Exception as e:
        unavailable.append("file"); sig["file"] = dict(changed=None, detail=str(e)[:80])

    # 信号 C · 资源
    try:
        if proc_pattern:
            r = t_sample_resources(proc_pattern)
            active = r["total_cpu_pct"] > 50
            sig["resource"] = dict(changed=bool(active), detail=
                                   f'{r["matched"]} 进程, CPU 合计 {r["total_cpu_pct"]}%')
        else:
            unavailable.append("resource")
            sig["resource"] = dict(changed=None, detail="未提供 proc_pattern")
    except Exception as e:
        unavailable.append("resource"); sig["resource"] = dict(changed=None, detail=str(e)[:80])

    first_round = not prev
    votes = [v["changed"] for v in sig.values() if v["changed"] is not None]
    stalled_for = prev.get("stalled_for_sec", 0)
    note = ""

    if first_round:
        # 首轮没有基线，"变化"无从谈起：只建基线，不下判定。
        # 唯一例外是作业已启动很久却连一个产物都没有 —— 那是首个进度信号超时。
        age = int(time.time()) - job_start_ts if job_start_ts else 0
        if job_start_ts and age > first_to and sig["file"].get("count", 0) == 0:
            status, conf = "STALLED", 0.8
            note = f"启动后 {age}s 未见任何产物，超过首个进度信号阈值 {first_to}s"
        else:
            status, conf = "UNKNOWN", 0.0
            note = "首轮采集，已建立基线；判定需下一轮比对"
    elif not votes:
        status, conf = "UNKNOWN", 0.0
        note = "全部信号不可采集"
    elif any(votes):
        status, conf, stalled_for = "RUNNING", round(0.6 + 0.2 * sum(votes), 2), 0
    else:
        stalled_for += int(prev.get("interval_sec", 300))
        status = "STALLED" if stalled_for >= stall_to else "RUNNING"
        conf = 0.9 if status == "STALLED" else 0.5

    return dict(status=status, workload_type=workload_type,
                adapter=ad.get("source", "?"), signals=sig,
                confidence=conf, stalled_for_sec=stalled_for,
                thresholds=dict(first_progress_sec=first_to, stall_sec=stall_to),
                unavailable_signals=unavailable,
                next_poll_sec=150 if unavailable else 300,
                note="；".join(x for x in (note,
                     ("部分信号不可用，判定精度下降" if unavailable else "")) if x),
                snapshot=dict(log=sig["log"], file=sig["file"],
                              stalled_for_sec=stalled_for, interval_sec=300))


TOOLS = {
    "probe_job_progress": (t_probe_job_progress, "三信号联合判定长时任务是否仍在推进（只读）", {
        "type": "object",
        "properties": {
            "run_dir": {"type": "string", "description": "作业运行目录"},
            "workload_type": {"type": "string", "enum": available_workloads(),
                              "description": "workload 类型，决定进度判据与阈值"},
            "log_path": {"type": "string", "description": "日志文件路径（可选）"},
            "proc_pattern": {"type": "string", "description": "进程命令行匹配片段（可选）"},
            "prev_snapshot": {"type": "object", "description": "上一轮返回的 snapshot；缺省表示首轮，只建基线不判定"},
            "job_start_ts": {"type": "integer", "description": "作业启动的 Unix 秒；提供后可在首轮检出'启动后长时间零产物'"},
        }, "required": ["run_dir"]}),
    "tail_log": (t_tail_log, "读取日志文件尾部（只读）", {
        "type": "object",
        "properties": {"path": {"type": "string"},
                       "lines": {"type": "integer", "minimum": 1, "maximum": 500}},
        "required": ["path"]}),
    "stat_outputs": (t_stat_outputs, "统计产物文件的 mtime 与 size（只读）", {
        "type": "object",
        "properties": {"run_dir": {"type": "string"},
                       "patterns": {"type": "string", "description": "glob，多个用 | 分隔"}},
        "required": ["run_dir"]}),
    "sample_resources": (t_sample_resources, "采样匹配进程的 CPU/内存/线程（只读，不发信号）", {
        "type": "object",
        "properties": {"pattern": {"type": "string", "description": "命令行匹配片段"}},
        "required": ["pattern"]}),
}


# ---------------------------------------------------------------- JSON-RPC

def handle(req: dict) -> dict | None:
    mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}

    if method == "initialize":
        return dict(jsonrpc="2.0", id=mid, result=dict(
            protocolVersion=PROTOCOL, serverInfo=SERVER,
            capabilities=dict(tools=dict(listChanged=False))))

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return dict(jsonrpc="2.0", id=mid, result=dict(tools=[
            dict(name=n, description=d, inputSchema=s) for n, (_, d, s) in TOOLS.items()]))

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            audit(name or "?", args, "deny", "unknown tool")
            return dict(jsonrpc="2.0", id=mid,
                        error=dict(code=-32601, message=f"未知工具 {name}"))
        try:
            out = TOOLS[name][0](**args)
            audit(name, args, "allow", "")
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text", text=json.dumps(out, ensure_ascii=False))],
                isError=False))
        except ToolError as e:
            audit(name, args, "deny", e.code)
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text",
                              text=json.dumps(dict(error=e.code, message=e.msg),
                                              ensure_ascii=False))], isError=True))
        except TypeError as e:
            audit(name, args, "deny", "bad arguments")
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text",
                              text=json.dumps(dict(error="BAD_ARGS", message=str(e)[:160]),
                                              ensure_ascii=False))], isError=True))

    return dict(jsonrpc="2.0", id=mid, error=dict(code=-32601, message="方法不支持"))


def main() -> int:
    global ALLOW, AUDIT
    ap = argparse.ArgumentParser(description="长时算力任务集群适配器 MCP Server")
    ap.add_argument("--allow", action="append", default=[], help="允许访问的根目录，可多次指定")
    ap.add_argument("--audit", help="审计记录输出路径（JSON Lines）")
    a = ap.parse_args()
    ALLOW = [Path(p).resolve() for p in (a.allow or [os.getcwd()])]
    AUDIT = Path(a.audit) if a.audit else None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = dict(jsonrpc="2.0", id=None,
                        error=dict(code=-32700, message="JSON 解析失败"))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
