"""GitHub Webhook 处理。

支持事件：pull_request（opened / synchronize / reopened 触发自动评审）。
安全：校验 X-Hub-Signature-256（HMAC-SHA256，密钥来自 .env 的 WEBHOOK_SECRET）。
"""
from __future__ import annotations

import hashlib
import hmac
import json

# 触发自动评审的 pull_request action
_TRIGGER_ACTIONS = {"opened", "synchronize", "reopened"}


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    """校验 GitHub Webhook 签名（X-Hub-Signature-256: sha256=...）。

    未配置 secret 时跳过校验（开发模式）。
    """
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[len("sha256="):], expected)


class WebhookHandler:

    def __init__(self, scheduler, store, secret: str = ""):
        self.scheduler_ = scheduler
        self.store_ = store
        self.secret_ = secret

    def handle(self, event: str | None, signature: str | None,
               payload: bytes) -> tuple[int, dict]:
        """处理一次 Webhook 投递。返回 (HTTP状态码, 响应体)。"""
        if not verify_signature(self.secret_, payload, signature):
            self.store_.record_event("webhook_rejected", {"reason": "签名校验失败"})
            return 401, {"ok": False, "error": "签名校验失败"}

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, {"ok": False, "error": "非法 JSON"}

        if event == "ping":
            self.store_.record_event("webhook_ping", {})
            return 200, {"ok": True, "msg": "pong"}

        if event != "pull_request":
            return 200, {"ok": True, "msg": f"忽略事件 {event}"}

        action = data.get("action", "")
        pr = data.get("pull_request") or {}
        pr_url = pr.get("html_url", "")
        self.store_.record_event("webhook_pull_request",
                                 {"action": action, "pr_url": pr_url})

        if action not in _TRIGGER_ACTIONS:
            return 200, {"ok": True, "msg": f"忽略 action {action}"}
        if not pr_url:
            return 400, {"ok": False, "error": "payload 缺少 pull_request.html_url"}

        submitted = self.scheduler_.submit(pr_url)
        return 200, {"ok": True,
                     "msg": "已触发评审" if submitted else "该 PR 正在评审中",
                     "submitted": submitted}
