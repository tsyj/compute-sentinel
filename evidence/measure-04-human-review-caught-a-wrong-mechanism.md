
# 实测 04 · 人工复核抓住了 Agent 一个"对了一半"的结论

> 承接 [实测 03](measure-03-agents-advanced-an-open-question.md)。
> Planner 把"读 WRF 源码确认 `override_restart_timers` 实现"列为 Step 1（L0，零成本）。
> 本文档就是执行这一步的结果。
>
> **结论：Agent 观察到的现象是真的，但它推测的机制是错的。而真正的机制里藏着一个可验证的代码缺陷。**

---

## 一、先说结论

| # | 结论 | 确定性 |
|---|---|---|
| 1 | `override_restart_timers` 的 **history 分支是不可达的死代码** | ✅ 源码可验证 |
| 2 | v2/v3 的 wrfout 时刻差异，机制是 **"跳过恢复"** 而不是 Agent 说的"覆盖" | ✅ 源码 + 观测吻合 |
| 3 | **源码解释不了 v3 的 restart 闹钟为什么没响** | ⚠️ 问题仍然开放 |
| 4 | 两个代码块对 `RingInterval` 的赋值不一致，是潜在缺陷 | ✅ 源码可验证 |

---

## 二、先解出常量（不解出来无法判定）

`WRF/frame/module_streams.F` + `inc/switches_and_alarms.inc`：

```
history_only    = 1
auxinput1_only  = 27
MAX_HISTORY     = 25
restart_only    = 2*MAX_HISTORY + 1 = 51
boundary_only   = 52
MAX_WRF_ALARMS  = 2*MAX_HISTORY + 5 = 55
```

## 三、`share/input_wrf.F` 里的两个代码块

### 块 1（L237-285，注释标 "INPUT ONLY"）

```fortran
IF ( switch .EQ. restart_only ) THEN              ! ← 无 override 守卫
  ...
  DO i = auxinput1_only, MAX_WRF_ALARMS           ! ← i = 27..55，含 restart(51)
    ...
    CALL WRFU_AlarmGet( grid%alarms(i), ringinterval=interval2 )
    IF (config_flags%override_restart_timers) THEN
       IF (i .EQ. restart_only) THEN
          seconds = <从 namelist 重算 restart_interval>
       ENDIF
    ENDIF
    CALL WRFU_TimeIntervalSet(interval, S=seconds)
    ringTime = curtime + interval
    CALL WRFU_AlarmSet( grid%alarms(i), RingInterval=interval2, RingTime=ringTime )
                                        ^^^^^^^^^^^^^^ 注意：用的是 interval2
```

### 块 2（L291-341，注释标 "OUTPUT ONLY"）

```fortran
IF ( switch .EQ. restart_only .AND. .NOT. config_flags%override_restart_timers ) THEN
                                    ^^^^^^^^^^^^^^^^^^ 只在 override 关闭时执行
  ...
  DO i = 1, auxinput1_only-1                      ! ← i = 1..26，含 history(1)
    ...
    IF (config_flags%override_restart_timers) THEN     ! ← 与外层守卫互斥
       IF (i .EQ. history_only) THEN
          seconds = <从 namelist 重算 history_interval>
       ENDIF
    ENDIF
    ...
    CALL WRFU_AlarmSet( grid%alarms(i), RingInterval=interval, RingTime=ringTime )
                                        ^^^^^^^^ 注意：这里用的是 interval
```

---

## 四、结论 1 · history 覆盖分支是死代码

块 2 的**外层守卫是 `.NOT. override`**，而块内那段 history 覆盖的**条件是 `override`**。
两者互斥：

- `override = .true.` → 整个块 2 不执行，块内代码永远到不了
- `override = .false.` → 块 2 执行，但块内 `IF (override)` 为假，覆盖不生效

**L316-323 这段 history 覆盖代码，在任何配置下都不会被执行。**

---

## 五、结论 2 · 真实机制是"跳过恢复"，不是"覆盖"

Triage 从产物文件名推断出「override 成功覆盖了 history 闹钟」。
**观察是对的，机制是错的。**

history 闹钟（i=1）只出现在块 2 的循环范围（i=1..26）里。而块 2 在 override 打开时**整块被跳过**。
所以：

| 配置 | 块 2 是否执行 | history 闹钟的实际来源 | 首个 wrfout | 实测 |
|---|---|---|---|---|
| v2：override 未设 | ✅ 执行 | 从 restart 文件恢复上一次运行存的"距下次响铃秒数" | 非 namelist 值 | **01:00** ✓ |
| v3：override = .true. | ❌ 跳过 | 保持启动时按 namelist 创建的值（180 分钟） | 180 分钟 | **03:00** ✓ |

两行都与实测吻合。

