"""review_engine.py 测试。"""
import json
import pytest
from unittest.mock import MagicMock

from ai_service import AIError
from review_engine import ReviewEngine


def make_ctx():
    return {
        "pr": {"title": "T", "body": "", "author": "dev", "base": "main",
               "head": "abc12345", "additions": 3, "deletions": 1,
               "changed_files": 1},
        "files": [{"filename": "a.py", "status": "modified",
                   "patch": "@@ -1 +1,2 @@\n+except:\n+x = 1"}],
        "commits": [{"sha": "abcdef12", "message": "提交"}],
        "changed_contents": {"a.py": "except:\nx = 1\ny = 2\n"},
        "related_files": {}, "call_chains": [], "history_comments": [],
    }


def make_engine(store, ai_review_json=None, ai_summary_json=None,
                ai_fix_json=None):
    gh, cb, ai = MagicMock(), MagicMock(), MagicMock()
    cb.build.return_value = make_ctx()
    ai.summarize.return_value = json.dumps(
        ai_summary_json if ai_summary_json is not None
        else {"overview": "概述", "modules": ["m"], "risk_level": "P2"})
    ai.review.return_value = json.dumps(
        ai_review_json if ai_review_json is not None
        else {"issues": [], "rejected": [], "conclusion": "结论"})
    ai.generate_fix.return_value = json.dumps(
        ai_fix_json if ai_fix_json is not None
        else {"plan": "方案", "patch": "- a\n+ b", "commit_message": "修复"})
    gh.create_review.return_value = {"id": 11}
    gh.create_issue_comment.return_value = {"id": 22}
    return ReviewEngine(store, gh, cb, ai), gh, cb, ai


# ---------- 整体流水线 ----------

def test_run_success_with_writeback(store):
    engine, gh, _, _ = make_engine(store, ai_review_json={
        "issues": [{"file": "a.py", "line": 1, "category": "reliability",
                    "level": "P1", "confidence": 0.9,
                    "message": "吞异常", "suggestion": "改具体异常"}],
        "rejected": [], "conclusion": "有问题",
    })
    result = engine.run("https://github.com/o/r/pull/5")
    assert result["status"] == "success"
    assert result["risk_level"] == "P1"
    task = store.get_task(result["task_id"])
    assert task["status"] == "success" and task["score"] is not None
    issues = store.list_issues(result["task_id"])
    assert issues and issues[0]["category"] == "reliability"
    # P1 -> REQUEST_CHANGES
    assert gh.create_review.call_args.kwargs["event"] == "REQUEST_CHANGES"
    assert result["review"]["event"] == "REQUEST_CHANGES"
    # 行级评论带等级与置信度
    comments = gh.create_review.call_args.kwargs["comments"]
    assert comments and "[P1]" in comments[0]["body"]
    # 报告落库
    assert store.list_comments(result["task_id"])
    # 修复建议生成
    assert result["fixes"] and result["fixes"][0]["plan"] == "方案"
    # 埋点
    for event in ("review_start", "review_finish", "ai_summary",
                  "ai_review", "ai_fix", "github_review"):
        assert store.count_events(event) == 1, event


def test_run_tiers_review_context_for_large_pr(store):
    """大 PR：review 上下文降级、记录 trim 事件、报告标注省略。"""
    engine, gh, cb, ai = make_engine(store)
    ctx = make_ctx()
    # 撑大 diff 超过 medium 阈值，并带上二/三/四级上下文
    ctx["files"][0]["patch"] = "@@ -1 +1 @@\n+" + "x = 1\n" * 9000
    ctx["related_files"] = {"dep.py": "def q(): pass"}
    ctx["call_chains"] = ["a.py -> dep.py"]
    ctx["history_comments"] = [{"scope": "repo_recent", "path": "a.py",
                                "body": "历史评论"}]
    cb.build.return_value = ctx

    captured = {}

    def spy_review(prompt, task_id=None, on_token=None):
        captured["prompt"] = prompt
        import json as _json
        return _json.dumps({"issues": [], "rejected": [], "conclusion": "c"})

    ai.review.side_effect = spy_review
    result = engine.run("https://github.com/o/r/pull/30")
    # 大 PR：关联文件与调用链被省略，历史保留
    assert "二级上下文" not in captured["prompt"]
    assert "三级上下文" not in captured["prompt"]
    assert "四级上下文" in captured["prompt"]
    # 记录了降级事件
    assert store.count_events("review_context_trimmed") == 1
    # 报告标注了省略
    assert "因 PR 体量较大" in result["report"]


