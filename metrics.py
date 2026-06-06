"""QoS 指标计算。

四组指标（见 design.md）：
    Review 指标   分析次数 / 成功率 / 平均耗时 / P95 / P99
    AI 指标       Token 消耗 / 平均耗时 / 失败率 / 各模型成功率
    误报指标      Issue 数量 / 用户采纳数 / 采纳率 / 误报率
    GitHub 指标   回写成功率 / Webhook 成功率 / Review 成功率
"""
from __future__ import annotations

import math


def percentile(values: list[float], pct: float) -> float:
    """最近邻法分位数。空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1,
                      math.ceil(pct / 100 * len(ordered)) - 1))
    return float(ordered[rank])


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


class Metrics:

    def __init__(self, store):
        self.store_ = store

    def review_metrics(self) -> dict:
        tasks = self.store_.list_tasks(limit=1000)
        finished = [t for t in tasks if t["status"] in ("success", "failed")]
        durations = [t["duration_ms"] for t in finished
                     if t["duration_ms"] is not None]
        success = [t for t in finished if t["status"] == "success"]
        return {
            "total": len(tasks),
            "success_rate": _rate(len(success), len(finished)),
            "avg_duration_ms": round(sum(durations) / len(durations))
                               if durations else 0,
            "p95_duration_ms": percentile(durations, 95),
            "p99_duration_ms": percentile(durations, 99),
        }

    def ai_metrics(self) -> dict:
        rows = self.store_.list_ai_metrics()
        durations = [r["duration_ms"] for r in rows]
        failures = [r for r in rows if not r["success"]]
        by_model: dict[str, dict] = {}
        for r in rows:
            stat = by_model.setdefault(r["model"], {"total": 0, "success": 0})
            stat["total"] += 1
            stat["success"] += 1 if r["success"] else 0
        return {
            "calls": len(rows),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "avg_duration_ms": round(sum(durations) / len(durations))
                               if durations else 0,
            "failure_rate": _rate(len(failures), len(rows)),
            "model_success_rate": {
                model: _rate(s["success"], s["total"])
                for model, s in by_model.items()
            },
        }

    def issue_metrics(self) -> dict:
        """误报指标。adopted: 1=采纳 0=误报 NULL=未标注。"""
        tasks = self.store_.list_tasks(limit=1000)
        issues = []
        for t in tasks:
            issues.extend(self.store_.list_issues(t["id"]))
        adopted = [i for i in issues if i["adopted"] == 1]
        rejected = [i for i in issues if i["adopted"] == 0]
        labeled = len(adopted) + len(rejected)
        return {
            "issues": len(issues),
            "adopted": len(adopted),
            "adoption_rate": _rate(len(adopted), labeled),
            "false_positive_rate": _rate(len(rejected), labeled),
        }

    def github_metrics(self) -> dict:
        rows = self.store_.list_github_metrics()

        def op_rate(*ops: str) -> float:
            sub = [r for r in rows if r["operation"] in ops]
            return _rate(sum(1 for r in sub if r["success"]), len(sub))

        webhook_ok = self.store_.count_events("webhook_pull_request") \
            + self.store_.count_events("webhook_ping")
        webhook_bad = self.store_.count_events("webhook_rejected")
        return {
            "writeback_success_rate": op_rate("comment", "review"),
            "review_success_rate": op_rate("review"),
            "fetch_success_rate": op_rate("fetch"),
            "webhook_success_rate": _rate(webhook_ok,
                                          webhook_ok + webhook_bad),
        }

    def all(self) -> dict:
        return {
            "review": self.review_metrics(),
            "ai": self.ai_metrics(),
            "issue": self.issue_metrics(),
            "github": self.github_metrics(),
        }
