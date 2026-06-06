"""Review 引擎 —— 串联整条评审流水线。

流程（多阶段验证）：
    GithubClient 拉取 -> ContextBuilder 四级上下文
        -> 规则引擎 (RiskDetector)
        -> LLM 分析 (AIService.review / summarize)
        -> 一致性检查（规则 x LLM 交叉验证，调整置信度）
        -> 阈值过滤（confidence < 0.70 不展示）
        -> 风险评分（P0~P3，security*0.4 + reliability*0.3
                     + performance*0.2 + style*0.1）
        -> AI 修复建议（P0/P1）
        -> 报告生成 + GitHub 回写
"""
from __future__ import annotations

import json

from ai_service import AIError, extract_json
from config import CONFIG
from context_builder import context_to_prompt
from github_client import parse_pr_url
from report_generator import ReportGenerator
from risk_detector import RiskDetector

_LEVELS = ("P0", "P1", "P2", "P3")
_CATEGORIES = ("security", "performance", "reliability", "maintainability", "style")

# 各风险等级对类别得分的扣分
_LEVEL_PENALTY = {"P0": 40, "P1": 20, "P2": 10, "P3": 4}

_SUMMARY_PROMPT = """你是资深代码评审专家。请阅读以下 Pull Request 上下文，输出变更总结。

严格只输出 JSON（不要任何解释文字），格式：
{{
  "overview": "修改内容概述（200字内）",
  "modules": ["受影响模块1", "受影响模块2"],
  "risk_level": "P0|P1|P2|P3"
}}

{context}
"""

_REVIEW_PROMPT = """你是资深代码评审专家，请基于以下 PR 上下文做严格的代码评审。

关注维度：security(SQL注入/XSS/SSRF/路径遍历/硬编码密钥/Token泄露)、
performance(N+1查询/重复计算/大循环)、reliability(空指针/异常遗漏/死锁)、
maintainability(重复代码/高复杂度)、style(命名/注释规范)。

规则引擎已扫出以下候选问题（可能有误报，请逐条判断并纳入或否决）：
{rule_findings}

要求：
1. 只评审本次 Diff 新增/修改的代码，行号必须是变更后文件的行号。
2. confidence 为 0~1 的小数，表示你判断该问题为真的把握。
3. 对规则引擎候选：确认为真则收入 issues（保持/修正行号），误报则放入 rejected。
4. 严格只输出 JSON（不要任何解释文字），格式：
{{
  "issues": [
    {{"file": "路径", "line": 123, "category": "security",
      "level": "P0|P1|P2|P3", "confidence": 0.9,
      "message": "问题描述", "suggestion": "修改建议"}}
  ],
  "rejected": [
    {{"file": "路径", "line": 10, "rule": "规则ID", "reason": "误报原因"}}
  ],
  "conclusion": "总体评审结论（300字内）"
}}

{context}
"""

_FIX_PROMPT = """你是资深工程师。针对以下代码问题，给出修复方案。

文件: {file}
行号: {line}
问题: {message}
相关代码上下文:
```
{snippet}
```

严格只输出 JSON（不要任何解释文字），格式：
{{
  "plan": "修复方案说明（100字内）",
  "patch": "unified diff 形式的修复补丁（- 旧行 + 新行）",
  "commit_message": "一行中文 commit 信息"
}}
"""


