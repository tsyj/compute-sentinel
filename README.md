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

长时算力任务需要的不是心跳，是**语义级进度判定**：跨日志、输出文件、资源占用三类信号做联合推理，再叠加领域知识判断故障类型。

判定规则是 **OR 而不是 AND** —— 任一信号有变化即判定为"在推进"，**全部信号静止且超过该 workload 的阈值**才判定为卡死。这一条看似简单，却是绝大多数监控脚本做错的地方。

这件事单个 Agent 做不了：它同时需要多源采集、领域知识检索、风险分级、受控执行、结果校验、经验沉淀。这就是多 Agent 的必要性。

## 适用范围

同一套失败模式与恢复语义，覆盖：

- 大模型预训练 / 微调（NCCL hang、loss spike、dataloader 死锁、掉卡）
- 气象海洋业务化预报（WRF / ROMS / SWAN 耦合）
- CAE / EDA 仿真（碰撞、流体、芯片验证）
- 生信流程与大规模数据处理流水线
- 科研 HPC 集群日常作业

换场景只需替换 **workload adapter**，Agent 与 Skill 不变。

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

详见 [`docs/architecture.md`](docs/architecture.md) 与 [`docs/agent-identity.md`](docs/agent-identity.md)。

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
adapters/    workload 适配器（WRF / ROMS / PyTorch / 下载任务 / generic）
evidence/    真实故障日志与运行证据（脱敏后）
```

## 状态

初赛阶段为方案设计，尚无可运行实现。已完成：

- [x] 问题定义与真实事故证据
- [x] 六 Agent 职责与决策边界
- [x] 八 Skill 规格
- [x] AgentTeams 能力映射
- [ ] 最小闭环实现（复赛）
- [ ] workload adapter 全套（复赛）

## 开源

Apache License 2.0。

计划把 `ProgressProbe` 的 workload adapter 规范与通用 Skill 贡献回上游 [AgentTeams](https://github.com/agentscope-ai/AgentTeams) 社区，而非独立 fork。