def test_run_small_pr_keeps_full_review_context(store):
    engine, gh, cb, ai = make_engine(store)
    ctx = make_ctx()  # 小 diff
    ctx["related_files"] = {"dep.py": "def q(): pass"}
    ctx["call_chains"] = ["a.py -> dep.py"]
    cb.build.return_value = ctx
    captured = {}

    def spy_review(prompt, task_id=None, on_token=None):
        captured["prompt"] = prompt
        import json as _json
        return _json.dumps({"issues": [], "rejected": [], "conclusion": "c"})

    ai.review.side_effect = spy_review
    result = engine.run("https://github.com/o/r/pull/31")
    assert "二级上下文" in captured["prompt"]  # 小 PR 喂全量
    assert store.count_events("review_context_trimmed") == 0
    assert "因 PR 体量较大" not in result["report"]


def test_run_streams_progress_and_clears(store):
    """运行中把模型增量输出写入进度；结束后清理进度。"""
    engine, gh, _, ai = make_engine(store)

    # 让 summarize 通过 on_token 回调推送流式片段
    def fake_summarize(prompt, task_id=None, on_token=None):
        if on_token:
            on_token(None, "kimi-k2.5")   # 模型开始
            on_token("总", "kimi-k2.5")
            on_token("结", "kimi-k2.5")
            # 回调期间进度应可见
            p = store.get_progress(task_id)
            assert p["model"] == "kimi-k2.5" and p["stream"] == "总结"
        import json as _json
        return _json.dumps({"overview": "o", "modules": [], "risk_level": "P2"})

    ai.summarize.side_effect = fake_summarize
    result = engine.run("https://github.com/o/r/pull/20")
    assert result["status"] == "success"
    # 结束后进度被清理
    assert store.get_progress(result["task_id"]) == {
        "stage": "", "model": "", "stream": ""}


def test_run_clears_progress_on_failure(store):
    engine, _, cb, _ = make_engine(store)
    cb.build.side_effect = RuntimeError("拉取失败")
    with pytest.raises(RuntimeError):
        engine.run("https://github.com/o/r/pull/21")
    task = store.list_tasks()[0]
    assert store.get_progress(task["id"]) == {
        "stage": "", "model": "", "stream": ""}


def test_run_clean_pr_approve(store):
    engine, gh, cb, _ = make_engine(store)
    ctx = make_ctx()
    ctx["files"][0]["patch"] = "@@ -1 +1 @@\n+x = 1"  # 无风险
    cb.build.return_value = ctx
    result = engine.run("https://github.com/o/r/pull/6")
    assert result["issues"] == []
    assert gh.create_review.call_args.kwargs["event"] == "APPROVE"


def test_run_no_writeback(store):
    engine, gh, _, _ = make_engine(store)
    result = engine.run("https://github.com/o/r/pull/7", write_back=False)
    assert result["review"] is None
    gh.create_review.assert_not_called()


def test_run_failure_recorded(store):
    engine, _, cb, _ = make_engine(store)
    cb.build.side_effect = RuntimeError("拉取失败")
    with pytest.raises(RuntimeError):
        engine.run("https://github.com/o/r/pull/8")
    task = store.list_tasks()[0]
    assert task["status"] == "failed" and "拉取失败" in task["error"]
    assert store.count_events("review_failed") == 1


