import pytest

import config
import core


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_file_cfg", {})
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY", "MIXER_AUTH_USER",
              "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH", "MIXER_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_normalize_url():
    assert core.normalize_url("http://http://10.0.10.149:9925/") == "http://10.0.10.149:9925/"
    assert core.normalize_url("10.0.10.149:9925") == "http://10.0.10.149:9925"
    assert core.normalize_url("  http://x  ") == "http://x"
    assert core.normalize_url("") == ""


def test_generate_api_key():
    a, b = core.generate_api_key(), core.generate_api_key()
    assert len(a) >= 32 and a.isascii() and a != b


def test_apply_config_keeps_blank_secrets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    core.apply_config(mealie_url="http://m:9925", mealie_token="tok", ai_key="key",
                      ai_base="", ai_model="", auth_user="", auth_pass="", api_key="API")
    core.apply_config(mealie_url="http://m:9925", mealie_token="", ai_key="",
                      ai_base="", ai_model="", auth_user="", auth_pass="", api_key="")
    assert config.get("MEALIE_TOKEN") == "tok"
    assert config.get("AI_API_KEY") == "key"
    assert config.get("MIXER_API_KEY") == "API"


def test_apply_config_normalizes_and_requires(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    core.apply_config(mealie_url="http://http://m:9925/", mealie_token="t", ai_key="k",
                      ai_base="", ai_model="", auth_user="", auth_pass="", api_key="")
    assert config.get("MEALIE_URL") == "http://m:9925/"
    with pytest.raises(core.ConfigError):  # blank token+key, nothing stored
        config._file_cfg = {}
        core.apply_config(mealie_url="http://m:9925", mealie_token="", ai_key="",
                          ai_base="", ai_model="", auth_user="", auth_pass="", api_key="")
