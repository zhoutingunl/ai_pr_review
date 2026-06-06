"""metrics.py / dashboard.py 测试。"""
from dashboard import Dashboard
from metrics import Metrics, percentile


def test_percentile():
    assert percentile([], 95) == 0.0
    assert percentile([7], 50) == 7.0
    assert percentile(list(range(1, 101)), 95) == 95.0
    assert percentile(list(range(1, 101)), 99) == 99.0
    assert percentile([1, 2], 100) == 2.0


def test_review_metrics_empty(store):
    m = Metrics(store).review_metrics()
    assert m["total"] == 0 and m["success_rate"] == 0.0
    assert m["avg_duration_ms"] == 0


def test_review_metrics(store):
    t1 = store.create_task("a/b", 1)
    store.finish_task(t1, "success", score=90)
    t2 = store.create_task("a/b", 2)
    store.finish_task(t2, "failed", error="x")
    store.create_task("a/b", 3)  # pending 不计入成功率
    m = Metrics(store).review_metrics()
    assert m["total"] == 3
    assert m["success_rate"] == 0.5
    assert m["p95_duration_ms"] >= 0


def test_ai_metrics(store):
    store.record_ai_metric("review", "glm-5.1", True, 1000, 100, 50)
    store.record_ai_metric("review", "glm-5.1", True, 2000, 200, 80)
    store.record_ai_metric("summary", "kimi-k2.5", False, 500, error="429")
    m = Metrics(store).ai_metrics()
    assert m["calls"] == 3
    assert m["input_tokens"] == 300 and m["output_tokens"] == 130
    assert m["failure_rate"] == round(1 / 3, 4)
    assert m["model_success_rate"]["glm-5.1"] == 1.0
    assert m["model_success_rate"]["kimi-k2.5"] == 0.0


def test_issue_metrics(store):
    tid = store.create_task("a/b", 1)
    i1 = store.add_issue(tid, "P1", "security", "a.py", 1, 0.9, "m1")
    i2 = store.add_issue(tid, "P2", "style", "b.py", 2, 0.8, "m2")
    store.add_issue(tid, "P3", "style", "c.py", 3, 0.7, "m3")  # 未标注
    store.set_issue_adopted(i1, True)
    store.set_issue_adopted(i2, False)
    m = Metrics(store).issue_metrics()
    assert m["issues"] == 3 and m["adopted"] == 1
    assert m["adoption_rate"] == 0.5
    assert m["false_positive_rate"] == 0.5


def test_github_metrics(store):
    store.record_github_metric("fetch", True, 100)
    store.record_github_metric("review", True, 200)
    store.record_github_metric("review", False, 300, error="422")
    store.record_github_metric("comment", True, 50)
    store.record_event("webhook_pull_request", {})
    store.record_event("webhook_rejected", {})
    m = Metrics(store).github_metrics()
    assert m["fetch_success_rate"] == 1.0
    assert m["review_success_rate"] == 0.5
    assert m["writeback_success_rate"] == round(2 / 3, 4)
    assert m["webhook_success_rate"] == 0.5


def test_dashboard_snapshot(store):
    tid = store.create_task("a/b", 9)
    store.finish_task(tid, "success", score=95, risk_level="P3")
    store.record_event("dashboard_open", {})
    snap = Dashboard(store).snapshot()
    assert set(snap) >= {"review", "ai", "issue", "github",
                         "events", "recent_tasks"}
    assert snap["events"]["dashboard_open"] == 1
    assert snap["recent_tasks"][0]["repo"] == "a/b"
