# measure-06 · GPU 退化路径卡死实测：我们自己的判定逻辑漏报了头号故障

> 2026-08-15　RTX 4090 / Windows / Python 3.13.5 / torch 2.6.0+cu124
> 原始数据：[`gpu-stall/`](gpu-stall/)　｜　复现命令见文末

---

## 为什么要做这一跑

README 开篇列的第一条痛点是「训练卡在退化代码路径：进程 RUNNING、CPU 100%、GPU 0%、
永不输出进度行」。这条痛点**确有其事，但发生在本项目立项之前**，未保留可发布产物，
所以 `evidence/README.md` 一直把它标注为「⚠️ 无实测」。

**方案里最醒目的那条主张，恰恰是证据最弱的一条。** 这一跑就是来补这个洞的。

## 怎么造的卡死

`gpu-stall/stall_train.py`：前 3 个 epoch 正常训练（4096→8192→8192→4096 的全连接网络，
batch 256，每 epoch 60 步，每轮存一个 checkpoint），随后写下最后一行日志 `ep 4 begin`，
**关闭日志文件**，进入一个纯 CPU 的整数重计算循环。

这不是 `sleep` 假装卡死 —— 循环里真的在算（`s = (s*31 + i) & 0xFFFFFFFF`，每轮 20 万次），
与真实退化路径同构：**进程活着、CPU 打满、GPU 闲置、不再写日志、不再产出文件**。

`gpu-stall/collect_signals.py` 每 20 秒采一次：日志字节数、checkpoint 数量与 mtime、
`nvidia-smi` 的 GPU 利用率、目标进程的内核态+用户态 CPU 时间增量。共 65 个快照。

## 采到了什么

| 时刻 | GPU | CPU | 日志字节 | ckpt | 阶段 |
|---|---|---|---|---|---|
| 22:57:53 | 0% | 84.3% | 270 | 3（1.61 GB） | 三个好 epoch 刚结束 |
| 22:58:14 | 0% | 100.1% | 270 | 3 | 退化路径 |
| … | 0% | 99.7–99.9% | 270 | 3 | 连续 60 个快照全部如此 |
| 23:17:49 | 0% | — | 270 | 3 | 卡死结束（脚本自行退出） |

日志停在 270 字节、checkpoint 停在 3 个、GPU 停在 0%，而 **CPU 稳定在 99.7% 以上**。
这正是心跳式监控会判「健康」的形态。

## 回放判定：修复前漏报，修复后 909 秒发现

`tools/replay_signals.py` 把这 65 个快照喂给 `cluster_mcp_server.decide()` ——
**仓库里那一份判定逻辑本身**，不是为回放另写的。完整输出见
[`gpu-stall/runA-replay.txt`](gpu-stall/runA-replay.txt)。

```
## 修复前（CPU 占用直接算进展票，无 GPU 信号）
23:12:36  RUNNING  0.8   0%  99.9%   3  log=False,file=False,resource=True
23:19:58  RUNNING  0.8   0%   0.1%   3  log=False,file=False,resource=True
→ 全程未判定卡死（漏报）—— 卡死已持续 1329s，判定始终为 RUNNING

## 修复后（GPU 型 workload 上 CPU 忙+GPU 闲不算进展）
23:12:36  RUNNING 0.55   0%  99.9%   3  log=False,file=False,resource=False,gpu=False
23:12:58  STALLED 0.95   0%  99.9%   3  log=False,file=False,resource=False,gpu=False
→ 首次判定 STALLED: 23:12:58，距退化开始 909s（15.2 分钟）

退化路径开始（日志最后一条进度行）: [22:57:49] ep 4 begin
```

| | 修复前 | 修复后 |
|---|---|---|
| 是否发现 | ❌ 全程未告警 | ✅ 告警 |
| 发现时延 | ∞（观测窗口 1329s 内始终 RUNNING） | **909 秒** |

## 根因：把「存活」当成了「进展」

原实现对三类信号做**对等 OR**：日志、产物文件、CPU 占用，任一有变化即判 RUNNING。

但 **CPU 占用是存活信号，不是进展信号**。它只能区分「卡住」和「死了」，不能说明在往前走。
GPU 训练掉进退化路径时 CPU 恰恰是打满的 —— 这一票把状态硬生生拉回 RUNNING。

更糟的是，`adapters/pytorch.json` 里早就声明了 `gpu_signal`
（`required` / `idle_util_pct` / `idle_timeout_sec`），**代码从来没有读过它**。
这与之前两轮审查里发现的 `first_progress_sec`、`progress_line_regex` 是同一类问题：
配置声明了，代码没接。

