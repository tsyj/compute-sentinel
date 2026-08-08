---
name: postmortem-write
description: |
  事故闭环后生成复盘，把新故障模式写回 Runbook，为 crash-triage 提新判据、为 safe-kill 提新护栏。知识入库走 PR，需人工 review。
  Triggers: "复盘", "总结一下", "postmortem", "写回文档", "沉淀"。
metadata:
  status: design
  risk_level: L1
  version: 0.1.0
---

# postmortem write

> 规格草案。字段按赛道一参赛手册附录 B「Skill 清单模板」组织，
> 完整输入输出 Schema、失败处理与判据表在复赛阶段补齐。

## 使用场景

事故闭环后生成复盘，把新故障模式写回 Runbook，为 crash-triage 提新判据、为 safe-kill 提新护栏。知识入库走 PR，需人工 review。

## 输入 / 输出

待定稿。

## 调用条件

待定稿。

## 依赖工具 / 系统

集群适配器（MCP 或等价集成契约）。凭证由网关注入，Skill 不持有真实凭证。

## 失败处理

统一原则：**证据不足时输出"缺什么"，不猜、不编造结论。**

## 权限与安全

风险等级 L1。分级定义见 [README](../../README.md#安全分级)。

## 复用价值

领域差异收敛到 adapter 配置，Skill 逻辑与 workload 无关。
