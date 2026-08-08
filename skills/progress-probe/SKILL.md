---
name: progress-probe
description: |
  判定一个长时计算任务"到底有没有在推进"。跨日志尾部、输出文件 mtime/size、CPU/GPU/IO 占用三类信号做联合推理，输出 RUNNING / STALLED / DEAD / UNKNOWN 与置信度。适用于数值模式（WRF、ROMS、SWAN、MITgcm）、深度学习训练、大规模数据下载与多阶段批处理流水线。核心规则是 OR 而非 AND：任一信号有变化即判定在推进，全部静止且超过该 workload 阈值才判卡死。
  Triggers: "作业卡住了吗", "还在跑吗", "进度", "stalled", "hang", "卡死", "没动静", "日志不动", "GPU 0%", "进程活着但", "long-running job health", "progress check", "巡检"。
metadata:
  status: design            # design | prototype | stable
  risk_level: L0            # 纯只读
  version: 0.1.0
---

# ProgressProbe

判定长时计算任务是否仍在推进。

## 为什么需要它

长时任务的日志静默是常态，不是异常。ROMS 按 `NTSAVG` 间隔写日志，WRF 按 timestep 写，
数据下载任务通常完全不写。因此"日志不动"这一个信号同时对应**一切正常**和**已经死了**。

只看日志会漏报；只看进程存活会漏掉"进程活着但陷入退化代码路径、CPU 100%、GPU 0%、
永远不产出任何东西"这种最昂贵的故障。

## 判定规则

采集三类信号，**任一变化即判定 RUNNING**（OR，不是 AND）：

| 信号 | 采集方式 | 说明 |
|---|---|---|
| A · 日志 | 日志文件 size 增长，或尾部出现新的进度行 | 进度行的正则由 adapter 提供 |
| B · 输出文件 | 主输出文件的 `mtime` 或 `size` 变化 | 数值模式的 history / restart 文件；训练的 checkpoint |
| C · 资源 | 该作业进程的 CPU%、GPU util、磁盘写入速率 | GPU 类作业单独看 GPU util |

**全部三类信号在连续 N 轮内都静止**，且累计静止时长超过该 workload 的 `stall_threshold`，
才判定 `STALLED`。

进程已不存在则判定 `DEAD`，交由 `crash-triage` 归类。

任一信号采集失败时**降级为 `UNKNOWN` 并缩短下次轮询间隔**，绝不因单信号缺失判 `DEAD`。

## 阈值

阈值按 workload 类型配置，不使用全局默认值：

| workload | 首个进度信号超时 | 中途静止阈值 | 备注 |
|---|---|---|---|
| 深度学习训练 | 10 min | 15 min | 预处理阶段完成后 GPU util 应跳到 70%+ |
| 数值模式（ROMS / COAWST / WRF） | 30 min | 30 min | warm-up 长；日志按输出间隔写，静默正常 |
| 预处理（metgrid / real） | 10 min | 10 min | 应快速产出中间文件 |
| 数据下载 | 10 min | 15 min | 日志通常静默，**主要看文件 size 增长** |
| generic | 15 min | 20 min | 兜底 |

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

回归测试固化为评估集，每次 PR 必跑；误报率变差即阻断合并。

**已知局限**：评估集目前来自单一集群与单一团队的作业，
对其他机构的 workload 分布是否成立，需要接入后重新标注。这一点不隐瞒。
