"""
Mealie Mixer — core config logic, Gradio-free.

Shared by the (current) Gradio UI and the REST API: URL normalization, config
validate+persist, and API-key generation. No `gradio`, no `fastapi` imports —
callers map `ConfigError` to their own error type (gr.Error / HTTPException).
"""

import secrets
from urllib.parse import urlparse

import config


class ConfigError(ValueError):
    """Submitted config is invalid (caller maps to its own error type)."""


def normalize_url(url: str) -> str:
    """Tidy a user-entered URL: trim, collapse an accidental doubled scheme
    (http://http://… → http://…), and add http:// if no scheme was given."""
    url = (url or "").strip()
    if not url:
        return ""
    low = url.lower()
    for scheme in ("http://", "https://"):
        while low.startswith(scheme + "http://") or low.startswith(scheme + "https://"):
            url = url[len(scheme):]
            low = url.lower()
    if not low.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def generate_api_key() -> str:
    """A strong random API key (URL-safe)."""
    return secrets.token_urlsafe(32)


def apply_config(*, mealie_url, mealie_token, ai_key, ai_base, ai_model,
                 auth_user, auth_pass, api_key=""):
    """Validate + persist config to the data volume.

    Mealie URL/token + AI key are required. AI base/model fall back to defaults.
    Secrets (token, AI key, API key): blank keeps the existing stored value.
    Login: blank username disables it; a blank password keeps the existing one.
    Raises ConfigError on bad input; config.save may raise OSError.
    """
    mealie_url = normalize_url(mealie_url)
    if mealie_url and not urlparse(mealie_url).hostname:
        raise ConfigError("Mealie URL looks invalid — use e.g. http://10.0.10.149:9925")

    updates = {
        "MEALIE_URL": mealie_url,
        "MEALIE_TOKEN": (mealie_token or "").strip() or config.get("MEALIE_TOKEN"),
        "AI_API_KEY": (ai_key or "").strip() or config.get("AI_API_KEY"),
        "AI_BASE_URL": (ai_base or "").strip() or config.DEFAULTS["AI_BASE_URL"],
        "AI_MODEL": (ai_model or "").strip() or config.DEFAULTS["AI_MODEL"],
        "MIXER_API_KEY": (api_key or "").strip() or config.get("MIXER_API_KEY"),
    }
    missing = [k for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY") if not updates[k]]
    if missing:
        raise ConfigError("Please fill in: " + ", ".join(missing))

    auth_user = (auth_user or "").strip()
    auth_pass = auth_pass or ""
    if not auth_user:
        updates["MIXER_AUTH_USER"] = ""          # "" reads back as unset (no login)
        updates["MIXER_AUTH_PASS_HASH"] = ""
    elif auth_pass:
        updates["MIXER_AUTH_USER"] = auth_user
        updates["MIXER_AUTH_PASS_HASH"] = config.hash_password(auth_pass)
    elif config.get("MIXER_AUTH_PASS_HASH"):
        updates["MIXER_AUTH_USER"] = auth_user   # keep existing password
    else:
        raise ConfigError("Set a password for the login, or clear the username to disable it.")

    config.save(updates)
