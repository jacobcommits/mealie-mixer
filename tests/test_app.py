import gradio as gr
import pytest

import app
import config


def test_parse_qty():
    assert app._parse_qty("") is None
    assert app._parse_qty("0") is None      # 0 = no amount
    assert app._parse_qty(None) is None
    assert app._parse_qty("garbage") is None
    assert app._parse_qty("0.5") == 0.5
    assert app._parse_qty("1,5") == 1.5     # comma decimal
    assert app._parse_qty("13") == 13.0


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_file_cfg", {})
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY",
              "MIXER_AUTH_USER", "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH"):
        monkeypatch.delenv(k, raising=False)


def test_apply_config_keeps_blank_secrets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    app._apply_config("http://m:9925", "tok-ORIG", "key-ORIG", "", "", "", "")
    app._apply_config("http://m:9925", "", "", "", "", "", "")  # blank secrets
    assert config.get("MEALIE_TOKEN") == "tok-ORIG"
    assert config.get("AI_API_KEY") == "key-ORIG"


def test_apply_config_requires_when_nothing_stored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(gr.Error):
        app._apply_config("http://m:9925", "", "", "", "", "", "")
