from fastapi.testclient import TestClient

import app
import config


def _isolate(monkeypatch, tmp_path, file_cfg=None):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_file_cfg", dict(file_cfg or {}))
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY", "MIXER_AUTH_USER",
              "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH", "MIXER_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_config_gate_open_when_no_login(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    j = TestClient(app.fastapi_app).get("/api/config").json()
    assert j["configured"] is False
    assert j["login_required"] is False
    assert j["authed"] is True          # no login → open


def test_foods_503_without_session_or_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # no session, no MIXER_API_KEY → fail-closed
    assert TestClient(app.fastapi_app).get("/api/foods").status_code == 503


def test_categories_503_without_session_or_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # no session, no MIXER_API_KEY → fail-closed (mirrors /api/foods)
    assert TestClient(app.fastapi_app).get("/api/categories").status_code == 503


def test_set_config_via_api(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = TestClient(app.fastapi_app)
    r = c.post("/api/config", json={"mealie_url": "http://m:9925",
                                    "mealie_token": "t", "ai_key": "k"})
    assert r.status_code == 200 and r.json()["configured"] is True
    assert config.get("MEALIE_URL") == "http://m:9925"


def test_recipe_image_requires_auth(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)  # no session, no MIXER_API_KEY → fail-closed
    r = TestClient(app.fastapi_app).put(
        "/api/recipe-image/x", files={"file": ("a.jpg", b"x", "image/jpeg")}
    )
    assert r.status_code in (401, 503)


def test_config_reports_env_pinned(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, {"MEALIE_URL": "http://m", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"})
    monkeypatch.setenv("MIXER_API_KEY", "from-env")   # pinned by env → overrides Settings
    j = TestClient(app.fastapi_app).get("/api/config").json()
    assert "MIXER_API_KEY" in j.get("env_pinned", [])


def test_login_required_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, {
        "MIXER_AUTH_USER": "admin",
        "MIXER_AUTH_PASS_HASH": config.hash_password("secret"),
        "MEALIE_URL": "http://m", "MEALIE_TOKEN": "t", "AI_API_KEY": "k",
    })
    c = TestClient(app.fastapi_app)
    j = c.get("/api/config").json()
    assert j["login_required"] is True and j["authed"] is False
    assert "mealie_url" not in j                      # not exposed pre-login
    assert c.post("/api/login", json={"username": "admin", "password": "no"}).status_code == 401
    assert c.post("/api/login", json={"username": "admin", "password": "secret"}).json()["ok"] is True
