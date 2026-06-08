"""report_generator.py 测试。"""
from report_generator import ReportGenerator


CTX = {"pr": {"title": "标题", "changed_files": 2,
              "additions": 10, "deletions": 3}}
CATS = {"security": 60.0, "reliability": 90.0,
        "performance": 100.0, "style": 96.0}


def test_full_report():
    issues = [
        {"file": "a.py", "line": 3, "category": "security", "level": "P0",
         "confidence": 0.95, "message": "SQL|注入\n多行"},
        {"file": "b.py", "line": None, "category": "style", "level": "P3",
         "confidence": 0.7, "message": "命名"},
    ]
    fixes = [{"file": "a.py", "line": 3, "plan": "参数化",
              "patch": "- bad\n+ good", "commit_message": "修复注入"}]
    report = ReportGenerator().generate(
        CTX, {"overview": "概述", "modules": ["mod1", "mod2"]},
        issues, fixes, 80.6, "P0", CATS)
    assert "AI PR Review 评审报告" in report
    assert "80.6" in report and "P0 阻塞" in report
    assert "mod1、mod2" in report
    assert "`a.py:3`" in report and "`b.py`" in report
    assert "SQL\\|注入 多行" in report  # 表格转义 + 换行清理
    assert "- bad" in report and "修复注入" in report
    assert "维度得分" in report


def test_report_omitted_context_note():
    report = ReportGenerator().generate(
        CTX, {"overview": "o"}, [], [], 90.0, "P3",
        {c: 100.0 for c in CATS},
        omitted_context=["二级 关联文件", "三级 调用链"])
    assert "因 PR 体量较大" in report
    assert "二级 关联文件、三级 调用链" in report
    assert "规则引擎仍全量扫描 Diff" in report


def test_report_no_omitted_note_when_none():
    report = ReportGenerator().generate(
        CTX, {"overview": "o"}, [], [], 90.0, "P3",
        {c: 100.0 for c in CATS})
    assert "因 PR 体量较大" not in report


def test_report_no_issues():
    report = ReportGenerator().generate(
        CTX, {"overview": ""}, [], [], 100.0, "P3",
        {c: 100.0 for c in CATS})
    assert "未发现达到置信度阈值的问题" in report
    assert "（无）" in report


def test_report_fix_without_optional_fields():
    fixes = [{"file": "x.py", "line": None, "plan": "",
              "patch": "", "commit_message": ""}]
    report = ReportGenerator().generate(
        CTX, {"overview": "o"}, [], fixes, 90.0, "P3",
        {c: 100.0 for c in CATS})
    assert "### `x.py`" in report
