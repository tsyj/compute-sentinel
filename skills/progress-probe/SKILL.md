---
name: progress-probe
description: |
  判定一个长时计算任务"到底有没有在推进"。跨日志尾部、输出文件 mtime/size、CPU 占用、GPU 利用率四类信号做联合推理，输出 RUNNING / STALLED / DEAD / UNKNOWN 与置信度。适用于数值模式（WRF、ROMS、SWAN、MITgcm）、深度学习训练、大规模数据下载与多阶段批处理流水线。核心规则是区分**进展信号**（日志进度行、产物文件）与**存活信号**（CPU/GPU 占用）：只有进展信号才能证明在推进，资源被占着不等于任务在往前走。
  Triggers: "作业卡住了吗", "还在跑吗", "进度", "stalled", "hang", "卡死", "没动静", "日志不动", "GPU 0%", "进程活着但", "long-running job health", "progress check", "巡检"。
metadata:
  status: prototype         # design | prototype | stable（0.2.0 起有实测回放数据）
  risk_level: L0            # 纯只读
  version: 0.2.0
---

# ProgressProbe

判定长时计算任务是否仍在推进。

## 为什么需要它

长时任务的日志静默是常态，不是异常。ROMS 按 `NTSAVG` 间隔写日志，WRF 按 timestep 写，
数据下载任务通常完全不写。因此"日志不动"这一个信号同时对应**一切正常**和**已经死了**。

只看日志会漏报；只看进程存活会漏掉"进程活着但陷入退化代码路径、CPU 100%、GPU 0%、
永远不产出任何东西"这种最昂贵的故障。

## 判定规则

### 进展信号与存活信号必须分开

> 这一条是 2026-08-15 实测**推翻原设计**后改的，详见 [measure-06](../../evidence/measure-06-gpu-stall.md)。
> 原规则是三类信号对等 OR，结果在 GPU 退化路径上**全程漏报** ——
> 因为那种故障里 CPU 恰恰是打满的。

| 类别 | 信号 | 采集方式 | 能证明什么 |
|---|---|---|---|
| **进展** | A · 日志 | 日志新增片段里匹配到 adapter 的进度行 | 任务在往前走 |
| **进展** | B · 输出文件 | 主输出文件的 `mtime` 或 `size` 变化 | 任务在往前走 |
| 存活 | C · CPU | 作业进程的 CPU% | 只能区分"卡住"与"死了" |
| 存活 | D · GPU | `nvidia-smi` 利用率（adapter 声明 `gpu_signal.required` 时才采） | 同上 |

**只有进展信号能投 RUNNING。** 存活信号的作用是：

1. 防止把"卡住"误判成 `DEAD`；
2. 对 GPU 型 workload，**「CPU 忙 + GPU 闲」是退化路径的正面指征** ——
   此时 CPU 不计进展票，并置 `degradation_suspected`。

对纯 CPU workload（ROMS / WRF / MITgcm）行为不变：CPU 忙仍算进展票，
因为它们的资源画像本就该是 CPU 忙。这条由 `tools/test_decide.py` 的 T3/T4 防回归护栏钉住。

**日志"在长"不等于"有进度"**：只统计新增片段里匹配进度行的条数。
一个疯狂刷同一行错误的作业，size 一直涨但进度行为零 —— 那不是 RUNNING。

**全部进展信号在连续 N 轮内都静止**，且累计静止时长超过该 workload 的阈值，
才判定 `STALLED`。累计用的是**调用方给出的真实轮询间隔**，不是写死的常数。

进程已不存在则判定 `DEAD`，交由 `crash-triage` 归类。

任一信号采集失败时**降级为 `UNKNOWN` 并缩短下次轮询间隔**，绝不因单信号缺失判 `DEAD`。

## 阈值

阈值按 workload 类型配置，不使用全局默认值：

| workload | 首个进度信号超时 | 中途静止阈值 | 备注 |
|---|---|---|---|
| 深度学习训练 | 10 min | 15 min | 另有 **5 min 快速通道**，见下 |
| 数值模式（ROMS / COAWST / WRF） | 30 min | 30 min | warm-up 长；日志按输出间隔写，静默正常 |
| 预处理（metgrid / real） | 10 min | 10 min | 应快速产出中间文件 |
| 数据下载 | 10 min | 15 min | 日志通常静默，**主要看文件 size 增长** |
| generic | 15 min | 20 min | 兜底 |

### 快速通道 `degraded_stall_sec`

GPU 型 workload 上，若出现「CPU 打满 + GPU 闲置」的退化画像，可用更短的阈值（默认 5 min）。

**但它有一个强制前置条件：本次运行中 GPU 必须曾经忙过（`gpu_ever_busy == True`）。**
数据预处理阶段 CPU 忙、GPU 闲是完全正常的，没有这个前提，短阈值会把预处理打成卡死。

`gpu_ever_busy` 只做**单向置位，绝不复位** —— GPU 利用率是脉冲式的，
"最近几次没采到"不代表预处理又开始了。

