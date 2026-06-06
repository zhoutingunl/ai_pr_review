"""app.py 路由测试。"""
import hashlib
import hmac
import json

import pytest

from app import create_app


@pytest.fixture
def web(store):
    return create_app(store)


@pytest.fixture
def client(web):
    return web.app_.test_client()


def test_index(client, store):
    store.create_task("a/b", 1, "https://github.com/a/b/pull/1")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "a/b" in resp.get_data(as_text=True)


def test_submit_review_invalid_url(client):
    resp = client.post("/api/review", json={"pr_url": "无效"})
    assert resp.status_code == 400


def test_submit_review_ok(client, web):
    submitted = []
    web.scheduler_.submit = lambda url, write_back=True: (
        submitted.append((url, write_back)) or True)
    resp = client.post("/api/review",
                       json={"pr_url": "https://github.com/a/b/pull/2",
                             "write_back": False})
    assert resp.get_json()["submitted"]
    assert submitted == [("https://github.com/a/b/pull/2", False)]


def test_submit_review_duplicate(client, web):
    web.scheduler_.submit = lambda url, write_back=True: False
    resp = client.post("/api/review",
                       json={"pr_url": "https://github.com/a/b/pull/3"})
    assert "正在评审中" in resp.get_json()["msg"]


def test_task_page_and_api(client, store):
    tid = store.create_task("a/b", 5, "https://github.com/a/b/pull/5")
    store.add_issue(tid, "P0", "security", "x.py", 9, 0.9, "注入",
                    suggestion="参数化", fix_patch="- a\n+ b")
    store.add_comment(tid, "# 报告正文")
    store.finish_task(tid, "success", score=70, risk_level="P0")

    page = client.get(f"/task/{tid}").get_data(as_text=True)
    assert "注入" in page and "报告正文" in page

    data = client.get(f"/api/task/{tid}").get_json()
    assert data["task"]["score"] == 70
    assert data["issues"][0]["level"] == "P0"
    assert data["comments"][0]["comment"] == "# 报告正文"


def test_task_not_found(client):
    assert client.get("/task/999").status_code == 404
    assert client.get("/api/task/999").status_code == 404


def test_tasks_api(client, store):
    store.create_task("a/b", 1)
    assert len(client.get("/api/tasks").get_json()["tasks"]) == 1


def test_adopt_issue(client, store):
    tid = store.create_task("a/b", 1)
    iid = store.add_issue(tid, "P1", "style", "a.py", 1, 0.8, "m")
    resp = client.post(f"/api/issue/{iid}/adopt", json={"adopted": True})
    assert resp.get_json()["ok"]
    assert store.get_issue(iid)["adopted"] == 1
    client.post(f"/api/issue/{iid}/adopt", json={"adopted": False})
    assert store.get_issue(iid)["adopted"] == 0
    assert client.post("/api/issue/999/adopt", json={}).status_code == 404


def test_metrics_page_and_api(client, store):
    page = client.get("/metrics")
    assert page.status_code == 200
    assert store.count_events("dashboard_open") == 1
    data = client.get("/api/metrics").get_json()["data"]
    assert "review" in data
    assert store.count_events("dashboard_refresh") == 1


def test_track(client, store):
    resp = client.post("/api/track",
                       json={"event": "review_start", "payload": {"a": 1}})
    assert resp.get_json()["ok"]
    assert store.count_events("review_start") == 1
    assert client.post("/api/track", json={}).status_code == 400
    # payload 非 dict 时按空 payload 落库
    client.post("/api/track", json={"event": "e2", "payload": [1]})
    assert store.list_events("e2")[0]["payload"] == {}


def test_webhook_route(client, web, store):
    web.webhook_.secret_ = "s3"
    submitted = []
    web.webhook_.scheduler_ = type(
        "S", (), {"submit": lambda self, url, write_back=True: (
            submitted.append(url) or True)})()
    body = json.dumps({
        "action": "opened",
        "pull_request": {"html_url": "https://github.com/a/b/pull/8"},
    }).encode()
    sig = "sha256=" + hmac.new(b"s3", body, hashlib.sha256).hexdigest()
    resp = client.post("/webhook", data=body,
                       headers={"X-GitHub-Event": "pull_request",
                                "X-Hub-Signature-256": sig,
                                "Content-Type": "application/json"})
    assert resp.status_code == 200 and resp.get_json()["submitted"]
    assert submitted == ["https://github.com/a/b/pull/8"]

    resp = client.post("/webhook", data=body,
                       headers={"X-GitHub-Event": "pull_request",
                                "X-Hub-Signature-256": "sha256=bad"})
    assert resp.status_code == 401
