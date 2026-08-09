# 实测 03 · 多 Agent 协作把一个悬了三周的问题往前推了一步

> 环境：AgentTeams v1.2.2，3 个 QwenPaw Worker，模型 `qwen3.7-plus`（阿里云百炼 Token Plan）
> 输入：证据案例 01 的真实材料（两次运行的编排日志、耦合日志片段、两份 WRF 配置、两份产物清单）
> 投递方式：按真实操作者口吻的含糊描述，**不给结构化任务**
> 时间：2026-08-09

---

## 一句话

Triage Agent 通过**对比两次运行的产物文件名**，推断出 `override_restart_timers` 是**部分生效**的
—— 它成功重置了 history 闹钟，但没能重置 restart 闹钟。
这把「v3 为什么加了修复还是失败」从**完全未知**推进到**已定位到差异行为**。

这个观察，当晚的操作者没做，我在前期分析里也没做。

---

## 它是怎么想到的

两次运行的 WRF 配置里，`history_interval` **完全相同，都是 180 分钟**。
但两次的第一个历史输出文件时刻不一样：

| 运行 | `override_restart_timers` | 首个 wrfout | 体积 |
|---|---|---|---|
| bridge_v2 | 未设置 | `wrfout_d01_2024-09-06_01:00:00` | 6.7 GB |
| bridge_v3 | `.true.` | `wrfout_d01_2024-09-06_03:00:00` | 1.7 GB |

作业从 00:00 起跑。namelist 声明的 180 分钟间隔 → 第一次写历史应该在 **03:00**。

- **v3 的首帧正好落在 03:00** —— 说明它的 history 闹钟是**按 namelist 重算**的
- **v2 的首帧在 01:00** —— 说明它的 history 闹钟**不是 180 分钟**，而是从 restart 文件里恢复来的（约 60 分钟）

体积比 6.7 : 1.7 ≈ 3.9，与 60 分钟 / 180 分钟的帧数比一致，相互印证。

**结论**：`override_restart_timers = .true.` 被读到了、也生效了 —— 它改变了 history 闹钟的行为。
但 `wrfrst` 文件数**依然是 0**。

所以问题的形态变了：不再是"这个开关有没有起作用"，而是
**"这个开关为什么只重置了 history 闹钟，没重置 restart 闹钟"**。

## 独立复核（本文档作者做的，不是 Agent 说了算）

```
$ grep -nE "history_interval|override_restart_timers" bridge_v{2,3}/cwd/namelist.input
  v2  L24: history_interval = 180          （无 override）
  v3  L24: history_interval = 180
      L37: override_restart_timers = .true.

$ ls bridge_v2/out/wrfout_*   →  wrfout_d01_2024-09-06_01:00:00   6.7G
$ ls bridge_v3/out/wrfout_*   →  wrfout_d01_2024-09-06_03:00:00   1.7G
```

可观察事实全部成立。**但因果机制仍是假设** —— Triage 自己给的置信度是 **0.85**，不是 1.0。

## Triage 诚实列出的缺失证据

> - WRF 完整运行日志（非片段）—— 无法确认 `override_restart_timers` 是否被读取、有无 warning
> - WRF 源码中 `override_restart_timers` 的具体实现 —— 无法确认是代码 bug 还是设计如此
> - production run 的 `restart_interval` 原值 —— 720 分钟是从注释推断的，未直接看到

第三条尤其克制：它注意到 720 这个数字**只出现在配置注释里**，没有看到原始 production 配置，
因此标注为推断而非事实。

---

## 完整的协作链路

| 阶段 | Agent | 产出 |
|---|---|---|
| 1 | **Sentinel** | `status: STALLED（性质：正常跑完但缺产物）`，置信度 0.95，附 9 条带文件行号的证据 |
| 2 | **Sentinel → Triage** | 主动 `@triage` 派单，附三个待验证的根因方向，**不下断言** |
| 3 | **Triage** | 不采信摘要，独立读完全部 7 个文件，逐条抓行号 |
| 4 | **Triage** | 7 条证据 + 良性退出判定 + 置信度 + 缺失证据清单 |
| 5 | **Planner** | `resumable: no` + 4 个带风险分级与回滚点的方案，含一个 10-15 分钟的廉价验证实验 |

第 1 步的判定回答了投递时故意设置的那个难题：
**"作业是中途死了，还是正常跑完但没产出该产出的东西？"**
这两者在表层日志上都表现为 `rc=1`，区分它们需要交叉比对产物清单与运行日志末段。Sentinel 分对了。

第 3 步值得单独说：Triage 的 SOUL 里**没有**写"要独立复核上游结论"，它自己去把 7 个文件全读了一遍。

---

## 第三棒 · Planner 的恢复方案

补上显式派单后（见下节"链路断在第二棒"），Planner 产出了结构化方案。三处值得单独说：

### 一、它给出了保守的正确答案

```
resumable: no
```

