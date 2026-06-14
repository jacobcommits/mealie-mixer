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

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

import config
import cookbook
import core
import history
import jobs
import transcribe
from extract import (
    extract_recipes_from_sources,
    extract_recipes_from_text,
    file_to_text,
    is_document,
    test_ai,
)
from push import (
    fetch_category_names,
    fetch_food_names,
    fetch_recipe,
    fetch_recipe_names,
    fetch_recipes,
    push_recipe,
    recipe_to_text,
    test_mealie,
    update_recipe,
    upload_recipe_image,
)


# ── Pydantic models ────────────────────────────────────────────────────

class Ingredient(BaseModel):
    quantity: float | None = None
    unit: str | None = None
    food: str | None = None
    note: str | None = None
    title: str | None = None   # section heading on the first ingredient of a group


class RecipeNote(BaseModel):
    """A useful culinary tip (storage, substitutions, ...) → Mealie recipe notes."""
    title: str = ""
    text: str = ""


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
    notes: list[RecipeNote] = []
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
    ai_rpm: str = ""


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
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    language: str = Form("English"),
    prompt: str = Form(""),
    units_system: str = Form("metric"),
):
    """Extract recipe(s) from ANY mix of sources, combined into one recipe.

    Accepts ``multipart/form-data`` with any of: image/document ``files``, a ``url``
    (recipe page or social/reel link), ``text`` (pasted), and an ``audio`` voice note /
    screen-recording. All provided sources are forwarded together to a single LLM call —
    e.g. ingredients from a reel caption + steps narrated in the video. Synchronous (agents
    can wait); the browser uses POST /api/extract/job for a progress bar on slow sources.
    """
    # Feed the user's existing Mealie categories to the prompt so the AI reuses
    # them instead of spawning near-dupes. Fail-soft: empty if Mealie's unreachable.
    try:
        known_categories = fetch_category_names()
    except Exception:
        known_categories = []

    tmp_paths: list[str] = []
    try:
        image_paths, doc_texts, audio_path = await _collect_sources(files, audio, tmp_paths)
        recipes = extract_recipes_from_sources(
            image_paths=image_paths,
            url=(url or "").strip(),
            text=text or "",
            doc_texts=doc_texts,
            audio_path=audio_path,
            user_note=prompt, target_language=language,
            known_categories=known_categories,
            units_system=units_system,
        )
    except ValueError as e:            # nothing provided / no speech in the audio
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:          # missing AI key / voice not built into this image
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


async def _collect_sources(files, audio, tmp_paths):
    """Read uploads into (image_paths, doc_texts, audio_path). Images/audio are written to
    temp files appended to `tmp_paths` (caller or the job owns cleanup); documents are
    decoded to text. Shared by the sync /api/extract and the async /api/extract/job."""
    image_paths: list[str] = []
    doc_texts: list[str] = []
    audio_path = ""
    for f in files or []:
        data = await f.read()
        if is_document(f.filename or ""):
            doc_texts.append(file_to_text(f.filename or "", data))
        else:
            suffix = os.path.splitext(f.filename or "img.jpg")[1] or ".jpg"
            fd, path = tempfile.mkstemp(suffix=suffix, prefix="mm-api-")
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            tmp_paths.append(path)
            image_paths.append(path)
    if audio is not None:
        suffix = os.path.splitext(audio.filename or "note.webm")[1] or ".webm"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="mm-audio-")
        with os.fdopen(fd, "wb") as out:
            out.write(await audio.read())
        tmp_paths.append(path)
        audio_path = path
    return image_paths, doc_texts, audio_path


