# Agent Identity 清单

按赛道一参赛手册附录 A 的字段组织。六个职能 Agent，覆盖从发现到沉淀的完整闭环。

设计原则一：**每个 Agent 都必须写清楚"不能做什么"**。能力边界比能力本身更重要——
在一个会对生产集群下手的系统里，越权是比无能更贵的失败。

设计原则二（实测教训）：**每个 Agent 的 `Outputs` 必须正向、显式地写明"交给谁"。**
「不要做 X（那是 Y 的职责）」**不构成**「把结果交给 Y」。
在 [实测 03](../evidence/measure-03-agents-advanced-an-open-question.md) 中，
Triage 的职责描述只写了前者，结果链路在第二棒静默中断 ——
而且中断时每个 Agent 单看都"没做错事"，这类失败最难发现。

---

## A1 · Sentinel（哨兵）

| 字段 | 内容 |
|---|---|
| **Name** | `sentinel` |
| **Role** | 多信号采集与"是否有进度"的语义判定 |
| **Capabilities** | 能：拉日志尾、stat 输出文件、采样 CPU/GPU/IO、按 workload 类型选适配器、输出进度判定与置信度。<br>**不能**：执行任何写操作、不能终止进程、不能修改配置 |
| **Inputs** | 作业 ID、workload 类型、运行目录、上一轮快照 |
| **Outputs** | `ProgressVerdict{ status, signals{log,file,resource}, confidence, stalled_for_sec, next_poll_sec }` |
| **Dependencies** | Skill: `progress-probe`、`resource-guard`；集群适配器 |
| **Decision Boundary** | 全自主，风险等级 L0（只读）。判定 STALLED 时**不自行处置**，只连同三类信号原始值升级给 Triage |
| **Trace** | 每轮采集一条 span，含三类信号原始值；快照存入 Incident State，判定过程可回放 |

## A2 · Triage（诊断）

| 字段 | 内容 |
|---|---|
| **Name** | `triage` |
| **Role** | 故障归类、根因候选生成、证据分级 |
| **Capabilities** | 能：解析运行日志与系统日志、匹配已知故障模式、检索 Runbook、输出根因候选与置信度、标注证据缺口。<br>**不能**：执行任何修复动作 |
| **Inputs** | Sentinel 的 ProgressVerdict + 运行目录 + 作业配置 |
| **Outputs** | `Diagnosis{ class, root_cause_candidates[], evidence{strong,weak,missing}, confidence }` |
| **Dependencies** | Skill: `crash-triage`、`runbook-rag`；Runbook 向量库 |
| **Decision Boundary** | 全自主，L0。**证据不足时必须输出"缺什么"而不是猜**——这是防幻觉的硬约束，也是与"LLM 编一个听起来合理的根因"的分界线 |
| **Trace** | 检索命中的 Runbook 条目 ID 与相似度入 span；每个根因结论必须挂证据引用，无引用的结论视为无效 |

## A3 · Planner（恢复规划）

| 字段 | 内容 |
|---|---|
| **Name** | `planner` |
| **Role** | 生成恢复方案并做风险分级 |
| **Capabilities** | 能：判断能否从断点续跑、定位续跑点、给出需修改的配置项、估算重跑代价、给方案打 L0–L3 风险等级、生成回滚点。<br>**不能**：自己执行任何动作 |
| **Inputs** | Diagnosis + 作业历史 + 集群资源现状 |
| **Outputs** | `RecoveryPlan{ steps[], risk_level, rollback_point, cost_estimate, preconditions[] }` |
| **Dependencies** | Skill: `restart-planner`、`config-precheck`、`resource-guard` |
| **Decision Boundary** | 全自主生成方案，**但方案本身不携带执行权**。判定为 L3 的方案只输出、明确标注"需人工执行"，不进入 Executor |
| **Trace** | 方案与其依据的 Diagnosis ID 绑定；风险等级的判定过程可回放 |

## A4 · Executor（受控执行）

