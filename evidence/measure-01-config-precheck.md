# 实测 01 · config-precheck 在证据案例 01 上的拦截结果

> 工具：[`tools/config_precheck.py`](../tools/config_precheck.py)（纯标准库 Python 3，无依赖）
> 复现：`bash tools/reproduce_case01.sh`
> 被检对象：`/data/xinyuan/crown_ab_v2_20260717/`（Yagi 三模式耦合，160 核）
> Registry：该 case 所用 binary `coawstM_yagi_new` 对应的构建树，2716 个已注册 namelist 变量
> 实测日期：2026-08-09

---

## 结论一句话

**那一夜三次失败，其中两次的根因可以在提交前零成本静态检出；第三次不能，工具也没有假装能。**

---

## 四个状态的实测结果

| # | 状态 | 来源 | 预检输出 | 实际后果 |
|---|---|---|---|---|
| 1 | `bridge_v2` | **原始文件，未经修改** | ✗ `C1.1` 1 个错误 | 跑满 2h10m × 160 核后 `ABORT: WRF rst missing` |
| 2 | 01:24 那次发射 | 按 worklog 重建 | ✗ `C1.1` ✗ `C2.1` 2 个错误 | 162 rank 起来后秒崩（namelist-read FATAL） |
| 3 | 复制后未改路径 | 按 worklog 重建 | ✗ `C1.1` + ✗ `C3.1`×4 共 5 个错误 | 会安静地把产出写进上一个分支目录 |
| 4 | `bridge_v3` | **原始文件，未经修改** | ! `C1.3` 0 错误 1 告警 | 跑满 2h22m × 160 核后**同样失败** |

状态 2、3 是按 `worklog/2026-07-18.md` 明确记录的中间态重建的；状态 1、4 是运行现场的原始配置文件，一个字节都没改。

---

## 逐条看

### 状态 1 —— 抓住了真正的根因

```
[✗] C1.1  热重启会覆盖 namelist 的 restart_interval，闹钟周期不可知
      restart = .true. 时 WRF 从重启文件恢复写重启闹钟，
      namelist 里的 restart_interval = 540 分钟不生效。
      若恢复的周期大于本次运行窗口，将一个重启文件都不产出。
      证据: namelist L28: restart_interval = 540；override_restart_timers 未设置
      依据: WRF/share/input_wrf.F L291
      建议: 在 &time_control 中加入 override_restart_timers = .true.
```

这正是操作者在事后花约 45 分钟读模式源码才定位到的结论。
**静态检查在提交前给出同样的结论，耗时约 0.3 秒。**

### 状态 2 —— 连正确的变量名都给了

```
[✗] C2.1  namelist 变量 `override_restart_intervals` 未在本 build 的 Registry 中注册
      WRF 在读取 namelist 阶段遇到未注册变量会直接 FATAL 并 MPI_Abort ——
      不是 warning，所有 rank 会在启动后立刻退出。
      证据: namelist L37  &time_control: override_restart_intervals = .true.
      建议: 本 build 中最接近的已注册名: override_restart_timers
```

01:24 那次发射用的是交接简报给的变量名 `override_restart_intervals`，
该名不在本 build 的 Registry 中，导致 162 个 rank 起来后秒崩。

**工具不仅拦下了它，还直接指出了正确的名字** —— 而这个名字是操作者当晚
在 `WRF/Registry/io_boilerplate_temporary.inc` 里一行行找出来的。

### 状态 3 —— 四个文件的残留全部列出，带计数

```
[✗] C3.1  `namelist.input`      残留 bridge_v2 路径（该文件中共 2 处）
[✗] C3.1  `ocean_bridge.in`     残留 bridge_v2 路径（该文件中共 4 处）
[✗] C3.1  `swan_bridge.in`      残留 bridge_v2 路径（该文件中共 10 处）
[✗] C3.1  `coupling_bridge.in`  残留 bridge_v2 路径（该文件中共 2 处）
```

