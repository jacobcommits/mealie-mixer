"""Tests for the multi-user account store (users.py)."""

import users


def _isolated(monkeypatch, tmp_path):
    """Point the store at a per-test data dir + clear legacy auth env/keys."""
    monkeypatch.setattr(users.config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(users.config, "_file_cfg", {})
    for k in ("MIXER_AUTH_USER", "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH"):
        monkeypatch.delenv(k, raising=False)


class TestCreateVerify:
    def test_create_and_verify(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        ok, _ = users.create_user("alice", "secret", is_admin=True)
        assert ok
        assert users.verify("alice", "secret") == {"username": "alice", "display_name": "", "is_admin": True}

    def test_verify_wrong_password(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "secret")
        assert users.verify("alice", "wrong") is None

    def test_verify_unknown_user(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        assert users.verify("nobody", "x") is None

    def test_username_case_insensitive(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("Alice", "secret")
        # login is case-insensitive (collation) — 'alice' matches 'Alice'
        assert users.verify("alice", "secret") is not None
        # duplicate (different case) is rejected
        ok, _ = users.create_user("ALICE", "other")
        assert not ok

    def test_rejects_short_username_and_empty_password(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        assert not users.create_user("a", "secret")[0]
        assert not users.create_user("bob", "")[0]

    def test_login_required_flag(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        assert users.login_required() is False       # empty store = open / first-run
        users.create_user("alice", "x")
        assert users.login_required() is True


class TestPasswordAndAdmin:
    def test_set_password(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "old")
        assert users.verify("alice", "old") is not None
        ok, _ = users.set_password("alice", "new")
        assert ok
        assert users.verify("alice", "old") is None
        assert users.verify("alice", "new") is not None

    def test_set_password_unknown_user(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        assert not users.set_password("ghost", "x")[0]

    def test_promote_demote(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "x", is_admin=True)
        users.create_user("bob", "x")
        assert users.set_admin("bob", True)[0]
        assert users.get("bob")["is_admin"] == 1
        # now alice (still admin) can be demoted since bob is admin
        assert users.set_admin("alice", False)[0]
        assert users.get("alice")["is_admin"] == 0

    def test_cannot_remove_last_admin(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "x", is_admin=True)
        ok, msg = users.set_admin("alice", False)
        assert not ok and "last admin" in msg.lower()
        ok, msg = users.delete_user("alice")
        assert not ok and "last admin" in msg.lower()

    def test_delete(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "x", is_admin=True)
        users.create_user("bob", "x")
        assert users.delete_user("bob")[0]
        assert users.get("bob") is None
        # deleting a non-user fails cleanly
        assert not users.delete_user("ghost")[0]


class TestListUsers:
    def test_list_excludes_secret_values(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "secret", is_admin=True)
        rows = users.list_users()
        assert len(rows) == 1
        assert rows[0]["username"] == "alice"
        assert "pass_hash" not in rows[0]


class TestLegacyFallback:
    """An empty store still honors the old single MIXER_AUTH_USER config, so
    pre-multi-user deploys (and the legacy session tests) keep logging in."""

    def test_login_required_via_legacy_config(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        monkeypatch.setattr(users.config, "_file_cfg", {"MIXER_AUTH_USER": "keeper"})
        assert users.login_required() is True

    def test_verify_falls_back_to_legacy(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        h = users.config.hash_password("oldpw")
        monkeypatch.setattr(users.config, "_file_cfg",
                            {"MIXER_AUTH_USER": "keeper", "MIXER_AUTH_PASS_HASH": h})
        assert users.verify("keeper", "oldpw") == {"username": "keeper", "display_name": "", "is_admin": True}
        assert users.verify("keeper", "wrong") is None

    def test_fallback_only_when_store_empty(self, monkeypatch, tmp_path):
        # once a real user exists, the legacy config is ignored
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "newpw", is_admin=True)
        monkeypatch.setattr(users.config, "_file_cfg",
                            {"MIXER_AUTH_USER": "keeper", "MIXER_AUTH_PASS_HASH": "whatever"})
        assert users.verify("keeper", "whatever") is None
        assert users.verify("alice", "newpw") is not None


class TestDisplayName:
    def test_create_and_update_display_name(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        ok, _ = users.create_user("alice", "secret", is_admin=True, display_name="Alice Smith")
        assert ok
        assert users.get("alice")["display_name"] == "Alice Smith"
        rows = users.list_users()
        assert rows[0]["display_name"] == "Alice Smith"

        ok, _ = users.set_display_name("alice", "Mama Alice")
        assert ok
        assert users.get("alice")["display_name"] == "Mama Alice"


class TestBootstrap:
    def test_seeds_from_legacy_hash(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        h = users.config.hash_password("legacy-pw")
        monkeypatch.setattr(users.config, "_file_cfg",
                            {"MIXER_AUTH_USER": "keeper", "MIXER_AUTH_PASS_HASH": h})
        users.ensure_bootstrap()
        # first user = the legacy one, as admin, password preserved
        assert users.verify("keeper", "legacy-pw") == {"username": "keeper", "display_name": "", "is_admin": True}
        assert users.login_required() is True

    def test_seeds_from_legacy_plaintext(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        monkeypatch.setattr(users.config, "_file_cfg",
                            {"MIXER_AUTH_USER": "keeper", "MIXER_AUTH_PASS": "plain"})
        users.ensure_bootstrap()
        assert users.verify("keeper", "plain") is not None

    def test_noop_when_store_nonempty(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.create_user("alice", "x", is_admin=True)
        monkeypatch.setattr(users.config, "_file_cfg",
                            {"MIXER_AUTH_USER": "keeper", "MIXER_AUTH_PASS_HASH": "anything"})
        users.ensure_bootstrap()
        assert users.count() == 1                      # didn't seed a second user
        assert users.get("keeper") is None

    def test_noop_without_legacy(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        users.ensure_bootstrap()
        assert users.count() == 0
        assert users.login_required() is False
