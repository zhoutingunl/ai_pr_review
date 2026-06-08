# AI PR Review

## 项目简介

AI PR Review 是一个基于大语言模型的 Pull Request 智能评审工具。

开发者只需要输入 GitHub Pull Request 地址，系统即可自动：

* 获取代码变更
* 获取关联上下文
* 分析风险代码
* 生成 Review 建议
* 输出评审报告
* 回写 GitHub Review

帮助开发团队提高代码评审效率与质量。

---

# 产品目标

解决传统 Code Review 存在的问题：

## Review耗时

大型PR：

* 文件数多
* Diff长
* 依赖复杂

人工Review成本高。

---

## Review质量不稳定

不同Reviewer：

* 经验不同
* 关注点不同

容易遗漏问题。

---

## 缺乏全局上下文

Reviewer通常只能看到：

```text
Diff
```

无法快速理解：

* 调用链
* 依赖关系
* 历史代码

---

## 安全与性能问题遗漏

例如：

* SQL注入
* 死循环
* N+1查询
* 锁竞争

不容易发现。

---

# 产品能力

系统支持：

## PR分析

输入：

```text
https://github.com/xxx/repo/pull/123
```

自动获取：

* Diff
* Commit
* Changed Files

---

## 变更总结

自动生成：

* 修改内容概述
* 模块影响范围
* 风险等级

---

## 风险代码识别

识别：

* 安全问题
* 性能问题
* 稳定性问题
* 逻辑缺陷

---

## Review建议

生成：

* 行级评论
* 文件级评论
* 总体Review结论

---

## 自动回写PR

支持：

* Comment
* Review
* Request Changes

---

## AI修复建议

生成：

* 修复方案
* Patch
* Commit建议

---

# 技术栈

## Runtime

Python 3.11

---

## Framework

Flask

统一风格：

```python
class ReviewWebApp:

    def __init__(self, name):
        self.app_ = Flask(name)

        self.store_ = Store()

        self.github_ = GithubClient()

        self.ai_ = AIService()
```

---

## Web Server

gevent

```python
from gevent.pywsgi import WSGIServer
```

---

## Database

SQLite

数据库：

```text
data/review.db
```

---

## Frontend

Jinja2

Bootstrap 5

原生JavaScript

---

# AI架构

## AI统一入口

Hermes

统一入口：

```text
http://10.210.32.30:8787/
```

所有AI能力统一通过：

```python
class AIService:
    pass
```

禁止业务代码直接访问模型。

---

# 多模型架构

支持：

## Review模型

负责：

* Review
* 风险分析

---

## Summary模型

负责：

* PR总结
* Commit总结

---

## Fix模型

负责：

* 自动修复建议

---

统一接口：

```python
class AIService:

    def summarize(self):
        pass

    def review(self):
        pass

    def generate_fix(self):
        pass
```

---

# 系统架构

```text
GitHub PR
      |
      v

GithubClient

      |
      v

Context Builder

      |
      v

Review Engine

      |
      v

AI Service

      |
      v

Review Report

      |
      v

GitHub Review
```

---

# Context获取设计

这是系统核心能力。

---

## 一级上下文

PR Diff

获取：

```text
Changed Files

Patch

Commit
```

---

## 二级上下文

关联文件

例如：

```python
service.py
```

发生变更。

自动获取：

```python
dao.py

model.py

util.py
```

---

## 三级上下文

调用链

例如：

```python
controller

↓

service

↓

dao
```

自动构建依赖图。

---

## 四级上下文

历史Review

获取：

* 历史评论
* 历史Bug

用于降低误报。

---

# Review引擎

## Security

检测：

* SQL注入
* XSS
* SSRF
* 路径遍历
* 硬编码密码
* Token泄露

---

## Performance

检测：

* N+1查询
* 重复计算
* 大循环

---

## Reliability

检测：

* 空指针
* 异常遗漏
* 死锁风险

---

## Maintainability

检测：

* 重复代码
* 高复杂度函数

---

## Style

检测：

* 命名规范
* 注释规范

---

# 风险评分系统

风险等级：

```text
P0 阻塞

P1 高风险

P2 中风险

P3 建议优化
```

---

评分：

```python
score =

security * 0.4

+ reliability * 0.3

+ performance * 0.2

+ style * 0.1
```

---

# 误报控制

## Confidence

每条建议：

```json
{
  "confidence": 0.91
}
```

---

## 阈值过滤

默认：

```text
0.70
```

以下不展示。

---

## 多阶段验证

流程：

