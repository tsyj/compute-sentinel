# 外部工具集成契约

> 对应参赛手册 9.2：*"如方案未使用 MCP，应给出等价的外部工具集成契约，包括协议、鉴权方式、
> 输入输出 Schema、错误处理、审计记录、后续迁移到 MCP 的成本。"*
>
> **本项目直接使用 MCP，不走等价契约路线。** 本文档给出完整的接口契约，
> 并说明三条集成路径各自的边界。

---

## 一、三条集成路径

| 路径 | 用途 | 协议 | 状态 |
|---|---|---|---|
| **A · 自研集群适配器** | 长时算力任务的观测能力（本项目核心） | **MCP over stdio** | ✅ 已实现并实测 |
| **B · 阿里云官方 MCP Server** | 云上弹性扩容分支（ECS / ACK / SLS） | MCP | ⬜ 设计已定，未接入 |
| **C · AgentTeams 内置工具** | Read / Bash / Glob 等基础能力 | 框架内置，非 MCP | ✅ 运行中，有审计 |

路径 C 不是 MCP，但它由框架提供且**自带治理审计**（`governance/audit.db`，
每次调用记录 `decision` + `reason`，见 [实测 05](../evidence/measure-05-observability.md)），
因此不需要我们另写契约 —— 契约由 AgentTeams 提供并已验证生效。

---

## 二、路径 A 的完整契约：cluster-mcp-server

实现：[`tools/cluster_mcp_server.py`](../tools/cluster_mcp_server.py)，**纯 Python 标准库，零第三方依赖**。

### 2.1 协议

| 项 | 值 |
|---|---|
| 协议 | Model Context Protocol |
| 传输 | stdio |
| 消息格式 | JSON-RPC 2.0，一行一条 |
| protocolVersion | `2024-11-05` |
| 支持方法 | `initialize`、`notifications/initialized`、`tools/list`、`tools/call` |
| serverInfo | `{"name":"cluster-mcp-server","version":"0.1.0"}` |

选 stdio 而非 HTTP 的理由：本 Server 需要读取集群本地文件与进程表，
天然与 Agent 运行在同一主机；stdio 不开监听端口，**攻击面最小**。

### 2.2 鉴权

本 Server **不做身份鉴权**，改用三层替代约束 —— 因为 stdio 的调用方就是宿主进程本身，
再做一层 token 只是自欺：

| 层 | 机制 |
|---|---|
| 进程边界 | 由 Agent 运行时以子进程方式拉起，不监听网络端口 |
| **路径白名单** | 启动参数 `--allow <root>`（可多次），**白名单外一律 `PATH_DENIED`** |
| 能力白名单 | 只暴露 4 个只读工具，**不提供任何写 / 删 / 终止类工具** |

真实凭证（集群 SSH 私钥、云 AccessKey）**不经过本 Server**。
需要凭证的操作属于 Executor 职责，走网关（见 [证据案例 03](../evidence/case-03-agentteams-deployment.md)）。

### 2.3 输入输出 Schema

四个工具，`inputSchema` 均为标准 JSON Schema（可由 `tools/list` 获取）：

| 工具 | 必填 | 可选 | 返回要点 |
|---|---|---|---|
| `probe_job_progress` | `run_dir` | `workload_type`、`log_path`、`proc_pattern`、`prev_snapshot`、`job_start_ts`、`interval_sec` | `status`、`signals{log,file,resource,gpu}`、`confidence`、`stalled_for_sec`、`degradation_suspected`、`thresholds`、`unavailable_signals`、`snapshot` |
| `tail_log` | `path` | `lines`（1–500） | `size`、`mtime`、`lines[]` |
| `stat_outputs` | `run_dir` | `patterns`（glob，`\|` 分隔） | `count`、`newest_mtime`、`total_size`、`files[]` |
| `sample_resources` | `pattern` | — | `matched`、`total_cpu_pct`、`procs[]` |

`probe_job_progress` 的 `status` 取值：`RUNNING` / `STALLED` / `DEAD` / `UNKNOWN`。

**判定规则区分进展信号与存活信号**：日志进度行、产物文件是**进展**，
CPU / GPU 占用只是**存活**。任一进展信号有变化即 `RUNNING`；
全部静止且累计时长超过该 workload 的 `stall_sec` 才 `STALLED`。

对声明了 `gpu_signal.required` 的 workload，「CPU 忙 + GPU 闲」不计进展票，
并在返回里置 `degradation_suspected=true`。详见 `evidence/measure-06`。

三条边界规则（均已实测）：

- **首轮无基线时返回 `UNKNOWN` 而非 `RUNNING`**。单次采样无法判断"变化"，
  强行判 RUNNING 会让一开始就卡死的作业永远检不出来。
