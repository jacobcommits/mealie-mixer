import config


def test_precedence_env_beats_file_beats_default(monkeypatch):
    monkeypatch.setattr(config, "_file_cfg", {"MEALIE_URL": "http://file:9925"})
    monkeypatch.delenv("MEALIE_URL", raising=False)
    assert config.get("MEALIE_URL") == "http://file:9925"          # file used

    monkeypatch.setenv("MEALIE_URL", "http://env:9925")
    assert config.get("MEALIE_URL") == "http://env:9925"           # env wins

    monkeypatch.setattr(config, "_file_cfg", {})
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    assert config.get("AI_BASE_URL") == config.DEFAULTS["AI_BASE_URL"]  # default


def test_empty_is_treated_as_unset(monkeypatch):
    monkeypatch.setattr(config, "_file_cfg", {})
    monkeypatch.setenv("MEALIE_URL", "")
    assert config.get("MEALIE_URL") == ""  # default for MEALIE_URL is ""


def test_save_and_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_file_cfg", {})
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    assert config.is_configured() is False
    config.save({"MEALIE_URL": "http://saved:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"})
    assert config.get("MEALIE_URL") == "http://saved:9925"
    assert config.is_configured() is True


def test_password_hashing():
    h = config.hash_password("hunter2")
    assert h.startswith("pbkdf2_sha256$")
    assert config.verify_password("hunter2", h) is True
    assert config.verify_password("wrong", h) is False
    assert config.verify_password("x", "not-a-hash") is False