@router.post("/extract/job", dependencies=[Depends(require_access)])
async def api_extract_job(
    files: list[UploadFile] | None = File(None),
    url: str | None = Form(None),
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    language: str = Form("English"),
    prompt: str = Form(""),
    units_system: str = Form("metric"),
):
    """Start a background combine-extraction job: any mix of image/document files, a url,
    text, and a voice note / screen-recording. Slow sources (whisper transcription, link
    scraping) run off the request; the browser polls ``GET /api/extract/job/{job_id}`` and
    shows a progress bar. Agents can use the synchronous /api/extract instead."""
    if audio is not None and not transcribe.is_available():
        raise HTTPException(
            status_code=503, detail="Voice transcription isn't enabled in this build.",
        )
    try:
        known_categories = fetch_category_names()
    except Exception:
        known_categories = []
    tmp_paths: list[str] = []
    image_paths, doc_texts, audio_path = await _collect_sources(files, audio, tmp_paths)
    sources = {
        "image_paths": image_paths,
        "url": (url or "").strip(),
        "text": text or "",
        "doc_texts": doc_texts,
        "audio_path": audio_path,
        "_tmp_paths": tmp_paths,   # the job removes these when it finishes
    }
    job_id = jobs.start_extract_job(
        sources, language=language, user_note=prompt, known_categories=known_categories,
        units_system=units_system,
    )
    return {"job_id": job_id}