## 输入

```json
{
  "job_id": "string",
  "workload_type": "wrf | roms | coawst | mitgcm | pytorch | download | generic",
  "run_dir": "/abs/path",
  "prev_snapshot": { "...上一轮采集结果，首轮为 null" }
}
```

## 输出

```json
{
  "status": "RUNNING | STALLED | DEAD | UNKNOWN",
  "signals": {
    "log":      { "changed": true,  "detail": "size 12.4MB → 12.9MB，尾行 timestep 4821" },
    "file":     { "changed": false, "detail": "his_0003.nc mtime 未变（18 min 前）" },
    "resource": { "changed": true,  "detail": "CPU 均值 782%，GPU util 0%" }
  },
  "confidence": 0.0,
  "stalled_for_sec": 0,
  "next_poll_sec": 300,
  "snapshot": { "...供下一轮比对" }
}
```

## 调用条件

- Sentinel Agent 的周期巡检
- 用户自然语言询问某个作业状态
- 其他 Skill 在执行恢复动作前后需要确认作业状态

## 依赖

集群适配器（MCP 或等价契约）提供：`tail` / `stat` / `ps` / `nvidia-smi` / 调度器查询。
不直接持有集群凭证——凭证由网关注入，本 Skill 只拿到消费令牌。

## 失败处理

| 情况 | 处理 |
|---|---|
| 日志文件不存在 | 信号 A 标记 unavailable，不影响 B / C 判定 |
| 输出目录无权限 | 信号 B unavailable，降级 UNKNOWN，上报权限问题 |
| `nvidia-smi` 不可用（非 GPU 作业） | 信号 C 只用 CPU 与 IO |
| 采集超时 | 返回 UNKNOWN，`next_poll_sec` 减半，连续 3 次超时上报 |
| adapter 缺失 | 回落到 generic adapter 并在输出中显式标注"判定精度下降" |

## 安全边界

**纯只读**。不写任何文件、不发送任何信号、不修改任何配置。风险等级恒为 L0。

## 复用价值

判定逻辑与 workload 无关，领域差异全部收敛到 adapter：

```
adapters/
  wrf.yaml        进度行正则、输出文件 glob、阈值
  roms.yaml
  coawst.yaml
  mitgcm.yaml
  pytorch.yaml
  download.yaml
  generic.yaml
```

接一个新场景 = 写一个 adapter 配置，不改 Skill 逻辑、不改 Agent。

## 与多 Agent 流程的关系

Sentinel Agent 的主 Skill。输出 `STALLED` 或 `DEAD` 时，Sentinel 不自行处置，
只把结论连同三类信号的原始值升级给 Triage Agent。判定过程整体写入 Trace，可回放。

## 版本与演进

采用语义化版本。**判定逻辑与 adapter 分开计版**，因为两者的变更频率差一个数量级。

| 变更类型 | 版本位 | 举例 |
|---|---|---|
| 输出 Schema 破坏性变更 | major | `status` 枚举值增删、字段改名 |
| 新增信号类别、新增可选输出字段 | minor | 增加「网络 IO」作为第四类信号 |
| 阈值调整、adapter 内部规则修正 | patch | 把训练类的静止阈值从 15 min 改为 12 min |
| **新增一个 workload adapter** | adapter 独立计版 | `wrf@0.2.0`，不影响 Skill 本体版本 |

**兼容性承诺**：`status` / `signals` / `confidence` 三个字段在同一 major 内保证向后兼容，
下游 Agent 可以安全地只依赖这三项。

**发布与回滚**：所有变更走 PR + review，禁止直接改线上。
每个版本在注册中心保留，Worker 通过标签引用；回滚 = 改标签指回上一版本，不需要重新部署。

**阈值不是代码**：per-workload 阈值放在 adapter 配置里，调阈值走配置变更流程，
不需要动 Skill 版本 —— 这是刻意的设计，因为阈值一定会在接入新集群时反复调整。

### 已发生的版本变更

| 版本 | 日期 | 变更 | 触发原因 |
|---|---|---|---|
| 0.1.0 | 2026-08 初 | 首版：三信号对等 OR | 依据真实事故设计 |
| **0.2.0** | 2026-08-15 | **进展信号与存活信号分层**；接入 GPU 信号；新增 `degraded_stall_sec` 快速通道（带 `gpu_ever_busy` 前置条件）；修正轮询间隔与置信度上界 | **实测证伪**：GPU 退化路径上旧规则全程漏报（[measure-06](../../evidence/measure-06-gpu-stall.md)） |

0.2.0 属于 minor 而非 major：`status` / `signals` / `confidence` 三个字段的契约未变，
下游 Agent 不需要改。新增的 `degradation_suspected` 是可选字段。

## 能力评估

这个 Skill 的评估**必须用真实历史运行数据回放**，不能用合成数据 —— 因为它要区分的
「正常静默」与「已经死了」在合成数据里没有分布差异。

