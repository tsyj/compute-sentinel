#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构造一次真实的「进程活着、CPU 满载、GPU 0%、日志不出进度行」训练卡死。

前 N 个 epoch 正常训练：GPU 高负载、日志出 ep 行、checkpoint 正常写。
第 N+1 个 epoch 进入退化路径 —— 模拟真实事故中的 O(N^2) CPU 分支：
进程仍在运行、CPU 打满、但 GPU 掉到 0%、不再输出进度行、不再写 checkpoint。

这不是 sleep 假装卡死 —— 是真的在 CPU 上做无用的重计算，与真实退化路径同构。
"""
import argparse, os, sys, time
import torch
import torch.nn as nn


def log(f, msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    f.write(line + "\n"); f.flush()
    os.fsync(f.fileno())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="run")
    ap.add_argument("--good-epochs", type=int, default=3)
    ap.add_argument("--stall-seconds", type=int, default=600)
    ap.add_argument("--steps", type=int, default=60)
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    lf = open(os.path.join(a.outdir, "train.log"), "a", encoding="utf-8")

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    log(lf, f"start device={dev} torch={torch.__version__} "
            f"gpu={torch.cuda.get_device_name(0) if dev=='cuda' else '-'}")

    # 一个够大的模型，保证 GPU 真的忙起来
    model = nn.Sequential(
        nn.Linear(4096, 8192), nn.ReLU(),
        nn.Linear(8192, 8192), nn.ReLU(),
        nn.Linear(8192, 4096),
    ).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=1e-4)
    lossf = nn.MSELoss()

    for ep in range(1, a.good_epochs + 1):
        t0 = time.time(); tot = 0.0
        for _ in range(a.steps):
            x = torch.randn(256, 4096, device=dev)
            y = torch.randn(256, 4096, device=dev)
            opt.zero_grad(); out = model(x)
            loss = lossf(out, y); loss.backward(); opt.step()
            tot += loss.item()
        if dev == "cuda":
            torch.cuda.synchronize()
        ck = os.path.join(a.outdir, f"ckpt_ep{ep}.pt")
        torch.save({"ep": ep, "sd": model.state_dict()}, ck)
        log(lf, f"ep {ep} loss={tot/a.steps:.5f} time={time.time()-t0:.1f}s ckpt={os.path.basename(ck)}")

    # ── 退化路径：CPU 密集、GPU 闲置、无日志、无产物 ──
    log(lf, f"ep {a.good_epochs+1} begin")     # 最后一条进度行，之后彻底静默
    lf.close()

    deadline = time.time() + a.stall_seconds
    acc = 0
    # 纯 CPU 的无效重计算，不碰 GPU、不写任何文件、不打印
    while time.time() < deadline:
        s = 0
        for i in range(200000):
            s = (s * 31 + i) & 0xFFFFFFFF
        acc = (acc + s) & 0xFFFFFFFF
    sys.exit(0)


if __name__ == "__main__":
    main()
