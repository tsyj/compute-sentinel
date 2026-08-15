#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从**历史运行日志**重建信号快照序列并回放判定，用来测误报率。

为什么需要它：measure-06/07 用的是我们自己造的卡死，只能测**漏报**。
而误报才是这个 Skill 的首要压制项 —— 一个爱叫的告警系统会被直接关掉。
测误报需要的是**真值为"一直在正常运行"的样本**，也就是成功跑完的历史作业。

重建方法：长时作业的日志里通常带着自己的时间信息 ——
  · 绝对时间戳（wget、下载器、带 timestamp 的应用日志）→ --ts-regex
  · 每步耗时增量（WRF 的 "Timing for main: ... elapsed seconds"）→ --elapsed-regex
两者都能还原出 (墙钟时刻 → 日志字节偏移) 的映射，再按采样周期切片，
就得到与线上采集同构的快照序列。日志字节数是真的，时间轴是日志自己写的。

产物文件信号：给了 --out-dir 就用真实 mtime；没给则标为不可采。
**不可采比可采更严格**（少一票进展票，更容易判卡死），所以这个默认是保守的。

用法:
  # WRF/COAWST：按每步 elapsed seconds 重建
  python3 tools/replay_from_log.py --log run.log --workload wrf \\
      --elapsed-regex 'Timing for main:.*?:\\s+([\\d.]+) elapsed seconds' \\
      --interval 300 --sweep

  # 下载任务：按 wget 进度行的累计字节与速率重建
  python3 tools/replay_from_log.py --log dl.log --workload download --wget --interval 300
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_mcp_server as S

KB = 1024


def timeline_from_elapsed(path: Path, rx: str):
    """每行给出一个耗时增量 → 累加成墙钟。返回 [(t_sec, byte_offset)]。"""
    pat = re.compile(rx.encode(), re.S)
    t, off, pts = 0.0, 0, []
    with path.open("rb") as f:
        for line in f:
            off += len(line)
            m = pat.search(line)
            if m:
                try:
                    t += float(m.group(1))
                except (ValueError, IndexError):
                    continue
                pts.append((t, off))
    return pts


def timeline_from_timestamps(path: Path, rx: str, fmt_groups=6):
    """每行带绝对时间戳 → 相对首行的秒数。"""
    pat = re.compile(rx.encode())
    t0, pts, off = None, [], 0
    with path.open("rb") as f:
        for line in f:
            off += len(line)
            m = pat.search(line)
            if not m:
                continue
            g = [int(x) for x in m.groups()[:6]]
            sec = ((g[2] * 24 + g[3]) * 60 + g[4]) * 60 + g[5]   # 日*24h + 时分秒
            if t0 is None:
                t0 = sec
            pts.append((sec - t0, off))
    return pts


def timeline_from_wget(path: Path):
    """
    wget 点状进度行：`  99800K .......... ....  0%  990K 7h39m`
    每行给出累计 KB 与瞬时速率。用 Δ字节/速率 积分出耗时，
    得到 (墙钟, 日志字节偏移, 已下载字节)。
    """
    pat = re.compile(rb"^\s*(\d+)K[ .]+\s*\d+%\s+([\d.]+)([KMG])\s")
    t, off, pts, prev_kb = 0.0, 0, [], None
    with path.open("rb") as f:
        for line in f:
            off += len(line)
            m = pat.match(line)
            if not m:
                continue
            kb = int(m.group(1))
            rate = float(m.group(2)) * {b"K": 1, b"M": 1024, b"G": 1024 * 1024}[m.group(3)]
            if prev_kb is not None and rate > 0:
                t += (kb - prev_kb) / rate
            prev_kb = kb
            pts.append((t, off, kb * KB))
    return pts


def sample(pts, interval: int):
    """把 (t, off[, bytes]) 折线按固定周期采样，得到快照序列。"""
    if not pts:
        return []
    end = pts[-1][0]
    snaps, i = [], 0
    tick = 0.0
    while tick <= end:
        while i + 1 < len(pts) and pts[i + 1][0] <= tick:
            i += 1
        p = pts[i]
        snaps.append(dict(t=tick, log_size=p[1], dl_bytes=(p[2] if len(p) > 2 else None)))
        tick += interval
    return snaps