class ReviewEngine:

    def __init__(self, store, github_client, context_builder, ai_service):
        self.store_ = store
        self.github_ = github_client
        self.context_ = context_builder
        self.ai_ = ai_service
        self.detector_ = RiskDetector()
        self.reporter_ = ReportGenerator()
        self.threshold_ = CONFIG.confidence_threshold
        self.weights_ = CONFIG.score_weights

    # ---------- 对外入口 ----------

    def run(self, pr_url: str, write_back: bool = True,
            max_fixes: int = 3) -> dict:
        """执行一次完整评审。返回 {task_id, status, score, ...}。"""
        owner, repo, number = parse_pr_url(pr_url)
        task_id = self.store_.create_task(f"{owner}/{repo}", number, pr_url)
        self.store_.record_event("review_start", {"task_id": task_id,
                                                  "pr_url": pr_url})
        self.store_.update_task(task_id, status="running")
        try:
            result = self._run_pipeline(task_id, owner, repo, number,
                                        write_back, max_fixes)
            self.store_.record_event("review_finish", {"task_id": task_id,
                                                       "score": result["score"]})
            return result
        except Exception as e:  # noqa: BLE001 - 失败统一落库
            self.store_.finish_task(task_id, "failed", error=str(e)[:1000])
            self.store_.record_event("review_failed", {"task_id": task_id,
                                                       "error": str(e)[:300]})
            raise

    # ---------- 流水线 ----------

    def _run_pipeline(self, task_id: int, owner: str, repo: str, number: int,
                      write_back: bool, max_fixes: int) -> dict:
        # 上下文（一~四级）
        self.store_.record_event("github_fetch", {"task_id": task_id})
        ctx = self.context_.build(owner, repo, number, task_id=task_id)
        prompt_ctx = context_to_prompt(ctx)

        # 规则引擎
        rule_findings = self.detector_.detect(ctx["files"])

        # LLM 总结
        self.store_.record_event("ai_summary", {"task_id": task_id})
        summary = self._ai_summary(prompt_ctx, task_id)

        # LLM 评审
        self.store_.record_event("ai_review", {"task_id": task_id})
        llm_result = self._ai_review(prompt_ctx, rule_findings, task_id)

        # 一致性检查 + 阈值过滤
        issues = self._consistency_check(rule_findings, llm_result)
        issues = [i for i in issues if i["confidence"] >= self.threshold_]

        # 评分
        score, risk_level, category_scores = self._score(issues)

        # 落库 issues
        issue_ids = []
        for issue in issues:
            iid = self.store_.add_issue(
                task_id, issue["level"], issue["category"], issue["file"],
                issue.get("line"), issue["confidence"], issue["message"],
                suggestion=issue.get("suggestion"))
            issue_ids.append(iid)
            issue["id"] = iid

        # AI 修复建议（P0/P1 取前 N 条）
        fixes = self._generate_fixes(ctx, issues, task_id, max_fixes)

        # 报告
        report = self.reporter_.generate(ctx, summary, issues, fixes, score,
                                         risk_level, category_scores)
        self.store_.add_comment(task_id, report)
        self.store_.finish_task(task_id, "success", score=score,
                                risk_level=risk_level,
                                summary=json.dumps(summary, ensure_ascii=False))

        # 回写 GitHub
        review_result = None
        if write_back:
            review_result = self._write_back(owner, repo, number, task_id,
                                             issues, report)

        return {"task_id": task_id, "status": "success", "score": score,
                "risk_level": risk_level, "issues": issues, "fixes": fixes,
                "summary": summary, "report": report,
                "review": review_result}

    # ---------- AI 调用 ----------

    def _ai_summary(self, prompt_ctx: str, task_id: int) -> dict:
        text = self.ai_.summarize(_SUMMARY_PROMPT.format(context=prompt_ctx),
                                  task_id=task_id)
        try:
            data = extract_json(text)
        except AIError:
            data = {"overview": text[:500], "modules": [], "risk_level": "P3"}
        if not isinstance(data, dict):
            data = {"overview": str(data)[:500], "modules": [], "risk_level": "P3"}
        data.setdefault("overview", "")
        data.setdefault("modules", [])
        data.setdefault("risk_level", "P3")
        return data

    def _ai_review(self, prompt_ctx: str, rule_findings: list[dict],
                   task_id: int) -> dict:
        findings_text = json.dumps(
            [{k: f[k] for k in ("rule", "category", "level", "file", "line",
                                "confidence", "message")}
             for f in rule_findings], ensure_ascii=False, indent=1) or "[]"
        text = self.ai_.review(
            _REVIEW_PROMPT.format(rule_findings=findings_text,
                                  context=prompt_ctx), task_id=task_id)
        try:
            data = extract_json(text)
        except AIError:
            data = {"issues": [], "rejected": [], "conclusion": text[:500]}
        if not isinstance(data, dict):
            data = {"issues": [], "rejected": [], "conclusion": ""}
        data.setdefault("issues", [])
        data.setdefault("rejected", [])
        data.setdefault("conclusion", "")
        return data

    def _generate_fixes(self, ctx: dict, issues: list[dict], task_id: int,
                        max_fixes: int) -> list[dict]:
        fixes = []
        targets = [i for i in issues if i["level"] in ("P0", "P1")][:max_fixes]
        for issue in targets:
            self.store_.record_event("ai_fix", {"task_id": task_id,
                                                "issue_id": issue.get("id")})
            snippet = self._snippet(ctx, issue["file"], issue.get("line"))
            try:
                text = self.ai_.generate_fix(
                    _FIX_PROMPT.format(file=issue["file"],
                                       line=issue.get("line", "?"),
                                       message=issue["message"],
                                       snippet=snippet),
                    task_id=task_id)
                data = extract_json(text)
            except AIError:
                continue
            if not isinstance(data, dict):
                continue
            fix = {"issue_id": issue.get("id"), "file": issue["file"],
                   "line": issue.get("line"),
                   "plan": str(data.get("plan", "")),
                   "patch": str(data.get("patch", "")),
                   "commit_message": str(data.get("commit_message", ""))}
            fixes.append(fix)
            if issue.get("id"):
                self.store_.set_issue_fix(issue["id"], fix["plan"], fix["patch"])
        return fixes

    @staticmethod
    def _snippet(ctx: dict, file: str, line: int | None, span: int = 10) -> str:
        content = ctx["changed_contents"].get(file, "")
        if not content or not line:
            return content[:1500]
        lines = content.splitlines()
        lo, hi = max(0, line - span - 1), min(len(lines), line + span)
        return "\n".join(f"{n + 1}: {lines[n]}" for n in range(lo, hi))

    # ---------- 一致性检查 ----------

    def _consistency_check(self, rule_findings: list[dict],
                           llm_result: dict) -> list[dict]:
        """规则引擎 x LLM 交叉验证：
            两者一致     -> 置信度提升
            仅 LLM 提出  -> 保留 LLM 置信度
            被 LLM 否决  -> 置信度打折
            仅规则命中   -> 轻度打折（LLM 未确认）
        """
        issues: list[dict] = []
        rejected = {(r.get("file"), r.get("rule"))
                    for r in llm_result.get("rejected", [])
                    if isinstance(r, dict)}

        def near(a: dict, b: dict) -> bool:
            if a.get("file") != b.get("file"):
                return False
            la, lb = a.get("line"), b.get("line")
            if la is None or lb is None:
                return a.get("category") == b.get("category")
            return abs(int(la) - int(lb)) <= 3

        llm_issues = [i for i in llm_result.get("issues", [])
                      if isinstance(i, dict) and i.get("file")]
        for issue in llm_issues:
            normalized = self._normalize(issue)
            if any(near(normalized, rf) for rf in rule_findings):
                normalized["confidence"] = min(
                    1.0, normalized["confidence"] + 0.1)  # 双重确认
                normalized["verified"] = "rule+llm"
            else:
                normalized["verified"] = "llm"
            issues.append(normalized)

        for rf in rule_findings:
            if any(near(rf, i) for i in issues):
                continue  # 已被 LLM 收入
            confidence = rf["confidence"]
            if (rf.get("file"), rf.get("rule")) in rejected:
                confidence *= 0.5  # LLM 判定误报
            else:
                confidence *= 0.85  # LLM 未确认
            issues.append({
                "file": rf["file"], "line": rf.get("line"),
                "category": rf["category"], "level": rf["level"],
                "confidence": round(confidence, 2),
                "message": rf["message"],
                "suggestion": "", "verified": "rule",
            })
        return issues

    @staticmethod
    def _normalize(issue: dict) -> dict:
        level = str(issue.get("level", "P3")).upper()
        category = str(issue.get("category", "style")).lower()
        try:
            confidence = float(issue.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        line = issue.get("line")
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        return {
            "file": str(issue.get("file", "")),
            "line": line,
            "category": category if category in _CATEGORIES else "style",
            "level": level if level in _LEVELS else "P3",
            "confidence": max(0.0, min(1.0, confidence)),
            "message": str(issue.get("message", ""))[:1000],
            "suggestion": str(issue.get("suggestion", ""))[:1000],
        }

    # ---------- 评分 ----------

    def _score(self, issues: list[dict]) -> tuple[float, str, dict]:
        """类别得分 = 100 - sum(扣分)；总分按 design.md 权重加权。

        maintainability 无独立权重，并入 style 维度计分。
        """
        category_scores = {c: 100.0 for c in
                           ("security", "reliability", "performance", "style")}
        for issue in issues:
            cat = issue["category"]
            if cat == "maintainability":
                cat = "style"
            penalty = _LEVEL_PENALTY.get(issue["level"], 4)
            category_scores[cat] = max(0.0, category_scores[cat] - penalty)

        score = round(
            category_scores["security"] * self.weights_["security"]
            + category_scores["reliability"] * self.weights_["reliability"]
            + category_scores["performance"] * self.weights_["performance"]
            + category_scores["style"] * self.weights_["style"], 1)

        levels = {i["level"] for i in issues}
        if "P0" in levels:
            risk = "P0"
        elif "P1" in levels:
            risk = "P1"
        elif "P2" in levels:
            risk = "P2"
        else:
            risk = "P3"
        return score, risk, category_scores

    # ---------- 回写 ----------

    def _write_back(self, owner: str, repo: str, number: int, task_id: int,
                    issues: list[dict], report: str) -> dict:
        """按风险等级选择 Review 动作并回写行级评论。"""
        levels = {i["level"] for i in issues}
        if "P0" in levels or "P1" in levels:
            event = "REQUEST_CHANGES"
        elif issues:
            event = "COMMENT"
        else:
            event = "APPROVE"

        comments = [
            {"path": i["file"], "line": i["line"],
             "body": f"**[{i['level']}][{i['category']}]** {i['message']}"
                     + (f"\n\n建议: {i['suggestion']}" if i.get("suggestion") else "")
                     + f"\n\n置信度: {i['confidence']:.2f} · 来源: {i.get('verified', 'ai')}"}
            for i in issues if i.get("line") and i.get("file")
        ]
        self.store_.record_event("github_review", {"task_id": task_id,
                                                   "event": event,
                                                   "comments": len(comments)})
        try:
            result = self.github_.create_review(
                owner, repo, number, report, event=event,
                comments=comments, task_id=task_id)
            return {"event": event, "comments": len(comments),
                    "review_id": result.get("id")}
        except Exception:
            # 行级评论可能因行号不在 diff 中被整体拒绝：降级为整体评论
            self.store_.record_event("github_comment", {"task_id": task_id,
                                                        "fallback": True})
            result = self.github_.create_issue_comment(
                owner, repo, number, report, task_id=task_id)
            return {"event": "COMMENT_FALLBACK", "comments": 0,
                    "review_id": result.get("id")}
