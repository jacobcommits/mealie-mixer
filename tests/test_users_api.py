"""End-to-end tests for the user-management endpoints (multi-user, v0.15.0)."""

from fastapi.testclient import TestClient

import app
import config
import users


def _isolate(monkeypatch, tmp_path):
    """Fresh config + empty users store per test (tmp_path DATA_DIR)."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_file_cfg", {})
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY", "MIXER_AUTH_USER",
              "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH", "MIXER_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _client():
    return TestClient(app.fastapi_app)


def test_first_admin_bootstrap_then_admin_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    # open state: no users → login not required
    assert c.get("/api/config").json()["login_required"] is False
    # create the first admin via the first-run endpoint (works while open)
    r = c.post("/api/users/first", json={"username": "keeper", "password": "pw"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert users.login_required() is True
    # not logged in → admin endpoints are forbidden (403)
    assert c.get("/api/users").status_code == 403
    # log in as keeper → is_admin reflected
    assert c.post("/api/login", json={"username": "keeper", "password": "pw"}).json()["is_admin"] is True
    # now authed: first-admin endpoint refuses with 409 (accounts already exist)
    assert c.post("/api/users/first", json={"username": "x", "password": "y"}).status_code == 409
    # admin list works
    r = c.get("/api/users")
    assert r.status_code == 200
    assert [u["username"] for u in r.json()["users"]] == ["keeper"]
    assert "pass_hash" not in str(r.json())  # never expose hashes


def test_admin_creates_deletes_users_and_guards(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    users.create_user("keeper", "pw", is_admin=True)
    c = _client()
    c.post("/api/login", json={"username": "keeper", "password": "pw"})
    # create a family member
    r = c.post("/api/users", json={"username": "bob", "password": "bpw"})
    assert r.status_code == 200
    assert "bob" in [u["username"] for u in r.json()["users"]]
    # duplicate username rejected
    assert c.post("/api/users", json={"username": "bob", "password": "x"}).status_code == 400
    # promote bob to admin
    assert c.post("/api/users/bob/admin", json={"is_admin": True}).status_code == 200
    # self-delete refused
    assert c.delete("/api/users/keeper").status_code == 400
    # delete bob (admin, but keeper is also admin → allowed)
    assert c.delete("/api/users/bob").status_code == 200
    # last-admin guard: can't delete the only remaining admin
    assert c.delete("/api/users/keeper").status_code == 400


def test_non_admin_is_forbidden(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    users.create_user("keeper", "pw", is_admin=True)
    users.create_user("bob", "bpw", is_admin=False)
    c = _client()
    c.post("/api/login", json={"username": "bob", "password": "bpw"})
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/users", json={"username": "x", "password": "y"}).status_code == 403
    # bob can still use normal endpoints (push etc. gated by require_access)
    assert c.get("/api/config").status_code == 200


def test_reset_password(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    users.create_user("keeper", "pw", is_admin=True)
    users.create_user("bob", "bpw")
    c = _client()
    c.post("/api/login", json={"username": "keeper", "password": "pw"})
    assert c.post("/api/users/bob/password", json={"password": "newpw"}).status_code == 200
    assert users.verify("bob", "newpw") is not None
    assert users.verify("bob", "bpw") is None


def test_demoting_last_admin_refused(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    users.create_user("keeper", "pw", is_admin=True)
    c = _client()
    c.post("/api/login", json={"username": "keeper", "password": "pw"})
    assert c.post("/api/users/keeper/admin", json={"is_admin": False}).status_code == 400


def test_self_service_account_updates(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    users.create_user("bob", "bpw", is_admin=False, display_name="Bobby")
    c = _client()
    # log in as standard user bob
    l = c.post("/api/login", json={"username": "bob", "password": "bpw"})
    assert l.status_code == 200
    assert l.json()["display_name"] == "Bobby"

    # bob changes his display name
    r1 = c.post("/api/users/me/display-name", json={"display_name": "Robert"})
    assert r1.status_code == 200
    assert r1.json()["display_name"] == "Robert"
    assert c.get("/api/config").json()["display_name"] == "Robert"

    # bob changes his own password
    r2 = c.post("/api/users/me/password", json={"password": "newbpw"})
    assert r2.status_code == 200
    assert users.verify("bob", "newbpw") is not None
