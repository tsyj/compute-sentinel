#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe-kill 的目标选择器 —— 三重判据，缺一即拒。

**这个模块本身不杀任何进程。** 它只回答一个问题：
「这些候选进程里，哪些是被允许操作的，哪些必须拒绝、理由是什么。」
真正的终止动作由 Executor 在拿到人工审批后执行，且恒为 L2。

三重判据（必须同时满足）：

  1. 属主 == 事故发起人本人
  2. 命令行参数包含事故登记的输入文件
  3. PGID 在事故登记时记录的范围内

这三条来自一次真实事故（evidence/case-02）：按进程名批量 kill，
差点干掉另一位用户 128 核的作业 —— **只因跨用户 kill 是 EPERM 静默失败才幸存**。
权限边界当时是最后一道墙，而它本不该是唯一一道。

设计取舍：本模块不追求「尽量把该杀的都杀掉」。
匹配不确定时**宁可少杀、让人工介入**，也不做启发式扩大匹配。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Proc:
    """一个候选进程。字段对应 `ps -eo user,pid,pgid,args`。"""
    user: str
    pid: int
    pgid: int
    args: str


@dataclass
class Registration:
    """事故登记：发起人是谁、作业用哪个输入文件、当时的 PGID 有哪些。

    登记必须发生在事故发现时，**不允许事后补登记** ——
    否则"PGID 在登记范围内"这一条就失去意义。
    """
    owner: str
    input_file: str
    pgids: frozenset[int]
    note: str = ""


@dataclass
class Decision:
    proc: Proc
    allowed: bool
    reasons: list[str] = field(default_factory=list)


# 名称匹配语义一律拒绝：pkill / killall 风格的模式串不是本模块的输入
_NAME_PATTERN = re.compile(r"[*?\[\]]|^-f$")


def select(reg: Registration, candidates: list[Proc],
           pattern_hint: str = "") -> tuple[list[Decision], list[str]]:
    """
    返回 (逐进程决定, 全局拒绝理由)。

    全局拒绝理由非空时，**整次调用作废**，不返回任何允许项 ——
    例如调用方传进来的是一个名称通配模式而不是登记信息。
    """
    fatal: list[str] = []

    if pattern_hint and _NAME_PATTERN.search(pattern_hint):
        fatal.append(
            f"拒绝：本 Skill 不接受名称匹配语义（收到 {pattern_hint!r}）。"
            "按名称匹配正是 case-02 事故的成因，不提供该入口。")
    if not reg.owner or not reg.input_file or not reg.pgids:
        fatal.append("拒绝：事故登记不完整（owner / input_file / pgids 缺一不可），无法建立可信目标集。")

    if fatal:
        return [Decision(p, False, ["整次调用已作废"]) for p in candidates], fatal

    out: list[Decision] = []
    for p in candidates:
        why: list[str] = []
        if p.user != reg.owner:
            why.append(f"属主不符：进程属主 {p.user}，事故发起人 {reg.owner}")
        if reg.input_file not in p.args:
            why.append(f"命令行未包含登记的输入文件 {reg.input_file}")
        if p.pgid not in reg.pgids:
            why.append(f"PGID {p.pgid} 不在登记范围 {sorted(reg.pgids)} 内")
        out.append(Decision(p, not why, why))
    return out, []


def summarize(decisions: list[Decision], fatal: list[str]) -> dict:
    allow = [d.proc.pid for d in decisions if d.allowed]
    refuse = {d.proc.pid: d.reasons for d in decisions if not d.allowed}
    return dict(
        fatal=fatal,
        allowed_pids=allow,
        refused_pids=refuse,
        # 空集是合法结果，不是错误：目标可能已自行退出（幂等）
        note=("目标集为空，视为幂等成功（进程可能已自行退出）"
              if not allow and not fatal and not refuse else ""),
    )
