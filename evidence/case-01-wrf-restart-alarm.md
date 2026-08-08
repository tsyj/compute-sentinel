# 证据案例 01 · 热重启的 restart 闹钟：一夜三次失败

> 来源：`/data/xinyuan/crown_ab_v2_20260717/` + `worklog/2026-07-18.md`
> 系统：Yagi 台风 WRF-ROMS-SWAN 三模式耦合，160 核（112 大气 + 8 海浪 + 40 海洋）
> 时段：2026-07-17 22:26 — 07-18 03:49
>
> **这份材料要证明的不是"专家需要 AI 帮忙找 bug"。恰恰相反——
> 操作者的根因分析全对、有源码实证、还自建了哨兵和预检。
> 代价出在反馈时延、失败暴露得太晚、以及硬挖出来的结论无处沉淀。**

---

## 一夜三次失败

| 时刻 | 事件 | 代价 |
|---|---|---|
| 22:26 | v2 桥接段启动，9 小时窗口 | |
| **00:37** | 跑完整窗。ROMS restart 09:00 记录 ✓、SWAN 8 分区内部时标 09:00 ✓，**WRF 的 `wrfrst_*` 零写出** | **2 小时 10 分 × 160 核** |
| 00:38:03 | 编排器：`bridge rc=1 accepted (benign ROMS STOP 1)` → `ABORT: WRF rst missing` | |
| 00:38→01:2x | 人工深挖：读 `WRF/share/input_wrf.F` L263-271 / L291、查 `WRF/Registry/io_boilerplate_temporary.inc`，定位根因 | 约 45 分钟 |
| **01:24** | 带修复第一次发射 —— 用了交接简报给的变量名 `override_restart_intervals`。**该名不在本 build 的 Registry**，WRF 在读配置阶段直接 FATAL + MPI_Abort，**162 个 rank 起来后秒崩** | 一次发射作废 |
| 01:25 | 改用真名 `override_restart_timers`（Registry 里查到的） | |
| 01:26 | v3 发射。预检全部 PASS | |
| 03:48:39 | 跑完整窗：`ROMS/TOMS: DONE`、SWAN 到达 `+time 20240906.090200`、`STOP 1`（良性，带 IEEE 浮点标志） | **2 小时 22 分 × 160 核** |
| **03:49:20** | **`ABORT: WRF rst missing` 再来一次**，`wrfrst_*` 仍然零个 | |

**合计**：约 4 小时 32 分 × 160 核，外加一次 162-rank 秒崩，跨越两个凌晨时段，三次都在无人值守时发生。

---

## 根因（操作者当晚挖出来的，有源码行号）

WRF 从 restart 起跑时，会**从 restart 文件里恢复"下次写重启"的闹钟剩余秒数**。
那个闹钟是用生产期的 `restart_interval = 720` 分钟设的。于是 WRF 认 720，
**无视 namelist 里新设的 540** —— 而运行窗口只有 540 分钟，720 分钟的闹钟永远等不到。

源码实证 `WRF/share/input_wrf.F` L291：

```fortran
IF (switch == restart_only .AND. .NOT. override_restart_timers) THEN
    ...recover the restart alarms from input...
```

强制从 namelist 重算闹钟的开关是 `override_restart_timers`
（`WRF/Registry/io_boilerplate_temporary.inc:7`）。

**⚠ 变量名陷阱**：看起来更"通用"的 `override_restart_intervals` **不在本 build 的 Registry**。
往 namelist 里塞未注册变量不是 warning，是**读配置阶段直接 FATAL 全崩**。01:24 那次 162-rank 秒崩就栽在这。

---

## 三件必须说清楚的事

### 一、操作者当晚已经自建了"哨兵"

worklog 原文（写于 01:29，v3 发射后）：

> **哨兵布置**（这次加中途哨兵，省得跑完才发现零写）：
> - 30-min liftoff monitor：**三模型步进联合核验**（ROMS step 行 + SWAN +time + WRFout 出现）
>   + crash 签名（namelist error / MPI_Abort / FATAL / BLOWUP / segv）立即报警
> - **09:00 wrfrst 中途哨兵**：检测耦合时钟过 09:00 后，5 分钟内检查
>   `out/wrfrst_d01_2024-09-06_09:00:00` 是否出现 —— 没出现立即 SENTINEL-ALARM

这就是本方案里 `progress-probe` 的**多信号联合判定**和 Sentinel Agent 的职责，
一字不差，连名字都叫"哨兵"。

**所以我们不是在提一个新点子，是在把一个已经被手工验证过的做法产品化。**
手搓版的问题是：它绑死在这一个 case 目录里，换个作业要重写；
没有分级、没有恢复动作、没有沉淀；而且——

