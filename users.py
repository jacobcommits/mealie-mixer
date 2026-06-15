"""
Mealie Mixer — multi-user account store (v0.15.0).

SQLite in /data (users.db). Replaces the single MIXER_AUTH_USER/PASS login with
per-user accounts; passwords hashed with config.hash_password (PBKDF2). The first
account is the admin — bootstrapped from the legacy MIXER_AUTH_USER on upgrade, or
created via the setup page on a fresh install. Family members are added/removed by
an admin via the in-app Users screen.

Single-user / LAN scope (like the rest of the app): a tiny store, one connection
per call.
"""

import os
import sqlite3
from datetime import UTC, datetime

import config


def _db_path() -> str:
    """users.db on the persistent volume, next to config.json + history.db."""
    return os.path.join(config.DATA_DIR, "users.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            pass_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )"""
    )
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def count() -> int:
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def login_required() -> bool:
    """True once at least one user exists, OR a legacy single-user login is set in
    config (MIXER_AUTH_USER). Empty store + no legacy = first-run / open state, so a
    fresh install can create its first admin without a login. The legacy fallback
    keeps existing single-user deploys working even before bootstrap runs."""
    if count() > 0:
        return True
    return bool((config.get("MIXER_AUTH_USER") or "").strip())


def get(username: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list[dict]:
    """All accounts (no secret values) — for the admin screen."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT username, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password: str, is_admin: bool = False) -> tuple[bool, str]:
    """Create a user. Returns (ok, message). Username ≥ 2 chars and unique
    (case-insensitive); password non-empty."""
    username = (username or "").strip()
    if len(username) < 2:
        return False, "Username must be at least 2 characters."
    if not (password or "").strip():
        return False, "Password can't be empty."
    if get(username):
        return False, f"A user named '{username}' already exists."
    conn = _conn()
    try:
        with conn:  # commits
            conn.execute(
                "INSERT INTO users (username, pass_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (username, config.hash_password(password), 1 if is_admin else 0, _now()),
            )
    finally:
        conn.close()
    return True, f"Created user '{username}'" + (" (admin)." if is_admin else ".")


def verify(username: str, password: str) -> dict | None:
    """Return {username, is_admin} on a match, else None. Constant-time via
    config.verify_password (hmac.compare_digest under the hood).

    Legacy fallback: if the store is empty and a single MIXER_AUTH_USER is set in
    config, honor it — so pre-multi-user deploys keep logging in even before the
    one-time bootstrap has migrated that user into the store.
    """
    user = get(username)
    if user:
        if config.verify_password(password or "", user["pass_hash"]):
            return {"username": user["username"], "is_admin": bool(user["is_admin"])}
        return None
    if count() == 0:
        legacy = (config.get("MIXER_AUTH_USER") or "").strip()
        legacy_hash = (config.get("MIXER_AUTH_PASS_HASH") or "").strip()
        if legacy and username == legacy and config.verify_password(password or "", legacy_hash):
            return {"username": legacy, "is_admin": True}
    return None


def set_password(username: str, password: str) -> tuple[bool, str]:
    if not (password or "").strip():
        return False, "Password can't be empty."
    if not get(username):
        return False, f"No user named '{username}'."
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET pass_hash = ? WHERE username = ?",
                (config.hash_password(password), username),
            )
    finally:
        conn.close()
    return True, f"Reset password for '{username}'."


def _admin_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]


def delete_user(username: str) -> tuple[bool, str]:
    """Delete a user. Guard: can't delete the last admin (would lock everyone out)."""
    user = get(username)
    if not user:
        return False, f"No user named '{username}'."
    conn = _conn()
    try:
        if user["is_admin"] and _admin_count(conn) <= 1:
            return False, "Can't delete the last admin — promote another user first."
        with conn:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
    finally:
        conn.close()
    return True, f"Deleted user '{username}'."


def set_admin(username: str, is_admin: bool) -> tuple[bool, str]:
    """Promote/demote. Guard: can't un-admin the last admin."""
    user = get(username)
    if not user:
        return False, f"No user named '{username}'."
    if bool(user["is_admin"]) == is_admin:
        return True, f"'{username}' is already {'an admin' if is_admin else 'not an admin'}."
    conn = _conn()
    try:
        if not is_admin and user["is_admin"] and _admin_count(conn) <= 1:
            return False, "Can't remove the last admin."
        with conn:
            conn.execute(
                "UPDATE users SET is_admin = ? WHERE username = ?",
                (1 if is_admin else 0, username),
            )
    finally:
        conn.close()
    return True, ("Promoted" if is_admin else "Demoted") + f" '{username}'."


def ensure_bootstrap() -> None:
    """One-time migration: if the store is empty, seed the first admin from the
    legacy single-user login (MIXER_AUTH_USER + MIXER_AUTH_PASS_HASH, or the legacy
    plaintext MIXER_AUTH_PASS) so an upgrading deploy keeps its login. No-op once any
    user exists. Safe to call on every startup."""
    if count() > 0:
        return
    legacy_user = (config.get("MIXER_AUTH_USER") or "").strip()
    if not legacy_user:
        return
    legacy_hash = (config.get("MIXER_AUTH_PASS_HASH") or "").strip()
    legacy_plain = (config.get("MIXER_AUTH_PASS") or "").strip()
    if legacy_hash:
        # preserve the existing hash verbatim (it's already pbkdf2_sha256$…)
        conn = _conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO users (username, pass_hash, is_admin, created_at) VALUES (?, ?, 1, ?)",
                    (legacy_user, legacy_hash, _now()),
                )
        finally:
            conn.close()
    elif legacy_plain:
        create_user(legacy_user, legacy_plain, is_admin=True)
