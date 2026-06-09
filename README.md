# AI PR Review

基于大语言模型的 Pull Request 智能评审工具。输入 GitHub PR 地址，自动完成：
拉取变更 → 构建四级上下文 → 规则引擎 + LLM 多阶段验证 → 风险评分 →
生成报告与 AI 修复建议 → 回写 GitHub 行级评论。

完整设计见 [design.md](design.md)。

DEMO视频链接 https://www.bilibili.com/video/BV1cVVE6WEac/?spm_id_from=333.1387.homepage.video_card.click&vd_source=27dd0ecc5860cffe2555156e912ee83e


## 环境要求

- **Python 3.11+**（开发与测试均基于 Python 3.11；下方命令使用 `python3.11`）。
- **一个可达的 Hermes AI 网关**——本系统所有 AI 能力都经它。默认配置指向公司
  **内网** Hermes（`http://10.210.32.30:8787`，仅 VPN 可达），**公网用户照默认会连不上**。
  自建 / 公网复刻只需部署 Hermes + 它的 WebUI 并把 `HERMES_BASE` 指向它，
  详见 **[docs/public.md](docs/public.md)**。
- 其余依赖见 `requirements.txt`。

## 快速开始

```bash
pip install -r requirements.txt

# 配置（敏感信息禁止入库）
cp .env.example .env          # 填 HERMES_BASE / GITHUB_TOKEN / WEBHOOK_SECRET
cp config.example.json config.json   # 可选：调整模型映射、端口、阈值

python3.11 app.py             # 默认 http://0.0.0.0:38001
```

> ⚠️ 内网假设：`HERMES_BASE` 默认是内网地址。公网部署请按
> [docs/public.md](docs/public.md) 配置自己的 Hermes 端点（或用根目录
> `docker-compose.yml` 模板，Hermes 仍为外部依赖）。

打开首页，粘贴 PR 地址（如 `https://github.com/owner/repo/pull/123`）即可发起评审；
`/metrics` 为 QoS Dashboard。

## 架构

```text
GitHub PR -> GithubClient -> ContextBuilder(四级上下文) -> ReviewEngine
          -> AIService(Hermes 多模型) -> 报告/修复建议 -> GitHub Review 回写
```

- **AI 统一入口**：所有模型调用经 `AIService` 走 Hermes（`http://10.210.32.30:8787`），
  业务代码禁止直连模型。
- **多模型**：review=`glm-5.1`、summary/fix=`kimi-k2.5`，429 限流自动跨 plan 故障转移
  （fallback 链含 `MiniMax-M3`），`config.json` 可调。
- **误报控制**：规则引擎 × LLM 一致性交叉验证，置信度 < 0.70 不展示。
- **评分**：`security*0.4 + reliability*0.3 + performance*0.2 + style*0.1`，
  风险等级 P0（阻塞）~ P3（建议优化）。
- **可插拔扩展**：AI 走配置驱动模型链（`config.json`）；规则引擎是
  `RuleProvider` / `RuleRegistry` 架构，第三方实现 `RuleProvider` 即可
  `RiskDetector().register(...)` 接入新规则；GitHub 端 API base 可配
  （`github_api_base`），支持 GitHub Enterprise（`https://<host>/api/v3`）。

