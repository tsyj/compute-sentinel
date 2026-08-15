# 拟向 AgentTeams 上游提交的问题

> 全部来自本项目在 AgentTeams v1.2.2 上的**真实部署与实跑**，不是读文档推测的。
> 每条都给了最小复现步骤、实际影响和建议改法，可直接作为 issue 正文提交。
>
> 环境：AgentTeams v1.2.2（embedded 镜像 `agentteams-embedded:v1.2.2`）／
> Docker（snap）／Ubuntu 22.04 ／ runtime `qwenpaw`

---

## 1. `agt llm-preflight --help` 把真实 API Key 打印在帮助文本里

**严重程度：高（凭证泄露）**

### 复现

```
$ agt llm-preflight --help
Flags:
      --api-key string    LLM API key (default "<此处原样打印了完整的真实密钥>")
```

`--api-key` 的默认值取自当前环境里配置的真实密钥，并作为 flag 默认值**原样打印**。

### 影响

- 任何人跑一次 `--help` 就拿到了生产密钥
- 更糟的是它会进入**终端记录、CI 日志、录屏、issue 粘贴**。
  我们就是在为参赛材料排查问题时，把它连同其他输出一起贴了出来
- `--help` 是最不设防的命令，用户不会预期它输出敏感信息

### 建议

帮助文本里对密钥类 flag 一律脱敏，例如显示 `(default "sk-sp-…<from env>")`
或干脆 `(default: read from AGENTTEAMS_LLM_API_KEY)`，不显示值本身。

---

## 2. `agt create human --permission-level` 的取值范围，CLI 与 CRD 不一致

**严重程度：中（可用性）**

### 复现

```
$ agt create human --name alice --display-name "Alice" --permission-level 100
Error: create human: HTTP 500: Human.agentteams.io "alice" is invalid:
  spec.permissionLevel: Invalid value: 100:
  spec.permissionLevel in body should be less than or equal to 3
```

而 `--help` 明写：

```
      --permission-level int    Permission level (0-100)
```

### 影响

- 按帮助文本填写会直接 500，且错误只在服务端暴露
- 500 而非 400，看起来像服务故障而不是参数非法

### 建议

二选一：把 CLI 帮助改为 `(0-3)`；或放宽 CRD schema。
无论哪种，客户端都应在提交前做本地校验并返回 4xx 语义的错误。

---

## 3. `agt update team` 缺少 `--admin`，已建的 Team 无法补设管理员

**严重程度：中（可用性）**

### 复现

```
$ agt create team --help
      --admin string    Existing Human resource used as Team Admin      ← 有

$ agt update team --help
      （无 --admin）                                                     ← 没有

$ agt update team --name myteam --admin alice
Error: unknown flag: --admin
```

### 影响

Team 通常先于 Human 创建（人是后来才加进来的）。
一旦 Team 建好，就没有官方途径补设 Team Admin，只能删掉重建整个 Team。
这对已经在跑的 Team 是破坏性的。

### 建议

给 `agt update team` 补上 `--admin` / `--admin-matrix-id`，与 `create` 对齐。

---

## 4. Worker 容器不继承 Manager 的 host-share 挂载

**严重程度：中（文档缺失导致的行为意外）**

### 复现

Manager 挂载了 `AGENTTEAMS_HOST_SHARE_DIR`（例如 `/home/<user>`）到
`/root/agentteams-fs/`，但由 controller 拉起的 Worker 容器**没有**这个挂载。
Worker 的 SOUL 里若写"材料在 `/host-share/...`"，Worker 会报文件不存在。

### 影响

- 多 Agent 协作场景里"把材料放共享目录，各 Agent 自己去读"是最自然的做法，
  但它默认不工作
- 现象是 Agent 报"文件不存在"，很容易被误判为 Agent 幻觉或路径写错，
  实际是挂载没传递。我们最初就往这个方向排查了很久

### 变通

`docker cp` 手动注入到每个 Worker 容器。

### 建议

要么让 Worker 继承同一 host-share 挂载，要么在文档中显式说明
"Worker 与 Manager 的文件系统视图不同"，并给出推荐的共享方式。

---

## 5. Matrix 房间里纯文本 `@name` 不构成 mention，Worker 不会被触发

**严重程度：低（文档缺失）**

### 复现

在协作房间发送正文含 `@sentinel ...` 的 `m.room.message`，Worker 无反应。
必须在事件 content 里带上：

```json
{"msgtype":"m.text","body":"@sentinel ...",
 "m.mentions":{"user_ids":["@sentinel:<domain>"]}}
```

### 影响

从 Element UI 里用自动补全 @ 是正常的（客户端会自动填 `m.mentions`），
但**通过 API 脚本化投递任务时**极易踩坑：消息发出去了、房间里看得见、
Worker 就是不动，且没有任何错误提示。

### 建议

在"如何给 Worker 派任务"的文档里显式说明需要 `m.mentions.user_ids`，
或让 Worker 侧同时接受正文里的纯文本 @ 作为触发条件。

---

## 6. 【非框架缺陷，但值得写进部署文档】snap 版 Docker 的 `docker cp` 会静默写出空文件

**严重程度：中（数据静默损坏）**

### 现象

snap 打包的 Docker 受 confinement 限制，读不到 `/data` 等路径，
也读不到**点开头的隐藏目录**（如 `~/.stage/`）。此时：

```
$ docker cp ~/.stage/namelist.input mycontainer:/tmp/
level=error msg="Can't add file … to tar: permission denied"
level=error msg="Can't close tar writer: archive/tar: missed writing 1271 bytes"
Error response from daemon: unexpected EOF

$ docker exec mycontainer wc -c /tmp/namelist.input
0 /tmp/namelist.input        ← 文件建出来了，内容是空的
```

**目标文件已经创建但内容为空**，后续步骤会拿到一个"存在但无意义"的文件。
我们因此浪费了一轮排查：容器里的预检工具读到空 namelist，报了个完全无关的错误。

### 建议

这属于 snap confinement 而非 AgentTeams 的问题，但由于 AgentTeams 的安装
文档推荐 `docker cp` 作为向 Worker 注入文件的方式，建议在文档里提示：
**`docker cp` 后应校验目标文件大小**，尤其在 snap Docker 环境下。

---

## 提交计划

上述 1–5 条向 AgentTeams 仓库提 issue，第 1 条因涉及凭证泄露，
按安全问题流程私下报告而非公开 issue。
第 6 条作为部署注意事项，同时写入本项目的部署说明。

若第 4 条（host-share 挂载）维护者认可为需要修复的行为，我们愿意提交 PR。
