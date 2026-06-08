"""SQLite 数据层。

数据库文件：data/review.db（WAL 模式）。

核心模型（见 design.md）：
    ReviewTask     一次 PR 评审任务
    ReviewIssue    评审发现的问题（行级，含置信度）
    ReviewComment  评审总评

辅助表（QoS Dashboard / 埋点）：
    ai_metric      AI 调用指标（模型、耗时、Token、成败）
    github_metric  GitHub 操作指标（拉取/评论/回写/Webhook）
    track_event    用户行为埋点 track(event, payload)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from config import CONFIG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    pr_url      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    score       REAL,
    risk_level  TEXT,
    summary     TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS review_issue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    level       TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    file        TEXT NOT NULL DEFAULT '',
    line        INTEGER,
    confidence  REAL NOT NULL DEFAULT 0,
    message     TEXT NOT NULL,
    suggestion  TEXT,
    fix_patch   TEXT,
    adopted     INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_comment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    comment     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_metric (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER,
    role          TEXT NOT NULL,
    model         TEXT NOT NULL,
    success       INTEGER NOT NULL,
    duration_ms   INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_metric (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    operation   TEXT NOT NULL,
    success     INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issue_task ON review_issue(task_id);
CREATE INDEX IF NOT EXISTS idx_comment_task ON review_comment(task_id);
CREATE INDEX IF NOT EXISTS idx_ai_metric_created ON ai_metric(created_at);
CREATE INDEX IF NOT EXISTS idx_github_metric_created ON github_metric(created_at);
CREATE INDEX IF NOT EXISTS idx_track_event_event ON track_event(event);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """统一数据访问层。"""

    def __init__(self, db_path: str | None = None):
        self.db_path_ = db_path or CONFIG.db_path
        os.makedirs(os.path.dirname(self.db_path_), exist_ok=True)
        self.lock_ = threading.Lock()
        # 实时进度（内存态，高频更新不落库）：task_id -> {stage, model, stream}
        self.progress_ = {}
        self.progress_lock_ = threading.Lock()
        self.conn_ = sqlite3.connect(self.db_path_, check_same_thread=False)
        self.conn_.row_factory = sqlite3.Row
        self.conn_.execute("PRAGMA journal_mode=WAL")
        self.conn_.executescript(_SCHEMA)
        self.conn_.commit()

    def close(self) -> None:
        self.conn_.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock_:
            cur = self.conn_.execute(sql, params)
            self.conn_.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock_:
            rows = self.conn_.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- ReviewTask ----------

    def create_task(self, repo: str, pr_number: int, pr_url: str = "") -> int:
        cur = self._execute(
            "INSERT INTO review_task (repo, pr_number, pr_url, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (repo, pr_number, pr_url, _now()),
        )
        return cur.lastrowid

    def update_task(self, task_id: int, **fields) -> None:
        allowed = {"status", "score", "risk_level", "summary", "error",
                   "finished_at", "duration_ms"}
        keys = [k for k in fields if k in allowed]
        if not keys:
            return
        sets = ", ".join(f"{k} = ?" for k in keys)
        values = tuple(fields[k] for k in keys) + (task_id,)
        self._execute(f"UPDATE review_task SET {sets} WHERE id = ?", values)

    def finish_task(self, task_id: int, status: str, score: float | None = None,
                    risk_level: str | None = None, summary: str | None = None,
                    error: str | None = None) -> None:
        task = self.get_task(task_id)
        duration_ms = None
        if task:
            started = datetime.fromisoformat(task["created_at"])
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        self.update_task(task_id, status=status, score=score, risk_level=risk_level,
                         summary=summary, error=error, finished_at=_now(),
                         duration_ms=duration_ms)

    def get_task(self, task_id: int) -> dict | None:
        rows = self._query("SELECT * FROM review_task WHERE id = ?", (task_id,))
        return rows[0] if rows else None

    def list_tasks(self, limit: int = 50) -> list[dict]:
        return self._query(
            "SELECT * FROM review_task ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- ReviewIssue ----------

    def add_issue(self, task_id: int, level: str, category: str, file: str,
                  line: int | None, confidence: float, message: str,
                  suggestion: str | None = None, fix_patch: str | None = None) -> int:
        cur = self._execute(
            "INSERT INTO review_issue (task_id, level, category, file, line, "
            "confidence, message, suggestion, fix_patch, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, level, category, file, line, confidence, message,
             suggestion, fix_patch, _now()),
        )
        return cur.lastrowid

    def list_issues(self, task_id: int) -> list[dict]:
        return self._query(
            "SELECT * FROM review_issue WHERE task_id = ? ORDER BY "
            "level, confidence DESC", (task_id,))

    def get_issue(self, issue_id: int) -> dict | None:
        rows = self._query("SELECT * FROM review_issue WHERE id = ?", (issue_id,))
        return rows[0] if rows else None

    def set_issue_adopted(self, issue_id: int, adopted: bool) -> None:
        self._execute("UPDATE review_issue SET adopted = ? WHERE id = ?",
                      (1 if adopted else 0, issue_id))

    def set_issue_fix(self, issue_id: int, suggestion: str, fix_patch: str) -> None:
        self._execute(
            "UPDATE review_issue SET suggestion = ?, fix_patch = ? WHERE id = ?",
            (suggestion, fix_patch, issue_id))

    # ---------- ReviewComment ----------

    def add_comment(self, task_id: int, comment: str) -> int:
        cur = self._execute(
            "INSERT INTO review_comment (task_id, comment, created_at) "
            "VALUES (?, ?, ?)", (task_id, comment, _now()))
        return cur.lastrowid

    def list_comments(self, task_id: int) -> list[dict]:
        return self._query(
            "SELECT * FROM review_comment WHERE task_id = ? ORDER BY id", (task_id,))

    # ---------- 指标 ----------

    def record_ai_metric(self, role: str, model: str, success: bool,
                         duration_ms: int, input_tokens: int = 0,
                         output_tokens: int = 0, task_id: int | None = None,
                         error: str | None = None) -> None:
        self._execute(
            "INSERT INTO ai_metric (task_id, role, model, success, duration_ms, "
            "input_tokens, output_tokens, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, role, model, 1 if success else 0, duration_ms,
             input_tokens, output_tokens, error, _now()))

    def record_github_metric(self, operation: str, success: bool, duration_ms: int,
                             task_id: int | None = None,
                             error: str | None = None) -> None:
        self._execute(
            "INSERT INTO github_metric (task_id, operation, success, duration_ms, "
            "error, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, operation, 1 if success else 0, duration_ms, error, _now()))

    def list_ai_metrics(self) -> list[dict]:
        return self._query("SELECT * FROM ai_metric ORDER BY id DESC")

    def list_github_metrics(self) -> list[dict]:
        return self._query("SELECT * FROM github_metric ORDER BY id DESC")

    # ---------- 实时进度（内存态） ----------

    _PROGRESS_CAP = 12000   # 流式片段最多保留的字符数（防无界增长）

    def set_progress(self, task_id: int, stage: str | None = None,
                     model: str | None = None, stream: str | None = None,
                     append: str | None = None) -> None:
        """更新任务实时进度。stream 置空字符串可清空已展示片段。"""
        with self.progress_lock_:
            p = self.progress_.setdefault(
                task_id, {"stage": "", "model": "", "stream": ""})
            if stage is not None:
                p["stage"] = stage
            if model is not None:
                p["model"] = model
            if stream is not None:
                p["stream"] = stream[-self._PROGRESS_CAP:]
            if append:
                p["stream"] = (p["stream"] + append)[-self._PROGRESS_CAP:]

    def get_progress(self, task_id: int) -> dict:
        with self.progress_lock_:
            p = self.progress_.get(task_id)
            return dict(p) if p else {"stage": "", "model": "", "stream": ""}

    def clear_progress(self, task_id: int) -> None:
        with self.progress_lock_:
            self.progress_.pop(task_id, None)

    # ---------- 埋点 ----------

    def record_event(self, event: str, payload: dict | None = None) -> None:
        self._execute(
            "INSERT INTO track_event (event, payload, created_at) VALUES (?, ?, ?)",
            (event, json.dumps(payload or {}, ensure_ascii=False), _now()))

    def list_events(self, event: str | None = None, limit: int = 200) -> list[dict]:
        if event:
            rows = self._query(
                "SELECT * FROM track_event WHERE event = ? ORDER BY id DESC LIMIT ?",
                (event, limit))
        else:
            rows = self._query(
                "SELECT * FROM track_event ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def count_events(self, event: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS n FROM track_event WHERE event = ?", (event,))
        return rows[0]["n"]