def test_writeback_degrade_to_comment_review(store):
    """自己的 PR 不允许 REQUEST_CHANGES：降级为 COMMENT 并保住行级评论。"""
    engine, gh, _, _ = make_engine(store, ai_review_json={
        "issues": [{"file": "a.py", "line": 1, "category": "security",
                    "level": "P0", "confidence": 0.95, "message": "注入"}],
        "rejected": [], "conclusion": "",
    })
    calls = []

    def create_review(owner, repo, number, body, event="COMMENT",
                      comments=None, task_id=None):
        calls.append(event)
        if event == "REQUEST_CHANGES":
            raise RuntimeError("422 Can not request changes on your own PR")
        return {"id": 33}

    gh.create_review.side_effect = create_review
    result = engine.run("https://github.com/o/r/pull/11")
    assert calls == ["REQUEST_CHANGES", "COMMENT"]
    assert result["review"]["event"] == "COMMENT"
    assert result["review"]["comments"] == 1  # 行级评论保留
    gh.create_issue_comment.assert_not_called()


def test_writeback_fallback_to_comment(store):
    engine, gh, _, _ = make_engine(store, ai_review_json={
        "issues": [{"file": "a.py", "line": 999, "category": "security",
                    "level": "P0", "confidence": 0.95, "message": "注入"}],
        "rejected": [], "conclusion": "",
    })
    gh.create_review.side_effect = RuntimeError("行号不在 diff 中")
    result = engine.run("https://github.com/o/r/pull/9")
    assert result["review"]["event"] == "COMMENT_FALLBACK"
    gh.create_issue_comment.assert_called_once()


def test_comment_event_for_p2_only(store):
    engine, gh, cb, _ = make_engine(store, ai_review_json={
        "issues": [{"file": "a.py", "line": 1, "category": "performance",
                    "level": "P2", "confidence": 0.9, "message": "慢"}],
        "rejected": [], "conclusion": "",
    })
    ctx = make_ctx()
    ctx["files"][0]["patch"] = "@@ -1 +1 @@\n+x = 1"
    cb.build.return_value = ctx
    engine.run("https://github.com/o/r/pull/10")
    assert gh.create_review.call_args.kwargs["event"] == "COMMENT"


# ---------- AI 输出容错 ----------

def test_ai_summary_not_json(store):
    engine, _, _, ai = make_engine(store)
    ai.summarize.return_value = "这不是 JSON 的自由发挥"
    summary = engine._ai_summary("ctx", 1)
    assert summary["overview"].startswith("这不是")
    assert summary["risk_level"] == "P3"


def test_ai_summary_json_array(store):
    engine, _, _, ai = make_engine(store)
    ai.summarize.return_value = "[1, 2]"
    summary = engine._ai_summary("ctx", 1)
    assert summary["risk_level"] == "P3"


def test_ai_review_not_json(store):
    engine, _, _, ai = make_engine(store)
    ai.review.return_value = "也不是 JSON"
    result = engine._ai_review("ctx", [], 1)
    assert result["issues"] == [] and "也不是" in result["conclusion"]


def test_ai_review_json_array(store):
    engine, _, _, ai = make_engine(store)
    ai.review.return_value = "[]"
    result = engine._ai_review("ctx", [], 1)
    assert result["issues"] == []


def test_fix_generation_failure_skipped(store):
    engine, _, _, ai = make_engine(store)
    ai.generate_fix.side_effect = AIError("挂了")
    fixes = engine._generate_fixes(
        make_ctx(),
        [{"id": 1, "file": "a.py", "line": 1, "level": "P0",
          "category": "security", "confidence": 0.9, "message": "m"}],
        1, 3)
    assert fixes == []


def test_fix_generation_bad_json(store):
    engine, _, _, ai = make_engine(store)
    ai.generate_fix.return_value = "[1]"
    fixes = engine._generate_fixes(
        make_ctx(),
        [{"id": 1, "file": "a.py", "line": 1, "level": "P1",
          "category": "security", "confidence": 0.9, "message": "m"}],
        1, 3)
    assert fixes == []


# ---------- 一致性检查 ----------