### 二、这次哨兵**没有留下任何文件痕迹**

全目录搜索 `SENTINEL` 无命中。它是否真的启动、是否在 03:30 左右报过警、
报了警有没有人看见，**从现存证据无法确认**。

这本身就是论据：**一次性的监控脚本，连"它有没有工作过"都无法事后审计。**
本方案要求所有判定进 Trace、可回放（见 `docs/agent-identity.md` 每个 Agent 的 Trace 字段）。

### 三、修复是对的，预检全过，结果仍然失败

v3 的 namelist 确实有 `override_restart_timers = .true.`（L37，已核验）。
worklog 记录预检全部 PASS：`NRST==1620 / NTIMES==1626 / restart_interval=540 /
override_restart_timers=.true. / SWAN COMPUTE end 09:02 / binary hash match / 源 restart hash match`。
运行也确实跑完了整窗。

**但 `wrfrst` 依然零个，原因至今未定论。本文档不做推测。**

这正是问题的核心形态：**验证一个假设要等 2 小时 22 分钟，答案在凌晨 3 点 49 分揭晓，没有人在场。**

---

## 顺带暴露的另一类问题：跨文件配置一致性

worklog 记录，从 v2 复制出 v3 时，**四个配置文件里全都硬编了 `bridge_v2` 路径**：

- `namelist.input` 的 `history_outname` / `rst_outname`
- `ocean_bridge.in` 的 `RSTNAME` / `HISNAME` / `AVGNAME` / `DIANAME`
- `swan_bridge.in` 的 10 处 `BLOCK` + `RESTART`
- `coupling_bridge.in` 的 `WAV_name` / `OCN_name`

> 否则 v3 会把产物写进 v2 目录、从 v2 读耦合子配置。

靠人工 sed 全量重指 + 残留核验才躲过。这是 `config-precheck` 的典型场景：
**跨文件路径一致性，错了不会立刻报错，会安静地污染另一个实验的结果。**

---

## 算力哨兵在这个案例里改变什么

| 环节 | 能力 | 效果 | 确定性 |
|---|---|---|---|
| 提交前 | `config-precheck`：跨文件路径一致性 + 闹钟是否会在运行窗口内触发 + namelist 变量是否在本 build Registry 中注册 | 三类问题**零成本拦截**，包括 01:24 那次 162-rank 秒崩 | 高——纯静态检查 |
| 运行中 | `progress-probe`：适配器知道"9 小时窗 + 540 分钟闹钟"意味着某时刻应出现 `wrfrst_*`，到点没有即升级 | 在**闹钟本该响时**发现，不是 2 小时后 | 高——手工版已验证可行 |
| 失败后 | `crash-triage`：区分"良性退出"与"产物缺失"。`rc=1` + IEEE 标志是良性，`零个 wrfrst` 才是故障 | 直接指向正确的问题 | 高——判据已存在于现有脚本 |
| 沉淀 | `curator`：把变量名陷阱、闹钟恢复语义、跨文件路径清单写回 Runbook 并转成 `config-precheck` 判据 | 第二次不必重挖 | 高——纯写入 |
| 根因分析 | 读模式源码定位 `input_wrf.F` L291 | **不承诺能替代** | 低——诚实标注 |

最后一行是刻意留的。这个系统**不赌**模型能替专家读 WRF 源码。
它赌的是：**把那 45 分钟换来的结论固化下来，把发现失败的时刻从"跑完之后"提前到"该发生而没发生的那一刻"。**
这两件事都不依赖模型有多聪明。

---

## 证据清单（原始文件，未经修改）

```
crown_ab_v2_20260717/
├── orchestrate.log                  5 行，00:38:03 第一次 ABORT
├── orchestrate_v3.log               5 行，03:49:20 第二次 ABORT
├── bridge_v2/cwd/namelist.input     mtime 07-17 22:24，无 override_restart_timers
├── bridge_v3/cwd/namelist.input     mtime 07-18 01:25，L37 有 override_restart_timers
│                                    L29-36 是操作者写的根因注释
├── bridge_v2/out/                   SWAN rst 8 分区齐全、wrfout 7.2 GB、wrfrst 数量 0
├── bridge_v3/out/                   SWAN rst 8 分区齐全、wrfout 1.8 GB、wrfrst 数量 0
└── bridge_v3/out/bridge_v3_*.log    32,974 行；L3379+ "RESTART run: opening
                                     wrfrst_d01_2024-09-06_00:00:00 for reading"（只读不写）
                                     末尾 ROMS/TOMS: DONE 03:48:39 + STOP 1 + rc=1

worklog/2026-07-18.md                当晚的完整排查记录，含根因、源码行号、哨兵设计、预检清单
```

对外发布前需脱敏主机名与绝对路径。
