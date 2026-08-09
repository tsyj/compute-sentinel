# 实测 05 · 可观测：Trace / Log / Metrics 三类全采，并采出一个真实安全问题

> 工具：[`tools/collect_observability.py`](../tools/collect_observability.py)（纯标准库）
> 复现：`python3 tools/collect_observability.py --json out.json`
> 原始产物：[`observability-report.txt`](observability-report.txt)、[`observability-snapshot.json`](observability-snapshot.json)
> 采集时间：2026-08-10

---

## 数据源：框架自带，不是我们埋的点

采集对象是 AgentTeams / QwenPaw 自带的治理审计库：

```
/root/agentteams-fs/agents/<worker>/.qwenpaw/governance/audit.db
表 audit_events(ts, workspace_dir, agent_id, session_id, tool_name, target, decision, reason)
```

**这一点很重要**：这些记录在框架层面天然产生，我们只是把它取出来聚合。
**不是事后补的埋点，也无法事后编造。**

（注：`:18888/metrics`、`:18001/api/traces` 这些端点返回的是控制台 SPA 首页，不是数据接口 ——
真正的可观测数据在上面这个审计库里。）

---

## Metrics

| 指标 | 值 |
|---|---|
| span 总数 | **56** |
| 时间跨度 | 00:21:49 → 01:27:03（3914 秒） |
| 会话数 | 1（一个事故房间 = 一条完整调用链） |
| 每 Agent span | triage 31、sentinel 15、planner 10 |
| 工具调用分布 | Read 29、Bash 22、Glob 5 |
| 放行决策分布 | allow 56（无拒绝） |
| **⚠ 无沙箱执行** | **22 / 56（39%）** |

### 每 Agent 的工具使用画像

| Agent | Bash | Glob | Read | 合计 |
|---|---|---|---|---|
| planner | 2 | 0 | 8 | 10 |
| sentinel | 8 | 0 | 7 | 15 |
| triage | 12 | 5 | 14 | 31 |

**只读工具（Read + Glob）占 34/56 = 61%**，与三个 Agent 都定位为 L0 只读一致。
其余 22 次是 Bash —— 全部用于 `ls` / `find` / `grep` / `cat -n` 这类查看操作，
审计库里的 `target` 字段逐条记录了完整命令，可核对无写操作。

triage 的调用量（31 次）显著高于另外两个，与 [实测 03](measure-03-agents-advanced-an-open-question.md) 里
"它没有采信上游摘要、把 7 个文件全读了一遍"的观察吻合 —— **行为在指标上留下了印记。**

---

## Trace

按会话（Matrix 房间 ID）聚合，一个事故 = 一条链路。每个 span 记录：
时间戳、Agent、工具、目标、放行决策、决策理由。

节选（完整链路见 [`observability-report.txt`](observability-report.txt)）：

```
00:21:49  sentinel  Bash   allow  ls -la /host-share/compute-sentinel-demo/incident-001/ ...
            ⚠ 未沙箱执行  ← sandbox unavailable (Cannot read /sys/kernel/security/lsm), running unsandboxed
00:21:52  sentinel  Bash   allow  ls /host-share/ 2>&1; ...
00:21:55  sentinel  Bash   allow  pwd; ls; find . -maxdepth 3 -type d ...
00:21:58  sentinel  Bash   allow  ls shared/ 2>&1; find / -path "*/incident-001*" ...
00:22:08  sentinel  Bash   allow  mount | grep -i share; ls /host*; ls /mnt/ ...
00:22:11  sentinel  Bash   allow  find shared/ -type f ...
00:22:16  sentinel  Bash   allow  ls media/ ...
```

这七条正是 [实测 02](measure-02-agent-refuses-to-guess.md) 里描述的"6 轮系统性排查"
在审计层面的原始记录 —— **叙述与 Trace 可以互相对账。**

---

## ⚠️ 采出来的真实安全问题：Agent 的 shell 未沙箱运行

审计库里每一条 Bash 调用的 `reason` 都是：

```
sandbox unavailable (Cannot read /sys/kernel/security/lsm), running unsandboxed
```

**22 次 Bash 调用，全部在无沙箱状态下执行。**

### 成因

QwenPaw 的沙箱依赖 Linux Security Module 接口 `/sys/kernel/security/lsm`。
Worker 容器内读不到该路径（securityfs 未挂载），框架**降级为不沙箱执行并记录理由**。

框架的处理本身是合理的 —— 它没有静默降级，而是把降级原因逐条写进了审计库。
**问题在于：如果没有人去读这个审计库，这个降级就等于不存在。**

### 对本项目的意义

我们的方案主张"高风险动作必须有安全边界"，并设计了 L0–L3 分级。
但这次实测说明：**分级设计只是第一层，执行环境是否真的隔离是第二层，两层缺一不可。**

当前状态如实记录：

| 层 | 状态 |
|---|---|
| 凭证隔离 | ✅ 已验证有效（网关 401/200 对照，见 [证据案例 03](case-03-agentteams-deployment.md)） |
| 权限策略 | ✅ 存在且生效（`governance/policy.yaml`，含 `env_blacklist` 屏蔽各类 API Key） |
| 审计留痕 | ✅ 每次工具调用都有 decision + reason |
| **执行沙箱** | **❌ 不可用，降级为无沙箱** |
| L0–L3 分级 | ⬜ 设计完成，**Executor 尚未实跑**，未经验证 |

### 该怎么修（写进 roadmap，不在本轮解决）

1. 容器启动时挂载 securityfs，或改用支持 seccomp/AppArmor 的运行时配置
2. 在 `progress-probe` 之外增加一条**启动自检**：若沙箱不可用则拒绝授予 L1 及以上权限，
   而不是照常执行 —— **把"降级"变成"拒绝"**
3. 把"无沙箱执行占比"作为常态化监控指标，非零即告警

第 2 条是这次实测直接催生的设计改动：
**当安全机制不可用时，正确的反应是收紧权限，而不是带着降级继续跑。**

---

## 这份材料对应的评分维度

官方评分图第 4 项：**「工程落地、运行验证与安全可审计 20% —— 可运行、有证据、权限审批回滚审计完整」**

| 判据 | 本材料提供的 |
|---|---|
| 可运行 | 采集工具可复现执行，56 个 span 来自真实运行 |
| 有证据 | 原始 JSON + 文本报告入库，可逐条核对 |
| 权限 | `policy.yaml` 权限策略 + 每次调用的 decision/reason |
| 审计 | 框架级审计库，非自建埋点 |
| 审批 / 回滚 | ⬜ **尚未验证** —— 需要跑通 Executor（见 roadmap） |

最后一行是本轮暴露的缺口，不掩饰。
