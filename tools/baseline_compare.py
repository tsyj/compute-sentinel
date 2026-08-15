#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基线对照：把本方案和几种常见做法放在**同一批真实数据**上比。

没有基线，"我们的判定更好"就是不可证伪的。这个脚本用同一组真实信号序列，
同时跑六种判据，各自报出在两类数据集上的表现：

  正样本（真卡死，应当告警）    → 看**是否发现**与**发现时延**
  负样本（成功跑完，不应告警）  → 看**误报次数**

六种判据：
  B1 进程存活         进程还在 = 健康。这是绝大多数心跳监控的做法
  B2 CPU 占用         CPU 高 = 在算
  B3 日志 size 增长   日志文件变大 = 在跑
  B4 日志进度行       日志新增内容里匹配到进度行 = 在跑
  B5 三信号对等 OR    本项目 v0.1.0：日志/产物/CPU 任一有变化即 RUNNING
  B6 本方案 v0.2.0    进展信号与存活信号分层

用法:
  python3 tools/baseline_compare.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import cluster_mcp_server as S
import replay_from_log as R


# ────────────────────────────────────────────────── 基线判据
# 每个基线是个纯函数：给定「本轮观测」与「上轮观测」，回答"看起来在推进吗"

def b1_alive(cur, prev):      return cur.get("alive")
def b2_cpu(cur, prev):        return None if cur.get("cpu") is None else cur["cpu"] > 50
def b3_log_size(cur, prev):   return None if prev is None else cur["log_size"] > prev["log_size"]

def b4_log_progress(cur, prev, logbytes, prog_re):
    if prev is None or cur["log_size"] <= prev["log_size"]:
        return False if prev is not None else None
    seg = logbytes[prev["log_size"]:cur["log_size"]].decode("utf-8", "replace")
    return len(re.findall(prog_re, seg, re.M)) > 0


def run_baseline(obs, name, vote_fn, stall_to, interval):
    """连续 N 轮 vote 都为 False 且累计超过阈值 → 该基线判定卡死。"""
    stalled, alarms, first = 0, 0, None
    prev = None
    for o in obs:
        v = vote_fn(o, prev)
        if v is None:
            pass                      # 不可判，不累计
        elif v:
            stalled = 0
        else:
            stalled += interval
            if stalled >= stall_to:
                alarms += 1
                if first is None:
                    first = o["t"]
        prev = o
    return dict(name=name, alarms=alarms, first_alarm_t=first)


def run_ours(obs, logbytes, prog_re, stall_to, interval, gpu_mode, degraded_to, first_to):
    """本方案 v0.2.0：调 decide() 本体。"""
    prev_snap, prev, alarms, first = {}, None, 0, None
    for o in obs:
        sig = {}
        grew = prev is not None and o["log_size"] > prev["log_size"]
        hits = 0
        if grew:
            seg = logbytes[prev["log_size"]:o["log_size"]].decode("utf-8", "replace")
            hits = len(re.findall(prog_re, seg, re.M))
        has = (hits != 0) if grew else (None if prev is None else False)
        sig["log"] = dict(changed=has, size=o["log_size"], grew=grew, progress_lines=hits, detail="")

        if o.get("file_key") is not None:
            ch = prev is not None and o["file_key"] > prev["file_key"]
            sig["file"] = dict(changed=(ch if prev is not None else None),
                               count=o.get("file_count", 1), total_size=o["file_key"],
                               newest_mtime=o["file_key"], detail="")
        else:
            sig["file"] = dict(changed=None, count=0, detail="不可采")

        sig["resource"] = (dict(changed=o["cpu"] > 50, detail="")
                           if o.get("cpu") is not None else dict(changed=None, detail=""))
        if gpu_mode:
            u = o.get("gpu")
            sig["gpu"] = (dict(changed=u >= 5, available=True, util_pct=u, detail="")
                          if u is not None and u >= 0 else dict(changed=None, available=False, detail=""))

        v = S.decide(sig, prev_snap, first_to, stall_to, 0, interval, gpu_mode, degraded_to)
        if v["status"] == "STALLED":
            alarms += 1
            if first is None:
                first = o["t"]
        prev_snap = dict(log=v["signals"]["log"], file=v["signals"]["file"],
                         stalled_for_sec=v["stalled_for_sec"], interval_sec=interval,
                         gpu_ever_busy=v["gpu_ever_busy"])
        prev = o
    return dict(name="B6 本方案 v0.2.0", alarms=alarms, first_alarm_t=first)


def run_v010(obs, logbytes, prog_re, stall_to, interval, first_to):
    """B5 三信号对等 OR = 本方案 v0.1.0：不看 GPU，CPU 直接算进展票。"""
    return run_ours(obs, logbytes, prog_re, stall_to, interval, False, 0, first_to) | \
           {"name": "B5 三信号对等 OR (v0.1.0)"}


# ────────────────────────────────────────────────── 数据集装载

