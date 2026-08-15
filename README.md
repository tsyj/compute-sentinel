# 算力哨兵 ComputeSentinel

**长时计算任务的零人工运维多 Agent 系统**

> GOAI 2026 世界人工智能开源大赛 · 赛道一「新智基座 Agent Infra」参赛作品
> 队伍：hsl　｜　当前阶段：初赛（方案设计）

---

## 问题

现有运维体系的健康检查是**心跳级**的：进程活着、CPU 高、端口通，就算健康。

但长时算力任务最典型的故障是——**进程活着、CPU 100%、却永远没有进度**。

Prometheus 看不出来，日志告警也看不出来，因为**长时任务的日志静默本来就是常态**：ROMS 按 `NTSAVG` 间隔写、WRF 按 timestep 写、数据下载任务干脆不写。于是"日志不动"这一个信号同时对应"一切正常"和"已经死了"，单信号无法区分。

我们踩过的真实事故：

| 事故 | 现象 | 为什么现有监控发现不了 |
|---|---|---|
| 训练卡在退化代码路径 | 进程 RUNNING、CPU 100%、**GPU 0%**、永不输出进度行 | 心跳全绿；日志 grep 永远等不到那一行，监控静默 2 小时 |
| 链式作业中断 | `bridge_v2 exited rc=1` → 判为"良性 ROMS STOP 1" → 但 `WRF rst missing` → 整链 ABORT | 退出码 1 既可能良性也可能致命，要跨三份日志做语义判断 |
| 动态链接库混栈 | `ldconfig` 后新启的 netcdf 程序秒崩 139 | 崩得太快，调度器只记"失败"，不知道是环境问题 |
| 跨用户误杀 | 按进程名批量 kill，几乎干掉同机另一位用户 128 核的作业 | 靠权限 `EPERM` 静默失败才幸存——**没有任何护栏，纯运气** |
| 资源超订 | 192 核机器跑 3×60 并行，偶发 rank SIGSEGV | 单看每个作业都正常，问题在全局资源账 |
| 上游配置不一致 | 强迫数据分次下载空间范围不一致 → 下游预处理在域角报缺值 | 错误在几小时后才暴露，根因在上游 |

## 主张

长时算力任务需要的不是心跳，是**语义级进度判定**：跨日志、输出文件、CPU 与 GPU 占用做联合推理，再叠加领域知识判断故障类型。

判定规则的核心是一句话：**资源占用只有在「资源画像符合该 workload 的预期」时，才算得上"在推进"的证据。** 日志进度行与产物文件无条件算进展；CPU / GPU 占用要看画像——纯 CPU 作业 CPU 忙算，GPU 训练 GPU 忙算，但**GPU 训练里 CPU 打满而 GPU 闲置就不算**，那反而是退化路径的指征。

我们最初把三类信号做对等 OR，直到 2026-08-15 在 4090 上真造了一次 GPU 退化路径卡死 —— 那种故障里 CPU 恰恰是打满的，于是 CPU 那一票把状态硬拉成"在推进"，**全程漏报**（[measure-06](evidence/measure-06-gpu-stall.md)）。**资源被占着，不等于任务在往前走。**

这件事单个 Agent 做不了：它同时需要多源采集、领域知识检索、风险分级、受控执行、结果校验、经验沉淀。这就是多 Agent 的必要性。

## 适用范围（区分"已验证"与"设计覆盖"）

同一套失败模式与恢复语义，**设计上**覆盖下列场景。
但证据强度差别很大，这里如实标注 —— 完整说明见 [`evidence/README.md`](evidence/README.md)：

| 场景 | 状态 |
|---|---|
| 气象海洋耦合（WRF / ROMS / SWAN） | ✅ **已实测**，多份证据 |
| 大模型预训练 / 微调 | ✅ **已实测**（RTX 4090，[measure-06](evidence/measure-06-gpu-stall.md)）。实测**证伪**了原判据：旧逻辑对该场景全程漏报 |
| 数据下载 / 批处理流水线 | ✅ **误报侧已实测**（18 GB / 4.29h 真实下载，误报 0 次，[measure-08](evidence/measure-08-false-positive-and-perf.md)）。批处理仍未实测 |
| CAE / EDA 仿真 | ❌ **仅结构相似性推断**，无证据 |