**但因果不是"override 把 history 闹钟改成了 namelist 值"，
而是"override 让 history 闹钟根本没被 restart 文件覆盖，于是保留了 namelist 原值"。**

结果一样，机制相反。

---

## 六、结论 3 · 源码解释不了 v3 为什么没写 wrfrst

restart 闹钟（i=51）落在块 1 的循环范围（27..55）内，而块 1 **没有** override 守卫，照常执行。
override 打开时：

```
seconds  = namelist restart_interval = 540 分钟
ringTime = curtime + 540 分钟 = 00:00 + 9h = 09:00
```

运行窗口到 09:02。**按这段源码，restart 闹钟应该在 09:00 响。**

但实测 `wrfrst` 文件数是 0。

**因此：源码读到这一层，仍然无法解释 v3 的失败。问题依旧开放。**

尚未排除的可能（本文档不做推测性归因，仅列出下一步该查什么）：

- 块 1 的守卫 `IF (ierr .EQ. 0 .AND. seconds .GE. 0)` —— 若 restart 文件里没有
  `WRF_ALARM_SECS_TIL_NEXT_RING_51` 属性，整段（含 override）被跳过
- 外层 `IF (max_wrf_alarms_compare .NE. MAX_WRF_ALARMS)` —— 若不一致会整体
  "Disregarding info in restart file"
- 闹钟设置之后、真正写 restart 之前是否还有别的路径改动它
- 需要**完整的 WRF 运行日志**（当前只有片段）确认有无相关 warning

这正好回到 Planner 方案 C 建议的那个 10-15 分钟短窗口实验 ——
**当静态阅读到达边界时，用一次廉价实验换确定性。**

---

## 七、结论 4 · 两个块对 `RingInterval` 的赋值不一致

| 块 | 语句 | 用的值 |
|---|---|---|
| 块 1（restart 族） | `WRFU_AlarmSet(..., RingInterval=interval2, ...)` | `interval2`，来自 `WRFU_AlarmGet` |
| 块 2（history 族） | `WRFU_AlarmSet(..., RingInterval=interval,  ...)` | `interval`，由本次 `seconds` 重算 |

同一段逻辑的两个副本，一个用旧值、一个用新值。
即便 override 逻辑本身没问题，**restart 族闹钟的循环周期也不会被本次计算更新** ——
对需要多次响铃的长作业是潜在隐患。

---

## 八、这件事对本项目意味着什么

这是**人工复核层拦下一个"听起来完全合理"的错误机制**的真实案例。

Triage 的推断有观测支持、有置信度标注（0.85）、有缺失证据清单 —— 已经是很规范的输出。
但它的因果解释仍然是错的。如果当时把它当结论固化：

- `curator` 会把"override 能覆盖 history 闹钟"写进 Runbook
- `config-precheck` 会据此加一条错误判据
- 下一个人遇到同类问题，会被这条错知识带偏

这正是本项目在 [`docs/agent-identity.md`](../docs/agent-identity.md) 里给 Curator 定的边界：

> **Decision Boundary**：自主生成，**知识入库需人工 review** ——
> 防止把一次错误归因固化成以后每次都用的判据。

以及 `postmortem-write` 的反指标：

> 被 review 打回的判据中，属于**"过度泛化"**的比例。

**这条边界不是写着好看的。这次它拦下的就是一条会长期误导人的错知识。**

同时也说明另一件事：Agent 的价值在**发现可观察的差异**（v2/v3 的 wrfout 时刻不同 —— 这个没人注意到），
而不在**解释差异的成因**。项目的分工设计正是按这个来的：
让 Agent 做多信号采集与归类，把因果判定留给"证据 + 源码 + 人"。

---

## 九、可复现

```bash
W=<COAWST 构建树>/WRF
grep -n "override_restart_timers" $W/share/input_wrf.F     # → 263, 291, 316
sed -n '237,285p'  $W/share/input_wrf.F                     # 块 1
sed -n '291,341p'  $W/share/input_wrf.F                     # 块 2
grep -n "restart_only\|history_only\|auxinput1_only\|MAX_HISTORY" \
     $W/frame/module_streams.F $W/inc/switches_and_alarms.inc
```

代码缺陷（结论 1、4）与具体算例无关，**对任何使用该版本 WRF 的用户都成立**，
可整理后向上游反馈。

---

# 第二轮 · 从 restart 文件里读出硬数据

源码读到边界之后，还有一步零成本检查没做：**直接看那个 restart 文件里存了什么闹钟状态**。
（Planner 方案里也提到要 `ncdump -h` 确认。）

## 读出的关键值

被 bridge 热重启读入的 `wrfrst_d01_2024-09-06_00:00:00`（3.8 GB）里，195 个全局属性中有 107 个与闹钟相关：

