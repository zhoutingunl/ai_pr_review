"""QoS Dashboard（/metrics）。

聚合 Metrics 四组指标 + 埋点统计，供页面渲染与 JSON 接口。
"""
from __future__ import annotations

from metrics import Metrics

# Dashboard 关注的埋点事件
_TRACKED_EVENTS = [
    "review_start", "review_finish", "review_failed",
    "github_fetch", "github_comment", "github_review",
    "ai_summary", "ai_review", "ai_fix",
    "dashboard_open", "dashboard_refresh",
]


class Dashboard:

    def __init__(self, store, scheduler=None):
        self.store_ = store
        self.scheduler_ = scheduler
        self.metrics_ = Metrics(store)

    def snapshot(self) -> dict:
        """完整 Dashboard 数据：四组 QoS 指标 + 调度并发 + 埋点计数 + 近期任务。"""
        data = self.metrics_.all()
        data["scheduler"] = (self.scheduler_.stats() if self.scheduler_
                             else {"in_flight": 0, "queued": 0,
                                   "max_concurrent": 0, "active": []})
        data["events"] = {e: self.store_.count_events(e)
                          for e in _TRACKED_EVENTS}
        data["recent_tasks"] = [
            {"id": t["id"], "repo": t["repo"], "pr_number": t["pr_number"],
             "status": t["status"], "score": t["score"],
             "risk_level": t["risk_level"], "duration_ms": t["duration_ms"],
             "created_at": t["created_at"]}
            for t in self.store_.list_tasks(limit=20)
        ]
        return data
