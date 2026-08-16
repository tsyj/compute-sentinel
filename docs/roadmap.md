# Roadmap · 已做 / 未做

> 依据 [三路对抗审查第一轮](../../对抗审查_第一轮.md) 的缺口清单排序。
> **原则：做过的给证据链接，没做的写在这里，不含糊过去。**

## 已完成（有可复核证据）

| 项 | 证据 |
|---|---|
| 真实事故取证（4 个案例） | [case-01](../evidence/case-01-wrf-restart-alarm.md) · [case-02](../evidence/case-02-kill-by-name-is-broken.md) · [case-03](../evidence/case-03-agentteams-deployment.md) · [case-04](../evidence/case-04-quota-exhausted.md) |
| `config-precheck` 实现 + 四状态实测 | [measure-01](../evidence/measure-01-config-precheck.md) |
| Agent 拒绝编造的边界验证 | [measure-02](../evidence/measure-02-agent-refuses-to-guess.md) |
| 三 Agent 闭环实跑 | [measure-03](../evidence/measure-03-agents-advanced-an-open-question.md) |
| 人工复核拦下错误机制 + WRF 源码缺陷 | [measure-04](../evidence/measure-04-human-review-caught-a-wrong-mechanism.md) |
| 可观测 Trace/Log/Metrics 三类采集 | [measure-05](../evidence/measure-05-observability.md) |
| 凭证隔离对照实验 | [case-03](../evidence/case-03-agentteams-deployment.md) |
| GPU 退化路径卡死实测（旧判据全程漏报，修复后 909s / 318s） | [measure-06](../evidence/measure-06-gpu-stall.md) · [measure-07](../evidence/measure-07-sampling-rate.md) |
| `progress-probe` 误报侧 + 性能（40.6h 零误报；100 作业巡检 11s→0.19s） | [measure-08](../evidence/measure-08-false-positive-and-perf.md) · [measure-09](../evidence/measure-09-baseline.md) |
| Executor L2 审批闭环（停住 18 分 39 秒；L3 拒绝；回滚点） | [measure-10](../evidence/measure-10-l2-approval.md) |
| Verifier 独立复核 | [measure-11](../evidence/measure-11-verifier.md) |
| Token 消耗与 Tool 成功率 | [measure-12](../evidence/measure-12-token-cost.md) |
| MCP Server + 集成契约 | [integration-contract.md](integration-contract.md) · `tools/cluster_mcp_server.py` |
| 项目一页纸（手册附录 C） | [one-pager.md](one-pager.md) |

## 未完成（按优先级）

### P1 · 影响核心评分维度

| 项 | 缺什么 | 为什么重要 |
|---|---|---|
| ~~**MCP 等价集成契约**~~ | ✅ 已完成：直接使用 MCP（`tools/cluster_mcp_server.py`，stdio / JSON-RPC 2.0），协议、鉴权、输入输出 Schema、错误处理、审计记录、迁移成本六项见 [integration-contract.md](integration-contract.md) | 手册明文要求"未使用 MCP 需给出等价集成契约" |
| ~~**RAG 四项落地 2 项**~~ | ✅ 已补论证：共享状态管理 + 轨迹可观测两项达成，含上下文机制有效性论证，见 [requirements-mapping.md](requirements-mapping.md) 的 9.4 一节 | 官方技术要求：4 项中至少落地 2 项 |
| ~~**`progress-probe` 可运行原型**~~ | ✅ 已完成：判定内核 `decide()` 实现于 `tools/cluster_mcp_server.py`（0.1.0 → 0.2.0），17 项语义单测 + GPU 卡死回放（909s / 318s）+ 误报侧回放随 `./verify.sh` 每次必跑 | Skill 工程体系 25% |
| **重复实验验证稳定性** | 同一事故只跑过 1 次，Agent 输出有随机性 | 能力评估的可信度 |

### P2 · 完备性

| 项 | 缺什么 |
|---|---|
| Executor 安全分级实证 | L2 审批、L3 拒绝、回滚点已实证（[measure-10](../evidence/measure-10-l2-approval.md)：审批点停住 18 分 39 秒一字节未改）；**幂等键仍未实跑；沙箱不可用问题也需一并解决**（见 measure-05） |
| Curator 实跑 | 六 Agent 已跑通五个（Sentinel→Triage→Planner→Executor→Verifier），Curator 未跑，闭环第 8 步「经验沉淀」仍无实证 |
| 其余 workload adapter | 已有两台机器、三类 workload 的实测（气象海洋耦合；GPU 训练于 RTX 4090，measure-06/07/09；下载任务误报侧，measure-08）；CAE / EDA、生信、批处理仍无实证 |
| ~~八步闭环逐条对照入库~~ | ✅ 已入库：[requirements-mapping.md](requirements-mapping.md) 8.3 节，逐步标注状态（第 2、8 步 ⚠️） |
| Skill 清单汇总表（手册附录 B 格式） | 8 个 SKILL.md 内容齐全，但缺一张按模板排布的汇总表 |
| ~~项目一页纸（手册附录 C）~~ | ✅ 已完成：[one-pager.md](one-pager.md) |

### P3 · 提交物

作品简介（≤500 字，✅ 已完成，499 字）、方案 PPT（✅ 已完成，17 页，`build_ppt.py` 生成）、讲解视频（复赛必交）—— 初赛截止 **2026-08-16 23:59**。

## 由实测直接催生的设计改动

1. **每个 Agent 的 `Outputs` 必须显式写"交给谁"** —— 来自 measure-03，
   "不做 X（那是 Y 的职责）"不构成"交给 Y"，链路会静默断在中间。已写入 `agent-identity.md`。
2. **安全机制不可用时应收紧权限而非降级继续** —— 来自 measure-05，
   沙箱不可用时当前是"降级为无沙箱照常跑"，应改为"拒绝授予 L1 及以上"。