| 字段 | 内容 |
|---|---|
| **Name** | `executor` |
| **Role** | 在安全护栏内执行恢复动作 |
| **Capabilities** | 能：执行白名单内动作、携带幂等键、写审计日志、失败自动回滚。<br>**不能**：执行白名单外的任何命令、不能跨用户操作、不能删除数据、不能在无审批时执行 L2 及以上动作 |
| **Inputs** | RecoveryPlan + 审批结果 |
| **Outputs** | `ExecutionResult{ actions[], stdout_ref, exit_codes, rollback_done, audit_id }` |
| **Dependencies** | Skill: `safe-kill`、`restart-planner`；集群适配器；网关（凭证不落地） |
| **Decision Boundary** | **L0 只读自动｜L1 白名单低风险自动｜L2 必须协作房间内人工审批｜L3 拒绝执行、转交人工** |
| **Trace** | 每个动作一条 span，含幂等键、审批消息 ID、回滚点；审计日志与 Trace 用同一 TraceId 关联 |

## A5 · Verifier（恢复验证）

| 字段 | 内容 |
|---|---|
| **Name** | `verifier` |
| **Role** | 验证恢复是否真的成功，而不是"命令返回 0" |
| **Capabilities** | 能：校验续跑后时间轴连续性、输出文件完整性、物理量是否发散、与续跑前重叠段做一致性比对。<br>**不能**：修改任何数据 |
| **Inputs** | ExecutionResult + 恢复前后的输出文件 |
| **Outputs** | `VerifyReport{ verdict: RECOVERED/PARTIAL/FAILED, checks[], anomalies[] }` |
| **Dependencies** | Skill: `restart-planner`（断点语义）、`config-precheck` |
| **Decision Boundary** | 全自主，L0。判定 FAILED 时自动回到 Triage 开第二轮，最多两轮后强制转人工 |
| **Trace** | 每项 check 的输入输出留存，报告可独立复现 |

## A6 · Curator（经验沉淀）

| 字段 | 内容 |
|---|---|
| **Name** | `curator` |
| **Role** | 把这次事故变成下次能自动处理的能力 |
| **Capabilities** | 能：生成复盘、把新故障模式写回 Runbook、为 `crash-triage` 提新判据、为 `safe-kill` 提新护栏、标记需人工确认的知识。<br>**不能**：直接修改线上 Skill（走 PR + review） |
| **Inputs** | 全链路 Incident State + VerifyReport |
| **Outputs** | `Postmortem{ timeline, root_cause, fix, new_rules[], skill_diff_pr }` |
| **Dependencies** | Skill: `postmortem-write`、`runbook-rag` |
| **Decision Boundary** | 自主生成，**知识入库需人工 review**——防止把一次错误归因固化成以后每次都用的判据 |
| **Trace** | 新增规则与来源事故 ID 双向可追溯 |

---

## 协作拓扑

```
             ┌──────────────┐
             │   Sentinel   │  L0 巡检
             └──────┬───────┘
              STALLED / DEAD
                    ▼
             ┌──────────────┐
             │    Triage    │  L0 归类 + 根因候选 + 证据分级
             └──────┬───────┘
                    ▼
             ┌──────────────┐
             │   Planner    │  L0 出方案 + 打风险等级
             └──────┬───────┘
              L2 ┌──┴──┐ L3
       人工审批 ─┤     ├─ 只出方案，转人工
                 ▼
             ┌──────────────┐
             │   Executor   │  L1 自动 / L2 审批后执行
             └──────┬───────┘
                    ▼
             ┌──────────────┐        FAILED
             │   Verifier   │─────────────────► 回到 Triage（最多 2 轮）
             └──────┬───────┘
              RECOVERED
                    ▼
             ┌──────────────┐
             │   Curator    │  复盘 → Runbook / 新判据 PR（需 review）
             └──────────────┘
```

诊断链（Sentinel / Triage / Planner / Verifier）全部是 L0 只读，可以放心自动跑。
唯一能改变系统状态的是 Executor，而它被白名单、幂等键、分级审批和回滚点四重约束。
