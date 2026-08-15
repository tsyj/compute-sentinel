# Roadmap · 已做 / 未做

> 依据 [三路对抗审查第一轮](../../对抗审查_第一轮.md) 的缺口清单排序。
> **原则：做过的给证据链接，没做的写在这里，不含糊过去。**

## 已完成（有可复核证据）

| 项 | 证据 |
|---|---|
| 真实事故取证（3 个案例） | [case-01](../evidence/case-01-wrf-restart-alarm.md) · [case-02](../evidence/case-02-kill-by-name-is-broken.md) · [case-03](../evidence/case-03-agentteams-deployment.md) |
| `config-precheck` 实现 + 四状态实测 | [measure-01](../evidence/measure-01-config-precheck.md) |
| Agent 拒绝编造的边界验证 | [measure-02](../evidence/measure-02-agent-refuses-to-guess.md) |
| 三 Agent 闭环实跑 | [measure-03](../evidence/measure-03-agents-advanced-an-open-question.md) |
| 人工复核拦下错误机制 + WRF 源码缺陷 | [measure-04](../evidence/measure-04-human-review-caught-a-wrong-mechanism.md) |
| 可观测 Trace/Log/Metrics 三类采集 | [measure-05](../evidence/measure-05-observability.md) |
| 凭证隔离对照实验 | [case-03](../evidence/case-03-agentteams-deployment.md) |

## 未完成（按优先级）

### P1 · 影响核心评分维度

| 项 | 缺什么 | 为什么重要 |
|---|---|---|
| **MCP 等价集成契约** | 协议、鉴权、输入输出 Schema、错误处理、审计记录、迁移成本 —— 一份都没有 | 手册明文要求"未使用 MCP 需给出等价集成契约" |
| ~~**RAG 四项落地 2 项**~~ | ✅ 已补论证：共享状态管理 + 轨迹可观测两项达成，含上下文机制有效性论证，见 [requirements-mapping.md](requirements-mapping.md) 的 9.4 一节 | 官方技术要求：4 项中至少落地 2 项 |
| **`progress-probe` 可运行原型** | 宣称的核心创新，目前只有规格文档 | Skill 工程体系 25% |
| **重复实验验证稳定性** | 同一事故只跑过 1 次，Agent 输出有随机性 | 能力评估的可信度 |

### P2 · 完备性

| 项 | 缺什么 |
|---|---|
| Executor 安全分级实证 | L0–L3、审批、回滚、幂等键全部未实跑；**沙箱不可用问题也需一并解决**（见 measure-05） |
| Verifier / Curator 实跑 | 六 Agent 只跑通三个 |
| 非本机 workload adapter | 全部证据来自同一台机器、同一类 workload，可迁移性缺实证 |
| 八步闭环逐条对照入库 | 现只在比赛资料目录，不在仓库 |
| Skill 清单汇总表（手册附录 B 格式） | 8 个 SKILL.md 内容齐全，但缺一张按模板排布的汇总表 |
| 项目一页纸（手册附录 C） | 未做 |

### P3 · 提交物

作品简介（≤500 字）、方案 PPT、讲解视频 —— 初赛截止 **2026-08-16 23:59**。

## 由实测直接催生的设计改动

1. **每个 Agent 的 `Outputs` 必须显式写"交给谁"** —— 来自 measure-03，
   "不做 X（那是 Y 的职责）"不构成"交给 Y"，链路会静默断在中间。已写入 `agent-identity.md`。
2. **安全机制不可用时应收紧权限而非降级继续** —— 来自 measure-05，
   沙箱不可用时当前是"降级为无沙箱照常跑"，应改为"拒绝授予 L1 及以上"。
