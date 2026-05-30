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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Header, UploadFile
from pydantic import BaseModel, Field

import config
from extract import extract_recipes, extract_recipes_from_url
from push import push_recipe


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

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def health():
    """Health check — no auth required."""
    return HealthResponse(status="ok", configured=config.is_configured())


@router.post(
    "/extract",
    response_model=ExtractResponse,
    dependencies=[Depends(require_api_key)],
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
    tmp_paths: list[str] = []
    try:
        if url and url.strip():
            recipes = extract_recipes_from_url(
                url.strip(), user_note=prompt, target_language=language,
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
    dependencies=[Depends(require_api_key)],
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
