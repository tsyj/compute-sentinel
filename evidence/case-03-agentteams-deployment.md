# 证据案例 03 · AgentTeams 本地部署与凭证隔离验证

> 环境：Ubuntu，Linux 6.8.0，256 核 / 540 GB 内存，snap Docker 29.6.1
> 时间：2026-08-09
> 版本：AgentTeams v1.2.2（Dashboard v1.2.0）
> 模型服务：阿里云百炼 Token Plan（OpenAI 兼容协议），默认模型 `qwen3.7-plus`

---

## 一、部署结果

拉取镜像 6 个，起容器 3 个：

| 容器 | 端口 | 作用 |
|---|---|---|
| `agentteams-controller` | 18080 网关 / 18001 Higress 控制台 / 18088 Element Web | 控制面 + AI 网关 + Matrix 服务 |
| `agentteams-manager` | 18888 | Manager Agent（QwenPaw 运行时） |
| `agentteams-dashboard` | 13000 | 管理面板 |

四个入口全部返回 HTTP 200：

```
Element Web 网关 (:18088) → HTTP 200
Higress 控制台   (:18001) → HTTP 200
Dashboard        (:13000) → HTTP 200
Manager          (:18888) → HTTP 200
```

**注意**：所有端口都只绑定 `127.0.0.1`，外部不可直连。远程访问需要 SSH 隧道或内网组网。
这是安装器的默认行为，也是合理的默认值。

---

## 二、凭证隔离：可复现的实证

参赛手册要求说明「高风险动作的安全边界」与「密钥管理」。
AgentTeams 通过 Higress AI 网关做凭证隔离——**真实的模型 API Key 只存在于网关侧，
Worker Agent 拿到的是消费令牌（consumer token），拿不到真实凭证。**

这不是文档里的宣称，是可以当场验证的。

### 验证一：不带令牌直连网关 → 拒绝

```bash
curl -s -i http://127.0.0.1:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.7-plus","messages":[{"role":"user","content":"hi"}]}'
```

```
HTTP/1.1 401 Unauthorized
www-authenticate: Key realm=MSE Gateway
server: istio-envoy
content-length: 0
```

### 验证二：带消费令牌 → 放行

```bash
GK=$(grep '^AGENTTEAMS_MANAGER_GATEWAY_KEY=' ~/agentteams-manager.env | cut -d= -f2-)
curl -s http://127.0.0.1:18080/v1/chat/completions \
  -H "Authorization: Bearer $GK" -H "Content-Type: application/json" \
  -d '{"model":"qwen3.7-plus","messages":[{"role":"user","content":"回复四个字：链路已通"}],"max_tokens":24}'
```

```
链路已通
usage: {prompt_tokens: 16, completion_tokens: 185, total_tokens: 201,
        completion_tokens_details: {reasoning_tokens: 177}}
```

### 这证明了什么

配置文件 `~/agentteams-manager.env` 里，两类凭证是分开的：

| 变量 | 谁持有 | 性质 |
|---|---|---|
| `AGENTTEAMS_LLM_API_KEY` | **只有网关** | 真实的模型服务 API Key，可直接产生费用 |
| `AGENTTEAMS_MANAGER_GATEWAY_KEY` | Manager / Worker | 消费令牌，只能经网关访问，可单独吊销 |

**对本方案的意义**：`executor` Agent 需要对生产集群下手（重启作业、修改配置、终止进程）。
它持有的是消费令牌，不是集群 SSH 私钥和云账号 AccessKey。
即使某个 Worker 被提示注入攻破，攻击者拿到的也不是能直接刷账单或登录集群的凭证。

也就是说，「高风险动作的安全边界」这个评分维度，**一半由框架结构性地保证**
（凭证不落到 Agent 手里），另一半由我们自己的 `safe-kill` 三重判据保证
（见 [证据案例 02](case-02-kill-by-name-is-broken.md)）。这两层是独立的，任一层失效另一层仍在。

---

## 三、部署过程中踩到的三个坑（对复现者有用）

本机 Docker 是 Canonical 的 **snap 版**，与常规 docker-ce 有三处差异：

1. **`docker` 组不会自动创建。** docker-ce 装完自带，snap 版没有。
   需要先 `sudo addgroup --system docker`，再 `usermod -aG`，
   再重启 daemon 让它把 socket 属主改成 `root:docker`。
2. **daemon 不继承 shell 的代理环境变量**，且 `snap set docker proxy.*` 在本环境实测无效。
3. **Docker Hub 不可达**（`registry-1.docker.io` 直连与经代理均超时），
   必须在 `/var/snap/docker/current/config/daemon.json` 配镜像加速源。

另外，用户组成员身份在**登录时**确定，已有会话不会自动生效。
自动化脚本里可以用 `sg docker -c "..."` 取得新组身份，**无需重新登录**。

---

## 四、配置要点

本机用的是百炼 **Token Plan 套餐**，它的专属 Base URL 与安装器内置 `qwen` provider
指向的 `dashscope.aliyuncs.com` 不同，因此走 `openai-compat` 模式显式指定：

```bash
AGENTTEAMS_LLM_PROVIDER=openai-compat
AGENTTEAMS_OPENAI_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
AGENTTEAMS_DEFAULT_MODEL=qwen3.7-plus
AGENTTEAMS_NON_INTERACTIVE=1
```

已实测可用的模型：`qwen3.7-plus`、`qwen3.8-max`、`qwen3.7-max`、`qwen3.6-flash`、
`deepseek-v4-pro`、`glm-5.2`。

**已知缺口**：该套餐**不含 embedding 模型**（`qwen3.7-text-embedding` 等均返回
`Model not exist`）。`runbook-rag` 的向量库需另行解决——倾向本地部署开源 embedding 模型，
理由是无外部依赖、零成本、可复现性强，且运维知识不出本地，在数据合规上更干净。

---

## 五、本案例在参赛材料中的作用

赛道红线第 3 条：*仅提交概念说明、PPT 或营销材料，无法提供 PoC、实验、仿真、日志、
视频、平台成绩或等价可验证材料，原则上淘汰或严重扣分。*

初赛不强制提交代码，但本案例提供了等价可验证材料：
真实部署、真实容器、真实模型调用、真实的 401/200 对照实验，全部可按上述命令复现。