与 worklog 的人工记录逐项吻合：

> namelist 的 `history_outname`/`rst_outname` + `ocean_bridge.in` 的
> `RSTNAME/HISNAME/AVGNAME/DIANAME` + `swan_bridge.in` 的 10× `BLOCK` + `RESTART`
> + `coupling_bridge.in` 的 `WAV_name`/`OCN_name` **全都硬编 bridge_v2 路径**

这类问题最阴：**不报错，会安静地把产出写进另一个实验的目录**。
当晚是靠人工 `sed` 全量重指 + 残留核验才躲过的。

### 状态 4 —— 修复确实生效了，但工具不解释它为什么还是失败

```
[!] C1.3  最后一次 restart 闹钟距窗口结束余量过薄
      闹钟预计在第 540 分钟触发，运行窗口 542 分钟，余量仅 2 分钟。
      证据: namelist L28
```

`C1.1` 没有触发 —— 说明 `override_restart_timers = .true.` 的修复被正确识别。
工具只给出一条告警：闹钟与窗口结束之间只剩 2 分钟余量。

**但本报告不声称这就是 v3 失败的原因。** v3 的失败原因至今未定论
（见[证据案例 01](case-01-wrf-restart-alarm.md)）。工具能做的是把"唯一可静态观察到的可疑点"
标出来供人判断，不能替代对未知原因的排查。

---

## 误报核验

预检器如果有误报，会被绕过，等于没有。因此对两份**真实且已知能正常跑起来**的配置做了回归：

| 配置 | C2 误报 | C3 误报 |
|---|---|---|
| `bridge_v2`（真实运行过，跑满全窗） | 0 | 0 |
| `bridge_v3`（真实运行过，跑满全窗） | 0 | 0 |

开发过程中确实出现过误报，两个都已修掉，记录在此：

| 误报 | 原因 | 修法 |
|---|---|---|
| `nio_groups` / `nio_tasks_per_group` 被判未注册 | 这两个变量属于 `&namelist_quilt` 组，是 WRF 源码里硬编码的 NAMELIST（`frame/module_dm.F:97`），**不经 Registry** | 检查改为**按组判定**：Registry 未声明的组整组跳过，并在输出中明示跳过了哪些组、为什么 |
| C3 在重建的 fixture 上不触发 | 原判据依赖配置目录的物理位置，重建目录不在原实验根下 | 增加与位置无关的判据：路径中出现与当前分支**同族但版本号不同**的目录名即判残留 |

第一个误报值得单独说：它的成因是"把 Registry 当成 namelist 变量的唯一真值来源"。
真实情况是模式里存在少量源码硬编码的 namelist 组。
**判据现在会显式列出跳过了哪些组**，而不是静默放过 —— 静默放过等于把误报换成漏报。

---

## 这个实测在参赛材料里对应什么

| 评分维度 | 权重 | 本实测提供的证据 |
|---|---|---|
| 场景价值与行业可复制性 | 25% | 痛点是真的（有原始日志），而且**可解**——不是提出一个问题就完事 |
| Skill 工程体系与生态复用 | 25% | `config-precheck` 的「能力评估」不是纸面设计，**评估集就是真实事故，结果可复现** |
| 工程落地、运行验证与安全可审计 | 20% | 有可运行工具、有实测输出、有误报回归、有复现脚本 |

对应官方评分图上那句判据：**「可运行、有证据」**。

---

## 局限（写在明面上）

1. **评估集只有一个案例。** 三条判据来自同一次事故，对其他机构的配置习惯是否成立未经验证。
2. **只覆盖 WRF/COAWST。** 换 workload 需要新写判据，判据表本身是可插拔的数据，但需要人来写。
3. **静态检查有天花板。** 状态 4 就是例子 —— 配置层面看不出问题的失败，这个工具帮不上忙。
   这也是为什么方案里还需要运行中的 `progress-probe` 和失败后的 `crash-triage`。