| 属性 | 值 | 换算 | 对应 |
|---|---|---|---|
| `WRF_ALARM_SECS_TIL_NEXT_RING_01` | 3600 | **60 分钟** | history 闹钟（`history_only = 1`） |
| `WRF_ALARM_SECS_TIL_NEXT_RING_51` | 43200 | **720 分钟 = 12 小时** | restart 闹钟（`restart_only = 51`） |
| `MAX_WRF_ALARMS` | 55 | — | 与代码里的 `2*25+5` **一致** |
| `WRF_ALARM_ISRINGING_51` | 1 | — | 保存时 restart 闹钟处于**响铃态** |
| `SIMULATION_START_DATE` | 2024-09-03_00:00:00 | — | production 起点 |

旁证：production 的 restart 文件在磁盘上就是每 12 小时一个
（`09-03_12:00`、`09-04_00:00`、`09-04_12:00`……`09-07_00:00`，各约 3.8 GB）。

## 结论 5 · production 的 720 分钟从推断升级为事实

Triage 当时只能标注 **"✅ 确认（来自注释自述，未直接看到 production namelist）"**，
Planner 也原样传递了这个保留。

现在有两条独立的直接证据：文件属性里的 `43200 秒`，以及磁盘上 12 小时一个的 restart 文件间隔。
**这个值不再是推断。**

## 结论 6 · history 机制得到数值级验证

第一轮从源码推出的机制（"override 打开 → 块 2 整块跳过 → history 闹钟保留 namelist 原值"），
现在可以用具体数字对上：

| 运行 | 块 2 | history 闹钟的值 | 首次响铃 | 实测首个 wrfout |
|---|---|---|---|---|
| v2（override 关） | ✅ 执行 | 取文件里的 **3600 秒** | 00:00 + 1h = **01:00** | `wrfout_..._01:00:00` ✓ |
| v3（override 开） | ❌ 跳过 | 保持 namelist 的 **180 分钟** | 00:00 + 3h = **03:00** | `wrfout_..._03:00:00` ✓ |

**两行都精确吻合，误差为零。** 机制从"源码推断"变成"数值验证"。

同时也彻底否掉了 Triage 的因果解释：
文件里 history 闹钟存的是 3600 秒，而 namelist 写的是 180 分钟。
如果 override 真的像 Triage 说的"覆盖了 history 闹钟"，
那 v2/v3 都会用 180 分钟，两次的 wrfout 首帧应该一样 —— 但实测不一样。

## 结论 7 · restart 闹钟为什么没响，仍然没有解决，但有了新线索

按块 1 的逻辑（i=51 落在其循环范围 27..55 内，且外层无 override 守卫）：

```
守卫 IF (ierr .EQ. 0 .AND. seconds .GE. 0)   → 属性存在且 43200 ≥ 0，通过 ✓
MAX_WRF_ALARMS 一致                          → 恢复逻辑不会被整体跳过 ✓
override 打开 → seconds 改写为 namelist 的 540 分钟 = 32400 秒
ringTime = curtime + 32400 秒 = 00:00 + 9h = 09:00
运行窗口到 09:02
```

**按这条路径，restart 闹钟应该在 09:00 响。但实测 wrfrst 文件数是 0。**

本文档不做推测性归因。但读出了一条**此前没注意到的新线索**：

> `WRF_ALARM_ISRINGING_51 = 1`

restart 闹钟在保存时处于**响铃态**（合理 —— 这个文件本身就是那次响铃写出来的）。
而恢复代码里有：

```fortran
IF ( iring .EQ. 1 ) THEN
  CALL WRFU_AlarmRingerOn( grid%alarms( i ) )
```

也就是说，**重启一开始就把 restart 闹钟的响铃状态重新打开了**。
这个状态与随后 `WRFU_AlarmSet` 设置的 `RingTime` 如何交互，静态读代码判断不了 ——
需要运行时才能观察。

## 三轮下来的账

| 问题 | 状态 | 花费 |
|---|---|---|
| production 的 restart 周期是多少 | ✅ **已确证 720 分钟** | 0（读文件属性） |
| v2/v3 的 wrfout 时刻为何不同 | ✅ **机制已确证并数值验证** | 0（读源码 + 读属性） |
| override 的 history 分支是否可达 | ✅ **确证为死代码** | 0（读源码） |
| v3 的 restart 闹钟为何不响 | ⚠️ **仍开放**，但有了 `ISRINGING` 新线索 | 需运行时实验 |

**三个问题被零成本的静态分析解决，第四个问题被收窄到一个具体的运行时观测点。**

这就是 `config-precheck` 与 `crash-triage` 这类确定性能力的价值 ——
它们不需要模型多聪明，只需要有人（或 Agent）**按顺序把便宜的检查做完再花钱**。
Planner 把"读源码"排在"重跑"之前，是对的。
