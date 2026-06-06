"""db.py 测试。"""


def test_task_lifecycle(store):
    tid = store.create_task("a/b", 7, "https://github.com/a/b/pull/7")
    task = store.get_task(tid)
    assert task["repo"] == "a/b" and task["status"] == "pending"

    store.update_task(tid, status="running")
    assert store.get_task(tid)["status"] == "running"

    store.update_task(tid, 不允许的字段="x")  # 非法字段被忽略
    store.finish_task(tid, "success", score=88.0, risk_level="P2",
                      summary="ok")
    task = store.get_task(tid)
    assert task["status"] == "success"
    assert task["score"] == 88.0
    assert task["duration_ms"] is not None and task["duration_ms"] >= 0
    assert store.list_tasks()[0]["id"] == tid


def test_finish_task_missing(store):
    store.finish_task(99999, "failed", error="不存在")  # 不抛异常


def test_get_task_missing(store):
    assert store.get_task(404) is None
    assert store.get_issue(404) is None


def test_issue_crud(store):
    tid = store.create_task("a/b", 1)
    iid = store.add_issue(tid, "P0", "security", "x.py", 5, 0.95, "注入",
                          suggestion="参数化", fix_patch="- a\n+ b")
    issues = store.list_issues(tid)
    assert len(issues) == 1 and issues[0]["level"] == "P0"
    assert issues[0]["adopted"] is None

    store.set_issue_adopted(iid, True)
    assert store.get_issue(iid)["adopted"] == 1
    store.set_issue_adopted(iid, False)
    assert store.get_issue(iid)["adopted"] == 0

    store.set_issue_fix(iid, "方案", "patch内容")
    assert store.get_issue(iid)["fix_patch"] == "patch内容"


def test_comment(store):
    tid = store.create_task("a/b", 1)
    store.add_comment(tid, "评审报告正文")
    comments = store.list_comments(tid)
    assert len(comments) == 1 and comments[0]["comment"] == "评审报告正文"


def test_metrics_tables(store):
    store.record_ai_metric("review", "glm-5.1", True, 100, 10, 20, 1)
    store.record_ai_metric("summary", "m3", False, 50, error="超时")
    rows = store.list_ai_metrics()
    assert len(rows) == 2 and rows[0]["model"] == "m3"

    store.record_github_metric("fetch", True, 30, 1)
    assert store.list_github_metrics()[0]["operation"] == "fetch"


def test_events(store):
    store.record_event("review_start", {"task_id": 1})
    store.record_event("review_start")
    store.record_event("其他", {"x": "中文"})
    assert store.count_events("review_start") == 2
    assert store.count_events("不存在") == 0
    events = store.list_events("review_start")
    assert len(events) == 2 and events[0]["payload"] in ({}, {"task_id": 1})
    assert len(store.list_events()) == 3
