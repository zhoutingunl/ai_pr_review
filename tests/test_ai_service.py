"""ai_service.py 测试（mock Hermes HTTP/SSE）。"""
import json
import pytest
from unittest.mock import MagicMock, patch

from ai_service import AIError, AIRateLimited, AIService, extract_json


# ---------- extract_json ----------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_code_block():
    assert extract_json('说明\n```json\n[{"b": 2}]\n```\n尾巴') == [{"b": 2}]


def test_extract_json_embedded():
    assert extract_json('前缀 {"c": [1, 2]} 后缀') == {"c": [1, 2]}


def test_extract_json_array_embedded():
    assert extract_json('x [1, 2, 3] y') == [1, 2, 3]


def test_extract_json_invalid():
    with pytest.raises(AIError):
        extract_json("完全不是 JSON")


# ---------- SSE 流 ----------

class FakeStreamResp:
    def __init__(self, lines, status=200):
        self.lines_ = lines
        self.status_code = status

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for line in self.lines_:
            yield line.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def sse(event, data):
    return [f"event: {event}", f"data: {json.dumps(data)}", ""]


def make_service(store=None, models=None):
    return AIService(base="http://hermes.test", store=store,
                     models=models or {
                         "review": {"primary": "m1", "fallback": ["m2"]},
                         "summary": {"primary": "m1", "fallback": []},
                         "fix": {"primary": "m1", "fallback": []},
                     })