def load_gpu(sig_path, log_path):
    """正样本：4090 上真造的 GPU 退化路径卡死。真值 = 应当告警。"""
    recs = [json.loads(l) for l in Path(sig_path).read_text().splitlines() if l.strip()]
    obs = []
    for r in recs:
        obs.append(dict(t=r["ts"], alive=r["alive"], cpu=r.get("cpu_pct"),
                        gpu=r.get("gpu_util"), log_size=max(0, r["log_size"]),
                        file_key=r["ckpt_bytes"], file_count=r["ckpt_count"]))
    t0 = obs[0]["t"]
    for o in obs:
        o["t"] -= t0
    iv = int(round((obs[-1]["t"] - obs[0]["t"]) / max(1, len(obs) - 1)))
    return obs, Path(log_path).read_bytes(), iv


def load_from_log(log_path, elapsed_rx=None, wget=False, interval=60):
    """负样本：成功跑完的历史作业。真值 = 全程不应告警。"""
    p = Path(log_path)
    pts = R.timeline_from_wget(p) if wget else R.timeline_from_elapsed(p, elapsed_rx)
    snaps = R.sample(pts, interval)
    obs = [dict(t=s["t"], alive=True, cpu=None, gpu=None,
                log_size=s["log_size"],
                file_key=s["dl_bytes"], file_count=(1 if s["dl_bytes"] else 0))
           for s in snaps]
    return obs, p.read_bytes(), interval


# ────────────────────────────────────────────────── 主流程

def evaluate(title, obs, logbytes, ad, interval, gpu_mode, positive: bool, onset=0.0):
    prog_re = ad.get("progress_line_regex", ".")
    stall_to = int(ad.get("stall_sec", 1200))
    first_to = int(ad.get("first_progress_sec", 900))
    deg = int((ad.get("gpu_signal") or {}).get("degraded_stall_sec", 0))

    rows = [
        run_baseline(obs, "B1 进程存活（心跳）", b1_alive, stall_to, interval),
        run_baseline(obs, "B2 CPU 占用", b2_cpu, stall_to, interval),
        run_baseline(obs, "B3 日志 size 增长", b3_log_size, stall_to, interval),
        run_baseline(obs, "B4 日志进度行",
                     lambda c, p: b4_log_progress(c, p, logbytes, prog_re), stall_to, interval),
        run_v010(obs, logbytes, prog_re, stall_to, interval, first_to),
        run_ours(obs, logbytes, prog_re, stall_to, interval, gpu_mode, deg, first_to),
    ]

    print(f"\n## {title}")
    print(f"   {len(obs)} 个观测点，采样 {interval}s，"
          f"adapter={ad.get('workload')} stall_sec={stall_to}s"
          + (f" degraded={deg}s" if gpu_mode and deg else ""))
    print(f"   真值：{'**真卡死，应当告警**' if positive else '**成功跑完，不应告警**'}\n")
    hdr = "发现时延" if positive else "误报次数"
    print(f"   {'判据':<26} {hdr:>10}   结论")
    for r in rows:
        if positive:
            if r["first_alarm_t"] is None:
                val, verd = "—", "❌ 漏报"
            else:
                d = r["first_alarm_t"] - onset
                val, verd = f"{d:.0f}s", "✅ 发现"
        else:
            val = f'{r["alarms"]} 次'
            verd = "✅ 无误报" if r["alarms"] == 0 else "❌ 误报"
        print(f"   {r['name']:<26} {val:>10}   {verd}")
    return rows


def main():
    ev = ROOT / "evidence" / "gpu-stall"
    print("# 基线对照 —— 同一批真实数据，六种判据")
    print("# 没有基线，'我们的判定更好'就是不可证伪的")

    ad_py = S.load_adapter("pytorch")

    for tag, sig, log, onset_line in (
        ("正样本 A · GPU 退化路径卡死（20s 采样，measure-06）",
         ev / "runA-signals.jsonl", ev / "runA-train.log", "ep 4 begin"),
        ("正样本 B · GPU 退化路径卡死（10s 采样，measure-07）",
         ev / "runB-signals.jsonl", ev / "runB-train.log", "ep 11 begin"),
    ):
        obs, lb, iv = load_gpu(sig, log)
        idx = lb.find(onset_line.encode())
        onset = 0.0
        if idx >= 0:
            for o in obs:
                if o["log_size"] >= idx + len(onset_line.encode()):
                    onset = o["t"]
                    break
        evaluate(tag, obs, lb, ad_py, iv, True, True, onset)

    COAWST = "/data/xinyuan/COAWST_Yagi_FNL_WRF3km_Sep05-08/coawst_run.log"
    DL = "/data/xinyuan/data/Global_Argo_download.log"
    if Path(COAWST).exists():
        obs, lb, iv = load_from_log(COAWST, elapsed_rx=r"Timing for main:.*?:\s+([\d.]+) elapsed seconds",
                                    interval=60)
        evaluate("负样本 A · COAWST 三模式耦合 33.79h，跑到 SUCCESS COMPLETE",
                 obs, lb, S.load_adapter("coawst"), iv, False, False)
    if Path(DL).exists():
        obs, lb, iv = load_from_log(DL, wget=True, interval=60)
        evaluate("负样本 B · 18 GB 数据集下载 4.29h，成功完成",
                 obs, lb, S.load_adapter("download"), iv, False, False)


if __name__ == "__main__":
    main()
