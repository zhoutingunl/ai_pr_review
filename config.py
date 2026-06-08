"""全局配置。

加载顺序（后者覆盖前者）：
    内置默认值  <-  config.json  <-  .env / 环境变量

敏感信息（GITHUB_TOKEN / WEBHOOK_SECRET 等）只允许放 .env 或环境变量，
禁止写入代码与 git 仓库。
"""
from __future__ import annotations

import json
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 内置默认值
_DEFAULTS = {
    # Web 服务
    "host": "0.0.0.0",
    "port": 38001,

    # 数据库
    "db_path": os.path.join(BASE_DIR, "data", "review.db"),

    # Hermes AI 统一入口
    "hermes_base": "http://10.210.32.30:8787",

    # 多模型架构：角色 -> 模型；fallback 为 429 限流时的跨 plan 故障转移链
    # 注：MiniMax-M3 属 minimax-cn plan，glm-5.1 / kimi-k2.5 属 ark plan
    "models": {
        "review": {"primary": "glm-5.1", "fallback": ["kimi-k2.5", "MiniMax-M3"]},
        "summary": {"primary": "kimi-k2.5", "fallback": ["glm-5.1", "MiniMax-M3"]},
        "fix": {"primary": "kimi-k2.5", "fallback": ["glm-5.1", "MiniMax-M3"]},
    },

    # AI 调用控制（看门狗策略见 ai_service._read_stream 注释）
    # 只在「真卡死」时干预：正在持续吐字的模型不限时长。
    "ai_no_progress_timeout": 420,  # 距上次新 token 超此值仍无新输出 = 卡死，换模型
    "ai_stall_timeout": 90,         # 连接静默上限（秒）：read timeout，无任何字节即断流
    "ai_timeout": 3600,             # 总时长硬上限（秒）：极大，仅防跑飞兜底

    # 误报控制
    "confidence_threshold": 0.70,

    # 风险评分权重
    "score_weights": {
        "security": 0.4,
        "reliability": 0.3,
        "performance": 0.2,
        "style": 0.1,
    },

    # 上下文构建
    "context_max_related_files": 8,    # 二级上下文最多拉取的关联文件数
    "context_max_file_bytes": 60000,   # 单文件最大读取字节数
    "context_history_reviews": 20,     # 四级上下文最多拉取的历史评论数
}


def _load_env_file(path: str) -> None:
    """轻量 .env 加载（KEY=VALUE，# 开头为注释），不覆盖已存在的环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """配置对象：合并默认值、config.json 与环境变量。"""

    def __init__(self, config_path: str | None = None, env_path: str | None = None):
        _load_env_file(env_path or os.path.join(BASE_DIR, ".env"))

        data = dict(_DEFAULTS)
        path = config_path or os.path.join(BASE_DIR, "config.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = _deep_merge(data, json.load(f))

        self.data_ = data

        # 环境变量覆盖
        self.hermes_base_ = os.environ.get("HERMES_BASE", data["hermes_base"]).rstrip("/")
        self.webhook_secret_ = os.environ.get("WEBHOOK_SECRET", "")
        self.github_token_ = self.resolve_github_token_()

    def get(self, key: str, default=None):
        return self.data_.get(key, default)

    def resolve_github_token_(self) -> str:
        """GitHub 令牌：优先 .env / 环境变量，缺省回落 `gh auth token`。"""
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            return token
        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ""

    @property
    def host(self) -> str:
        return self.data_["host"]

    @property
    def port(self) -> int:
        return int(self.data_["port"])

    @property
    def db_path(self) -> str:
        return self.data_["db_path"]

    @property
    def models(self) -> dict:
        return self.data_["models"]

    @property
    def confidence_threshold(self) -> float:
        return float(self.data_["confidence_threshold"])

    @property
    def score_weights(self) -> dict:
        return self.data_["score_weights"]


# 模块级单例：业务代码统一 `from config import CONFIG`
CONFIG = Config()