- **提供 `job_start_ts` 时启用首个进度信号超时**：作业启动已久却零产物，
  首轮即可判 `STALLED`，无需等下一轮。
- **`interval_sec` 由调用方给出真实轮询间隔**。不给则沿用上轮 snapshot、再缺省 300。
  卡死计时按真实间隔累加，不使用固定常数。

### 2.4 错误处理

| 场景 | 行为 |
|---|---|
| 路径越界 | `isError=true`，`{"error":"PATH_DENIED"}`，**不回显白名单内容** |
| 文件/目录不存在 | `{"error":"NOT_FOUND"}` |
| 匹配串含非法字符 | `{"error":"BAD_PATTERN"}` —— 只允许 `[\w./=-]`，阻断命令注入 |
| 参数不匹配 | `{"error":"BAD_ARGS"}` |
| 未知工具 | JSON-RPC `-32601` |
| 单信号采集失败 | **不报错**，该信号标 `changed=null` 并计入 `unavailable_signals`，判定降级为 `UNKNOWN` |

统一原则：**错误结构化返回，不抛异常栈**。异常栈会把宿主机目录结构泄露给模型上下文。

### 2.5 审计记录

启动参数 `--audit <path>` 开启，JSON Lines 逐条追加：

```json
{"ts":1786336..., "tool":"tail_log", "args":{...}, "decision":"allow|deny", "reason":"PATH_DENIED"}
```

**被拒绝的调用同样入审计** —— 拒绝记录是判据生效的证据，比放行记录更重要。
审计写入失败不影响主流程，但也不静默改变放行行为。

### 2.6 实测验证

```
[1] initialize      → protocol 2024-11-05, server cluster-mcp-server
[2] tools/list      → 4 个工具
[3] tail_log        ✓ 放行  真实日志，size=245
[4] stat_outputs    ✓ 放行  9 个产物，合计 1.88 GB
[5] tail_log /etc/shadow          ✗ 拒绝  PATH_DENIED
[6] sample_resources "; rm -rf /" ✗ 拒绝  BAD_PATTERN

审计：tail_log allow / stat_outputs allow / tail_log deny PATH_DENIED / sample_resources deny BAD_PATTERN
```

`probe_job_progress` 的两轮判定实测：

| 轮次 | 信号 | 结果 |
|---|---|---|
| 第 1 轮（无基线） | — | `UNKNOWN`，note：首轮采集，已建立基线 |
| 第 2 轮（全静止 1800s） | log ✗ / file ✗ / resource ✗ | **`STALLED`**，confidence 0.9 |
| 空目录 + 启动 7200s | 产物数 0 | **`STALLED`**，note：超过首个进度信号阈值 1800s |

---

## 三、路径 B：阿里云官方 MCP Server（设计，未接入）

云上弹性扩容分支需要访问 ECS / ACK / SLS 时，**不自己造轮子**，直接接官方 Server：

| Server | 用途 |
|---|---|
| `alibaba-cloud-ops-mcp-server` | 实例生命周期、运维动作 |
| `alibabacloud-observability-mcp-server` | 云上指标与日志 |
| `alibabacloud-ack-mcp-server` | 容器集群 |

鉴权由官方 Server 自行处理（AccessKey / STS），**我们不接触凭证**。
接入成本主要是配置 `Worker.spec.mcpServers`，AgentTeams 已原生支持声明式挂载。

这与官方口径一致：阿里云 Skills / MCP 的定位是"帮 Agent 更好地访问云产品"，
**不需要云环境的场景就不必强上** —— 我们的核心场景是本地 HPC 集群，因此路径 A 是主，路径 B 是可选分支。

---

## 四、迁移成本

本项目**已经是 MCP**，因此不存在"迁移到 MCP"的成本。反向的成本记录如下，供替换方案参考：

| 目标 | 成本 |
|---|---|
| stdio → HTTP/SSE 传输 | 低。JSON-RPC 消息体不变，只换传输层；但需新增鉴权（stdio 靠进程边界，HTTP 必须加 token） |
| 换用 `mcp` 官方 SDK 重写 | 低。工具函数与 Schema 可直接复用，约 100 行胶水代码 |
| 接入其他 Agent 框架 | 低。MCP 是开放协议，任何支持 MCP 的运行时都能直接挂载 |
| **换 workload（如接入训练集群）** | **低 —— 这是设计目标**。只需在 `ADAPTERS` 增一条配置（进度行正则、产物 glob、两个阈值），工具逻辑与 Agent 定义均不变 |

最后一行是本契约最重要的性质：**领域差异收敛在配置里，不在代码里，也不在 Agent 的提示词里。**