理由链条完整：`wrfrst` 从未写入 → ROMS 和 SWAN 的 restart 虽然都在，但 WRF 没有对应文件 →
**WRF 不能从 wrfout（history 文件）做 hot-restart，它需要 wrfrst** → 下一段耦合无法初始化 WRF。

它明确写出"缺什么才能续跑"：一个窗口内任意时刻的合法 `wrfrst`，而当前没有任何路径能获得它。

这符合 `planner` 的 SOUL 约束：**误判"可以续跑"的代价远高于误判"不能续跑"** ——
前者会跑出一份不可信的结果，后者只是多花一次全量重跑。

### 二、⭐ 它独立想到了"用便宜实验换确定性"

方案 C：

> 先做一个短窗口测试：bridge 窗口设为 60 min，`restart_interval` 设为 30 min，
> 验证 wrfrst 是否在 30 min 被写入。如果写入，说明 override 对 restart 也有效；
> 如果不写入，确认 override 对 restart 无效。
> **短测试约 10-15 分钟即可完成（vs 全量 bridge 2+ 小时），可快速排除不确定性。**
> `risk_level: L1`，产物在独立目录，不影响现有数据

这正是本项目的核心主张 —— **证据案例 01 那一夜烧掉 4 小时 32 分，
根本原因就是"验证一个假设要等 2 小时 22 分钟"**。
Planner 没有被告知这个主张，它自己从"存在不确定性"推出了"设计一个廉价实验先消除它"。

### 三、风险分级与回滚点都落到了实处

| 动作 | 等级 | 回滚点 |
|---|---|---|
| 读 WRF 源码确认 override 实现 | **L0** | 只读，无需回滚 |
| 短窗口验证测试 | **L1** | 产物在独立目录，不影响现有数据 |
| 重跑 production（改 `restart_interval` 720→540） | **L2** | 旧 wrfrst 备份为 `.720min.bak`，失败可恢复 |
| **直接编辑 wrfrst 内的 alarm 变量** | **L3** | 编辑前 `cp` 原件；并主动标注 `idempotent: false` |

L3 那条它还自己写了风险提示：

> alarm 可能不是简单的标量属性，可能与 WRF 内部状态耦合。

代价估算也给了：每次全量 bridge 重跑约 **350 rank-hours**（2h15m × 160 MPI ranks）；
production 重跑代价则标注为"未知，取决于 production 段长度"—— 不编数字。

前置确认表里，"Production restart_interval = 720 min"这一行标的是
**"✅ 确认（来自注释自述，未直接看到 production namelist）"** ——
把 Triage 的存疑原样传递下来了，没有在传递中变成事实。

---

## 链路断在第二棒（如实记录）

**Planner 在第一次运行中完全没有被触发。** 闭环停在 Sentinel → Triage 两棒。

原因是本项目 SOUL 编写上的疏漏，不是框架问题：

| Agent | SOUL 里的相关表述 | 效果 |
|---|---|---|
| Sentinel | "判定 STALLED 或 DEAD 时……**升级给 Triage**" | ✅ 主动 @triage 派单 |
| Triage | "不要生成修复方案 —— **那是 Planner 的职责**" | ❌ 没有派单 |

**教训：「不要做 X（那是 Y 的职责）」不构成「把结果交给 Y」。**
下游交接必须在每个 Agent 的职责描述里**正向、显式**地写出来，
否则多 Agent 链路会在任意一棒静默中断 —— 而且中断时每个 Agent 单看都"没做错事"。

这条会写回 `docs/agent-identity.md`：每个 Agent 的 `Outputs` 字段必须包含**交给谁**，
不能只写产出什么。

---

## 与外部基准的对照

[OpenRCA](https://github.com/microsoft/OpenRCA)（ICLR 2025，微软 + 清华）上，
表现最好的模型在 335 个企业故障案例中只解决了 **11.34%**。

本次实测**不是**在说我们的 Agent 比那个数字强 —— 单案例、有针对性的 SOUL 约束、
材料已经过筛选，不具可比性。

它说明的是另一件事：**在模型根因分析能力普遍不可靠的前提下，
把 Agent 约束在"只出带证据引用的结论、说不清就说说不清"的边界内，
仍然能产出有价值的东西。** 本次的价值不在于它"猜对了根因"，
而在于它**发现了一条此前没人注意到的可观察差异**，并且**明确标注了哪些还是推断**。

---

## 复现材料

- 完整对话记录：`evidence/transcript-run2-sentinel-triage.txt`
- Agent 定义：`agents/{sentinel,triage,planner}.worker.yaml`
- 事故材料：`/home/xinyuan/compute-sentinel-demo/incident-001/`（脱敏后可发布）

两个环境注意事项（第一轮踩过，见 [实测 02](measure-02-agent-refuses-to-guess.md)）：

1. Worker 容器**不继承** Manager 的 host-share 挂载，需 `docker cp` 注入
   `/root/agentteams-fs/shared/knowledge/`
2. Team 房里纯文本 `@name` **不构成 mention**，必须带 Matrix 标准的 `m.mentions.user_ids`
