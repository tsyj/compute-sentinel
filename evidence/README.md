# 运行证据

真实故障日志与运行记录，用于支撑方案的可验证性。

## ⚠️ 证据覆盖说明（务必先读）

**本项目的实测证据集中在一类 workload 上，不覆盖 README 中列举的全部适用场景。**
这一节把边界写在明面上，避免读者高估证据强度。

| README 中声称的适用场景 | 证据状态 |
|---|---|
| 气象海洋耦合（WRF / ROMS / SWAN） | ✅ **有完整实测**：case-01/02、measure-01/03/04 |
| 大模型预训练 / 微调 | ⚠️ **无实测**。`adapters/pytorch.json` 依据团队规约中记录的真实事故编写（进程 RUNNING、CPU 100%、GPU 0%、永不输出进度行），但**本项目未采集到该事故的原始产物**，适配器未经验证 |
| CAE / EDA 仿真 | ❌ **无证据**。仅为结构相似性推断 |
| 生信流程与大规模数据处理 | ❌ **无证据**。仅为结构相似性推断 |
| 数据下载任务 | ⚠️ **无实测**。`adapters/download.json` 依据团队规约编写 |

### 这意味着什么

- **"跨 workload 通用"目前是设计主张，不是实测结论。**
  可迁移性的依据是：判定逻辑与领域知识已分离（见 `adapters/*.json`），换场景只改配置文件。
  这个**结构**是可验证的（`adapter=wrf.json` 会出现在每次判定的返回里），
  但"换到训练场景后误报率如何"**没有数据**。
- README 开篇引用的 GPU 0% 卡死事故是**真实发生过的**，
  但它发生在本项目立项之前，当时未保留可发布的原始产物。
  该事故是 `adapters/pytorch.json` 中 `gpu_signal` 配置的来源，**不能作为本项目的实测证据引用**。

### 补齐路径

1. 在一次真实训练任务上采集三信号快照序列，验证 `pytorch` 适配器
2. 构造 GPU util 持续为 0 而 CPU 满载的复现场景，验证 `gpu_signal` 判据
3. 两者完成前，对外表述统一用"**设计上覆盖**"而非"**已验证覆盖**"

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
| `measure-01-config-precheck.md` | 工具实测 | 四个配置状态，真实配置零误报 |
| `measure-02-agent-refuses-to-guess.md` | 行为验证 | 证据不可达时 Agent 拒绝编造 |
| `measure-03-agents-advanced-an-open-question.md` | 闭环实测 | 三 Agent 协作推进了一个真实悬案 |
| `measure-04-human-review-caught-a-wrong-mechanism.md` | 复核验证 | 人工复核拦下 Agent 的错误机制；WRF 死代码 |
| `measure-05-observability.md` | 可观测 | Trace/Log/Metrics 三类；采出无沙箱执行问题 |
| `mcp-server-test.txt` | 接口验证 | MCP 握手、工具调用、安全边界拦截 |
