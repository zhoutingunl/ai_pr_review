# AI PR Review

基于大语言模型的 Pull Request 智能评审工具。输入 GitHub PR 地址，自动完成：
拉取变更 → 构建四级上下文 → 规则引擎 + LLM 多阶段验证 → 风险评分 →
生成报告与 AI 修复建议 → 回写 GitHub 行级评论。

完整设计见 [design.md](design.md)。

DEMO视频链接 https://www.bilibili.com/video/BV1cVVE6WEac/?spm_id_from=333.1387.homepage.video_card.click&vd_source=27dd0ecc5860cffe2555156e912ee83e


## 环境要求

**Python 3.11+**（开发与测试均基于 Python 3.11；下方命令使用 `python3.11`）。
其余依赖见 `requirements.txt`。

## 快速开始

```bash
pip install -r requirements.txt

# 配置（敏感信息禁止入库）
cp .env.example .env          # 填 GITHUB_TOKEN / WEBHOOK_SECRET（缺省回落 gh auth token）
cp config.example.json config.json   # 可选：调整模型映射、端口、阈值

python3.11 app.py             # 默认 http://0.0.0.0:38001
```

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

> 模型选择、上下文获取方式与未来扩展方向的完整设计思路见
> [design.md «设计思路说明»](design.md#设计思路说明)。

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
