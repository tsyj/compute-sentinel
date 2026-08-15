#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decide() 判定内核的单元测试 —— 纯标准库，不依赖 pytest。

    python3 tools/test_decide.py

这些用例钉住的是**判定语义**，不是实现细节。其中 T2 故意断言"修复前会漏报"，
用来说明这次改动修的到底是什么；T3/T4 是防回归的护栏 ——
把 CPU 从进展票里摘出去，绝不能反过来把纯 CPU 作业和预处理阶段打成卡死。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster_mcp_server import decide

STALL, DEG = 900, 300
FAILED = []


def check(name, got, want):
    ok = got == want
    print(f'{"PASS" if ok else "FAIL"}  {name}: got={got} want={want}')
    if not ok:
        FAILED.append(name)


def sig(log=False, file=False, cpu=None, gpu=None):
    """cpu/gpu 传 None 表示该信号不可采（不投票）。"""
    s = {"log": dict(changed=log, size=0, count=0),
         "file": dict(changed=file, count=1)}
    s["resource"] = dict(changed=cpu, detail="CPU") if cpu is not None else dict(changed=None, detail="-")
    if gpu is not None:
        s["gpu"] = dict(changed=gpu, available=True, util_pct=99 if gpu else 0, detail="GPU")
    return s


def drive(rounds, s, gpu_req, prev=None, iv=20, deg=DEG):
    """连续跑 N 轮相同信号，返回最后一轮结果。"""
    prev = prev or {"log": {}, "file": {}, "stalled_for_sec": 0, "interval_sec": iv}
    v = None
    for _ in range(rounds):
        v = decide(dict(s), prev, 600, STALL, 0, iv, gpu_req, deg)
        prev = dict(prev, stalled_for_sec=v["stalled_for_sec"],
                    gpu_ever_busy=v["gpu_ever_busy"])
    return v


print("── T1 首轮只建基线，不下判定 ──")
check("首轮 UNKNOWN", decide(sig(cpu=True), {}, 600, STALL, 0, 20, False, 0)["status"], "UNKNOWN")

print("\n── T2 GPU 退化路径：日志静默 + 无新产物 + CPU 打满 + GPU 闲置 ──")
degen = sig(log=False, file=False, cpu=True, gpu=False)
# 修复前：无 GPU 信号，CPU 直接算进展票 —— 永远 RUNNING，这就是漏报
check("修复前 60 轮仍 RUNNING（漏报）",
      drive(60, sig(log=False, file=False, cpu=True), False)["status"], "RUNNING")
# 修复后：CPU 票被降级，且 GPU 曾忙过 ⇒ 300s 阈值
warm = {"log": {}, "file": {}, "stalled_for_sec": 0, "interval_sec": 20, "gpu_ever_busy": True}
check("修复后 14 轮(280s) 尚未告警", drive(14, degen, True, dict(warm))["status"], "RUNNING")
check("修复后 15 轮(300s) 判定 STALLED", drive(15, degen, True, dict(warm))["status"], "STALLED")
check("退化画像被标记", drive(15, degen, True, dict(warm))["degradation_suspected"], True)

print("\n── T3 防回归：纯 CPU 作业（ROMS/WRF）CPU 忙仍算在推进 ──")
check("ROMS 60 轮 RUNNING", drive(60, sig(log=False, file=False, cpu=True), False)["status"], "RUNNING")

print("\n── T4 防回归：预处理阶段 CPU 忙 GPU 闲，GPU 从未忙过 ⇒ 不得用短阈值 ──")
cold = {"log": {}, "file": {}, "stalled_for_sec": 0, "interval_sec": 20, "gpu_ever_busy": False}
check("预处理 20 轮(400s) 不告警", drive(20, degen, True, dict(cold))["status"], "RUNNING")
check("预处理 45 轮(900s) 才到通用阈值", drive(45, degen, True, dict(cold))["status"], "STALLED")

print("\n── T5 任一进展信号存活即 RUNNING，且清零计数 ──")
check("有新产物 ⇒ RUNNING", drive(5, sig(file=True, cpu=True, gpu=False), True, dict(warm))["status"], "RUNNING")
check("有新产物 ⇒ 计数清零", drive(5, sig(file=True, cpu=True, gpu=False), True, dict(warm))["stalled_for_sec"], 0)

print("\n── T6 真实轮询间隔被采纳（原实现写死 300s，20s 轮询 3 轮就误报）──")
check("20s×3 轮累计 60s", drive(3, degen, True, dict(warm), iv=20)["stalled_for_sec"], 60)
check("20s×3 轮不告警", drive(3, degen, True, dict(warm), iv=20)["status"], "RUNNING")

print("\n── T7 全部信号不可采 ⇒ UNKNOWN，绝不误判 ──")
allnone = {"log": dict(changed=None), "file": dict(changed=None), "resource": dict(changed=None)}
check("全不可采 UNKNOWN", decide(allnone, dict(warm), 600, STALL, 0, 20, True, DEG)["status"], "UNKNOWN")

print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
sys.exit(1 if FAILED else 0)