> 模型选择、上下文获取方式与未来扩展方向的完整设计思路见
> [design.md «设计思路说明»](design.md#设计思路说明)。

## 异步与并发

提交评审（`POST /api/review` 或 Webhook）后**立即返回**，评审流水线在后台异步执行，
前端轮询 `/api/task/<id>/progress` 看实时进度。后台并发受
`scheduler_max_concurrent`（默认 2）限制，超额的提交排队等待，避免 Webhook 涌入时
无上限并发拖垮 Hermes / GitHub。当前「在跑 / 排队」数在 `/metrics` 顶部可见。

## 配置项

复制 `config.example.json` 为 `config.json` 覆盖默认值（JSON 无注释，含义见下表）。
加载优先级：内置默认 < `config.json` < 环境变量（`.env`）。

| 字段 | 默认 | 含义 |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `38001` | Web 监听地址与端口 |
| `db_path` | `data/review.db` | SQLite 路径（WAL） |
| `hermes_base` | `http://10.210.32.30:8787` | Hermes AI 网关（**私网**，公网需自配，见上方环境要求） |
| `github_api_base` | `https://api.github.com` | GitHub API base，GHE 填 `https://<host>/api/v3` |
| `models` | review=glm-5.1 / summary·fix=kimi-k2.5 | 三角色模型链，`fallback` 为限流跨 plan 转移顺序 |
| `ai_no_progress_timeout` | `420` | 距上次新 token 超此秒数无输出判卡死、换模型 |
| `ai_stall_timeout` | `90` | 连接静默（无任何字节）读超时秒数 |
| `ai_timeout` | `3600` | 单轮总时长硬上限（仅防跑飞兜底） |
| `confidence_threshold` | `0.70` | 低于此置信度的问题不展示 |
| `score_weights` | 0.4/0.3/0.2/0.1 | 安全/稳定性/性能/风格 评分权重 |
| `scheduler_max_concurrent` | `2` | 后台同时在跑的评审上限，超额排队 |
| `context_max_related_files` | `8` | 二级上下文最多拉取的关联文件数 |
| `context_max_file_bytes` | `60000` | 单文件最大读取字节数 |
| `context_history_reviews` | `20` | 四级上下文最多拉取的历史评论数 |
| `review_context_small_max` / `_medium_max` | `15000` / `40000` | review 上下文分级阈值（变更 Diff 字符数） |

## Webhook 自动触发

GitHub 仓库 Webhook 指向 `POST /webhook`（事件选 `pull_request`，
Secret 与 `.env` 的 `WEBHOOK_SECRET` 一致）。PR 创建 / 更新 / 重新打开时自动评审。

## 测试

```bash
python3.11 -m pytest          # 138+ 用例，行覆盖率 ~99%（门槛 85%），含分支覆盖
```

## 项目结构

| 文件 | 职责 |
|---|---|
| `app.py` | Flask + gevent Web 入口（`ReviewWebApp`） |
| `config.py` | 配置（默认值 < config.json < 环境变量） |
| `db.py` | SQLite 数据层（`data/review.db`，WAL） |
| `github_client.py` | PR 拉取 / 文件内容 / 历史评论 / Review 回写 |
| `ai_service.py` | Hermes 统一 AI 入口，多模型 + 限流故障转移 |
| `context_builder.py` | 四级上下文：Diff / 关联文件 / 调用链 / 历史 Review |
| `risk_detector.py` | 规则引擎（安全/性能/稳定性/可维护性/风格） |
| `review_engine.py` | 评审流水线：多阶段验证 + 评分 + 回写 |
| `report_generator.py` | Markdown 评审报告 |
| `webhook.py` | GitHub Webhook（HMAC 签名校验） |
| `scheduler.py` | 后台任务调度（同一 PR 去重） |
| `metrics.py` / `dashboard.py` | QoS 四组指标 + `/metrics` Dashboard |
| `static/track.js` | 前端埋点 SDK `track(event, payload)` |

## AI 协作说明

本项目以 **Claude Code（AI 结对编程）** 协作完成。每个 commit 带 `Co-Authored-By: Claude Opus 4.8` 作为机器可读署名；此处再做一份人类可读的分工说明，便于评审了解协作边界。**透明披露 AI 参与不等于不独立**——独立性体现在需求定义、关键工程决策与对实现的审查/纠偏上，而非由谁敲下代码。

### 作者（人类）负责 / 独立决策

- **产品规格与需求**：`design.md` 全部。
- **关键选型确认**：GitHub 凭证方式、Hermes 纯文本接入、三角色模型映射、监听端口、交付范围（完整 DoD）。
- **几项决定系统行为的工程指令**（均为作者提出、Claude 实现）：
  - 限流时**跨模型/跨 plan 切换**的策略方向（"M3 被限流就改用其他模型"）。
  - **看门狗语义**："模型只要还在持续输出就不该被超时杀掉，只有真卡死才干预"——此为作者明确拍板的设计，**并非 Claude 独立决定**；它纠正了 Claude 最初写的"绝对计时 240s"错误方案。
  - **实时流式展示**模型思考/输出过程的产品需求。
  - 指出"review 只吃一级 Diff、四级上下文被阉割"的**架构问题**，并选定"按 PR 体量分级"的方向。
- **对每一处实现的审查、验收与纠偏**（多轮 code review 式追问）。

### Claude 生成 / 负责

- 全部模块代码与**测试套件（158 用例，覆盖率 ~99%）**。
- 四级上下文的轻量静态分析实现（import 解析 / 调用链构建）、规则引擎规则集、一致性交叉验证与评分、报告生成。
- 故障转移链、看门狗、上下文分级、流式回调等的**具体代码实现**。
- 平台首-token 高延迟问题的**抓包诊断与根因分析**。

### 边界小结（诚实版）

| 能力 | 谁主导 |
|---|---|
| "做什么 / 可接受标准 / 方向纠偏" | **作者** |
| "怎么实现" 的绝大多数细节 | **Claude** |
| 上下文**分层**（四级 + 按体量分级） | 作者发现问题并定方向，Claude 设计具体档位与实现 → **联合** |
| **兜底**（fallback 链 / 看门狗） | 机制由 Claude 提出与实现，但看门狗**核心语义是作者的设计指令** → 非 Claude 独立决策 |
