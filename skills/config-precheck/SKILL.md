---
name: config-precheck
description: |
  长时算力作业**提交前**的静态预检：在一个核都还没烧之前，零成本检出那些会让作业"跑满整个窗口才发现失败"的配置问题。当前实现覆盖 WRF / COAWST 耦合作业的三类判据——restart 闹钟不可达（C1）、namelist 变量未在本 build 的 Registry 注册（C2）、跨文件残留上一个实验的硬编码路径（C3）；多份上游数据的空间范围 / 时间窗 / 网格一致性检查属设计覆盖、尚未实现。
  Triggers: "提交前检查", "配置对不对", "预检", "namelist", "参数一致性", "重启文件没产出", "变量名对不对", "复制过来的配置", "缺值"。
metadata:
  status: prototype         # design | prototype | stable（0.1.0：有可运行实现，且在真实事故上跑过评估）
  risk_level: L0            # 纯只读，不改任何文件
  version: 0.1.0
---

# ConfigPrecheck

在作业提交之前，把"跑两小时才能知道的失败"变成"0.3 秒就知道的失败"。

## 为什么需要它

真实事故（[证据案例 01](../../evidence/case-01-wrf-restart-alarm.md)）：
一次 160 核的三模式耦合作业，跑满 2h10m 后 `ABORT: WRF rst missing`——整个窗口一个重启文件都没写出来。
按交接简报改了变量名再发，162 个 rank 起来后秒崩；再改，跑满 2h22m 又失败。
一夜三次，4h32m × 160 核，人在凌晨读 WRF 源码才定位到根因：
`restart = .true.` 时 WRF 从重启文件恢复写重启闹钟，namelist 里新写的 `restart_interval` **根本不生效**（`WRF/share/input_wrf.F L291`）。

这三次失败里有两次的根因**全部写在配置文件里**，提交前静态就能看出来。
但没有任何一层现有工具去看——调度器只管资源，模式只在跑到那一步时才 FATAL。

## 判据

全部为**只读**规则，每一条都来自一次真实的踩坑：

| ID | 级别 | 检查内容 | 为什么致命 |
|---|---|---|---|
| **C1.1** | ERROR | `restart = .true.` 且未设 `override_restart_timers = .true.` | 重启文件里的旧闹钟覆盖 namelist，本次 `restart_interval` 不生效；若旧周期大于运行窗口，一个重启文件都不产出。依据 `WRF/share/input_wrf.F L291` |
| **C1.2** | ERROR | `restart_interval` > 运行窗口（`run_days/hours/minutes/seconds` 之和） | 无论冷热启动，闹钟永远不会触发 |
| C1.3 | WARN | 闹钟会触发，但距窗口结束余量 < 10 分钟 | 步长不整除或末段耗时略偏，重启文件可能写不出来 |
| C1.0 | WARN | 未设置 `restart_interval` | 中断只能全量重跑 |
| **C2.1** | ERROR | namelist 变量未在本 build 的 Registry 中注册 | 读配置阶段直接 FATAL + `MPI_Abort`，所有 rank 秒退。**输出里附本 build 中最接近的已注册名** |
| C2.2 | ERROR | 变量已注册但写错了 namelist 组 | 读取同样失败 |
| C2.0 | INFO / WARN | 显式列出**跳过了哪些组、为什么**（源码硬编码的 `&namelist_quilt`、Registry 未声明的组）；Registry 为空则整体跳过 C2 并告警 | 静默放过等于把误报换成漏报，所以必须明说 |
| **C3.1** | ERROR | `namelist.input` 与各 `*.in` 里残留另一个分支的绝对路径（同实验根下的其他分支 R1，或同族不同版本号的目录名 R2） | **不报错**，安静地把产出写进上一个实验的目录、或从上一个实验读耦合子配置。按文件汇总并给出残留计数 |

判据表**只增不减**（见「版本与演进」）。

## 输入

命令行（也是 Executor / Verifier 实跑时的调用方式）：

```bash
python3 tools/config_precheck.py --case <配置目录> [--registry <WRF/Registry 目录>] [--json]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--case` | 是 | 配置目录，须含 `namelist.input`；同目录下所有 `*.in`（如 `ocean_*.in` / `swan_*.in` / `coupling_*.in`）一并纳入 C3 |
| `--registry` | 否 | 本 build 的 `WRF/Registry` 目录。不给则跳过 C2 并在输出中标明 |
| `--json` | 否 | 输出结构化 JSON，供 Agent 消费 |

