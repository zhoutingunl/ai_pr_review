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
        self.models_ = models if models is not None else CONFIG.models
        # 无进展上限（秒）：距上次新 token 超过此值仍无新输出 = 卡死，故障转移。
        # 只要模型在持续吐字就不会触发——即便总时长很长也照常进行。
        # 首 token 前 last_progress=start，故该值也兼作「首 token 最长等待」。
        self.no_progress_timeout_ = int(CONFIG.get("ai_no_progress_timeout", 420))
        # 连接静默上限（秒）：read timeout，超过此值无任何字节（含心跳）判断断流
        self.stall_timeout_ = int(CONFIG.get("ai_stall_timeout", 90))
        # 总时长硬上限（秒）：仅防跑飞兜底，设得很大；正常不应由它触发
        self.timeout_ = int(CONFIG.get("ai_timeout", 3600))

    # ---------- 对外能力 ----------

    def summarize(self, prompt: str, task_id: int | None = None,
                  on_token=None) -> str:
        """PR 总结 / Commit 总结。"""
        return self._ask_with_fallback("summary", prompt, task_id, on_token)

    def review(self, prompt: str, task_id: int | None = None,
               on_token=None) -> str:
        """Review / 风险分析。"""
        return self._ask_with_fallback("review", prompt, task_id, on_token)

    def generate_fix(self, prompt: str, task_id: int | None = None,
                     on_token=None) -> str:
        """自动修复建议（Patch + Commit 建议）。"""
        return self._ask_with_fallback("fix", prompt, task_id, on_token)

    # ---------- 模型调度 ----------

    def _role_models(self, role: str) -> list[str]:
        cfg = self.models_.get(role, {})
        chain = [cfg.get("primary")] + list(cfg.get("fallback", []))
        return [m for m in chain if m]

    def _ask_with_fallback(self, role: str, prompt: str,
                           task_id: int | None = None, on_token=None) -> str:
        """按 fallback 链依次尝试；429/卡死/异常切下一个模型（跨 plan）。

        on_token(text, model): 每个增量 token 回调，用于把模型输出实时推到界面。
        切换模型时上层可据此重置已展示的片段。
        """
        chain = self._role_models(role)
        if not chain:
            raise AIError(f"角色 {role} 未配置模型")
        last_error: Exception | None = None
        for model in chain:
            started = time.time()
            try:
                text, usage = self._ask_once(prompt, model, on_token)
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

    def _ask_once(self, prompt: str, model: str,
                  on_token=None) -> tuple[str, dict]:
        """单模型一轮对话：session/new -> chat/start -> SSE 读流。

        返回 (全文, {input_tokens, output_tokens})。
        on_token(text, model): 增量 token 回调。
        """
        session_id = self._post("/api/session/new",
                                {"model": model})["session"]["session_id"]
        if on_token:
            # 通知上层：新模型开始（用于重置界面上的流式片段）
            try:
                on_token(None, model)
            except Exception:  # noqa: BLE001 - 回调异常不影响主流程
                pass
        try:
            stream_id = self._post(
                "/api/chat/start",
                {"session_id": session_id, "message": prompt, "model": model},
            )["stream_id"]
            return self._read_stream(session_id, stream_id, model, on_token)
        except Exception:
            self._cancel(session_id)
            raise

    def _read_stream(self, session_id: str, stream_id: str, model: str = "",
                     on_token=None) -> tuple[str, dict]:
        """读取 SSE 流。

        看门狗策略（务必看 hermes_webui 踩坑记录）：
        Hermes 在模型「思考」阶段会每 2~3 秒发一个空行心跳，连接其实活着，
        只是推理模型首 token 延迟可达 100~300 秒，且大 PR 输出可能持续很久。
        因此只在「真卡死」时才干预，正在持续吐字的模型不限时长：
          * 无进展看门狗：以「距上次新 token」为准。持续输出 -> 一直不触发；
            心跳不断却长时间(no_progress_timeout)无任何新 token -> 判卡死并转移
            （首 token 前以会话开始计时，故也兼作首 token 最长等待）；
          * 连接静默：完全收不到字节(含心跳) stall_timeout 秒 -> read timeout 断流；
          * 总时长：极大的硬上限，仅防跑飞兜底，正常不触发。
        """
        full, usage, event = [], {}, ""
        start = time.time()
        hard_cap = start + self.timeout_
        last_progress = start   # 上次「新 token」时间；首 token 前即会话开始时间
        got_token = False
        # 连接静默（stall_timeout 内无任何字节，含心跳）由 requests 读超时负责，
        # 触发 ReadTimeout -> 上层捕获 -> 作废会话并故障转移
        with requests.get(f"{self.base_}/api/chat/stream",
                          params={"stream_id": stream_id},
                          stream=True,
                          timeout=(15, self.stall_timeout_)) as resp:
            if resp.status_code == 429:
                raise AIRateLimited("429 限流: chat/stream")
            resp.raise_for_status()
            for raw in resp.iter_lines():
                now = time.time()
                if now > hard_cap:
                    raise AIError("AI 调用超时（总时长硬上限，疑似跑飞）")
                # 心跳在到达（连接活着）但长时间没有新 token：判卡死，故障转移
                if now - last_progress > self.no_progress_timeout_:
                    raise AIError(
                        f"AI 调用卡死（{self.no_progress_timeout_}秒无新输出）")
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
                    got_token = True
                    last_progress = now   # 有新输出，刷新无进展计时
                    text = data.get("text", "")
                    full.append(text)
                    if on_token and text:
                        try:
                            on_token(text, model)
                        except Exception:  # noqa: BLE001 - 回调异常不影响主流程
                            pass
                elif event == "tool":
                    got_token = True
                    last_progress = now
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