def test_consistency_boost_and_penalty(store):
    engine, _, _, _ = make_engine(store)
    rule_findings = [
        {"rule": "R1", "category": "security", "level": "P0",
         "confidence": 0.9, "file": "a.py", "line": 10, "message": "规则一"},
        {"rule": "R2", "category": "style", "level": "P3",
         "confidence": 0.6, "file": "b.py", "line": 5, "message": "规则二"},
        {"rule": "R3", "category": "performance", "level": "P2",
         "confidence": 0.8, "file": "c.py", "line": 1, "message": "规则三"},
    ]
    llm = {"issues": [
        {"file": "a.py", "line": 12, "category": "security", "level": "P0",
         "confidence": 0.85, "message": "LLM确认"},
        {"file": "d.py", "line": 2, "category": "reliability", "level": "P2",
         "confidence": 0.75, "message": "LLM独有"},
    ], "rejected": [{"file": "b.py", "rule": "R2", "reason": "误报"}]}
    issues = engine._consistency_check(rule_findings, llm)
    by_file = {i["file"]: i for i in issues}
    assert by_file["a.py"]["verified"] == "rule+llm"
    assert by_file["a.py"]["confidence"] == pytest.approx(0.95)
    assert by_file["d.py"]["verified"] == "llm"
    assert by_file["b.py"]["confidence"] == pytest.approx(0.3)   # 0.6*0.5
    assert by_file["c.py"]["confidence"] == pytest.approx(0.68)  # 0.8*0.85


def test_consistency_no_line_match_by_category(store):
    engine, _, _, _ = make_engine(store)
    rule_findings = [{"rule": "R", "category": "security", "level": "P1",
                      "confidence": 0.8, "file": "a.py", "line": None,
                      "message": "m"}]
    llm = {"issues": [{"file": "a.py", "category": "security",
                       "level": "P1", "confidence": 0.8, "message": "m2"}],
           "rejected": []}
    issues = engine._consistency_check(rule_findings, llm)
    assert len(issues) == 1 and issues[0]["verified"] == "rule+llm"


def test_normalize_dirty_issue(store):
    engine, _, _, _ = make_engine(store)
    issue = engine._normalize({"file": "a.py", "line": "不是数字",
                               "category": "未知类", "level": "p9",
                               "confidence": "高"})
    assert issue["line"] is None
    assert issue["category"] == "style" and issue["level"] == "P3"
    assert issue["confidence"] == 0.5
    issue2 = engine._normalize({"file": "a.py", "confidence": 99})
    assert issue2["confidence"] == 1.0


# ---------- 评分 ----------

def test_score_levels(store):
    engine, _, _, _ = make_engine(store)
    score, risk, cats = engine._score([])
    assert score == 100.0 and risk == "P3"

    score, risk, _ = engine._score([
        {"category": "security", "level": "P0"},
        {"category": "maintainability", "level": "P3"},  # 并入 style
    ])
    assert risk == "P0"
    # security 100-40=60, style 100-4=96(maintainability 并入), 其余满分
    assert score == pytest.approx(60 * 0.4 + 100 * 0.3 + 100 * 0.2 + 96 * 0.1,
                                  abs=0.05)

    _, risk, _ = engine._score([{"category": "performance", "level": "P2"}])
    assert risk == "P2"
    _, risk, _ = engine._score([{"category": "reliability", "level": "P1"}])
    assert risk == "P1"


def test_score_floor_zero(store):
    engine, _, _, _ = make_engine(store)
    issues = [{"category": "security", "level": "P0"}] * 5
    score, _, cats = engine._score(issues)
    assert cats["security"] == 0.0 and score >= 0


# ---------- 片段提取 ----------

def test_snippet(store):
    engine, _, _, _ = make_engine(store)
    ctx = make_ctx()
    snippet = engine._snippet(ctx, "a.py", 2, span=1)
    assert "1:" in snippet or "2:" in snippet
    assert engine._snippet(ctx, "不存在.py", 1) == ""
    assert engine._snippet(ctx, "a.py", None).startswith("except:")
