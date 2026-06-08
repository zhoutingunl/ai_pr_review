# AI PR Review

基于大语言模型的 Pull Request 智能评审工具。输入 GitHub PR 地址，自动完成：
拉取变更 → 构建四级上下文 → 规则引擎 + LLM 多阶段验证 → 风险评分 →
生成报告与 AI 修复建议 → 回写 GitHub 行级评论。

完整设计见 [design.md](design.md)。

DEMO视频链接 https://www.bilibili.com/video/BV1cVVE6WEac/?spm_id_from=333.1387.homepage.video_card.click&vd_source=27dd0ecc5860cffe2555156e912ee83e


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
