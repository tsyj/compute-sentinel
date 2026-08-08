# 证据案例 02 · 按进程名终止作业，在结构上就是错的

> 来源：`/data/xinyuan/COAWST_Yagi_ERA5_WRF3km_GCE_Sep03-08/` 目录实测
> 支撑 Skill：`safe-kill`
>
> 这个案例不靠回忆，**可以在任何一台 Linux 上当场复现**。

---

## 一、Linux 的硬限制

内核的 `TASK_COMM_LEN = 16`（含结尾的 NUL），所以 `ps` 的 `comm` 字段
**只保留可执行文件名的前 15 个字符**。这不是配置，是编译期常量。

## 二、在真实的实验目录里，这个限制会咬人

一个 COAWST 耦合实验目录下，为了对照不同物理方案、不同 bug 修复状态，
同时存在 14 个只有后缀不同的二进制：

| 二进制文件名 | 长度 | `ps` 里看到的 `comm` |
|---|---|---|
| `coawstM_yagi_AB_buggy` | 21 | `coawstM_yagi_AB` |
| `coawstM_yagi_AB_fixed` | 21 | `coawstM_yagi_AB` |
| `coawstM_yagi_C0_buggy` | 21 | `coawstM_yagi_C0` |
| `coawstM_yagi_C_fixed` | 20 | `coawstM_yagi_C_` |
| `coawstM_yagi_CLEAN` | 18 | `coawstM_yagi_CL` |
| `coawstM_yagi_DIAG` | 17 | `coawstM_yagi_DI` |
| `coawstM_yagi_FORCE` | 18 | `coawstM_yagi_FO` |
| `coawstM_yagi_fpetrap` | 20 | `coawstM_yagi_fp` |
| `coawstM_yagi_FRESH` | 18 | `coawstM_yagi_FR` |
| `coawstM_yagi_new` | 16 | `coawstM_yagi_ne` |
| `coawstM_yagi_PRODFIX` | 20 | `coawstM_yagi_PR` |
| `coawstM_yagi_PRODv3` | 19 | `coawstM_yagi_PR` |
| `coawstM_yagi_stc2fix` | 20 | `coawstM_yagi_st` |
| `coawstM_yagi_VECTOR` | 19 | `coawstM_yagi_VE` |

**两组碰撞：**

```
comm = "coawstM_yagi_AB"  →  coawstM_yagi_AB_buggy
                             coawstM_yagi_AB_fixed

comm = "coawstM_yagi_PR"  →  coawstM_yagi_PRODFIX
                             coawstM_yagi_PRODv3
```

第一组是**带 bug 的构建和修好的构建**。这恰恰是你最需要区分的两个东西 —— 而它们在
`ps` 里长得一模一样。按名字批量终止，等于抛硬币。

第二组是**生产修复版和生产 v3**，同理。

## 三、跨用户时后果更严重

同一台共享节点上，不同用户跑着同名的 binary。
真实事故（记录于团队规约 §2.56，2026-06-25）：

为清理自己的试跑进程，执行了

```bash
ps -eo pgid,comm | awk '$2=="coawstM" {print $1}' | sort -u | xargs -I{} kill -9 -{}
```

同机另一位用户的 **128 核生产作业用的是同名 binary**。
这条命令对他们的每一个进程组都发起了终止。

**只因为跨用户 `kill` 会以 `EPERM` 静默失败，那个作业才幸存。
当时唯一起作用的护栏是操作系统的权限模型 —— 而它不是我们设计的。**

---

## 四、结论：区分作业的不是名字，是它读的输入

同名 binary 靠什么区分？**命令行参数里的输入文件。**

```
挖矿参数实验   coawstM  ...  roms_mining_3mon_128cpu.in     ← 别人的
海山试跑       coawstM  ...  roms_real.in                   ← 我的
Yagi 主 case   coawstM_yagi_new  ...                        ← 我的另一个
```

正确的匹配方式：

```bash
ps -eo user,pid,pgid,args \
  | awk '$1=="xinyuan" && /roms_real\.in/ {print $3}' | sort -u
```

同时约束**属主**和**输入文件**，而不是进程名。

---

## 五、`safe-kill` 因此这样设计

三重判据，缺一拒绝：

| 判据 | 检查 | 拦住的是 |
|---|---|---|
| **A · 属主** | 进程 user 必须等于发起人本人 | 跨用户误伤 |
| **B · 命令行** | args 必须匹配本次事故登记的输入文件名 | 同名 binary 的不同作业（AB_buggy vs AB_fixed） |
| **C · 进程组** | PGID 必须在事故开始时预登记的范围内 | 中途新起的无关进程 |

另加四条硬约束：

- 风险等级**恒为 L2**，无论目标多少个、无论信号是 TERM 还是 KILL
- 首次调用强制 `dry_run`，人工看过匹配清单才能实际执行
- **跨用户目标恒拒绝**，不提供任何提权路径
- **不接受 `pkill` / `killall` 式的名称匹配语义**
- 被拒绝的目标必须完整写入审计 —— *被拒绝的比被终止的更重要，它们是判据生效的证据*

详见 [`skills/safe-kill/SKILL.md`](../skills/safe-kill/SKILL.md)。

---

## 六、复现方法

```bash
# 列出目录下所有 binary，算出各自在 ps 里的 comm，找碰撞
ls coawstM_yagi_* | xargs -n1 basename | cut -c1-15 | sort | uniq -d
```

在本案例的目录上执行，输出 `coawstM_yagi_AB` 与 `coawstM_yagi_PR` 两条。

这个案例的价值在于：它不是一个"我们很小心"的态度声明，
而是一个**可当场验证的结构性缺陷**。任何在共享集群上按进程名做批量操作的团队，
都在同一个坑上面走。
