"""
Mealie Mixer — import history (B4).

A tiny SQLite log of what's been pushed to Mealie, kept on the /data volume so it
survives restarts/rebuilds (same place as config.json). Each successful push records
name / slug / source / timestamp / status. Powers the history screen, GET /api/history,
and the "already imported" dedupe warning.

Convenience only — NEVER on the critical path. Callers wrap log_import() in try/except
so a logging failure can't break a push.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime

import config


def _db_path() -> str:
    """history.db next to config.json on the persistent volume. Read live so the path
    follows config.DATA_DIR (and tests can monkeypatch config.DATA_DIR)."""
    return os.path.join(config.DATA_DIR, "history.db")


def _conn() -> sqlite3.Connection:
    """Open a short-lived connection and ensure the schema exists. One connection per
    call — FastAPI runs sync endpoints in a threadpool, so we don't share a handle."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            slug TEXT,
            source_url TEXT,
            mealie_url TEXT,
            status TEXT,
            created_at TEXT,
            payload TEXT
        )"""
    )
    # Migration: DBs created before the discard-restore feature lack `payload`.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(imports)").fetchall()]
    if "payload" not in cols:
        conn.execute("ALTER TABLE imports ADD COLUMN payload TEXT")
    return conn


def _normalize_source(url: str) -> str:
    """Trim + drop trailing slash(es) so the same link matches regardless of a stray /."""
    return (url or "").strip().rstrip("/")


def log_import(name, slug, source_url="", mealie_url="", status="success", payload=None) -> None:
    """Record one import. `payload` (the full review recipe) is stored as JSON for
    "discarded" entries so a misclick can be restored. Best-effort: callers swallow
    exceptions (a logging failure must never break a push)."""
    conn = _conn()
    try:
        with conn:  # commits the transaction
            conn.execute(
                "INSERT INTO imports (name, slug, source_url, mealie_url, status, created_at, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (name or "").strip(),
                    (slug or "").strip(),
                    _normalize_source(source_url),
                    (mealie_url or "").strip(),
                    status,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    json.dumps(payload) if payload is not None else None,
                ),
            )
    finally:
        conn.close()


def list_imports(limit: int = 500) -> list[dict]:
    """Recent imports, newest first, as plain dicts. Excludes the heavy `payload`
    (fetch a single entry's payload via get_import() when restoring)."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, name, slug, source_url, mealie_url, status, created_at"
            " FROM imports ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_import(item_id: int) -> dict | None:
    """One import by id, with its `payload` parsed back to a dict (or None). Used to
    restore a discarded recipe into the review screen."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name, slug, source_url, mealie_url, status, created_at, payload"
            " FROM imports WHERE id = ?",
            (item_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    raw = d.get("payload")
    try:
        d["payload"] = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        d["payload"] = None
    return d


def find_recent_by_source(source_url: str) -> dict | None:
    """Most recent SUCCESSFUL import whose source matches this URL (normalised), or
    None. Discarded entries don't count as "already imported". Blank URLs never match
    — image/text imports have no stable identity and aren't deduped."""
    key = _normalize_source(source_url)
    if not key:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name, slug, source_url, mealie_url, status, created_at"
            " FROM imports WHERE source_url = ? AND status = 'success'"
            " ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
