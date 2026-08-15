#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把真实采集到的信号序列离线回放给判定内核，量化「多久才发现卡死」。

用的是 cluster_mcp_server.decide() —— **仓库里那份唯一的判定逻辑**，
不是为回放另写一份。这样回放结论和线上判定不会漂移。

输入:
  signals.jsonl  采集器输出的快照序列（每行一条，见 evidence/gpu-stall/）
  train.log      作业日志，用于按字节偏移还原每轮新增内容里的进度行条数
  adapters/*.json 该 workload 的进度判据与阈值

用法:
  python3 tools/replay_signals.py --signals evidence/gpu-stall/signals.jsonl \
      --log evidence/gpu-stall/train.log --workload pytorch --stall-from-line "ep 4 begin"
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_mcp_server as S


def build_signals(rec, prev, logbytes, prog_re, want_gpu):
    """把一条采集快照翻译成 decide() 认识的信号字典。"""
    sig = {}

    # 日志：不是"长了就算有进度"，要数新增片段里的进度行
    cur_size, prev_size = rec["log_size"], (prev["log_size"] if prev else -1)
    grew = cur_size > prev_size
    hits = 0
    if grew and prev_size >= 0:
        seg = logbytes[max(0, prev_size):max(0, cur_size)].decode("utf-8", "replace")
        hits = len(re.findall(prog_re, seg, re.M))
    has_prog = (hits != 0) if (grew and prev_size >= 0) else grew
    sig["log"] = dict(changed=bool(has_prog), size=cur_size, grew=bool(grew),
                      progress_lines=hits, detail=f"size={cur_size}, 进度行 {hits}")

    # 产物文件
    changed = bool(prev) and (rec["ckpt_mtime"] > prev["ckpt_mtime"]
                              or rec["ckpt_bytes"] > prev["ckpt_bytes"])
    if not prev:
        changed = rec["ckpt_count"] > 0
    sig["file"] = dict(changed=bool(changed), count=rec["ckpt_count"],
                       newest_mtime=rec["ckpt_mtime"], total_size=rec["ckpt_bytes"],
                       detail=f'{rec["ckpt_count"]} 个 ckpt, 合计 {rec["ckpt_bytes"]} B')

    # 资源（CPU）
    cpu = rec.get("cpu_pct")
    if cpu is None:
        sig["resource"] = dict(changed=None, detail="首轮无法算增量 CPU")
    else:
        sig["resource"] = dict(changed=bool(cpu > 50), detail=f"CPU {cpu}%")

    # GPU：只有 adapter 声明必须用 GPU 时才纳入
    if want_gpu:
        u = rec.get("gpu_util", -1)
        if u < 0:
            sig["gpu"] = dict(changed=None, available=False, detail="不可采")
        else:
            sig["gpu"] = dict(changed=bool(u >= 5), available=True, util_pct=u,
                              detail=f"GPU util {u}%")
    return sig


def run(records, logbytes, ad, gpu_mode: bool):
    prog_re = ad.get("progress_line_regex", ".")
    first_to = int(ad.get("first_progress_sec", 900))
    stall_to = int(ad.get("stall_sec", 1200))
    degraded_to = int((ad.get("gpu_signal") or {}).get("degraded_stall_sec", 0))
    prev_rec, prev_snap, out = None, {}, []
    for rec in records:
        iv = int(round(rec["ts"] - prev_rec["ts"])) if prev_rec else 0
        sig = build_signals(rec, prev_rec, logbytes, prog_re, gpu_mode)
        v = S.decide(sig, prev_snap, first_to, stall_to, 0, iv or 20, gpu_mode,
                     degraded_to if gpu_mode else 0)
        out.append(dict(t=rec["t"], ts=rec["ts"], status=v["status"], conf=v["confidence"],
                        stalled_for=v["stalled_for_sec"],
                        degraded=v["degradation_suspected"], note=v["note"],
                        gpu=rec.get("gpu_util"), cpu=rec.get("cpu_pct"),
                        log=rec["log_size"], ckpt=rec["ckpt_count"],
                        votes={k: v2.get("changed") for k, v2 in v["signals"].items()}))
        prev_snap = dict(log=v["signals"]["log"], file=v["signals"]["file"],
                         stalled_for_sec=v["stalled_for_sec"], interval_sec=iv or 20,
                         gpu_ever_busy=v["gpu_ever_busy"])
        prev_rec = rec
    return out


def first_stall(rows):
    for r in rows:
        if r["status"] == "STALLED":
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--workload", default="pytorch")
    ap.add_argument("--stall-from-line", default="",
                    help="日志里标志退化路径开始的那行；用于算发现时延")
    a = ap.parse_args()

    records = [json.loads(l) for l in Path(a.signals).read_text(encoding="utf-8").splitlines() if l.strip()]
    logbytes = Path(a.log).read_bytes()
    ad = S.load_adapter(a.workload)

    # 卡死真实发生的时刻：日志里最后一条进度行写下的时间
    onset = None
    if a.stall_from_line:
        idx = logbytes.find(a.stall_from_line.encode())
        if idx >= 0:
            size_at = idx + len(a.stall_from_line.encode())
            for r in records:
                if r["log_size"] >= size_at:
                    onset = r
                    break

    print(f"# 回放 {len(records)} 个快照 · workload={a.workload} · "
          f'stall_sec={ad.get("stall_sec")}s\n')

    for mode, label in ((False, "修复前（CPU 占用直接算进展票，无 GPU 信号）"),
                        (True,  "修复后（GPU 型 workload 上 CPU 忙+GPU 闲不算进展）")):
        rows = run(records, logbytes, ad, mode)
        fs = first_stall(rows)
        print(f"## {label}")
        print(f"{'时刻':>8} {'状态':>8} {'置信':>5} {'GPU':>5} {'CPU':>7} {'ckpt':>5}  票型")
        for r in rows:
            print(f'{r["t"]:>8} {r["status"]:>8} {r["conf"]:>5} '
                  f'{str(r["gpu"])+"%":>5} {str(r["cpu"])+"%":>7} {r["ckpt"]:>5}  '
                  + ",".join(f"{k}={v}" for k, v in r["votes"].items()))
        if fs:
            lat = f'{fs["ts"] - onset["ts"]:.0f}s' if onset else "—"
            print(f'→ 首次判定 STALLED: {fs["t"]}，距退化开始 {lat}\n')
        else:
            print(f"→ **全程未判定卡死（漏报）**\n")

    if onset:
        print(f'退化路径开始（日志最后一条进度行）: {onset["t"]}')


if __name__ == "__main__":
    main()