def replay(snaps, logbytes, ad, stall_to: int, interval: int, out_files=None):
    """回放。返回 (判定序列, 误报次数)。真值恒为 RUNNING，任何 STALLED 即误报。"""
    prog_re = ad.get("progress_line_regex", ".")
    first_to = int(ad.get("first_progress_sec", 900))
    prev_snap, rows, fp = {}, [], 0
    prev = None
    for s in snaps:
        sig = {}
        cur, pv = s["log_size"], (prev["log_size"] if prev else -1)
        grew = cur > pv
        hits = 0
        if grew and pv >= 0:
            seg = logbytes[max(0, pv):max(0, cur)].decode("utf-8", "replace")
            hits = len(re.findall(prog_re, seg, re.M))
        has = (hits != 0) if (grew and pv >= 0) else grew
        sig["log"] = dict(changed=bool(has), size=cur, grew=bool(grew), progress_lines=hits,
                          detail=f"size={cur}, 进度行 {hits}")

        if s["dl_bytes"] is not None:      # 下载任务：产物就是那个正在写的文件
            pb = prev["dl_bytes"] if prev else -1
            sig["file"] = dict(changed=bool(s["dl_bytes"] > pb), count=1,
                               total_size=s["dl_bytes"], newest_mtime=int(s["t"]),
                               detail=f'已下载 {s["dl_bytes"]} B')
        elif out_files:
            newest = max((m for m, _ in out_files if m <= s["t"]), default=-1)
            pn = prev.get("newest", -2) if prev else -2
            sig["file"] = dict(changed=bool(newest > pn), count=sum(1 for m, _ in out_files if m <= s["t"]),
                               newest_mtime=newest, total_size=0, detail=f"最新产物 t={newest}")
            s["newest"] = newest
        else:
            # 没有产物 mtime 序列时标为不可采 —— 少一票进展票，判定更容易喊卡死，
            # 也就是说这个默认让误报测试更严格，不是更宽松。
            sig["file"] = dict(changed=None, count=0, detail="无产物 mtime 序列，不可采")

        sig["resource"] = dict(changed=None, detail="历史回放无法采资源")

        v = S.decide(sig, prev_snap, first_to, stall_to, 0, interval, False, 0)
        if v["status"] == "STALLED":
            fp += 1
        rows.append((s["t"], v["status"], v["stalled_for_sec"]))
        prev_snap = dict(log=v["signals"]["log"], file=v["signals"]["file"],
                         stalled_for_sec=v["stalled_for_sec"], interval_sec=interval)
        prev = dict(log_size=s["log_size"], dl_bytes=s["dl_bytes"], newest=s.get("newest", -2))
    return rows, fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--workload", default="generic")
    ap.add_argument("--elapsed-regex", default="")
    ap.add_argument("--ts-regex", default="")
    ap.add_argument("--wget", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--out-dir", default="", help="产物目录，用真实 mtime 作为文件信号")
    ap.add_argument("--sweep", action="store_true", help="扫 stall_sec，给出误报-阈值曲线")
    a = ap.parse_args()

    p = Path(a.log)
    if a.wget:
        pts = timeline_from_wget(p)
    elif a.elapsed_regex:
        pts = timeline_from_elapsed(p, a.elapsed_regex)
    elif a.ts_regex:
        pts = timeline_from_timestamps(p, a.ts_regex)
    else:
        sys.exit("需要 --wget / --elapsed-regex / --ts-regex 之一")

    if not pts:
        sys.exit("未能从日志重建出任何时间点，检查正则")

    total = pts[-1][0]
    logbytes = p.read_bytes()
    ad = S.load_adapter(a.workload)
    snaps = sample(pts, a.interval)

    out_files = None
    if a.out_dir:
        import os
        d = Path(a.out_dir)
        fs = [(os.path.getmtime(f), f.name) for f in d.iterdir() if f.is_file()]
        if fs:
            base = min(m for m, _ in fs)
            out_files = sorted((m - base, n) for m, n in fs)

    print(f"# 误报测试 · {p.name}")
    print(f"# 真值：该作业**成功跑完**，全程应判 RUNNING，任何一次 STALLED 都是误报")
    print(f"# 重建时间轴 {total/3600:.2f} 小时，{len(pts)} 个进度点；"
          f"按 {a.interval}s 采样得 {len(snaps)} 个快照\n")

    if not a.sweep:
        rows, fp = replay(snaps, logbytes, ad, int(ad.get("stall_sec", 1200)), a.interval, out_files)
        print(f"stall_sec={ad.get('stall_sec')}s → 误报 {fp}/{len(rows)}")
        return

    print(f"{'stall_sec':>10} {'误报次数':>8} {'误报率':>9}   说明")
    base = int(ad.get("stall_sec", 1200))
    for st in (60, 120, 300, 600, 900, 1200, 1800, 3600):
        rows, fp = replay(snaps, logbytes, ad, st, a.interval, out_files)
        mark = "  ← adapter 当前值" if st == base else ""
        print(f"{st:>10} {fp:>8} {fp/len(rows)*100:>8.2f}%{mark}")


if __name__ == "__main__":
    main()
