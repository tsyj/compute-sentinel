# 运行证据

真实故障日志与运行记录，用于支撑方案的可验证性。

## ⚠️ 证据覆盖说明（务必先读）

**本项目的实测证据集中在一类 workload 上，不覆盖 README 中列举的全部适用场景。**
这一节把边界写在明面上，避免读者高估证据强度。

| README 中声称的适用场景 | 证据状态 |
|---|---|
| 气象海洋耦合（WRF / ROMS / SWAN） | ✅ **有完整实测**：case-01/02、measure-01/03/04 |
| 大模型预训练 / 微调 | ✅ **有实测**：measure-06/07。在 RTX 4090 上构造了同构的退化路径卡死（进程 RUNNING、CPU ~100%、GPU 0%、日志静默、无新产物），采集 65+ 个信号快照并回放判定。**实测证伪了原有判据**：旧逻辑全程漏报，修复后 909 秒发现。局限见 measure-06 的「本次实测的局限」一节 |
| CAE / EDA 仿真 | ❌ **无证据**。仅为结构相似性推断 |
| 生信流程与大规模数据处理 | ❌ **无证据**。仅为结构相似性推断 |
| 数据下载任务 | ✅ **有实测**（误报侧）：18 GB / 4.29 小时成功下载，全阈值组合误报 0 次，见 measure-08 |

### 这意味着什么

- **"跨 workload 通用"已有两类 workload 的实测，但仍不覆盖全部声称场景。**
  已实测：气象海洋耦合（WRF/ROMS/SWAN）、GPU 训练（PyTorch）。
  CAE/EDA、生信、下载任务仍无证据。
  可迁移性的依据是：判定逻辑与领域知识已分离（见 `adapters/*.json`），换场景只改配置文件。
  这个**结构**是可验证的（`adapter=wrf.json` 会出现在每次判定的返回里），
  但"换到训练场景后误报率如何"**没有数据**。
- README 开篇引用的那次 GPU 0% 卡死事故**发生在本项目立项之前**，未保留可发布的原始产物，
  仍然**不作为证据引用**。measure-06/07 是本项目自己在 4090 上重新构造并采集的复现，
  与那次事故是同构而非同一件事 —— 它验证的是**判据与阈值**，不是"这类事故有多常见"。

### 补齐路径

1. ~~在训练任务上采集信号快照序列，验证 `pytorch` 适配器~~ ✅ measure-06（2026-08-15）
2. ~~构造 GPU util 持续为 0 而 CPU 满载的场景，验证 `gpu_signal` 判据~~ ✅ measure-06/07
3. ~~误报率~~ ✅ measure-08：COAWST 33.79h + 下载 4.29h，当前阈值下误报 0 次
4. 仍未做：多卡 / 分布式训练；**GPU 训练场景的误报率**（现有误报样本都是 CPU 型 workload）
5. CAE/EDA、生信：仍无证据，对外统一表述为"**设计上覆盖**"

---

## 目录约定

- 只提交**脱敏后**的样例（去掉主机名、路径中的个人信息、其他用户的作业信息）
- 原始日志放 `raw/`，已在 `.gitignore` 中排除，不入库
- 每份证据标注来源事故、时间、以及它证明了什么

## 索引

| 文件 | 类型 | 证明了什么 |
|---|---|---|
| `case-01-wrf-restart-alarm.md` | 事故取证 | 一夜三次失败；问题在反馈时延而非诊断能力 |
| `case-02-kill-by-name-is-broken.md` | 结构性缺陷 | `ps` comm 截断导致同名 binary 无法区分，可当场复现 |
| `case-03-agentteams-deployment.md` | 部署验证 | 凭证隔离的 401/200 对照实验 |
| `case-04-quota-exhausted.md` | 真实故障 | 配额耗尽：容器全 Up、编排全 Running、日志无错误栈，但系统一件事做不成——发生在我们自己的 Agent 系统上 |
| `measure-01-config-precheck.md` | 工具实测 | 四个配置状态，真实配置零误报 |
| `measure-02-agent-refuses-to-guess.md` | 行为验证 | 证据不可达时 Agent 拒绝编造 |
| `measure-03-agents-advanced-an-open-question.md` | 闭环实测 | 三 Agent 协作推进了一个真实悬案 |
| `measure-04-human-review-caught-a-wrong-mechanism.md` | 复核验证 | 人工复核拦下 Agent 的错误机制；WRF 死代码 |
| `measure-05-observability.md` | 可观测 | Trace/Log/Metrics 三类；采出无沙箱执行问题 |
| `measure-06-gpu-stall.md` | 实测证伪 | 4090 上真造 GPU 退化路径卡死；**旧判定逻辑全程漏报**，修复后 909s 发现 |
| `measure-07-sampling-rate.md` | 对照实验 | 加密采样后 `gpu_ever_busy` 可观测，快速通道生效；采样周期必须细于最短工作相位 |
| `measure-08-false-positive-and-perf.md` | 误报与性能 | **误报 0 次 / 38.1 小时真实成功运行**；ps 扇出优化后 100 作业巡检 11s→0.19s |
| `gpu-stall/` | 原始数据 | 信号快照序列、训练日志、回放输出、复现脚本 |
| `perf/` | 原始数据 | 误报-阈值二维扫描输出、性能 benchmark 输出 |
| `mcp-server-test.txt` | 接口验证 | MCP 握手、工具调用、安全边界拦截 |
