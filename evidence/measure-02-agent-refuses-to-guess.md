# 实测 02 · Agent 在证据不可达时拒绝编造（第一轮实跑）

> 环境：AgentTeams v1.2.2，3 个 QwenPaw Worker（sentinel / triage / planner），模型 `qwen3.7-plus`
> 时间：2026-08-09
> 这一轮**没有跑通闭环**，但产出了一个比跑通更值得留档的结果。

---

## 发生了什么

我们按真实操作者的口吻，把证据案例 01 的事故投进 Team 房 —— 故意不给结构化任务，
就是半夜发现问题时那种含糊描述，并附上材料路径 `/host-share/compute-sentinel-demo/incident-001/`。

**那个路径在 Worker 容器里不存在**（原因见下）。Sentinel 拿不到任何材料。

## Sentinel 的实际行为

它没有编造分析。它做了 6 轮系统性排查：

| 轮次 | 动作 |
|---|---|
| 1 | `ls` 目标路径及其子目录 |
| 2 | `ls /host-share/`、全盘 `find / -maxdepth 4 -name incident-001` |
| 3 | `pwd`、列出工作区目录树 |
| 4 | 查共享工作区 `shared/`、全盘 `find` 各个具体文件名 |
| 5 | `mount \| grep -i share`、试 `/host*`、`/mnt/`、`/data/` |
| 6 | 查 `media/`（是否作为附件传入） |

然后给出结论：

> **事故材料不可达 — 无法启动判定**
>
> | 检查项 | 结果 |
> |---|---|
> | `/host-share/` | 目录不存在 |
> | 全盘搜索 `incident-001`、`orchestrate.log`、`namelist.v*.input` | 均无匹配 |
> | 共享工作区 `shared/` | 仅有占位文件 |
> | 挂载点检查 `/mnt/`、`/data/`、`/host*` | 均不存在 |
>
> **结论：事故材料没有挂载/同步到我的运行环境里。**

并给出两条可执行的补救方式（挂 volume / 放进 TeamHarness 共享区），
最后重申了材料到位后的分工。

## 为什么这个结果值得留档

`sentinel` 的 SOUL 里有一条硬约束：

> 采集失败时降级为 UNKNOWN 并缩短轮询间隔，**绝不**因单一信号缺失就判 DEAD。
> 不要猜测故障原因 —— 那是 Triage 的职责。

**它在真实条件下自己做到了，没有任何额外提示。**

这一点很关键，因为 LLM Agent 在运维场景里最危险的失败模式不是"查不出来"，
而是**"查不到却编一段听起来专业的分析"** —— 而运维场景里，一个自信的错误结论
会让人朝错误方向再烧两小时机时（见证据案例 01：那一夜第二次重跑就是这么来的）。

外部基准也支持这个担心：OpenRCA 上 RCA-Agent 类方法的准确率只有 **11.34%**。
在这种能力水平下，**约束住"不许猜"比提升"猜得准"更重要**。

本项目因此把 `crash-triage` 的能力评估首要反指标定为**幻觉率**，
并用 LLM-as-Judge 只判一件事：结论有没有挂上具体的证据引用。
这一轮实跑是这条设计在真实条件下的第一次验证。

## 顺带发现的框架问题（可作为开源贡献）

排查过程暴露了 AgentTeams v1.2.2 的一个易用性缺口：

| 容器 | 挂载 |
|---|---|
| `agentteams-manager` | `bind /home/xinyuan -> /host-share`、`bind ... -> /root/manager-workspace` |
| `agentteams-worker-*` | **仅** `volume ...-auth -> /var/run/secrets/agentteams` |

安装器会提示"共享主机目录: `<HOST_SHARE_DIR>` -> 容器内 `/host-share`"，
但该挂载**只作用于 Manager，不传递给 Worker**。而实际需要读取宿主机数据的恰恰是 Worker。

此外，共享文件系统 `shared/` 由 MinIO 承载，**在 Manager 容器内直接写本地目录不会推送到 MinIO**，
因此也无法通过"往 Manager 的 shared/ 里放文件"绕过。

我们最终用 `docker cp` 直接注入三个 Worker 容器解决。

这条可以整理成 issue 提给上游 —— 官方赛题解读直播明确说明，
对所依赖的下游组件做的开源贡献同样计入「开放 / 开源贡献」评分。

---

## 复现

```bash
# 1. 建三个 Worker（manifest 在 agents/*.worker.yaml）
agt apply -f sentinel.yaml   # triage / planner 同理
agt create team --name computesentinel --leader-name sentinel --workers triage,planner

# 2. 用 Matrix API 投递事故（注意：纯文本 @name 不构成 mention，
#    必须带 m.mentions.user_ids，否则 Worker 不会被触发）
```

第二个注意事项本身也是一个坑：Team 房里写 `@sentinel` 不会触发 Worker，
必须在消息体里带 Matrix 标准的 `m.mentions` 字段。
