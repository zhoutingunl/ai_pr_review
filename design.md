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

```
```