### 改法

1. **进展信号与存活信号分开**：日志进度行、产物文件是进展；CPU / GPU 是存活。
2. GPU 型 workload 上，「CPU 忙 + GPU 闲」不计进展票，并标记 `degradation_suspected`。
3. **仅当本次运行中 GPU 曾经忙过**，才启用更短的 `degraded_stall_sec` 阈值 ——
   数据预处理阶段 CPU 忙、GPU 闲是正常的，不能用短阈值去打它。
4. 纯 CPU workload（ROMS / WRF / MITgcm）行为完全不变：CPU 忙依然算进展票，
   因为它们的资源画像本就该是 CPU 忙。

第 4 条由 `tools/test_decide.py` 的 T3 / T4 作为防回归护栏钉住 ——
把 CPU 从进展票里摘出去，绝不能反过来把纯 CPU 作业和预处理阶段误判成卡死。

## 顺带修出的三个缺陷

| 缺陷 | 后果 | 状态 |
|---|---|---|
| `snapshot` 里 `interval_sec` 写死 300 | 20 秒轮询时每轮却给卡死计数加 300 秒，3 轮就误报 | 已修（T6 覆盖） |
| 置信度 `0.6 + 0.2×票数` | 三票时算出 **1.2**，置信度越界 | 已修，封顶 0.95（T7 覆盖） |
| `decide()` 原地修改入参 `sig` | 同一字典连续判定两次，第二次不再降级 | 已修，改为不修改入参 |

## 本次实测的局限（如实披露）

1. **`degraded_stall_sec = 300` 的快速通道在本跑中没有触发**，判定走的是通用的 900 秒阈值。
   原因是 `gpu_ever_busy` 从未被观测到 —— 三个好 epoch 总共只跑了 **16 秒**，
   而采样周期是 **20 秒**，GPU 忙的整个相位被采样漏过去了。
   **这本身是一条真实的可观测性教训：采样周期必须比最短工作相位更细。**
   已另跑一次加密采样的对照实验（见 measure-07）。

2. **采集器把自己也匹配进去了**。`--procmatch stall_train` 这个参数值出现在采集器自身的
   命令行里，于是 CPU 读数里混进了采集器自己的约 0.1%（这就是若干个 100.1% 读数的由来），
   且目标退出后 `alive` 仍为真。已修（排除自身 PID 与 `collect_signals`）。
   **对判定结论无影响**：判定只用日志 / 产物 / GPU / CPU 是否过 50% 阈值，
   0.1% 的偏差不改变任何一轮的票型。原始数据保持原样，不做事后修饰。

3. **这是构造的复现，不是生产事故现场**。退化循环是真实 CPU 计算而非 sleep，
   信号形态与真实事故一致，但触发原因是人为设定的。
   它验证的是**判据与阈值**，不是"这类事故有多常见"。

4. 单卡、单机、Windows。多卡（`nvidia-smi` 取的是各卡最大值）与分布式训练未验证。

## 复现

```bash
# 回放（纯标准库，不需要 GPU，不调模型）
python3 tools/replay_signals.py \
    --signals evidence/gpu-stall/runA-signals.jsonl \
    --log     evidence/gpu-stall/runA-train.log \
    --workload pytorch --stall-from-line "ep 4 begin"

# 判定内核单元测试
python3 tools/test_decide.py

# 重新造一次卡死（需要一块 GPU）
python3 evidence/gpu-stall/stall_train.py --outdir run --good-epochs 3 --stall-seconds 1200
python3 evidence/gpu-stall/collect_signals.py --rundir run --procmatch stall_train \
    --interval 20 --out signals.jsonl
```

## 这一跑改变了什么

| 项 | 之前 | 现在 |
|---|---|---|
| `adapters/pytorch.json` | 依据规约编写，未经验证 | 已实测，且实测反过来改了判据 |
| README 第一条痛点 | ⚠️ 有记录、无实测证据 | ✅ 已实测 |
| `progress-probe` 能力评估 | 只设计了方法 | 首次真跑，产出漏报/时延数字 |
| 判定逻辑 | 三信号对等 OR | 进展 / 存活分层，GPU 信号真正生效 |

最后一行是这一跑最有价值的产出：**实测没有证实我们的设计，而是证伪了它**。
如果不真跑一次，这个漏报会一直躺在代码里 —— 而且躺在我们宣称要解决的那个场景上。
