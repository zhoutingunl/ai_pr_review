"""后台任务调度器。

Web 请求只负责提交任务并立即返回，真正的评审流水线在后台执行。
使用 threading.Thread：在 gevent monkey patch 后的运行时中即为协程，
未 patch 的测试环境中为普通线程，两种环境行为一致。
"""
from __future__ import annotations

import threading


class Scheduler:

    def __init__(self, review_engine):
        self.engine_ = review_engine
        self.lock_ = threading.Lock()
        self.running_ = {}   # pr_url -> thread，防止同一 PR 并发重复评审

    def submit(self, pr_url: str, write_back: bool = True) -> bool:
        """提交评审任务。同一 PR 已在评审中则拒绝，返回 False。"""
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
        try:
            self.engine_.run(pr_url, write_back=write_back)
        except Exception:  # noqa: BLE001 - 失败已由引擎落库，调度器不再上抛
            pass
        finally:
            with self.lock_:
                self.running_.pop(pr_url, None)

    def is_running(self, pr_url: str) -> bool:
        with self.lock_:
            worker = self.running_.get(pr_url)
            return bool(worker and worker.is_alive())

    def wait_all(self, timeout: float | None = None) -> None:
        """等待全部后台任务结束（测试 / 优雅退出用）。"""
        with self.lock_:
            workers = list(self.running_.values())
        for worker in workers:
            worker.join(timeout)
