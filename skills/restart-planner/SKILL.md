---
name: restart-planner
description: |
  决定作业能否从断点续跑、从哪个断点续、需要修改哪些配置项、重跑代价多大。
  Triggers: "续跑", "restart", "断点", "重启作业", "从哪续", "checkpoint", "接着跑"。
metadata:
  status: design
  risk_level: L0
  version: 0.1.0
---

# restart planner

> 规格草案。字段按赛道一参赛手册附录 B「Skill 清单模板」组织，
> 完整输入输出 Schema、失败处理与判据表在复赛阶段补齐。

## 使用场景

决定作业能否从断点续跑、从哪个断点续、需要修改哪些配置项、重跑代价多大。

## 输入 / 输出

待定稿。

## 调用条件

待定稿。

## 依赖工具 / 系统

集群适配器（MCP 或等价集成契约）。凭证由网关注入，Skill 不持有真实凭证。

## 失败处理

统一原则：**证据不足时输出"缺什么"，不猜、不编造结论。**

## 权限与安全

风险等级 L0。分级定义见 [README](../../README.md#安全分级)。

## 复用价值

领域差异收敛到 adapter 配置，Skill 逻辑与 workload 无关。