换场景只需替换 **workload adapter**（[`adapters/*.json`](adapters/)），Agent 与 Skill 不变 ——
这个**结构**是可验证的（每次判定的返回里都带 `adapter` 字段标明用了哪份配置），
漏报与误报两侧都有数据，且**做了基线对照**（[measure-09](evidence/measure-09-baseline.md)）：
六种常见判据放在同一批真实数据上跑，**只有本方案在五个数据集上全对**——
心跳与 CPU 占用两次真卡死全漏；只看日志能抓住卡死，却在一次**真实跑完的训练**上误报 190 次
（日志 20 分钟才写一行，静默 18.2 分钟，超过 15 分钟阈值）。
累计 **40.6 小时真实成功运行零误报**。

> 痛点表里那句"进程活着、CPU 100%、GPU 0%"最初来自一次立项之前的事故，
> 当时未保留可发布产物。2026-08-15 我们在 RTX 4090 上**重新构造了同构的退化路径卡死**
> 并完成采集与回放（[measure-06](evidence/measure-06-gpu-stall.md)）——
> 结果是**旧判定逻辑全程漏报**，因为它把 CPU 占用当成了进展信号，
> 而 GPU 卡死时 CPU 恰恰是打满的。修复后发现时延 909 秒。
> 这是构造的复现而非生产事故现场，验证的是判据与阈值，不是事故频率。

---

## 先验一遍再看别的

```bash
./verify.sh
```

一条命令重算本仓库全部判定类结论：**11 项断言，不需要 GPU、不需要网络、不调任何大模型、不读任何凭证**，纯 Python 标准库。

```
[1/5] 判定内核语义单测            17 项断言全部通过
[2/5] MCP 契约与安全边界          握手 / 越权拒绝 / 注入拒绝
[3/5] GPU 卡死回放（漏报侧）      修复前全程漏报，修复后 909s / 318s 发现
[4/5] 误报侧回放                  38.1 小时真实成功运行，当前阈值误报 0 次
[5/5] 静态预检                    三个配置状态：两个真根因检出 + 修正后零误报

通过 11  失败 0
```

第 4 项需要历史运行日志（含主机路径，未随仓库发布），设 `COAWST_LOG=` 与 `DOWNLOAD_LOG=` 环境变量后可复现，否则自动跳过并标注。

**如果哪一项在你的机器上没通过，那是我们的问题，不是环境问题。**

---

## 赛题要求逐条对照

评委不必翻遍仓库找证据：[`docs/requirements-mapping.md`](docs/requirements-mapping.md)
把手册第 8–11 章的**每一条技术要求**对到我们的落地位置与证据文件，
状态只有三种：✅ 已实现且有证据 ／ ⚠️ 已实现但证据不足 ／ ❌ 未实现。
其中 ❌ 有 3 条、⚠️ 有 8 条，全部写明原因。

---

## 架构

```
任务输入（作业事件 / 定时巡检 / 自然语言询问）
        ↓
AgentTeams 编排层（Manager 建 Incident Room，taskflow 派单）
        ↓
六个职能 Agent
  Sentinel → Triage → Planner → Executor → Verifier → Curator
   哨兵      诊断      恢复规划   受控执行    验证      沉淀
        ↓
八个 Skill（能力抽象层）
  ProgressProbe · CrashTriage · RestartPlanner · ResourceGuard
  SafeKill · ConfigPrecheck · RunbookRAG · PostmortemWrite
        ↓
MCP / 适配器层（集群适配器 + 云产品 MCP，Mock 与真实共用 Schema）
        ↓
证据与治理层（Incident State · Runbook 向量库 · OTel Trace · 网关凭证隔离）
```

六个 Agent 的完整 Identity 清单与协作拓扑见 [`docs/agent-identity.md`](docs/agent-identity.md)。

