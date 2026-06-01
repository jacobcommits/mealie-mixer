"""
Mealie Mixer — REST API (Phase 5)

Exposes extract and push as HTTP endpoints so an external agent (e.g. a
Telegram bot) can drive the pipeline programmatically:

  POST /api/extract  — image upload or URL → structured recipe JSON
  POST /api/push     — recipe JSON → create in Mealie, return slug + URL
  GET  /api/health   — no auth — configured status

Auth: Bearer token (or X-API-Key header) checked against MIXER_API_KEY.
Fail-closed: if MIXER_API_KEY is empty the authenticated endpoints return 503.

Typed contract via Pydantic — gives auto /docs (interactive OpenAPI) for free.
"""

from __future__ import annotations

import hmac
import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

import config
import core
from extract import (
    extract_recipes,
    extract_recipes_from_url,
    extract_recipes_from_video,
    is_video_url,
    test_ai,
)
from push import (
    fetch_category_names,
    fetch_food_names,
    fetch_recipe_names,
    push_recipe,
    test_mealie,
    upload_recipe_image,
)


# ── Pydantic models ────────────────────────────────────────────────────

class Ingredient(BaseModel):
    quantity: float | None = None
    unit: str | None = None
    food: str | None = None
    note: str | None = None


class Recipe(BaseModel):
    """Typed recipe — the contract between extract, the agent, and push.

    `yield` is a Python keyword, so the field is `recipe_yield` internally
    with a `"yield"` alias.  Use `model_dump(by_alias=True)` to get the
    dict shape that push_recipe expects.
    """

    model_config = {"populate_by_name": True}

    name: str
    description: str = ""
    servings: float | None = None
    recipe_yield: str = Field("", alias="yield")
    ingredients: list[Ingredient] = []
    instructions: list[str] = []
    tags: list[str] = []
    categories: list[str] = []
    source_url: str = ""
    image_url: str | None = None


# ── Response models ────────────────────────────────────────────────────

class ExtractResponse(BaseModel):
    recipes: list[Recipe]


class PushResponse(BaseModel):
    slug: str
    url: str


class HealthResponse(BaseModel):
    status: str
    configured: bool


# ── Auth dependency ────────────────────────────────────────────────────