```text
规则引擎

↓

LLM分析

↓

一致性检查

↓

输出
```

降低误报。

---

# 行级评论

支持：

```json
{
  "file": "service.py",
  "line": 123,
  "comment": "存在空指针风险"
}
```

---

GitHub定位：

```text
file + line
```

精确评论。

---

# GitHub集成

## 支持

Pull Request

Commit

Review

Comment

Webhook

---

## 回写Review

支持：

```text
Comment

Approve

Request Changes
```

---

# CI集成

支持：

## GitHub Webhook

事件：

```text
pull_request
```

---

自动触发：

```text
PR创建

PR更新

PR重新打开
```

---

# 多语言支持

第一阶段：

* Python
* Java
* Go
* JavaScript
* TypeScript

---

第二阶段：

* Rust
* C++
* Kotlin

---

# AI自动修复

支持：

## Patch生成

输出：

```diff
- old code
+ new code
```

---

## Commit建议

输出：

```text
修复空指针问题
增加异常处理
```

---

# 数据模型

## ReviewTask

```python
class ReviewTask:

    id

    repo

    pr_number

    status

    score

    created_at
```

---

## ReviewIssue

```python
class ReviewIssue:

    id

    task_id

    level

    file

    line

    confidence

    message
```

---

## ReviewComment

```python
class ReviewComment:

    id

    task_id

    comment
```

---

# QoS Dashboard

地址：

```text
/metrics
```

---

# Review指标

分析次数

成功率

平均耗时

P95

P99

---

# AI指标

Token消耗

平均耗时

失败率

模型成功率

---

# 误报指标

Issue数量

用户采纳数

采纳率

误报率

---

# GitHub指标

回写成功率

Webhook成功率

Review成功率

---

# 用户行为埋点

统一SDK：

```javascript
track(event,payload)
```

---

## Review

review_start

review_finish

review_failed

---

## GitHub

github_fetch

github_comment

github_review

---

## AI
ai_summary

ai_review

ai_fix

---

## Dashboard

dashboard_open

dashboard_refresh

---

# 项目目录

```text
ai_pr_review/

├── app.py

├── config.py

├── db.py

├── github_client.py

├── ai_service.py

├── context_builder.py

├── review_engine.py

├── risk_detector.py

├── report_generator.py

├── webhook.py

├── dashboard.py

├── scheduler.py

├── metrics.py

├── templates/

├── static/

├── tests/

├── data/
│   └── review.db

└── design.md
```

---

# 测试要求

pytest

pytest-cov

---

覆盖率要求

Line Coverage > 85%

Function Coverage > 85%

Branch Coverage > 80%

---

必须覆盖

GithubClient

ContextBuilder

RiskDetector

ReviewEngine

AIService

Webhook

ReportGenerator

Metrics

---

# Git提交规范

GIT 仓库 地址 git@github.com:zhoutingunl/ai_pr_review.git

全部使用中文。

示例：

```text
功能: 实现PR变更拉取

功能: 增加上下文构建模块

功能: 实现风险代码识别

功能: 增加GitHub评论回写

功能: 支持Webhook自动触发

测试: 提升Review模块覆盖率

文档: 更新设计文档
```

---

# 安全要求

禁止提交：

* API_KEY
* AccessToken
* Cookie
* Hermes认证信息
* GitHub Token

所有敏感信息统一放入：

```text
.env

config.json
```

并加入：

```text
.gitignore
```

---

# Definition Of Done

仅当满足以下条件才允许标记完成：

* 所有Feature可运行
* GitHub集成完成
* Hermes接入成功
* 行级评论完成
* Webhook完成
* Dashboard完成
* 覆盖率 >85%
* 中文提交规范完成
* design.md完整

---

# 设计思路说明

本节按题目要求，说明系统在**模型选择**、**上下文获取方式**与**未来扩展方向**上的设计思路。所有要点均对应到已落地的实现（标注了关键文件/模块）。

## 一、模型选择

### AI 统一入口，业务不直连模型

所有 AI 能力统一经 `AIService`（`ai_service.py`）走 Hermes 网关（`http://10.210.32.30:8787`），业务代码**禁止直连任何模型**。这样模型切换、灰度、限流治理、计费埋点都收敛在一处，更换底层模型不影响上层流水线。

### 分角色多模型，按任务价值匹配

| 角色 | 默认模型 | 选型理由 |
|---|---|---|
| review（找 bug） | `glm-5.1` | 评审是最高价值环节，用推理能力更强的模型 |
| summary（变更总结） | `kimi-k2.5` | 总结对延迟更敏感、对推理深度要求低 |
| fix（修复建议） | `kimi-k2.5` | 生成式任务，质量/延迟平衡 |

