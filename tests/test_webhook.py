"""webhook.py 测试。"""
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

from webhook import WebhookHandler, verify_signature

SECRET = "测试密钥"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body,
                                hashlib.sha256).hexdigest()


def payload(action="opened", url="https://github.com/a/b/pull/1") -> bytes:
    return json.dumps({"action": action,
                       "pull_request": {"html_url": url}}).encode()


def make_handler(store, secret=SECRET):
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    return WebhookHandler(scheduler, store, secret), scheduler


# ---------- 签名 ----------

def test_verify_signature_ok():
    body = b'{"x": 1}'
    assert verify_signature(SECRET, body, sign(body))


def test_verify_signature_bad():
    body = b'{"x": 1}'
    assert not verify_signature(SECRET, body, "sha256=deadbeef")
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature(SECRET, body, "md5=xx")


def test_verify_signature_no_secret():
    assert verify_signature("", b"anything", None)


# ---------- 事件处理 ----------

def test_handle_trigger_actions(store):
    for action in ("opened", "synchronize", "reopened"):
        handler, scheduler = make_handler(store)
        body = payload(action)
        code, resp = handler.handle("pull_request", sign(body), body)
        assert code == 200 and resp["submitted"], action
        scheduler.submit.assert_called_once_with(
            "https://github.com/a/b/pull/1")


def test_handle_ignored_action(store):
    handler, scheduler = make_handler(store)
    body = payload("closed")
    code, resp = handler.handle("pull_request", sign(body), body)
    assert code == 200 and "忽略 action" in resp["msg"]
    scheduler.submit.assert_not_called()


def test_handle_bad_signature(store):
    handler, _ = make_handler(store)
    code, resp = handler.handle("pull_request", "sha256=bad", payload())
    assert code == 401 and not resp["ok"]
    assert store.count_events("webhook_rejected") == 1


def test_handle_bad_json(store):
    handler, _ = make_handler(store)
    body = b"\xff\xfe not json"
    code, resp = handler.handle("pull_request", sign(body), body)
    assert code == 400


def test_handle_ping(store):
    handler, _ = make_handler(store)
    body = b"{}"
    code, resp = handler.handle("ping", sign(body), body)
    assert code == 200 and resp["msg"] == "pong"
    assert store.count_events("webhook_ping") == 1


def test_handle_other_event(store):
    handler, _ = make_handler(store)
    body = b"{}"
    code, resp = handler.handle("issues", sign(body), body)
    assert code == 200 and "忽略事件" in resp["msg"]


def test_handle_missing_url(store):
    handler, _ = make_handler(store)
    body = json.dumps({"action": "opened", "pull_request": {}}).encode()
    code, resp = handler.handle("pull_request", sign(body), body)
    assert code == 400 and "html_url" in resp["error"]


def test_handle_duplicate_submit(store):
    handler, scheduler = make_handler(store)
    scheduler.submit.return_value = False
    body = payload()
    code, resp = handler.handle("pull_request", sign(body), body)
    assert code == 200 and not resp["submitted"]
    assert "正在评审中" in resp["msg"]
