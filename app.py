"""AI PR Review Web 应用入口。

启动：
    python3.11 app.py        # gevent WSGIServer，默认 0.0.0.0:38001
"""
from __future__ import annotations

import json

from flask import Flask, jsonify, render_template, request

from ai_service import AIService
from config import CONFIG
from context_builder import ContextBuilder
from dashboard import Dashboard
from db import Store
from github_client import GithubClient, parse_pr_url, GithubError
from review_engine import ReviewEngine
from scheduler import Scheduler
from webhook import WebhookHandler


class ReviewWebApp:

    def __init__(self, name: str, store: Store | None = None):
        self.app_ = Flask(name)

        self.store_ = store or Store()

        self.github_ = GithubClient(store=self.store_)

        self.ai_ = AIService(store=self.store_)

        self.context_ = ContextBuilder(self.github_)
        self.engine_ = ReviewEngine(self.store_, self.github_,
                                    self.context_, self.ai_)
        self.scheduler_ = Scheduler(self.engine_)
        self.webhook_ = WebhookHandler(self.scheduler_, self.store_,
                                       CONFIG.webhook_secret_)
        self.dashboard_ = Dashboard(self.store_)

        self._register_routes()

    # ---------- 路由 ----------

    def _register_routes(self) -> None:
        app = self.app_

        @app.get("/")
        def index():
            tasks = self.store_.list_tasks(limit=20)
            return render_template("index.html", tasks=tasks)

        @app.post("/api/review")
        def submit_review():
            data = request.get_json(silent=True) or {}
            pr_url = (data.get("pr_url") or "").strip()
            write_back = bool(data.get("write_back", True))
            try:
                parse_pr_url(pr_url)
            except GithubError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            submitted = self.scheduler_.submit(pr_url, write_back=write_back)
            return jsonify({"ok": True, "submitted": submitted,
                            "msg": "已提交评审" if submitted
                                   else "该 PR 正在评审中"})

        @app.get("/task/<int:task_id>")
        def task_page(task_id: int):
            task = self.store_.get_task(task_id)
            if not task:
                return render_template("index.html",
                                       tasks=self.store_.list_tasks(20)), 404
            issues = self.store_.list_issues(task_id)
            comments = self.store_.list_comments(task_id)
            return render_template("task.html", task=task, issues=issues,
                                   comments=comments)

        @app.get("/api/task/<int:task_id>")
        def task_api(task_id: int):
            task = self.store_.get_task(task_id)
            if not task:
                return jsonify({"ok": False, "error": "任务不存在"}), 404
            return jsonify({"ok": True, "task": task,
                            "issues": self.store_.list_issues(task_id),
                            "comments": self.store_.list_comments(task_id)})

        @app.get("/api/tasks")
        def tasks_api():
            return jsonify({"ok": True,
                            "tasks": self.store_.list_tasks(limit=50)})

        @app.post("/api/issue/<int:issue_id>/adopt")
        def adopt_issue(issue_id: int):
            issue = self.store_.get_issue(issue_id)
            if not issue:
                return jsonify({"ok": False, "error": "问题不存在"}), 404
            data = request.get_json(silent=True) or {}
            adopted = bool(data.get("adopted", True))
            self.store_.set_issue_adopted(issue_id, adopted)
            return jsonify({"ok": True, "adopted": adopted})

        @app.get("/metrics")
        def metrics_page():
            self.store_.record_event("dashboard_open", {})
            return render_template("metrics.html",
                                   data=self.dashboard_.snapshot())

        @app.get("/api/metrics")
        def metrics_api():
            self.store_.record_event("dashboard_refresh", {})
            return jsonify({"ok": True, "data": self.dashboard_.snapshot()})

        @app.post("/api/track")
        def track_api():
            data = request.get_json(silent=True) or {}
            event = (data.get("event") or "").strip()
            if not event:
                return jsonify({"ok": False, "error": "缺少 event"}), 400
            payload = data.get("payload")
            self.store_.record_event(
                event, payload if isinstance(payload, dict) else {})
            return jsonify({"ok": True})

        @app.post("/webhook")
        def webhook():
            code, body = self.webhook_.handle(
                request.headers.get("X-GitHub-Event"),
                request.headers.get("X-Hub-Signature-256"),
                request.get_data())
            return jsonify(body), code

    # ---------- 启动 ----------

    def run(self) -> None:
        from gevent.pywsgi import WSGIServer
        server = WSGIServer((CONFIG.host, CONFIG.port), self.app_)
        print(f"AI PR Review 已启动: http://{CONFIG.host}:{CONFIG.port}")
        server.serve_forever()


def create_app(store: Store | None = None) -> ReviewWebApp:
    return ReviewWebApp(__name__, store=store)


if __name__ == "__main__":
    from gevent import monkey
    monkey.patch_all()
    create_app().run()