映射写在 `config.json` 可改，不改代码即可调整策略。

### 跨 plan 故障转移，而非同 plan 换模型

`MiniMax-M3` 属 `minimax-cn` plan，`glm-5.1`/`kimi-k2.5` 属 `ark` plan。429 限流时按 fallback 链**跨 plan**切换（同 plan 内换模型往往一起被限流，无意义）。fallback 链在 config 中显式配置。

### 适配推理模型的高首-token 延迟

实测 Hermes 上推理模型首 token 延迟可达 2–5 分钟，思考阶段每 2–3 秒发空行心跳。看门狗据此以「**距上次新 token 的间隔**」判卡死：模型只要持续输出就不限总时长，仅在长时间无任何新输出或连接断流时才故障转移（`ai_service._read_stream`），避免误杀正在工作的模型。

### 多模型协同做误报控制

规则引擎候选 + LLM 评审做**一致性交叉验证**，双方都确认则提升置信度、被否决则打折，再以阈值（默认 0.70）过滤（`review_engine._consistency_check`）。

## 二、上下文获取方式

### 四级上下文是系统核心能力

| 级别 | 内容 | 获取方式 |
|---|---|---|
| L1 | 变更 Diff / Commit / Changed Files | GitHub PR API |
| L2 | 关联文件 | 解析变更文件 import，拉取其依赖的仓库内文件 |
| L3 | 调用链 | 在「变更+关联」文件集合内构建依赖图，输出调用链 |
| L4 | 历史 Review | 本 PR + 仓库近期评论，用于**降误报** |

实现于 `context_builder.py`。

### 轻量静态分析，而非重型 AST

L2/L3 采用 import/require 正则提取 + 路径匹配（覆盖 Python/Java/Go/JS/TS），不引入语言专属 AST 工具链——好处是**零额外依赖、跨语言一致、快**；代价是精度弱于 AST，作为未来升级方向（见下）。

### review 阶段按 PR 体量分级取舍上下文

全量四级上下文喂给大 PR 会让推理模型长时间空转。因此 review 阶段按变更 Diff 字符数分级（`context_builder.review_context_to_prompt`）：

| 体量 | review 看到的上下文 |
|---|---|
| 小 < 15k | 全量 L1+L2+L3+L4 |
| 中 15k–40k | L1 + L3 调用链 + L4 历史，省略最占字数的 L2 关联文件 |
| 大 ≥ 40k | L1 + L4 历史（字数小、降误报价值最高），省略 L2/L3 |

按「价值/字数比」取舍：历史评论小而值钱尽量留、关联文件全文最占字数优先砍。被省略的级别在**报告中显式标注**，不静默阉割。阈值 config 可调。summary 仍用全量上下文保证总结质量。

### 规则引擎 × LLM 双路 + 行级精确回写

规则引擎（`risk_detector.py`）只扫 Diff 出候选，LLM 吃分级上下文做判断，二者交叉验证。最终问题按 `file + line` 精确定位回写为 GitHub 行级评论。

## 三、未来扩展方向

### 分析精度
* L2/L3 由正则升级为 **tree-sitter / LSP 真 AST**，调用链与依赖图更准。
* 仓库级 **RAG**：对整库做 embedding 索引，跨文件、跨 PR 的语义检索，突破单 PR 上下文边界；增量索引降低成本。

### 评审质量与误报
* **per-file 分块评审**：大 PR 拆成每文件一次小调用（实测单文件 prompt 稳定返回），可靠性高、失败只影响单文件。
* **多评委圆桌 + 对抗式验证**：多模型/多视角投票，专门的 refuter 角色尝试反驳每条发现，进一步降误报（可对接现有 Hermes code_quality_roundtable）。
* **反馈闭环**：利用已采集的 issue 采纳率/误报率埋点做阈值自适应、few-shot 选样，乃至离线微调。

### 工程与体验
* **真实公网 Webhook**：内网经 smee.io / 隧道接入真实 GitHub 回调（当前为端点 + 签名校验 + 单测）。
* **CI 门禁**：把评分卡接入 CI，P0/P1 阻断合并。
* **结果缓存与增量**：同 head SHA 复用结果；只评审 push 的增量提交。
* **IDE 插件**：把行级评论与修复建议带进编辑器。
* 流式输出已落地（任务页实时展示模型思考/输出过程）。

---

```
```