## 安全分级

所有动作强制落在四个等级之一：

| 等级 | 含义 | 执行方式 |
|---|---|---|
| **L0** | 只读诊断 | 自动 |
| **L1** | 低风险写操作（白名单内） | 自动，带幂等键 |
| **L2** | 中风险（重启作业、改配置、续跑） | **必须 Incident Room 内人工审批** |
| **L3** | 高风险（终止进程、删除数据、改共享配置） | **拒绝自动执行，只输出方案** |

`SafeKill` 恒为 L2，且要求**三重判据同时满足**才允许操作：属主是发起人本人 + 命令行参数匹配指定输入文件 + PGID 在预登记范围内。缺一即拒绝，不做"尽力而为"。这条规则直接来自上表那次跨用户误杀事故。

---

## 目录

```
docs/        架构、Agent Identity 清单、AgentTeams 映射
agents/      六个 Agent 的声明式定义
skills/      八个 Skill（SKILL.md 规范）
adapters/    workload 适配器配置（7 份 JSON，判定逻辑与领域知识分离的落点）
tools/       可运行工具：config_precheck / cluster_mcp_server / collect_observability
evidence/    真实故障日志与运行证据（脱敏后）
```

## 状态

| 项 | 状态 |
|---|---|
| 问题定义与真实事故证据 | ✅ 三个案例，原始日志可复核 |
| 六 Agent 职责与决策边界 | ✅ [`docs/agent-identity.md`](docs/agent-identity.md) |
| 八 Skill 规格（含版本演进与能力评估） | ✅ [`skills/`](skills/) |
| AgentTeams 部署与凭证隔离验证 | ✅ [证据案例 03](evidence/case-03-agentteams-deployment.md) |
| `config-precheck` 可运行实现 + 实测 | ✅ [实测 01](evidence/measure-01-config-precheck.md)，四状态验证、真实配置零误报 |
| 多 Agent 闭环实跑（Sentinel→Triage→Planner） | ✅ [实测 03](evidence/measure-03-agents-advanced-an-open-question.md) |
| MCP 等价集成契约 | ⬜ 同上 |
| `progress-probe` 可运行原型 | ⬜ 同上 |
| Executor 安全分级实证（L0–L3） | ⬜ 同上 |
| workload adapter 全套 | ⬜ 同上 |

**已跑通的部分都有可复核的证据；未做的部分在上表里，不含糊。**

## 开源、依赖与合规披露

本节按赛道要求逐项披露。**任何一项与实际不符都请以本仓库的运行记录为准。**

### 开源范围与协议

| 项目 | 说明 |
|---|---|
| 本项目协议 | **Apache License 2.0**（与上游 AgentTeams 一致） |
| 开放范围 | Agent 定义、Skill 规格、workload adapter 规范、预检工具、证据与实测报告**全部开放** |
| 暂不开放 | 原始运行日志中包含他人作业信息与内网路径的部分（脱敏后再发布，见 `evidence/README.md`） |
| 上游贡献 | 部署与实跑中发现 **6 个问题**，已整理为可直接提交的 issue 正文（含最小复现、影响与建议改法）：[`docs/upstream-issues.md`](docs/upstream-issues.md)。<br>其中 1 条为 **CLI 帮助文本泄露真实 API Key**，按安全问题流程私下报告；<br>`progress-probe` 的 adapter 规范若通用，另行提 PR |

### 第三方依赖

