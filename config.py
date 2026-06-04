"""
Mealie Mixer — configuration layer.

Single source of truth for runtime config. Each key resolves in this order:
  1. Environment variable (including values loaded from a local .env)
  2. /data/config.json  (written by the in-app setup page — later stage)
  3. Built-in default

So existing .env / env-var deploys keep working unchanged — env always wins.
The JSON file is just a fallback the setup wizard writes to, on a persistent
volume, so containerised users can configure via the web UI instead of editing
files. Values are read live via get(), so a saved config can be picked up with
reload() without caring about import order.
"""

import base64
import hashlib
import hmac
import json
import os

from dotenv import load_dotenv

load_dotenv()  # local .env (a no-op in a container running on real env vars)

# Where the setup page persists config. Mount a volume here in the container.
DATA_DIR = os.environ.get("MIXER_DATA_DIR")
if not DATA_DIR:
    # If running in a container, default to /data; otherwise default to a local "data" dir
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        DATA_DIR = "/data"
    else:
        DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# Keys we manage, with built-in defaults.
DEFAULTS = {
    "AI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "AI_MODEL": "gemini-3.1-flash-lite",
    "AI_API_KEY": "",
    "AI_RPM_LIMIT": "",           # cap on AI requests/min (bulk imports); "" or 0 = no limit
    "MEALIE_URL": "",
    "MEALIE_TOKEN": "",
    "MIXER_AUTH_USER": "",
    "MIXER_AUTH_PASS": "",        # legacy plaintext (env only)
    "MIXER_AUTH_PASS_HASH": "",   # hashed (written by the setup page)
    "MIXER_API_KEY": "",          # Phase 5: API key for /api/* endpoints (empty = API disabled)
    "MIXER_SESSION_SECRET": "",   # Phase 6: signs browser session cookies (auto-generated)
}

_file_cfg: dict = {}


def _load_file() -> dict:
    """Read config.json; tolerate it being absent or malformed."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


_file_cfg = _load_file()


def reload() -> None:
    """Re-read the config file (call after the setup page writes it)."""
    global _file_cfg
    _file_cfg = _load_file()


def get(key: str, default: str = "") -> str:
    """Resolve a config value: env var > config.json > built-in default."""
    env = os.environ.get(key)
    if env not in (None, ""):
        return env
    val = _file_cfg.get(key)
    if val not in (None, ""):
        return val
    return DEFAULTS.get(key, default)


def save(updates: dict) -> None:
    """Merge non-empty updates into config.json (atomic write), then reload.
    Creates the data dir if needed. Used by the setup page in a later stage."""
    os.makedirs(DATA_DIR, exist_ok=True)
    current = _load_file()
    for k, v in updates.items():
        if v is not None:
            current[k] = v
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    reload()


def is_configured() -> bool:
    """True once the essentials are present (from env or file)."""
    return bool(get("AI_API_KEY") and get("MEALIE_URL") and get("MEALIE_TOKEN"))


def env_pinned() -> list[str]:
    """Managed keys currently set (non-empty) via an environment variable — these
    take precedence over the volume config, so the Settings page can't change
    them. Lets the UI warn instead of silently no-op'ing a save."""
    return [k for k in DEFAULTS if os.environ.get(k)]


# ── Password hashing (stdlib only) ──────────────────────────────────────────
def hash_password(password: str, iterations: int = 200_000) -> str:
    """PBKDF2-SHA256 with a random salt. Returns a self-describing string so
    verify_password() needs nothing else stored."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a hash from hash_password()."""
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False
