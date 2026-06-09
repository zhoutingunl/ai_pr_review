# 公网 / 自建复刻指南

本系统**所有 AI 能力都经一个 Hermes 网关**（`AIService` 是唯一出口，业务代码不直连模型）。
默认配置指向公司内网的 Hermes（`http://10.210.32.30:8787`，仅 VPN 可达），所以
**外部用户照搬默认会连不上**。要在公网 / 自己的环境里复刻，只需把这个网关换成你自己的。

> 关键认知：复刻的真正依赖是 **Hermes + 它的 WebUI**，不是 Docker。把 Hermes 跑起来、
> 让本应用的 `HERMES_BASE` 指向它，就能复刻。

---

## 一、部署 Hermes + WebUI（唯一外部依赖）

Hermes 是一个带工具、SSE 流式的 agent 平台；本系统把每次评审做成一次 Hermes 会话。

1. 部署 Hermes 及其 WebUI（参考实现：<https://github.com/nesquena/hermes-webui>）。
2. 在 Hermes 侧配置至少一个**有密钥可用**的模型 plan（本系统默认用
   `glm-5.1` / `kimi-k2.5` / `MiniMax-M3`；你可在 `config.json` 的 `models` 改成你那台
   实际可用的模型 id）。
3. 记下它的访问地址，例如 `https://hermes.example.com`。
4. 自检：浏览器打开该地址应能看到 Hermes WebUI；`GET /api/models` 应返回当前 active 模型。

## 二、配置本应用指向你的 Hermes

```bash
cp .env.example .env
# 编辑 .env：
#   HERMES_BASE=https://hermes.example.com     # 你的 Hermes 地址（HTTPS 推荐）
#   GITHUB_TOKEN=ghp_xxx                        # 或留空回落 gh auth token
#   WEBHOOK_SECRET=xxx                          # 启用 Webhook 时填

cp config.example.json config.json
# 按需改 models / 端口 / 阈值（字段含义见 README「配置项」表）
```

`HERMES_BASE` 留空会回落到内置默认（内网地址）——公网务必显式填自己的。

## 三、启动

### 方式 A：直接跑（推荐，无需 Docker）

```bash
pip install -r requirements.txt
python3.11 app.py          # 默认 http://0.0.0.0:38001
```

### 方式 B：Docker（可选）

仓库根有 `Dockerfile` 与 `docker-compose.yml`，仅打包**本应用**（Hermes 仍是外部依赖，
通过 `HERMES_BASE` 指向）。

```bash
# .env 里已写好 HERMES_BASE / GITHUB_TOKEN 后：
docker compose up -d        # 映射 38001 端口，挂载 ./data 持久化 SQLite
```

> 若所在网络拉不到基础镜像，用方式 A 即可，二者等价。

## 四、验证复刻成功

```bash
curl -s -X POST http://127.0.0.1:38001/api/review \
  -H 'Content-Type: application/json' \
  -d '{"pr_url":"https://github.com/octocat/Hello-World/pull/1","write_back":false}'
# 打开 http://127.0.0.1:38001/task/<id> 看实时流式评审过程
```

能看到模型流式输出、最终生成报告，即复刻成功。

## 常见问题

- **502 / 连不上 AI**：`HERMES_BASE` 仍是默认内网地址，或你的 Hermes 没起。
- **AI 一直“思考中”不出字**：Hermes 上对应模型首 token 延迟高（推理模型常见），
  本系统看门狗只在「长时间无任何新输出」时才换模型，耐心等或在 `config.json` 调
  `ai_no_progress_timeout`。
- **GitHub 企业版（GHE）**：`config.json` 设 `github_api_base: https://<你的GHE>/api/v3`。
