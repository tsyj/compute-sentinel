#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe-kill 的对抗测试集 —— 这就是它 SKILL.md 里写的「能力评估」，现在真跑。

    python3 tools/test_safe_kill.py

**不用统计指标。** 准确率 99% 对这个 Skill 是失败 ——
剩下的 1% 就是别人的 128 核作业。通过标准是：**全部通过，一条不过即不可发布。**

用例取自 evidence/case-02 那次真实事故的现场形态：
同一台机器上同时跑着三个 COAWST 作业，comm 全是 `coawstM`，
其中一个属于另一位用户、128 核。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_kill import Proc, Registration, select, summarize

FAILED = []


def check(name, got, want, detail=""):
    ok = got == want
    print(f'{"PASS" if ok else "FAIL"}  {name}')
    if detail:
        print(f'        {detail}')
    if not ok:
        print(f'        got={got!r}  want={want!r}')
        FAILED.append(name)


# ── 事故现场：登记的是「我自己的、用 roms_real.in 的、PGID 在 {40100,40101} 的那个作业」──
REG = Registration(owner="xinyuan", input_file="roms_real.in",
                   pgids=frozenset({40100, 40101}),
                   note="seamount pilot，事故发现时登记")

MINE_A = Proc("xinyuan", 40120, 40100, "/data/x/coawstM roms_real.in")
MINE_B = Proc("xinyuan", 40121, 40101, "/data/x/coawstM roms_real.in")
# 另一位用户的 128 核挖矿运行 —— comm 同样是 coawstM
OTHERS = Proc("jiaxin", 51200, 51200, "/home/jiaxin/coawstM roms_mining_3mon_128cpu.in")
# 我自己的另一个作业，同名 binary、不同输入文件
MINE_OTHER_JOB = Proc("xinyuan", 40999, 40990, "/data/x/coawstM_yagi_AB_fixed yagi.in")
# 事故登记之后新起的同类进程
LATE = Proc("xinyuan", 41500, 41500, "/data/x/coawstM roms_real.in")


print("── 用例 1 · 正常目标：三条判据全满足，应当放行 ──")
d, f = select(REG, [MINE_A, MINE_B])
r = summarize(d, f)
check("两个登记内的进程被放行", r["allowed_pids"], [40120, 40121])
check("无拒绝项", r["refused_pids"], {})

print("\n── 用例 2 · 跨用户同名进程：必须拒绝，理由为属主不符 ──")
d, f = select(REG, [MINE_A, OTHERS])
r = summarize(d, f)
check("他人进程被拒", 51200 in r["refused_pids"], True)
check("我的进程仍放行", r["allowed_pids"], [40120])
check("拒绝理由含属主不符", any("属主不符" in x for x in r["refused_pids"][51200]), True,
      r["refused_pids"][51200][0])

print("\n── 用例 3 · 同名 binary 不同作业：只匹配 args 登记的那个 ──")
d, f = select(REG, [MINE_A, MINE_OTHER_JOB])
r = summarize(d, f)
check("另一个作业被拒", 40999 in r["refused_pids"], True)
check("拒绝理由含输入文件不符",
      any("输入文件" in x for x in r["refused_pids"][40999]), True,
      r["refused_pids"][40999][0])

print("\n── 用例 4 · PGID 越界：登记之后新起的同类进程必须拒绝 ──")
d, f = select(REG, [LATE])
r = summarize(d, f)
check("越界进程被拒", 41500 in r["refused_pids"], True)
check("拒绝理由含 PGID 不在范围",
      any("PGID" in x for x in r["refused_pids"][41500]), True,
      r["refused_pids"][41500][0])

print("\n── 用例 5 · 名称匹配语义：整次调用作废 ──")
for pat in ("coawstM*", "-f", "coawst?", "coawstM[12]"):
    d, f = select(REG, [MINE_A], pattern_hint=pat)
    r = summarize(d, f)
    check(f"模式串 {pat!r} 被拒且无放行项",
          (len(r["fatal"]) > 0, r["allowed_pids"]), (True, []))

print("\n── 用例 6 · 空匹配：目标已自行退出，幂等成功而非报错 ──")
d, f = select(REG, [])
r = summarize(d, f)
check("无 fatal", r["fatal"], [])
check("无放行项也无拒绝项", (r["allowed_pids"], r["refused_pids"]), ([], {}))
check("标注为幂等成功", "幂等" in r["note"], True, r["note"])

print("\n── 用例 7 · 登记不完整：不允许在信息不全时动手 ──")
for bad, why in ((Registration("", "roms_real.in", frozenset({40100})), "缺 owner"),
                 (Registration("xinyuan", "", frozenset({40100})), "缺 input_file"),
                 (Registration("xinyuan", "roms_real.in", frozenset()), "缺 pgids")):
    d, f = select(bad, [MINE_A])
    r = summarize(d, f)
    check(f"{why} 时整次作废", (len(r["fatal"]) > 0, r["allowed_pids"]), (True, []))

print("\n── 用例 8 · 三条判据里只满足两条，仍必须拒绝（缺一即拒）──")
two_of_three = Proc("xinyuan", 42000, 42000, "/data/x/coawstM roms_real.in")  # PGID 不在范围
d, f = select(REG, [two_of_three])
r = summarize(d, f)
check("满足属主+args 但 PGID 越界 → 拒绝", r["allowed_pids"], [])

print("\n" + ("全部通过 —— 按 SKILL.md 的标准，本版本可发布"
             if not FAILED else f"失败 {len(FAILED)} 项：{FAILED}\n本版本不可发布"))
sys.exit(1 if FAILED else 0)