### 评估集构造

从历史运行中截取信号快照序列，人工标注真值：

| 样本类型 | 来源 | 为什么关键 |
|---|---|---|
| **正常静默**（负样本） | 数值模式两次输出之间的长间隔、下载任务的静默期 | **最重要**。误报会让人不再相信告警，比漏报更致命 |
| 真卡死 | 已确认的退化路径、死锁、掉卡事故 | 正样本 |
| 缓慢但正常 | 慢节点上的作业 | 区分「慢」与「停」 |
| 采集失败 | 权限缺失、文件被删、节点失联 | 验证降级为 `UNKNOWN` 而不是误判 `DEAD` |

### 指标

| 指标 | 定义 | 目标 |
|---|---|---|
| **误报率** | 正常静默被判为 `STALLED` 的比例 | 首要压制项。宁可晚报，不可错报 |
| **漏报时延** | 真实卡死发生 → 判定为 `STALLED` 的时间差 | 应显著小于该 workload 的一次重跑代价 |
| `UNKNOWN` 正确率 | 采集失败时正确降级、且未误判为 `DEAD` 的比例 | 接近 100% |
| adapter 覆盖率 | 有专用 adapter 的作业占全部作业的比例 | 兜底 generic 时须在输出中标注精度下降 |

### 评估方式

以**规则回放**为主：把标注好的快照序列喂进去，比对输出与真值。
判定逻辑是确定性的（信号比对 + 阈值），因此评估结果可复现，不依赖模型采样。

回放工具 `tools/replay_signals.py` 调用的是 `cluster_mcp_server.decide()` ——
**线上那一份判定逻辑本身**，不是为评估另写一份，因此评估结论不会与线上实现漂移。

回归测试固化为 `tools/test_decide.py`（17 项，纯标准库），每次 PR 必跑。

### 已跑过的评估（2026-08-15）

| 数据集 | 规模 | 结果 |
|---|---|---|
| GPU 退化路径 · 20s 采样（[measure-06](../../evidence/measure-06-gpu-stall.md)） | 65 个真实快照 | 0.1.0 **全程漏报**；0.2.0 漏报时延 **909s** |
| GPU 退化路径 · 10s 采样（[measure-07](../../evidence/measure-07-sampling-rate.md)） | 45 个真实快照 | 0.1.0 **全程漏报**；0.2.0 漏报时延 **318s** |
| 语义单测 | 17 项 | 全通过，含纯 CPU workload 与预处理阶段的防误报护栏 |
| **误报** · COAWST 耦合 33.79h 成功运行（[measure-08](../../evidence/measure-08-false-positive-and-perf.md)） | 28800 个进度点 | 当前阈值 1800s → **误报 0 次**；误报归零的最小阈值 600s，裕量 3 倍 |
| **误报** · 18 GB 下载 4.29h 成功完成 | 351824 个进度点 | 全部采样×阈值组合 → **误报 0 次** |

**漏报与误报的权衡（选工作点的依据）**：

| `stall_sec` | 误报率（33.8h 真实成功运行） | 漏报时延（GPU 退化路径） |
|---|---|---|
| 300 | 0.05% | 318 秒 |
| 600 | 0% | ~610 秒 |
| 900 | 0% | 909 秒 |
| 1800 | 0% | ~1810 秒 |

阈值压到 60 秒要付 2.2% 误报 —— 按 30 秒采样算是每天约 64 次假告警，
一周之内没人会再看这个告警。这就是"宁可晚报，不可错报"具体值多少钱。

**采样准则**（由 measure-07 得出，接新集群时必须核对）：
采样周期必须细于该 workload 的**最短工作相位**，否则整个相位可能被跳过 ——
measure-06 里 20 秒采样完全漏掉了 16 秒的 GPU 工作相位，导致快速通道无法启用。
GPU 训练建议 ≤ 10 秒。

**已知局限**（不隐瞒）：

- **GPU 训练场景的误报率仍无数据。** 误报测的是 CPU 型 workload（COAWST、下载），
  训练作业的正常静默分布可能不同，缺一份成功跑完的训练日志。
- 误报样本只有 2 个作业、来自同一集群同一团队。38.1 小时是真实的，
  但样本多样性不足以谈"泛化"。
- 两次 GPU 实验都是**构造的复现**而非生产事故现场。
- 单卡。多卡下"部分 rank 卡住"这一情形**当前判据覆盖不到**，需要 per-rank 采集。

### 性能

| 项 | 数字 |
|---|---|
| 单次判定开销 | 0.33 ms（中位）／ 0.35 ms（P90） |
| 资源信号（全机 `ps` 扫描） | 108 ms —— 唯一的瓶颈 |
| 一轮巡检 100 个作业 | **11.0 s → 0.19 s**（`ps` 快照加 2s TTL 让同轮共用一次扫描） |

`ps` 扫描是整套判定里**唯一随作业数线性劣化**的地方，接入前必须确认这个缓存生效。