def ok_post(path_payloads):
    """构造 requests.post mock：session/new -> chat/start -> cancel。"""
    def _post(url, json=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/api/session/new"):
            resp.json.return_value = {"session": {"session_id": "s1"}}
        elif url.endswith("/api/chat/start"):
            resp.json.return_value = {"stream_id": "st1"}
        else:
            resp.json.return_value = {}
        return resp
    return _post


def test_ask_success(store):
    svc = make_service(store)
    lines = (sse("token", {"text": "你"}) + sse("token", {"text": "好"})
             + sse("tool", {"name": "grep"})
             + sse("done", {"session": {"input_tokens": 10,
                                        "output_tokens": 5}}))
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.review("问题") == "你好"
    metric = store.list_ai_metrics()[0]
    assert metric["success"] == 1 and metric["model"] == "m1"
    assert metric["input_tokens"] == 10 and metric["output_tokens"] == 5


def test_ask_fallback_on_429(store):
    svc = make_service(store)
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        resp = MagicMock()
        if url.endswith("/api/session/new"):
            calls["n"] += 1
            # 第一个模型限流，第二个正常
            resp.status_code = 429 if calls["n"] == 1 else 200
            resp.json.return_value = {"session": {"session_id": "s"}}
        else:
            resp.status_code = 200
            resp.json.return_value = {"stream_id": "st"}
        return resp

    lines = sse("token", {"text": "OK"}) + sse("done", {"session": {}})
    with patch("ai_service.requests.post", side_effect=post), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.review("q") == "OK"
    metrics = store.list_ai_metrics()
    assert len(metrics) == 2  # m1 失败 + m2 成功
    assert metrics[1]["success"] == 0 and "429" in metrics[1]["error"]
    assert metrics[0]["success"] == 1 and metrics[0]["model"] == "m2"


def test_ask_all_models_fail(store):
    svc = make_service(store)

    def post(url, **kw):
        resp = MagicMock()
        resp.status_code = 429
        return resp

    with patch("ai_service.requests.post", side_effect=post):
        with pytest.raises(AIError, match="所有模型均失败"):
            svc.summarize("q")


def test_stream_error_event():
    svc = make_service()
    lines = sse("error", {"message": "boom"})
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        with pytest.raises(AIError, match="所有模型均失败"):
            svc.summarize("q")


def test_stream_429_error_event():
    svc = make_service()
    lines = sse("error", {"message": "rate limited 429"})
    captured = []
    original = svc._ask_once

    def spy(prompt, model):
        try:
            return original(prompt, model)
        except Exception as e:
            captured.append(e)
            raise

    def spy2(prompt, model, on_token=None):
        return spy(prompt, model)

    svc._ask_once = spy2
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        with pytest.raises(AIError):
            svc.summarize("q")
    assert isinstance(captured[0], AIRateLimited)


def test_stream_empty_response():
    svc = make_service()
    lines = sse("done", {"session": {}})
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        with pytest.raises(AIError, match="所有模型均失败"):
            svc.summarize("q")


def test_stream_approval_event():
    svc = make_service()
    lines = (sse("approval", {}) + sse("token", {"text": "通过"})
             + sse("done", {"session": {}}))
    with patch("ai_service.requests.post", side_effect=ok_post(None)) as post, \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.summarize("q") == "通过"
        urls = [c.args[0] for c in post.call_args_list]
        assert any(u.endswith("/api/approval/respond") for u in urls)


def test_stream_garbage_data_skipped():
    svc = make_service()
    lines = (["event: token", "data: 不是JSON", ""]
             + sse("token", {"text": "好"}) + sse("done", {"session": {}}))
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.summarize("q") == "好"


def test_stream_deadline_exceeded():
    svc = make_service()
    svc.timeout_ = -1  # 立即超时
    lines = sse("token", {"text": "x"}) + sse("done", {"session": {}})
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        with pytest.raises(AIError, match="所有模型均失败"):
            svc.summarize("q")


def test_no_progress_watchdog_trips():
    svc = make_service()
    svc.no_progress_timeout_ = -1  # 任何无新 token 的间隔都判卡死
    lines = sse("done", {"session": {}})
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        with pytest.raises(AIError, match="所有模型均失败"):
            svc.summarize("q")


def test_streaming_long_output_not_killed():
    """持续吐字的模型即使 token 很多也不被无进展看门狗杀掉。"""
    svc = make_service()
    svc.no_progress_timeout_ = 0.5  # 很短，但每个 token 都刷新进展
    lines = []
    for i in range(50):
        lines += sse("token", {"text": f"t{i}"})
    lines += sse("done", {"session": {"input_tokens": 1, "output_tokens": 50}})
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        out = svc.summarize("q")
    assert out.startswith("t0") and out.endswith("t49")


def test_on_token_callback_receives_stream():
    svc = make_service()
    received = []
    lines = (sse("token", {"text": "你"}) + sse("token", {"text": "好"})
             + sse("done", {"session": {}}))
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        svc.summarize("q", on_token=lambda text, model: received.append((text, model)))
    # 首元素是模型开始信号 (None, model)，其后是增量 token
    assert received[0] == (None, "m1")
    assert ("你", "m1") in received and ("好", "m1") in received


def test_on_token_callback_exception_swallowed():
    svc = make_service()
    lines = sse("token", {"text": "x"}) + sse("done", {"session": {}})

    def boom(text, model):
        raise ValueError("回调炸了")

    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.summarize("q", on_token=boom) == "x"  # 回调异常不影响主流程


def test_heartbeat_blank_lines_do_not_trip_watchdog():
    """空行心跳不应触发看门狗：心跳穿插后仍能正常收到 token 与 done。"""
    svc = make_service()
    # 模拟思考阶段的若干空行心跳，随后才出 token
    lines = ([""] * 5 + sse("token", {"text": "终"})
             + [""] * 3 + sse("token", {"text": "于"})
             + sse("done", {"session": {"input_tokens": 1,
                                        "output_tokens": 2}}))
    with patch("ai_service.requests.post", side_effect=ok_post(None)), \
         patch("ai_service.requests.get",
               return_value=FakeStreamResp(lines)):
        assert svc.summarize("q") == "终于"


def test_role_not_configured():
    svc = AIService(base="http://x", models={})
    with pytest.raises(AIError, match="未配置模型"):
        svc.generate_fix("q")


def test_cancel_swallow_error():
    import requests as real_requests
    svc = make_service()
    with patch("ai_service.requests.post",
               side_effect=real_requests.ConnectionError("网络挂了")):
        svc._cancel("sid")  # 不应抛异常