def require_api_key(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """Fail-closed API-key check.

    Reads from ``Authorization: Bearer <key>`` or ``X-API-Key`` header.
    Returns the validated key on success.

    - MIXER_API_KEY empty/unset → **503** (API disabled)
    - App not configured        → **503**
    - Missing/wrong key         → **401**
    """
    server_key = config.get("MIXER_API_KEY")
    if not server_key:
        raise HTTPException(
            status_code=503,
            detail="API disabled — set MIXER_API_KEY to enable it.",
        )
    if not config.is_configured():
        raise HTTPException(
            status_code=503,
            detail="App not configured — complete the setup page first.",
        )

    # Extract the token from whichever header was provided.
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token and x_api_key:
        token = x_api_key.strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing API key.")

    # Constant-time comparison to avoid timing side-channels.
    if not hmac.compare_digest(token.encode(), server_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key.")

    return token


# ── Routes ─────────────────────────────────────────────────────────────

def require_access(request: Request,
                   authorization: Annotated[str | None, Header()] = None,
                   x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Allow a logged-in browser session OR a valid agent API key (used by the
    endpoints that both the web UI and agents call)."""
    if request.session.get("authed"):
        return
    require_api_key(authorization, x_api_key)  # raises 503/401 exactly as before


def require_ui(request: Request) -> None:
    """UI/config endpoints: allow a browser session, or open if no login is set."""
    if request.session.get("authed"):
        return
    if not config.get("MIXER_AUTH_USER"):
        return
    raise HTTPException(status_code=401, detail="Log in required.")


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class ConfigBody(BaseModel):
    mealie_url: str = ""
    mealie_token: str = ""
    ai_key: str = ""
    ai_base: str = ""
    ai_model: str = ""
    auth_user: str = ""
    auth_pass: str = ""
    api_key: str = ""


router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def health():
    """Health check — no auth required."""
    return HealthResponse(status="ok", configured=config.is_configured())


@router.post(
    "/extract",
    response_model=ExtractResponse,
    dependencies=[Depends(require_access)],
)
async def api_extract(
    files: list[UploadFile] | None = File(None),
    url: str | None = Form(None),
    language: str = Form("English"),
    prompt: str = Form(""),
):
    """Extract recipe(s) from uploaded image(s) or a recipe URL.

    Accepts ``multipart/form-data`` with one or more image ``files``, or a
    ``url`` field pointing at a recipe page.  Returns structured JSON.
    """
    # Feed the user's existing Mealie categories to the prompt so the AI reuses
    # them instead of spawning near-dupes. Fail-soft: empty if Mealie's unreachable.
    try:
        known_categories = fetch_category_names()
    except Exception:
        known_categories = []

    tmp_paths: list[str] = []
    try:
        if url and url.strip():
            u = url.strip()
            if is_video_url(u):
                recipes = extract_recipes_from_video(
                    u, user_note=prompt, target_language=language,
                    known_categories=known_categories,
                )
            else:
                recipes = extract_recipes_from_url(
                    u, user_note=prompt, target_language=language,
                    known_categories=known_categories,
                )
        elif files:
            for f in files:
                suffix = os.path.splitext(f.filename or "img.jpg")[1] or ".jpg"
                fd, path = tempfile.mkstemp(suffix=suffix, prefix="mm-api-")
                with os.fdopen(fd, "wb") as out:
                    out.write(await f.read())
                tmp_paths.append(path)
            recipes = extract_recipes(
                tmp_paths, user_note=prompt, target_language=language,
                known_categories=known_categories,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide image file(s) or a 'url' field.",
            )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Extraction failed: {str(e)[:300]}",
        )
    finally:
        for p in tmp_paths:
            try:
                os.remove(p)
            except OSError:
                pass

    return ExtractResponse(
        recipes=[Recipe.model_validate(r) for r in recipes],
    )


@router.post(
    "/push",
    response_model=PushResponse,
    dependencies=[Depends(require_access)],
)
def api_push(recipe: Recipe):
    """Push a recipe to Mealie.  Returns the slug and full URL."""
    # Convert the Pydantic model back to the dict shape push_recipe expects.
    recipe_dict = recipe.model_dump(by_alias=True)

    try:
        slug = push_recipe(recipe_dict, structured=True)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Push failed: {str(e)[:300]}",
        )

    mealie_url = config.get("MEALIE_URL").rstrip("/")
    return PushResponse(slug=slug, url=f"{mealie_url}/g/home/r/{slug}")


# ── Browser session + UI/config endpoints (Phase 6: powers the web UI) ──────

@router.post("/login")
def api_login(request: Request, body: LoginBody):
    """Establish a browser session. If no login is configured, any call grants
    an (open) session; otherwise the username + password are verified."""
    user = config.get("MIXER_AUTH_USER")
    if not user:
        request.session["authed"] = True
        return {"ok": True, "login_required": False}
    if body.username == user and config.verify_password(
        body.password or "", config.get("MIXER_AUTH_PASS_HASH")
    ):
        request.session["authed"] = True
        request.session["user"] = user
        return {"ok": True, "login_required": True}
    raise HTTPException(status_code=401, detail="Invalid username or password.")


@router.post("/logout")
def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/config")
def api_get_config(request: Request):
    """Gate info for the UI. Open returns only {configured, login_required};
    settings fields (never secret *values*) are added once authed/open."""
    authed = bool(request.session.get("authed")) or not config.get("MIXER_AUTH_USER")
    out = {
        "configured": config.is_configured(),
        "login_required": bool(config.get("MIXER_AUTH_USER")),
        "authed": authed,
    }
    if authed:
        out.update({
            "mealie_url": config.get("MEALIE_URL"),
            "ai_base_url": config.get("AI_BASE_URL"),
            "ai_model": config.get("AI_MODEL"),
            "auth_user": config.get("MIXER_AUTH_USER"),
            "has_mealie_token": bool(config.get("MEALIE_TOKEN")),
            "has_ai_key": bool(config.get("AI_API_KEY")),
            "has_api_key": bool(config.get("MIXER_API_KEY")),
            "env_pinned": config.env_pinned(),   # keys set via env (override Settings)
        })
    return out


@router.post("/config", dependencies=[Depends(require_ui)])
def api_set_config(body: ConfigBody):
    try:
        core.apply_config(
            mealie_url=body.mealie_url, mealie_token=body.mealie_token,
            ai_key=body.ai_key, ai_base=body.ai_base, ai_model=body.ai_model,
            auth_user=body.auth_user, auth_pass=body.auth_pass, api_key=body.api_key,
        )
    except core.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Couldn't write config: {e}")
    return {"ok": True, "configured": config.is_configured()}


@router.post("/config/test-mealie", dependencies=[Depends(require_ui)])
def api_test_mealie(body: ConfigBody):
    url = core.normalize_url(body.mealie_url) or config.get("MEALIE_URL")
    token = (body.mealie_token or "").strip() or config.get("MEALIE_TOKEN")
    ok, message = test_mealie(url, token)
    return {"ok": ok, "message": message}


@router.post("/config/test-ai", dependencies=[Depends(require_ui)])
def api_test_ai(body: ConfigBody):
    base = (body.ai_base or "").strip() or config.get("AI_BASE_URL")
    model = (body.ai_model or "").strip() or config.get("AI_MODEL")
    key = (body.ai_key or "").strip() or config.get("AI_API_KEY")
    ok, message = test_ai(base, model, key)
    return {"ok": ok, "message": message}


@router.post("/config/generate-key", dependencies=[Depends(require_ui)])
def api_generate_key():
    return {"key": core.generate_api_key()}


@router.get("/foods", dependencies=[Depends(require_access)])
def api_foods():
    """Food names for the UI autocomplete (session or key auth)."""
    return {"foods": fetch_food_names()}


@router.get("/categories", dependencies=[Depends(require_access)])
def api_categories():
    """Category names for the review-step autocomplete (session or key auth)."""
    return {"categories": fetch_category_names()}


@router.get("/recipe-names", dependencies=[Depends(require_access)])
def api_recipe_names():
    """Existing recipe names for the review-step duplicate warning."""
    return {"names": fetch_recipe_names()}


@router.put("/recipe-image/{slug}", dependencies=[Depends(require_access)])
async def api_recipe_image(slug: str, file: UploadFile = File(...)):
    """Attach an uploaded photo to a recipe (Mealie resizes/thumbnails it).
    Called after /api/push, with the slug it returned."""
    try:
        upload_recipe_image(slug, await file.read(), file.filename or "photo.jpg")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Image upload failed: {str(e)[:200]}")
    return {"ok": True}
