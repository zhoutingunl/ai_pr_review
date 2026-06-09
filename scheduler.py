"""后台任务调度器。

Web 请求只负责提交任务并立即返回（异步），真正的评审流水线在后台执行。
使用 threading.Thread：在 gevent monkey patch 后的运行时中即为协程，
未 patch 的测试环境中为普通线程，两种环境行为一致。

并发治理：用 Semaphore 限制**同时在跑**的评审数（scheduler_max_concurrent，
默认 2）。超额的提交会先排队（占线程但阻塞在信号量上），避免 Webhook 涌入时
无上限并发拖垮 Hermes / GitHub。in_flight / queued 计数经 stats() 暴露给
/metrics，前端可见。
"""
from __future__ import annotations

import logging
import threading

from config import CONFIG

_log = logging.getLogger(__name__)


class Scheduler:

    def __init__(self, review_engine, max_concurrent: int | None = None):
        self.engine_ = review_engine
        self.lock_ = threading.Lock()
        self.running_ = {}   # pr_url -> thread，防止同一 PR 并发重复评审
        self.max_concurrent_ = int(
            max_concurrent if max_concurrent is not None
            else CONFIG.get("scheduler_max_concurrent", 2))
        self.sem_ = threading.Semaphore(self.max_concurrent_)
        self.in_flight_ = 0   # 正在执行（持有信号量）的任务数
        self.queued_ = 0      # 已提交但在等待信号量的任务数

    def submit(self, pr_url: str, write_back: bool = True) -> bool:
        """提交评审任务，立即返回。同一 PR 已在评审/排队中则拒绝，返回 False。

        超过并发上限时不拒绝，而是排队（在后台线程内阻塞等待信号量）。
        """
        with self.lock_:
            existing = self.running_.get(pr_url)
            if existing and existing.is_alive():
                return False
            worker = threading.Thread(
                target=self._run, args=(pr_url, write_back), daemon=True)
            self.running_[pr_url] = worker
            worker.start()
            return True

    def _run(self, pr_url: str, write_back: bool) -> None:
        with self.lock_:
            self.queued_ += 1
        self.sem_.acquire()
        with self.lock_:
            self.queued_ -= 1
            self.in_flight_ += 1
        try:
            self.engine_.run(pr_url, write_back=write_back)
        except Exception as e:  # noqa: BLE001 - 失败已由引擎落库，调度器不再上抛
            _log.warning("后台评审任务异常(%s): %s", pr_url, e)
        finally:
            with self.lock_:
                self.in_flight_ -= 1
                self.running_.pop(pr_url, None)
            self.sem_.release()

    def is_running(self, pr_url: str) -> bool:
        with self.lock_:
            worker = self.running_.get(pr_url)
            return bool(worker and worker.is_alive())

    def stats(self) -> dict:
        """并发状态：在跑 / 排队 / 上限 / 活跃 PR 列表。供 /metrics 展示。"""
        with self.lock_:
            return {
                "in_flight": self.in_flight_,
                "queued": self.queued_,
                "max_concurrent": self.max_concurrent_,
                "active": [url for url, t in self.running_.items()
                           if t.is_alive()],
            }

    def wait_all(self, timeout: float | None = None) -> None:
        """等待全部后台任务结束（测试 / 优雅退出用）。"""
        with self.lock_:
            workers = list(self.running_.values())
        for worker in workers:
            worker.join(timeout)
