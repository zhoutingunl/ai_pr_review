"""scheduler.py 测试。"""
import threading
import time
from unittest.mock import MagicMock

from scheduler import Scheduler


def test_submit_runs_engine():
    engine = MagicMock()
    scheduler = Scheduler(engine)
    assert scheduler.submit("https://github.com/a/b/pull/1")
    scheduler.wait_all(timeout=5)
    engine.run.assert_called_once_with("https://github.com/a/b/pull/1",
                                       write_back=True)


def test_submit_no_writeback():
    engine = MagicMock()
    scheduler = Scheduler(engine)
    scheduler.submit("https://github.com/a/b/pull/2", write_back=False)
    scheduler.wait_all(timeout=5)
    engine.run.assert_called_once_with("https://github.com/a/b/pull/2",
                                       write_back=False)


def test_duplicate_submit_rejected():
    engine = MagicMock()
    gate = threading.Event()
    engine.run.side_effect = lambda *a, **k: gate.wait(5)
    scheduler = Scheduler(engine)
    url = "https://github.com/a/b/pull/3"
    assert scheduler.submit(url)
    time.sleep(0.05)
    assert scheduler.is_running(url)
    assert not scheduler.submit(url)  # 重复提交被拒
    gate.set()
    scheduler.wait_all(timeout=5)
    assert not scheduler.is_running(url)
    assert engine.run.call_count == 1


def test_resubmit_after_finish():
    engine = MagicMock()
    scheduler = Scheduler(engine)
    url = "https://github.com/a/b/pull/4"
    scheduler.submit(url)
    scheduler.wait_all(timeout=5)
    assert scheduler.submit(url)  # 完成后可再次提交
    scheduler.wait_all(timeout=5)
    assert engine.run.call_count == 2


def test_engine_exception_swallowed():
    engine = MagicMock()
    engine.run.side_effect = RuntimeError("评审失败")
    scheduler = Scheduler(engine)
    scheduler.submit("https://github.com/a/b/pull/5")
    scheduler.wait_all(timeout=5)  # 不应抛异常
    assert not scheduler.is_running("https://github.com/a/b/pull/5")


def test_concurrency_cap_queues_excess():
    """并发上限=1 时，第二个任务排队，in_flight 不超上限。"""
    engine = MagicMock()
    started = threading.Event()
    release = threading.Event()

    def run(pr_url, write_back=True):
        started.set()
        release.wait(5)

    engine.run.side_effect = run
    scheduler = Scheduler(engine, max_concurrent=1)
    scheduler.submit("https://github.com/a/b/pull/1")
    started.wait(5)
    time.sleep(0.05)
    scheduler.submit("https://github.com/a/b/pull/2")  # 应排队
    time.sleep(0.1)
    stats = scheduler.stats()
    assert stats["max_concurrent"] == 1
    assert stats["in_flight"] == 1          # 只有 1 个在跑
    assert stats["queued"] == 1             # 另一个排队
    release.set()
    scheduler.wait_all(timeout=5)
    assert scheduler.stats() == {"in_flight": 0, "queued": 0,
                                 "max_concurrent": 1, "active": []}
    assert engine.run.call_count == 2       # 排队的最终也执行


def test_stats_default():
    scheduler = Scheduler(MagicMock(), max_concurrent=3)
    s = scheduler.stats()
    assert s["in_flight"] == 0 and s["queued"] == 0
    assert s["max_concurrent"] == 3 and s["active"] == []


def test_engine_exception_logged(caplog):
    """后台任务异常应落日志（可线上排错），而非纯静默。"""
    import logging
    engine = MagicMock()
    engine.run.side_effect = RuntimeError("评审炸了")
    scheduler = Scheduler(engine)
    with caplog.at_level(logging.WARNING):
        scheduler.submit("https://github.com/a/b/pull/9")
        scheduler.wait_all(timeout=5)
    assert any("后台评审任务异常" in r.message for r in caplog.records)
