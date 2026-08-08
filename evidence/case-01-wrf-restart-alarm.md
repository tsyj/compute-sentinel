# 证据案例 01 · 热重启的 restart 闹钟未触发，两夜连续失败

> 来源：`/data/xinyuan/crown_ab_v2_20260717/`（Yagi 台风 WRF-ROMS-SWAN 耦合，48 核）
> 时间：2026-07-17 22:32 — 2026-07-18 03:49
> 这份材料证明的是：**问题不在诊断能力，在反馈时延。**

---

## 时间线（全部有文件与日志佐证）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 07-17 22:24 | 写好 v2 的 WRF 配置：`restart = .true.`，`restart_interval = 540`（9 小时），运行窗口 9 小时 | `bridge_v2/cwd/namelist.input` mtime |
| 22:32:01 | 启动第一次链式作业，等待桥接段完成 | `orchestrate.log` |
| **00:38:03** | 桥接段退出 `rc=1` → 编排器判定「良性 ROMS STOP 1」，放行 → 校验 09:00 三件套 → **`ABORT: WRF rst missing`** | `orchestrate.log` |
| | 实际产物：SWAN restart 8 个分区齐全、`wrfout_d01_2024-09-06_01:00:00` 7.2 GB 写出正常，**`wrfrst_*` 一个都没有** | `bridge_v2/out/` 目录 |
| 00:38 → 01:25 | **人工深度排查约 47 分钟**：读 WRF 源码 `share/input_wrf.F` L263-271、查 Registry，定位根因 | `bridge_v3/cwd/namelist.input` 注释 |
| 01:25:31 | 写入修复：`override_restart_timers = .true.` | 同上，L37 |
| 01:28:18 | 带着修复重新启动 | `orchestrate_v3.log` |
| **03:49:20** | **同样的 `ABORT: WRF rst missing` 再来一次** | `orchestrate_v3.log` |
| | 实际产物：SWAN 齐全、`wrfout_d01_2024-09-06_03:00:00` 1.8 GB，**`wrfrst_*` 仍然一个都没有** | `bridge_v3/out/` 目录 |

**代价**：两次运行合计约 4 小时 27 分钟的 48 核机时，跨越两个凌晨时段，两次都在无人值守时失败。

---

## 根因（操作者本人在 47 分钟排查后写在配置注释里的原话）

> a hot-restart run RESTORES the write-restart alarm from the restart file, which was set at
> production `restart_interval=720`. WRF then honors 720 and **IGNORES the new 540** ->
> in v2 the 540-min (09:00) wrfrst was **NEVER written (zero wrfrst)**.
> The switch that forces WRF to RE-INITIALIZE the restart (and history) alarm from the
> namelist value at restart time is `override_restart_timers`.
> NOTE: the generic name `override_restart_intervals` is **NOT** in this build's Registry ->
> namelist-read FATAL.

翻译：热重启的作业会**从 restart 文件里恢复"下次写 restart"的闹钟**，而那个闹钟带的是生产配置的 720 分钟。
新配置里写的 540 分钟被忽略。运行窗口只有 9 小时（540 分钟），720 分钟的闹钟永远等不到 —— 所以一个 restart 文件都没产出。

这是一个**需要读模式源码才能确认**的根因，不是粗心。而且操作者还额外发现了一个陷阱：
看起来更"通用"的参数名 `override_restart_intervals` 在这个编译版本的 Registry 里不存在，写进去会导致配置读取阶段直接 FATAL。

---

## 关键事实：修复是对的，但第二次仍然失败

v3 的配置**确实带上了** `override_restart_timers = .true.`（第 37 行，已核验），
但 03:49 的失败与 v2 完全相同，产物同样是零个 `wrfrst`。

**为什么修复没生效，本案例不下结论** —— 这正是问题所在：验证一次假设要等 2 小时 21 分钟，
而结果在凌晨 3 点 49 分揭晓，没有人在场。

---

## 这个案例证明了什么

**不是**"专家需要 AI 帮忙找根因"。操作者的根因分析是正确、深入、有源码依据的。

真正的成本在三个地方：

**A. 反馈时延。** 每验证一个假设要 2 小时以上，而且结果落在凌晨。假设错了，代价是一整夜。

**B. 失败发现得太晚。** WRF 从第一分钟起就在写 `wrfout`，一切"看起来正常"。
`wrfrst` 缺失只在**全部跑完之后**的产物校验环节才暴露。
但这个失败其实在**闹钟本该响起而没响的那一刻**就已经注定了。

**C. 硬找出来的知识没有沉淀。** 那 47 分钟挖出来的两条结论
（热重启会覆盖闹钟、`override_restart_intervals` 会导致 FATAL）
目前只存在于一个 case 目录的配置注释里。换个目录、换个人、过三个月，就得重挖一遍。

---

## 算力哨兵在这个案例里做什么

| 环节 | 能力 | 在本案例中的效果 |
|---|---|---|
| 提交前 | `config-precheck` 校验「restart 闹钟是否会在运行窗口内触发」，并识别热重启会从 restart 文件恢复闹钟这一语义 | **零成本拦截**，第一次就不会跑 |
| 运行中 | `progress-probe` 按 workload 适配器知道「9 小时窗口 + 540 分钟闹钟」意味着某个时刻应出现 `wrfrst_*`；到点没有即告警 | 在**闹钟本该响时**发现，而不是 2 小时后 |
| 失败后 | `crash-triage` 区分「良性退出」与「产物缺失」——`rc=1` 是良性的，`零个 wrfrst` 才是故障 | 直接指向正确的问题 |
| 沉淀 | `curator` 把两条结论写回 Runbook，并为 `config-precheck` 增加判据 | 第二次、第三次不必重挖 |

注意最后一行：即使系统**没能**替代那 47 分钟的源码排查（它大概率不能），
只要它把结论沉淀下来，第二次 2 小时 21 分钟的重跑就不会发生。

**这就是这个系统真正的价值主张：不是替代专家的判断，是缩短反馈回路、保住已经付出代价换来的结论。**

---

## 可复现性

本案例全部证据均为运行现场的原始文件，未经修改：

```
crown_ab_v2_20260717/
├── orchestrate.log            5 行，第一次 ABORT
├── orchestrate_v3.log         5 行，第二次 ABORT
├── bridge_v2/cwd/namelist.input   无 override_restart_timers
├── bridge_v3/cwd/namelist.input   有 override_restart_timers（L37）+ 根因注释（L29-36）
├── bridge_v2/out/             SWAN rst 齐全、wrfout 7.2 GB、wrfrst 数量 0
└── bridge_v3/out/             SWAN rst 齐全、wrfout 1.8 GB、wrfrst 数量 0
```

对外材料中主机名与绝对路径需脱敏后再发布。
