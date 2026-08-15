#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 GPU 机器上采集三类信号快照序列，输出 JSON Lines。

进程发现按**命令行子串**匹配，不按进程名 —— 同名 python.exe 满地都是，
按名字匹配既可能采错对象，也可能误伤别人的进程（见 evidence/case-02）。
"""
import argparse, glob, json, os, subprocess, time

PS = "powershell"

def _ps(cmd, timeout=25):
    try:
        return subprocess.run([PS, "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def gpu_stats():
    try:
        o = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[0]
        u, m = [int(x.strip()) for x in o.split(",")]
        return u, m
    except Exception:
        return -1, -1

def find_procs(match, self_pid):
    """返回 [(pid, kernel+user 时间(100ns), rss)]，按 CommandLine 子串匹配。

    必须排除采集器自身 —— 它的命令行里带着 --procmatch <match>，
    会把自己也匹配进去，导致 CPU 读数混入采集器、且目标退出后 alive 仍为真。
    """
    out = _ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Select-Object ProcessId,KernelModeTime,UserModeTime,WorkingSetSize,CommandLine | "
              "ConvertTo-Json -Compress")
    try:
        data = json.loads(out) if out.strip() else []
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    res = []
    for p in data:
        cl = p.get("CommandLine") or ""
        pid = int(p["ProcessId"])
        if pid == self_pid or "collect_signals" in cl:
            continue
        if match in cl:
            res.append((pid,
                        int(p.get("KernelModeTime") or 0) + int(p.get("UserModeTime") or 0),
                        int(p.get("WorkingSetSize") or 0)))
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--procmatch", required=True, help="目标进程 CommandLine 子串")
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--out", default="signals.jsonl")
    ap.add_argument("--quit-after-gone", type=int, default=2, help="连续几轮找不到进程才退出")
    a = ap.parse_args()

    logp = os.path.join(a.rundir, "train.log")
    self_pid = os.getpid()
    prev, gone = None, 0
    with open(a.out, "a", encoding="utf-8") as f:
        while True:
            procs = find_procs(a.procmatch, self_pid)
            alive = len(procs) > 0
            cputime = sum(p[1] for p in procs)
            rss = sum(p[2] for p in procs)

            lsz = os.path.getsize(logp) if os.path.exists(logp) else -1
            cks = sorted(glob.glob(os.path.join(a.rundir, "ckpt_*.pt")))
            cmt = max([os.path.getmtime(c) for c in cks], default=0)
            csz = sum(os.path.getsize(c) for c in cks)
            util, mem = gpu_stats()

            cpu_pct = None
            if prev and alive and prev["alive"]:
                dt = time.time() - prev["ts"]
                if dt > 0:
                    cpu_pct = round((cputime - prev["cputime"]) / 1e7 / dt * 100, 1)

            rec = dict(ts=round(time.time(), 2), t=time.strftime("%H:%M:%S"),
                       alive=alive, pids=[p[0] for p in procs],
                       log_size=lsz, log_grew=(prev is not None and lsz > prev["log_size"]),
                       ckpt_count=len(cks), ckpt_mtime=round(cmt, 2), ckpt_bytes=csz,
                       ckpt_new=(prev is not None and cmt > prev["ckpt_mtime"]),
                       gpu_util=util, gpu_mem=mem, cpu_pct=cpu_pct, rss=rss)
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
            print(f"{rec['t']} alive={alive} log={lsz} ckpt={len(cks)} gpu={util}% cpu={cpu_pct}", flush=True)

            prev = dict(ts=rec["ts"], log_size=lsz, ckpt_mtime=cmt, cputime=cputime, alive=alive)
            gone = gone + 1 if not alive else 0
            if gone >= a.quit_after_gone:
                break
            time.sleep(a.interval)

if __name__ == "__main__":
    main()
