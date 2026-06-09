"""config.py 测试。"""
import json
import os
import tempfile
from unittest.mock import patch

from config import Config, _deep_merge, _load_env_file


def test_example_config_mirrors_defaults():
    """config.example.json 必须与 _DEFAULTS 顶层字段保持一致（防漂移）。"""
    from config import _DEFAULTS, BASE_DIR
    with open(os.path.join(BASE_DIR, "config.example.json"),
              encoding="utf-8") as f:
        example = json.load(f)
    assert set(example.keys()) == set(_DEFAULTS.keys()), (
        "config.example.json 与 _DEFAULTS 字段不同步：缺 "
        f"{set(_DEFAULTS) - set(example)}，多 {set(example) - set(_DEFAULTS)}")


def test_deep_merge():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 9}, "e": 5}
    merged = _deep_merge(base, override)
    assert merged == {"a": 1, "b": {"c": 9, "d": 3}, "e": 5}
    assert base["b"]["c"] == 2  # 原对象不被污染


def test_load_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# 注释\nTEST_KEY_X=hello\nBAD_LINE\nQUOTED='v1'\n")
    _load_env_file(str(env))
    assert os.environ.get("TEST_KEY_X") == "hello"
    assert os.environ.get("QUOTED") == "v1"
    os.environ.pop("TEST_KEY_X", None)
    os.environ.pop("QUOTED", None)


def test_load_env_file_missing():
    _load_env_file("/nonexistent/.env")  # 不应抛异常


def test_load_env_not_override():
    os.environ["TEST_KEY_Y"] = "origin"
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write("TEST_KEY_Y=new\n")
    _load_env_file(f.name)
    assert os.environ["TEST_KEY_Y"] == "origin"
    os.environ.pop("TEST_KEY_Y", None)
    os.unlink(f.name)


def test_config_defaults_and_json_override(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"port": 12345,
                                    "models": {"review": {"primary": "m-x"}}}))
    cfg = Config(config_path=str(cfg_file), env_path="/nonexistent/.env")
    assert cfg.port == 12345
    assert cfg.models["review"]["primary"] == "m-x"
    # 未覆盖的默认保留
    assert cfg.models["summary"]["primary"]
    assert cfg.host == "0.0.0.0"
    assert cfg.confidence_threshold == 0.70
    assert cfg.score_weights["security"] == 0.4
    assert cfg.get("不存在", "默认") == "默认"
    assert cfg.db_path.endswith("review.db")


def test_github_token_from_env(tmp_path):
    with patch.dict(os.environ, {"GITHUB_TOKEN": "tok123"}):
        cfg = Config(config_path="/nonexistent", env_path="/nonexistent")
        assert cfg.github_token_ == "tok123"


def test_github_token_gh_fallback():
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        with patch("config.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "gho_xyz\n"
            cfg = Config(config_path="/nonexistent", env_path="/nonexistent")
            assert cfg.github_token_ == "gho_xyz"


def test_github_token_gh_missing():
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        with patch("config.subprocess.run", side_effect=OSError):
            cfg = Config(config_path="/nonexistent", env_path="/nonexistent")
            assert cfg.github_token_ == ""


def test_github_token_gh_nonzero():
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        with patch("config.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            cfg = Config(config_path="/nonexistent", env_path="/nonexistent")
            assert cfg.github_token_ == ""