@router.get("/extract/job/{job_id}", dependencies=[Depends(require_access)])
def api_extract_job_status(job_id: str):
    """Status + (when done) structured recipes for a combine-extraction job."""
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


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
    url = f"{mealie_url}/g/home/r/{slug}"

    # Record the import (B4). Best-effort — a logging failure must never fail a push
    # that already landed. Covers UI + agents since both go through this endpoint.
    try:
        history.log_import(
            name=recipe.name, slug=slug,
            source_url=recipe.source_url, mealie_url=url,
            payload=recipe_dict,   # store the pushed recipe so history can preview it
        )
    except Exception:
        pass

    return PushResponse(slug=slug, url=url)


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
        "voice": transcribe.is_available(),   # is the voice-note feature usable in this build?
    }
    if authed:
        out.update({
            "mealie_url": config.get("MEALIE_URL"),
            "ai_base_url": config.get("AI_BASE_URL"),
            "ai_model": config.get("AI_MODEL"),
            "ai_rpm": config.get("AI_RPM_LIMIT"),
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
            ai_rpm=body.ai_rpm,
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


# ── B9: Fix existing Mealie recipes (re-standardize) ─────────────────────────

class RestandardizeBody(BaseModel):
    slug: str
    language: str = "English"
    units_system: str = "metric"


class UpdateResponse(BaseModel):
    slug: str
    url: str


@router.get("/mealie-recipes", dependencies=[Depends(require_access)])
def api_mealie_recipes():
    """All Mealie recipes as [{slug, name}] — powers the B9 browse list."""
    return {"recipes": fetch_recipes()}


@router.post("/restandardize", dependencies=[Depends(require_access)])
def api_restandardize(body: RestandardizeBody):
    """Fetch an existing Mealie recipe by slug, render it to text, run it
    through the same LLM structuring pipeline, and return the cleaned recipe
    for review. One LLM call — fast enough to be synchronous."""
    try:
        mealie_rec = fetch_recipe(body.slug)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't fetch the recipe from Mealie: {str(e)[:200]}")

    text = recipe_to_text(mealie_rec)
    if not text.strip():
        raise HTTPException(status_code=400, detail="That recipe has no usable content to re-standardize.")

    try:
        known_categories = fetch_category_names()
    except Exception:
        known_categories = []

    try:
        recipes = extract_recipes_from_text(
            text, target_language=body.language, known_categories=known_categories,
            units_system=body.units_system,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI structuring failed: {str(e)[:300]}")

    if not recipes:
        raise HTTPException(status_code=502, detail="The AI didn't return a recipe — try again.")

    # Take the first (should be exactly one), and carry over the existing
    # slug, image, and source URL so the update targets the right recipe.
    result = recipes[0]
    result["_slug"] = body.slug
    # Preserve the existing image (Mealie stores it; we don't want to clear it)
    result.setdefault("image_url", "")
    # Preserve the original source URL if the Mealie recipe had one
    result.setdefault("source_url", mealie_rec.get("orgURL") or "")

    return {"recipe": result, "slug": body.slug}


@router.post("/recipes/{slug}/update", dependencies=[Depends(require_access)])
def api_update_recipe(slug: str, recipe: Recipe):
    """Update an existing Mealie recipe in place (B9). Returns the slug
    and full URL. Does NOT create a new recipe or delete on failure."""
    recipe_dict = recipe.model_dump(by_alias=True)

    try:
        result_slug = update_recipe(slug, recipe_dict)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Update failed: {str(e)[:300]}")

    mealie_url = config.get("MEALIE_URL").rstrip("/")
    url = f"{mealie_url}/g/home/r/{result_slug}"

    # Record in history (best-effort)
    try:
        history.log_import(
            name=recipe.name, slug=result_slug,
            source_url=recipe.source_url, mealie_url=url,
            status="updated", payload=recipe_dict,
        )
    except Exception:
        pass

    return UpdateResponse(slug=result_slug, url=url)


@router.post("/cookbook/split", dependencies=[Depends(require_access)])
async def api_cookbook_split(file: UploadFile = File(...)):
    """Split a cookbook PDF into per-recipe chunks (text + hero image) — B7. No LLM;
    the browser then structures each chunk via /api/extract and pushes via /api/push."""
    data = await file.read()
    try:
        recipes = cookbook.split_cookbook(data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't read that PDF: {str(e)[:200]}")
    if not recipes:
        raise HTTPException(
            status_code=400,
            detail="No recipe pages found. Bulk cookbook import expects a recipe-per-page "
                   "layout with 'Ingredients' and 'Directions' sections (like most photo "
                   "cookbooks). This looks like a text-only / LaTeX-style or scanned book — "
                   "for those, screenshot an individual recipe and use the normal image "
                   "import, or paste one recipe's text at a time.",
        )
    return {"recipes": recipes}


class CookbookJobBody(BaseModel):
    recipes: list[dict] = []
    language: str = "English"
    units_system: str = "metric"


@router.post("/cookbook/job", dependencies=[Depends(require_access)])
def api_cookbook_job(body: CookbookJobBody):
    """Start a background structuring job for the selected cookbook chunks (B7 Phase B).
    Returns a job_id the browser polls; the run survives closing the tab."""
    if not body.recipes:
        raise HTTPException(status_code=400, detail="No recipes selected to process.")
    return {"job_id": jobs.start_job(body.recipes, body.language, body.units_system)}


@router.get("/cookbook/job/{job_id}", dependencies=[Depends(require_access)])
def api_cookbook_job_status(job_id: str):
    """Status + (when done) structured recipes for a cookbook job."""
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/cookbook/job/{job_id}/cancel", dependencies=[Depends(require_access)])
def api_cookbook_job_cancel(job_id: str):
    """Stop a running cookbook job (e.g. wrong book uploaded)."""
    jobs.cancel_job(job_id)
    return {"ok": True}


@router.get("/cookbook/jobs", dependencies=[Depends(require_access)])
def api_cookbook_jobs():
    """Recent cookbook job summaries — powers the 'ready to review' banner/badge."""
    return {"jobs": jobs.list_jobs()}


@router.get("/history", dependencies=[Depends(require_access)])
def api_history():
    """Recent import history (B4) — powers the history screen + dedupe warning.
    Session or key auth, like the other UI data endpoints."""
    return {"items": history.list_imports()}


@router.post("/history/discard", dependencies=[Depends(require_access)])
def api_history_discard(item: dict = Body(...)):
    """Stash a discarded review recipe so a misclick can be restored. Stores the
    recipe verbatim (free-form, not the strict Recipe model) for faithful restore."""
    try:
        history.log_import(
            name=item.get("name") or "", slug="",
            source_url=item.get("source_url") or "", mealie_url="",
            status="discarded", payload=item,
        )
    except Exception:
        pass  # best-effort, never block the discard
    return {"ok": True}


@router.get("/history/{item_id}", dependencies=[Depends(require_access)])
def api_history_item(item_id: int):
    """One history entry incl. its stored recipe payload — used to restore a discard."""
    row = history.get_import(item_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found.")
    return row


@router.put("/recipe-image/{slug}", dependencies=[Depends(require_access)])
async def api_recipe_image(slug: str, file: UploadFile = File(...)):
    """Attach an uploaded photo to a recipe (Mealie resizes/thumbnails it).
    Called after /api/push, with the slug it returned."""
    try:
        upload_recipe_image(slug, await file.read(), file.filename or "photo.jpg")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Image upload failed: {str(e)[:200]}")
    return {"ok": True}