| 依赖 | 版本 | 用途 | 协议 | 可替换性 |
|---|---|---|---|---|
| [AgentTeams](https://github.com/agentscope-ai/AgentTeams) | v1.2.2 | 多 Agent 编排运行时（**赛道必选**） | Apache-2.0 | 赛道要求，不替换 |
| QwenPaw | 随 AgentTeams v1.2.2 | Worker 运行时 | 见上游 | 可换 OpenClaw / Hermes |
| Higress | 随 AgentTeams 内置 | AI 网关，凭证隔离 | Apache-2.0 | 可换其他网关，但需自行实现凭证隔离 |
| Docker | 29.6.1（snap） | 容器运行时 | Apache-2.0 | 可换 containerd/podman |
| Python 标准库 | 3.9+ | `tools/config_precheck.py` **无第三方依赖** | PSF | — |

### ⚠️ 商业 API 调用

| 项目 | 说明 |
|---|---|
| 服务商 | **阿里云百炼（Model Studio）** |
| 套餐 | Token Plan Standard，**2026-08-09 开通，2026-09-10 到期** |
| 接口 | OpenAI 兼容协议，`https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| 调用环节 | **仅 Agent 推理**。`config_precheck` 等确定性工具**不调用任何模型** |
| 费用假设 | 套餐制，含 5 小时 / 7 天双重限额；单次事故处理实测消耗约 20 万 token 量级 |
| 权限范围 | API Key 权限为「全部」（宽于实际所需，后续应收窄至模型调用） |
| 密钥管理 | 真实 Key **只存在于网关侧**，Worker 仅持消费令牌（见 [证据案例 03](evidence/case-03-agentteams-deployment.md) 的 401/200 对照实验） |
| **锁定风险** | **中**。接口为 OpenAI 兼容协议，换供应商只需改 Base URL 与模型名；<br>但套餐到期（9-10）晚于复赛（9-3）、**早于决赛（9-22）**，需提前续期 |

### ⚠️ 闭源模型使用

| 项目 | 说明 |
|---|---|
| 模型 | **`qwen3.7-plus`**（闭源，通过百炼 API 调用） |
| 使用范围 | Sentinel / Triage / Planner 三个 Agent 的推理 |
| 选择原因 | 赛道承办方生态内、国内直连稳定、Token Plan 套餐已覆盖 |
| 备选 | 同套餐内已实测可用：`qwen3.8-max`、`qwen3.7-max`、`qwen3.6-flash`、`deepseek-v4-pro`、`glm-5.2` |
| 迁移成本 | **低**。模型名是一处配置项，Agent 定义与 Skill 规格与模型无关 |
| **对可复现性的影响** | **有**。闭源模型输出不保证逐字复现。<br>因此本项目把**确定性能力**（`config-precheck` 静态检查、`progress-probe` 多信号比对）与**模型参与环节**（归类、表达）分开：<br>前者可精确复现（见 [实测 01](evidence/measure-01-config-precheck.md)），后者只保证结论要素可复核（每条结论必须挂证据引用） |

### 数据来源与授权

| 数据 | 来源 | 授权 | 处理 |
|---|---|---|---|
| 事故日志、配置、产物清单 | 作者本人在自有课题组集群上的真实运行 | 自有 | 发布前需脱敏主机名、绝对路径、他人作业信息 |
| WRF 源码引用 | COAWST 构建树中的 WRF | 公开 | 仅引用行号与逻辑，未复制大段代码 |
| 外部文献数据 | 公开论文与官方仓库 | 公开 | 均给出链接 |
| **未使用** | 任何企业内部数据、个人信息、第三方非公开数据 | — | — |

### 可复现方式与部署依赖

```bash
# 确定性部分（无需模型，无需网络）
python3 tools/config_precheck.py --case <配置目录> --registry <WRF/Registry>
bash tools/reproduce_case01.sh

# Agent 部分（需 Docker + 模型 API Key）
bash <(curl -sSL https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh)
agt apply -f agents/sentinel.worker.yaml   # triage / planner 同理
```

已知环境坑（实测踩过，见 [证据案例 03](evidence/case-03-agentteams-deployment.md)）：
snap 版 Docker 不自动创建 `docker` 组、daemon 不继承代理、Docker Hub 需配镜像源；
Worker 容器不继承 Manager 的 host-share 挂载；Matrix 房间内纯文本 `@name` 不构成 mention。

### 后续维护计划

初赛后按 `docs/roadmap.md` 推进：补齐可观测链路、MCP 等价契约、`progress-probe` 原型、
Executor 安全分级实证。上游可复用的部分持续向 AgentTeams 社区提交。