## 输出

```json
{
  "case": "/abs/path/to/cwd",
  "registry_vars": 2716,
  "files": ["namelist.input", "coupling_bridge.in", "ocean_bridge.in", "swan_bridge.in"],
  "errors": 1,
  "warnings": 0,
  "findings": [
    {
      "id": "C1.1",
      "level": "ERROR",
      "title": "热重启会覆盖 namelist 的 restart_interval，闹钟周期不可知",
      "detail": "…",
      "evidence": "namelist L28: restart_interval = 540；override_restart_timers 未设置",
      "source": "WRF/share/input_wrf.F L291",
      "fix": "在 &time_control 中加入 override_restart_timers = .true.，…"
    }
  ]
}
```

每条 finding 必带 `evidence`（文件 + 行号 + 原文）；有源码依据的带 `source`；能给修法的带 `fix`。
**退出码：0 通过 / 1 有告警 / 2 有错误**——Executor 用它决定"能不能提交"。

## 调用条件

- 作业提交前（人或 Planner 发起）
- Executor 在执行 L2 配置改动**之前**（确认改动本身合理）与**之后**（验证改动生效、未引入新错误）
- Verifier 独立复核 Executor 的改动时，**自己再跑一遍**，不采信 Executor 自述

## 依赖

纯 Python 3 标准库，零第三方依赖；只读配置目录与 Registry 目录。
不持有集群凭证、不访问网络、不调用任何模型。

## 失败处理

| 情况 | 处理 |
|---|---|
| `namelist.input` 不存在 | 报错退出（码 2），不猜 |
| 未提供 Registry / Registry 解析为空 | 跳过 C2 并在输出中**显式标明**（C2.0），其余判据照常 |
| namelist 组不由 Registry 驱动 | 整组跳过并列出组名与原因（C2.0），不静默 |
| 配置在静态层面看不出问题 | 如实输出 0 错误，**不假装能解释**运行时才暴露的失败（见 measure-01 状态 4） |

统一原则：**证据不足时输出"缺什么、跳过了什么"，不猜、不编造结论。**

## 安全边界

- **风险等级恒为 L0**：只读文件、不改任何配置、不发任何信号
- 修法只以 `fix` 文本给出，**由人或 Executor 在审批流程内决定是否采用**；本 Skill 自身不改文件
- 输出中出现的路径可能含主机内部路径，入库前按 `evidence/README.md` 的脱敏规则处理

## 复用价值

判据是**数据**（每条 = 检查逻辑 + 证据 + 依据 + 修法），Skill 骨架与 workload 无关。
当前判据表针对 WRF / COAWST；接其他模式（ROMS 独立、MITgcm、训练脚本的配置）是**补判据**，不是改架构。
最有价值的一点：**修法建议里附源码依据**（如 `input_wrf.F L291`）——它把"我们踩过的坑"变成了可以交给别人、别人也能核对的知识。

## 与多 Agent 流程的关系

已在实跑中被两个 Agent 使用（不是纸面依赖）：

- **Executor**（[measure-10](../../evidence/measure-10-l2-approval.md)）：改配置前先跑预检确认改动合理 → 建回滚点 → 请求 L2 审批 → 批准后执行 → **重跑预检**验证（1 个错误 → 0 个错误）并贴出结果
- **Verifier**（[measure-11](../../evidence/measure-11-verifier.md)）：不采信 Executor 自述，**自己再跑一遍**预检；阴性对照那次正是靠预检报出的 1 个错误戳穿了 Executor 声称的"0 个错误"
- Planner 在生成"改配置"类方案时应引用预检结论；Curator 的复盘产出是新判据的来源

## 版本与演进

判据库**只增不减**（同 `safe-kill` 的思路，但审查强度低一档）：
每一条判据都来自一次真实的踩坑，删除意味着放弃一次已经付过学费的教训。

| 变更 | 版本位 |
|---|---|
| 新增判据 | 判据库 minor |
| 放宽 / 删除判据 | 判据库 major，变更说明须写明「放宽后哪一类历史事故会重新变为可能」 |
| 输出 Schema 变更 | Skill 本体 major |

判据来源：Curator 的复盘产出、以及接入新场景时人工补充。

