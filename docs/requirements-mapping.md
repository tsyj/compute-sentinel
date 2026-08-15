# 赛题技术要求逐条对照

> 对照《赛道一 新智基座 Agent Infra 参赛手册》第 8–11 章。
> **每一条都给出：我们做到什么程度、证据在哪、没做到的部分是什么。**
> 状态只有三种：✅ 已实现且有证据 ／ ⚠️ 已实现但证据不足 ／ ❌ 未实现。

---

## 8.1 多 Agent 协同要求

| 要求 | 状态 | 我们的落地与证据 |
|---|---|---|
| 至少 3 个**不同职能**的 Agent | ✅ | 设计 6 个：Sentinel / Triage / Planner / Executor / Verifier / Curator。**实跑 3 个**（Sentinel→Triage→Planner），见 [measure-03](../evidence/measure-03-agents-advanced-an-open-question.md) |
| 每个 Agent 有清晰身份定义 | ✅ | [`docs/agent-identity.md`](agent-identity.md)，按附录 A 八字段 |
| 通过协作完成端到端闭环 | ⚠️ | 发现→诊断→规划走通并留有完整对话记录；**执行→验证→沉淀三段未实跑** |
| **必须以 AgentTeams 作为协同设计基点** | ✅ | AgentTeams v1.2.2 实际部署（6 容器），Worker CRD 见 [`agents/*.worker.yaml`](../agents/)，部署验证见 [case-03](../evidence/case-03-agentteams-deployment.md) |
| 说明角色编排如何映射到框架能力 | ✅ | Manager-Workers 模型；角色=Worker CRD，编排=Matrix 房间 mention，上下文=共享文件系统，状态追踪=Matrix 会话 + governance/audit.db |

## 8.2 Agent Identity 清单

✅ [`docs/agent-identity.md`](agent-identity.md) —— 6 个 Agent 各按附录 A 的八字段填写。

两条自定的设计原则写在文档开头：
1. 每个 Agent 必须明写**不能做什么**（能力边界不是靠"没提到"来划的）；
2. Outputs 必须**正面写明交给谁** —— 实跑中吃过亏：Triage 的 SOUL 里写了
   "不要生成修复方案（那是 Planner 的职责）"，这种**否定式表述不会触发交接**，
   Triage 直接停在那里。见 [measure-03](../evidence/measure-03-agents-advanced-an-open-question.md)。

## 8.3 多 Agent 闭环八步

| # | 手册要求 | 状态 | 落地 |
|---|---|---|---|
| 1 | 任务输入 | ✅ | 操作者在 Matrix 房间用自然语言投递事故（非结构化任务），[transcript](../evidence/transcript-run2-sentinel-triage.txt) |
| 2 | 任务拆解 | ⚠️ | 实跑用的是 Matrix mention 触发，**Manager 的 taskflow 派单未真正使用** |
| 3 | 上下文传递 | ✅ | 共享文件系统 `shared/knowledge/`；实跑中 Triage 读到了 Sentinel 落盘的材料 |
| 4 | 工具调用 | ✅ | Skill + 自研 `cluster-mcp-server`（MCP over stdio，4 只读工具），[契约](integration-contract.md) |
| 5 | 结果验证 | ❌ | Verifier 未实跑 |
| 6 | 执行证据沉淀 | ✅ | 审计库 56 span + 完整对话记录 + 判定回放输出，全部入库 |
| 7 | 审批与回滚 | ❌ | L0–L3 分级已设计，**Executor 未实跑**，审批与回滚未验证 |
| 8 | 经验沉淀 | ⚠️ | 复盘写回 Runbook 的机制已设计；实跑中人工复核**拦下了 Agent 一个错误机制**，验证了"知识入库需人工 review"这条边界（[measure-04](../evidence/measure-04-human-review-caught-a-wrong-mechanism.md)），但 Curator 本身未跑 |

**第 5、7 步是本方案当前最大的缺口，不掩饰。**

---

## 9.1 Skill 要求（**必选项**）

✅ 8 个 Skill，均为独立 `SKILL.md`：`progress-probe`、`crash-triage`、`restart-planner`、
`resource-guard`、`safe-kill`、`config-precheck`、`runbook-rag`、`postmortem-write`。

手册要求的 9 个说明项，每个 Skill 都写了：

| 手册要求项 | 我们的章节 |
|---|---|
| Skill 名称 / 用途 | frontmatter `name` + `description` |
| 输入与输出 | `## 输入` / `## 输出` |
| 调用条件 | `## 调用条件` + frontmatter 的 `Triggers` |
| 依赖工具 | `## 依赖` |
| 失败处理机制 | `## 失败处理` |
| 安全边界 | `## 安全边界` + `metadata.risk_level`（L0–L3） |
| 复用价值 | `## 复用价值` |
| 与多 Agent 协同流程的关系 | `## 与多 Agent 流程的关系` |

额外做了手册没要求但**官方直播重点提过**的两项：**版本与演进**、**能力评估**。

