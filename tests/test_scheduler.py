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