**已发生的变更**（0.1.0 内的两次误报修复，记录在 [measure-01](../../evidence/measure-01-config-precheck.md)）：
C2 从"逐变量查 Registry"改为**按组判定**并显式列出跳过的组（修掉 `&namelist_quilt` 的误报）；
C3 增加与物理位置无关的判据 R2（同族不同版本号的目录名），修掉重建 fixture 上的漏报。

## 能力评估

**评估集直接用历史事故**。基线来自证据案例 01，该案例包含三个独立的可静态检出问题：

| # | 问题 | 期望 |
|---|---|---|
| 1 | restart 闹钟被重启文件覆盖 / 周期超出运行窗口，永远不会触发 | 拦下 |
| 2 | namelist 里写了未在本 build Registry 注册的变量名 | 拦下，并指出正确名字来自 Registry 查询 |
| 3 | 四个配置文件里残留上一个实验的硬编码路径 | 拦下，列出全部残留位置 |

这三条**全部拦下**是本 Skill 的合格线——因为它们各自都曾造成一次两小时以上的无效运行，
而且**全部可以在提交前零成本静态检出**。

| 指标 | 目标 |
|---|---|
| 历史事故拦截率 | 高，且新增事故须同步补进评估集 |
| **误报率**（反指标） | 低。误报会让人养成无视预检的习惯 |
| 修复建议可用率 | 给出的 fix 建议能被直接采纳的比例 |

### 已跑过的评估（2026-08-09，[measure-01](../../evidence/measure-01-config-precheck.md)）

被测对象：`tools/config_precheck.py`；被检对象：案例 01 的真实配置目录（Yagi 三模式耦合，160 核），
Registry 为该 case 所用 binary 对应的构建树（2716 个已注册变量）。复现：`bash tools/reproduce_case01.sh`。

| # | 配置状态 | 来源 | 预检输出 | 当晚实际后果 |
|---|---|---|---|---|
| 1 | `bridge_v2` | 原始文件，未改 | ✗ C1.1（1 错误） | 跑满 2h10m × 160 核后 `WRF rst missing` |
| 2 | 01:24 那次发射 | 按 worklog 重建 | ✗ C1.1 + ✗ C2.1（2 错误），**并直接给出正确变量名 `override_restart_timers`** | 162 rank 秒崩 |
| 3 | 复制后未改路径 | 按 worklog 重建 | ✗ C1.1 + ✗ C3.1 × 4 文件（5 错误），残留计数 2/4/10/2 与人工记录逐项吻合 | 会安静地把产出写进上一个分支目录 |
| 4 | `bridge_v3` | 原始文件，未改 | ! C1.3（0 错误 1 告警） | 跑满 2h22m 后同样失败——**工具不声称能解释这一次** |

**结论：三次失败中两次的根因零成本检出**（状态 1 那条正是人工读源码 45 分钟才定位到的；工具约 0.3 秒），
第三次不能，工具也没有假装能。

| 指标 | 结果 |
|---|---|
| 历史事故拦截率 | 3 条可静态检出的问题 **3/3 拦下**（合格线达成） |
| 误报率 | 两份**真实跑过、且跑满全窗**的配置（`bridge_v2` / `bridge_v3`）C2 / C3 **零误报**。开发中出现过 2 个误报，均已修掉并记录 |
| 修复建议可用率 | 事后对照（非前瞻实测）：3 条建议（加 `override_restart_timers`、改用正确变量名、全量替换残留路径）与操作者当晚实际采用的修法一致 |

**回归固化**：`./verify.sh` 的 [5/6] 每次必跑三个合成 namelist 状态——
状态 A 检出 C1.2、状态 B 检出 C1.1、状态 C 两处修正后零误报——不依赖 `/data` 下的真实配置即可复现判据本身。

**已知局限**（不隐瞒）：

- **评估集只有一个案例。** 三条判据来自同一次事故，对其他机构的配置习惯是否成立未经验证。
- **只覆盖 WRF / COAWST。** 判据表可插拔，但换 workload 需要人来写新判据；description 里的上游数据一致性检查（空间范围 / 时间窗 / 网格）**尚未实现**。
- **静态检查有天花板。** 状态 4 就是例子——配置层面看不出问题的失败，这个工具帮不上忙。这也是为什么方案里还需要运行中的 `progress-probe` 和失败后的 `crash-triage`。
- **未测"工具误报时 Agent 会不会盲从"**（measure-11 局限 3）：若预检报了一个假错误，Verifier 会不会照样判 FAIL，尚无实测。