> `progress-probe` 已经真实发生过一次版本演进：**0.1.0 → 0.2.0**，
> 触发原因是实测**证伪**了原判据（GPU 退化路径全程漏报）。
> 版本历史、变更原因、兼容性承诺见 [SKILL.md](../skills/progress-probe/SKILL.md#版本与演进)。

**已跑过能力评估的 Skill：2 个**（`progress-probe`、`config-precheck`），其余 6 个只写了评估方法。

## 9.2 MCP 与工具集成（推荐可选项）

✅ 已实现 MCP Server：[`tools/cluster_mcp_server.py`](../tools/cluster_mcp_server.py)，
MCP over stdio / JSON-RPC 2.0 / protocolVersion `2024-11-05`，**纯标准库零依赖**。

手册对"未使用 MCP 时的等价契约"要求的 6 项，我们即使用了 MCP 也一并写明
（见 [`docs/integration-contract.md`](integration-contract.md)）：协议、鉴权方式、
输入输出 Schema、错误处理、审计记录、后续迁移成本。

安全边界经实测拦截：路径越界 → `PATH_DENIED`；命令注入串 → `BAD_PATTERN`。
见 [`evidence/mcp-server-test.txt`](../evidence/mcp-server-test.txt)。

## 9.3 可观测要求（推荐可选项）

✅ 已使用。手册要求说明的 5 项：

| 手册要求 | 我们的答复 |
|---|---|
| **观测数据采集方式** | 从 AgentTeams / QwenPaw **自带的治理审计库**读取：`<worker>/.qwenpaw/governance/audit.db`（SQLite）。采集脚本 [`tools/collect_observability.py`](../tools/collect_observability.py) 从各 Worker 容器 `docker cp` 出库文件后统一解析。**不侵入 Agent 运行时** |
| **语义规范** | ⚠️ **不遵循 OpenTelemetry GenAI 语义约定**。我们消费的是框架自带审计库的原生字段（`ts / agent_id / session_id / tool_name / target / decision / reason`）。这是如实说明：目前是框架私有语义，未做 OTel 映射。若要接 LoongSuite / AgentScope Studio，需要一层字段映射，成本在字段级而非架构级 |
| **数据类型** | Trace（56 span）／ Log（每 Worker 40 条结构化日志）／ Metrics（见下） |
| **后端存储与检索方案** | 当前为**文件级快照**（`evidence/observability-snapshot.json`），检索靠脚本聚合。⚠️ 没有接时序库或向量库 —— 单次事故量级够用，规模化需要换后端 |
| **应用场景与效果** | **不是摆设，采出了真问题**：39% 的 Bash 调用处于无沙箱执行状态（框架因容器缺 LSM 接口降级，并把原因逐条写进了审计库，但没人读）。由此催生一条设计改动：**安全机制不可用时应收紧权限，而不是降级继续跑**。见 [measure-05](../evidence/measure-05-observability.md) |

手册点名的 Metrics，逐项对照：

| 手册点名指标 | 我们有没有 | 数值 |
|---|---|---|
| 对话数 / 会话数 | ✅ | 1 会话，56 span，按 Worker 15 / 31 / 10 |
| 端到端时延 | ✅ | 端到端 65.2 分钟（含人工间隔）；Agent 活跃合计约 11 分钟；相邻工具调用间隔中位 0–3.4 s |
| TTFT | ❌ | 未采。框架审计库不记录首 token 时延 |
| **Token 消耗** | ❌ | **未采**。这是明确缺口 |
| Tool 成功率 | ⚠️ | 只有**决策层**成功率：56/56 allow。审计库不记录工具自身执行成败，无法给出真正的 Tool 成功率 |

另有一组**判定侧**的性能与质量指标，是本项目自己测的（[measure-08](../evidence/measure-08-false-positive-and-perf.md)）：
单次判定 0.33 ms、百作业巡检 0.19 s、40.6 小时真实成功运行误报 0 次、GPU 退化路径发现时延 318–909 s。

## 9.4 RAG 与上下文增强（推荐可选项）

手册要求：**4 项能力中至少实现 2 项**；明确不使用 RAG 时，需在其余 3 项中至少 2 项并论证上下文机制有效性。

| # | 手册的四项能力 | 状态 | 我们的落地 |
|---|---|---|---|
| 1 | Agent 记忆存储 | ⚠️ | Matrix 房间天然持久化会话历史，支持按时间窗回看；**但没有向量语义检索**，只能算部分实现 |
| 2 | 知识库 RAG | ⚠️ | `runbook-rag` Skill 已定义（含"无匹配就说无匹配、绝不编造"的边界）；**向量库未落地**，当前是文件级检索 |
| 3 | **共享状态管理** | ✅ | AgentTeams 共享文件系统 `shared/knowledge/` + MinIO 同步，保证多 Agent 并发下的一致性。**实跑验证**：Triage 读到了 Sentinel 落盘的材料。同时踩到并记录了一个框架坑——**Worker 容器不继承 Manager 的 host-share 挂载**，需 `docker cp` 注入（[case-03](../evidence/case-03-agentteams-deployment.md)） |
| 4 | **轨迹可观测** | ✅ | 执行轨迹与证据链持久化到 `governance/audit.db`，支持全链路回放与审计。56 span 已导出留证（[measure-05](../evidence/measure-05-observability.md)） |

**结论：第 3、4 项达成，满足"至少 2 项"。** 第 1、2 项是部分实现，如实标注为 ⚠️。

**上下文机制有效性的论证**：本项目的上下文不是靠模型记忆维持的，而是**落盘的证据文件**。
Sentinel 把三信号快照与判定结果写进共享目录，Triage 读文件而不是读对话历史 ——
这带来一个可验证的性质：**同一份证据文件重放，判定结果可复现**
（`tools/replay_signals.py` 用的就是线上那份 `decide()`，不依赖模型采样）。
反过来，当证据文件不可达时，Agent 应当**如实报告而不是靠记忆编造** ——
这一条经过对抗实测：材料路径设为不可达后，Agent 经 6 轮排查如实报"材料不可达"，
全程未编造分析（[measure-02](../evidence/measure-02-agent-refuses-to-guess.md)）。

---

## 10 推荐工具链

| 手册项 | 要求 | 我们 |
|---|---|---|
| **AgentTeams** | **必须** | ✅ v1.2.2 实际部署运行 |
| Higress AI 网关 | 推荐 | ✅ 用于凭证隔离，401/200 对照实验验证真实 Key 不下发到 Worker |
| 云 Skills / Nacos / PolarDB / RocketMQ / LoongSuite / AgentScope Studio / AgentLoop | 推荐 | ❌ 未使用 |

手册明写「**推荐项目和云产品不按使用数量评分**」，因此不做堆叠。
未使用项的替代说明：可观测直接消费框架自带审计库（见 9.3），
不引入额外组件是刻意选择——本项目要能部署在**离线的 HPC 集群**上，
自研 MCP Server 也因此坚持纯标准库、零第三方依赖。

## 11 开源与合规

| 手册要求披露项 | 我们的说明 |
|---|---|
| 开源或开放的范围 | 全部：Agent 定义、Skill 规格、适配器、预检工具、MCP Server、证据与实测报告 |
| 开源协议 | **Apache License 2.0** |
| 第三方依赖 | AgentTeams v1.2.2（Apache-2.0）、Higress、Tuwunel/Element、MinIO、Docker。**自研工具零第三方依赖**（纯 Python 标准库） |
| 商业 API 调用情况 | 阿里云百炼 Token Plan（OpenAI 兼容协议），默认模型 `qwen3.7-plus`。**费用假设与锁定风险**：套餐按周计配额且整体 2026-09-10 到期（决赛 9-22），[case-04](../evidence/case-04-quota-exhausted.md) 记录了一次真实的配额耗尽。**同套餐内换模型**仅改模型名（已实测 5 个备选可用）；**跨供应商切换**实测需协调修改 3 个 Higress 托管资源 + 容器重建，成本高于同套餐换模型 |
| 闭源模型使用情况 | `qwen3.7-plus` 为闭源模型。**使用范围**：仅 Agent 的自然语言推理；**判定逻辑本身不调模型**（纯规则，可离线复现），因此闭源模型不影响核心结论的可复现性 |
| 数据来源与授权边界 | 实测数据来自**本团队自己的科研运行**（气象海洋耦合模式、GPU 训练、数据集下载）。历史日志含主机路径，**未随仓库发布**，仓库内只有脱敏统计与复现命令 |
| 可复现方式 | **一条命令** `./verify.sh` 复现全部判定类结论（11 项断言，覆盖漏报与误报两侧），**纯标准库、不需要 GPU、不需要网络、不调模型、不读凭证** |
| 部署依赖 | 见 [case-03](../evidence/case-03-agentteams-deployment.md)（含 Docker 镜像源、group 创建等实际踩坑） |
| 后续维护计划 | 见 [roadmap.md](roadmap.md) |

### 11.1 其他限制说明

| 手册条款 | 状态 |
|---|---|
| 至少 3 个不同职能 Agent | ✅ 设计 6，实跑 3 |
| Skill 为必选项 | ✅ 8 个 |
| **高风险动作必须保留审批、回滚与审计边界** | ⚠️ **已设计未实证**。L0–L3 分级、审批点、回滚点均已写入 Skill 与 Agent 定义，审计链已跑通；但 **Executor 未实跑，审批与回滚未验证** |
| 未使用 MCP 时需给等价集成契约 | ✅ 已用 MCP，且契约文档一并提供 |

---

## 一句话总结缺口

**做到了**：AgentTeams 实跑、8 个 Skill、MCP Server 与安全边界实测、可观测采集并采出真问题、
判定逻辑的漏报与误报双侧实测、RAG 四项达成 2 项。

**没做到**：Executor / Verifier / Curator 三个 Agent 未实跑，因此**闭环八步的第 5、7 步无实证**；
Token 消耗与 TTFT 未采；可观测未做 OTel 语义映射，也未接时序/向量后端。
