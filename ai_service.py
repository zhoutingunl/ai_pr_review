"""AI 统一入口（Hermes）。

所有 AI 能力统一通过本模块访问，禁止业务代码直接访问模型。

多模型架构（config.json 可调）：
    review  -> glm-5.1      Review / 风险分析
    summary -> kimi-k2.5    PR 总结 / Commit 总结
    fix     -> kimi-k2.5    自动修复建议

可靠性策略（对应 hermes_webui 技能踩坑记录）：
    * 纯文本问答：上下文由 ContextBuilder 构建后拼 prompt，不依赖 Hermes 工具
    * 双闸超时：总时长预算 + 首事件看门狗
    * approval 事件就地批准（纯文本问答一般不触发，兜底）
    * 429 限流：跨 plan 故障转移（fallback 链）
    * 失败后作废脏会话（chat/cancel），不复用 session_id
"""
from __future__ import annotations

import json
import re
import time

import requests

from config import CONFIG


class AIError(Exception):
    """AI 调用失败（含所有 fallback 模型耗尽）。"""


class AIRateLimited(AIError):
    """单一模型被限流（429）。"""


def extract_json(text: str):
    """从模型输出提取 JSON（容错：裸 JSON / ```json 代码块 / 前后缀噪声）。"""
    text = text.strip()
    # 优先取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退化：截取首个 { 或 [ 到最后一个 } 或 ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise AIError(f"模型输出无法解析为 JSON: {text[:200]}")


class AIService:

    def __init__(self, base: str | None = None, store=None,
                 models: dict | None = None):
        self.base_ = (base or CONFIG.hermes_base_).rstrip("/")
        self.store_ = store
        self.models_ = models or CONFIG.models
        self.timeout_ = int(CONFIG.get("ai_timeout", 300))
        self.first_event_timeout_ = int(CONFIG.get("ai_first_event_timeout", 60))

    # ---------- 对外能力 ----------

    def summarize(self, prompt: str, task_id: int | None = None) -> str:
        """PR 总结 / Commit 总结。"""
        return self._ask_with_fallback("summary", prompt, task_id)

    def review(self, prompt: str, task_id: int | None = None) -> str:
        """Review / 风险分析。"""
        return self._ask_with_fallback("review", prompt, task_id)

    def generate_fix(self, prompt: str, task_id: int | None = None) -> str:
        """自动修复建议（Patch + Commit 建议）。"""
        return self._ask_with_fallback("fix", prompt, task_id)

    # ---------- 模型调度 ----------

    def _role_models(self, role: str) -> list[str]:
        cfg = self.models_.get(role, {})
        chain = [cfg.get("primary")] + list(cfg.get("fallback", []))
        return [m for m in chain if m]

    def _ask_with_fallback(self, role: str, prompt: str,
                           task_id: int | None = None) -> str:
        """按 fallback 链依次尝试；429/卡死/异常切下一个模型（跨 plan）。"""
        chain = self._role_models(role)
        if not chain:
            raise AIError(f"角色 {role} 未配置模型")
        last_error: Exception | None = None
        for model in chain:
            started = time.time()
            try:
                text, usage = self._ask_once(prompt, model)
                self._record(role, model, True, started, usage, task_id)
                return text
            except Exception as e:  # noqa: BLE001 - 任一失败都尝试下一模型
                self._record(role, model, False, started, None, task_id, str(e))
                last_error = e
        raise AIError(f"角色 {role} 所有模型均失败: {last_error}") from last_error

    def _record(self, role: str, model: str, success: bool, started: float,
                usage: dict | None, task_id: int | None,
                error: str | None = None) -> None:
        if self.store_:
            usage = usage or {}
            self.store_.record_ai_metric(
                role, model, success, int((time.time() - started) * 1000),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                task_id=task_id, error=error)

    # ---------- Hermes 单轮对话 ----------

    def _post(self, path: str, payload: dict, timeout: int = 30) -> dict:
        resp = requests.post(f"{self.base_}{path}", json=payload, timeout=timeout)
        if resp.status_code == 429:
            raise AIRateLimited(f"429 限流: {path}")
        resp.raise_for_status()
        return resp.json()

    def _cancel(self, session_id: str) -> None:
        """作废脏会话，避免 409 连锁。"""
        try:
            requests.post(f"{self.base_}/api/chat/cancel",
                          json={"session_id": session_id}, timeout=5)
        except requests.RequestException:
            pass

    def _ask_once(self, prompt: str, model: str) -> tuple[str, dict]:
        """单模型一轮对话：session/new -> chat/start -> SSE 读流。

        返回 (全文, {input_tokens, output_tokens})。
        """
        session_id = self._post("/api/session/new",
                                {"model": model})["session"]["session_id"]
        try:
            stream_id = self._post(
                "/api/chat/start",
                {"session_id": session_id, "message": prompt, "model": model},
            )["stream_id"]
            return self._read_stream(session_id, stream_id)
        except Exception:
            self._cancel(session_id)
            raise

    def _read_stream(self, session_id: str, stream_id: str) -> tuple[str, dict]:
        full, usage, event = [], {}, ""
        deadline = time.time() + self.timeout_
        first_event_deadline = time.time() + self.first_event_timeout_
        got_event = False
        with requests.get(f"{self.base_}/api/chat/stream",
                          params={"stream_id": stream_id},
                          stream=True, timeout=(15, 120)) as resp:
            if resp.status_code == 429:
                raise AIRateLimited("429 限流: chat/stream")
            resp.raise_for_status()
            for raw in resp.iter_lines():
                now = time.time()
                if now > deadline:
                    raise AIError("AI 调用超时（总时长预算耗尽）")
                if not got_event and now > first_event_deadline:
                    raise AIError("AI 调用卡死（首事件看门狗超时）")
                if not raw:
                    continue
                line = raw.decode("utf-8")
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event == "token":
                    got_event = True
                    full.append(data.get("text", ""))
                elif event == "tool":
                    got_event = True
                elif event == "approval":
                    # 纯文本问答一般不触发；兜底立即批准防卡死
                    try:
                        requests.post(f"{self.base_}/api/approval/respond",
                                      json={"session_id": session_id,
                                            "choice": "always"}, timeout=5)
                    except requests.RequestException:
                        pass
                elif event == "done":
                    s = data.get("session") or {}
                    usage = {"input_tokens": s.get("input_tokens", 0),
                             "output_tokens": s.get("output_tokens", 0)}
                    break
                elif event == "error":
                    message = str(data.get("message", ""))
                    if "429" in message or "rate" in message.lower():
                        raise AIRateLimited(f"429 限流: {message}")
                    raise AIError(f"Hermes 流错误: {message}")
        text = "".join(full).strip()
        if not text:
            raise AIError("模型返回空内容")
        # 正常收尾也 cancel，释放服务端会话
        self._cancel(session_id)
        return text, usage
