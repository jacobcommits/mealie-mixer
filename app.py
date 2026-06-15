"""
Mealie Mixer — web server (FastAPI).

A thin shell that wires the REST API (``api.py``) and the static web UI
(``static/``) onto one FastAPI app on a single port. All the real work lives
elsewhere: ``extract.py`` (extraction), ``push.py`` (Mealie), ``core.py`` /
``config.py`` (config), and ``api.py`` (the endpoints the UI + agents call).

Run:
    # config comes from a gitignored .env OR the in-app setup page (/data volume)
    python app.py            # http://0.0.0.0:7860 — web UI, /docs, /api/*
"""

import mimetypes
import os

import core

# Serve fonts with the correct MIME — some environments have no .woff2 mapping,
# so StaticFiles would otherwise fall back to application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")


def create_app():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.sessions import SessionMiddleware

    from api import router as api_router

    app = FastAPI(
        title="Mealie Mixer",
        description="Recipe extraction + push API.  Interactive docs at /docs.",
    )

    # Signed-cookie sessions for the web UI login. The secret is env/config-set
    # if MIXER_SESSION_SECRET is provided, otherwise generated ONCE and persisted
    # to the /data volume (core.session_secret) so restarts/rebuilds don't log
    # everyone out.
    secret = core.session_secret()
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=False)

    app.include_router(api_router)

    # One-time migration: seed the first admin from the legacy single-user login
    # (if any) so an upgrading deploy keeps its login. No-op once a user exists or
    # if there's no legacy login. Best-effort — a /data we can't write to just
    # means the legacy fallback in users.verify() carries the login instead.
    try:
        import users
        users.ensure_bootstrap()
    except Exception as exc:  # noqa: BLE001 — never block boot on account migration
        print(f"  ! user-store bootstrap skipped: {exc}", flush=True)

    # The web UI (static/) at / — mounted LAST so /api/* and /docs win over this
    # catch-all. Dir resolved next to this file (→ /app/static in the container).
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app


fastapi_app = create_app()


if __name__ == "__main__":
    import uvicorn

    # LAN only — this app can write to Mealie. Do NOT expose to the internet.
    print("──────────────────────────────────────────────")
    print("  Mealie Mixer running on http://0.0.0.0:7860")
    print("  Web UI:    http://0.0.0.0:7860/")
    print("  API docs:  http://0.0.0.0:7860/docs")
    print("──────────────────────────────────────────────")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
