"""Mealie Mixer — review UI + REST API (Phase 5 / Stage 2)

The front door for the pipeline: upload screenshot(s) -> extract -> review &
EDIT a structured preview -> Approve pushes to Mealie, Discard clears.

Phase 5 adds a REST API alongside the Gradio UI so an external agent (e.g. a
Telegram bot) can drive extract + push programmatically.  The server is a
FastAPI app: the API routes live in api.py and the Gradio UI is mounted at /.
Both share the same port; the API has its own key-based auth.

This module is deliberately thin: all the real work lives in extract.py
(extraction) and push.py (Mealie). The UI just collects input, shows an
editable preview, and calls those. Nothing is saved to Mealie until you
click Approve — that's the human-review-before-push guarantee.

The food fields autocomplete from your live Mealie food list, so you can snap
variants ("black pepper") onto an existing food ("pepper") as you review,
instead of creating near-dupes. (Units stay free text — units are arbitrary.)

Editing note: the ingredient rows re-draw ONLY on structural changes (extract /
add / remove), never on keystrokes. Per-field edits update state silently via
their change handlers, so typing into a prefilled box doesn't recreate it. The
`redraw` state is the single trigger that forces a re-render.

Run:
    # secrets come from the gitignored .env (AI_*, MEALIE_*)
    python app.py            # http://0.0.0.0:7860  UI + /docs + /api/*
"""

import os
import tempfile
from urllib.parse import urlparse

# Opt out of Gradio's phone-home telemetry (must be set before importing gradio).
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402
import httpx  # noqa: E402

import config  # noqa: E402
import push
from extract import extract_recipes, extract_recipes_from_url, test_ai
from push import fetch_food_names, push_recipe, test_mealie

# Existing Mealie foods, loaded once at startup, for the food autocomplete.
# If Mealie is unreachable this is just [] and the dropdowns become free text.
try:
    FOOD_CHOICES = fetch_food_names()
except Exception as e:  # don't let a Mealie hiccup stop the UI from loading
    print(f"WARN: could not load Mealie foods for autocomplete: {e}")
    FOOD_CHOICES = []

LANGUAGES = ["English", "Polish", "German", "French", "Spanish", "Italian", "Ukrainian"]

EMPTY_INGREDIENT = {"quantity": None, "unit": None, "food": None, "note": None}


_PREVIEW_FILES: list[str] = []  # bound the temp preview files we create


def _download_preview(url: str):
    """Download the dish photo to a temp file for the gr.Image preview (the
    remote URL itself goes to Mealie on push). Returns a local path, or None.
    Keeps only the few most recent temp files so /tmp doesn't grow."""
    if not url:
        return None
    try:
        r = httpx.get(
            url, timeout=15, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MealieMixer/1.0"},
        )
        r.raise_for_status()
        suffix = os.path.splitext(url.split("?")[0])[1] or ".jpg"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="mm-preview-")
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        _PREVIEW_FILES.append(path)
        while len(_PREVIEW_FILES) > 5:  # prune the oldest
            old = _PREVIEW_FILES.pop(0)
            try:
                os.remove(old)
            except OSError:
                pass
        return path
    except Exception as e:
        print(f"WARN: couldn't download preview image: {e}")
        return None


# ── Handlers ─────────────────────────────────────────────────────────────
def _load_form(r, status_msg, redraw, queue):
    """Preview-output tuple that loads recipe dict `r` into the editable form."""
    image_url = r.get("image_url", "")
    ingredients = [
        {"quantity": i.get("quantity"), "unit": i.get("unit"),
         "food": i.get("food"), "note": i.get("note")}
        for i in r.get("ingredients", [])
    ]
    return (
        r.get("name", ""),
        r.get("description", ""),
        r.get("servings"),
        r.get("yield", ""),
        ingredients,
        "\n".join(r.get("instructions", [])),
        status_msg,
        redraw + 1,  # force the ingredient rows to re-draw
        image_url,
        _download_preview(image_url),
        queue,
    )


def _clear_form(status_msg, redraw):
    """Preview-output tuple that blanks the whole form and empties the queue."""
    return ("", "", None, "", [], "", status_msg, redraw + 1, "", None, [])


def _refresh_food_choices():
    """Re-pull Mealie foods into the autocomplete list (module global)."""
    global FOOD_CHOICES
    try:
        FOOD_CHOICES = fetch_food_names()
    except Exception as e:
        print(f"WARN: couldn't refresh food list: {e}")
    return FOOD_CHOICES


def do_extract(files, url, user_prompt, language, redraw):
    """Extract from a URL (if given) or the image(s). The first recipe loads into
    the form; any others queue up for review after you push this one."""
    note, lang = user_prompt or "", language or "English"
    try:
        if url and url.strip():
            recipes = extract_recipes_from_url(url.strip(), user_note=note, target_language=lang)
        elif files:
            paths = [f if isinstance(f, str) else f.name for f in files]
            recipes = extract_recipes(paths, user_note=note, target_language=lang)
        else:
            raise gr.Error("Upload a screenshot or paste a recipe link first.")
    except gr.Error:
        raise  # already user-friendly
    except Exception as e:
        raise gr.Error(str(e))  # missing key, LLM/network failure, bad JSON, etc.
    if not recipes:
        raise gr.Error("No recipe found. Try a clearer shot or a different link.")

    first, queue = recipes[0], recipes[1:]
    if queue:
        msg = (f"Extracted **{len(recipes)} recipes** — reviewing #1; **{len(queue)} queued**. "
               "Approve to move to the next.")
    else:
        msg = "Extracted — review and edit below, then Approve."
    return _load_form(first, msg, redraw, queue)


def _parse_qty(v):
    """Qty field -> float or None. Empty or 0 means 'no amount' (null); accepts
    numbers or strings like '0.5' / '1,5'. 0 is never a real recipe quantity."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v == 0 else float(v)
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return None if f == 0 else f


def do_push(name, description, servings, recipe_yield, ingredients, instructions, image_url, queue, redraw):
    """Push the edited recipe, refresh the food list, then load the next queued
    recipe (if any) or clear. On failure: raises (aborting outputs), so your
    edits stay put to retry.
    """
    if not name or not name.strip():
        raise gr.Error("Give the recipe a name before pushing.")

    recipe = {
        "name": name.strip(),
        "description": (description or "").strip(),
        "servings": (servings or None),  # 0/None -> don't set
        "yield": (recipe_yield or "").strip(),
        "image_url": (image_url or "").strip() or None,
        "ingredients": [
            {
                "quantity": _parse_qty(ing.get("quantity")),
                "unit": (ing.get("unit") or None),
                "food": (ing.get("food") or None),
                "note": (ing.get("note") or None),
            }
            for ing in ingredients
            if (ing.get("food") or ing.get("note"))  # drop empty rows
        ],
        "instructions": [s.strip() for s in (instructions or "").splitlines() if s.strip()],
        "tags": [],  # skipped on purpose — Mealie v3 PATCH bug
    }

    try:
        slug = push_recipe(recipe, structured=True)
    except httpx.HTTPStatusError as e:
        raise gr.Error(f"Mealie rejected it ({e.response.status_code}): {e.response.text[:300]}")

    _refresh_food_choices()  # any newly-created foods are now in the autocomplete
    url = f"{config.get('MEALIE_URL').rstrip('/')}/g/home/r/{slug}"
    gr.Info(f"✅ Pushed “{recipe['name']}” to Mealie")  # toast — visible anywhere

    # also clear the left-side inputs (screenshot + link) — the source is consumed
    if queue:
        nxt, rest = queue[0], queue[1:]
        more = f" — {len(rest)} still queued" if rest else ""
        msg = f"✅ Pushed **{recipe['name']}** → [open in Mealie]({url}). Loaded next recipe{more}."
        return (*_load_form(nxt, msg, redraw, rest), [], "")

    msg = f"✅ Pushed **{recipe['name']}** → [open in Mealie]({url}). Form cleared — ready for the next recipe."
    return (*_clear_form(msg, redraw), [], "")


def do_discard(redraw):
    """Clear the form, the queue, and the left-side inputs (screenshot + link)."""
    return (*_clear_form("🗑️ Discarded — nothing was saved.", redraw), [], "")


def do_refresh_foods(redraw):
    """Manual food-list refresh; bumps redraw so open rows get the fresh choices."""
    foods = _refresh_food_choices()
    gr.Info(f"Food list refreshed ({len(foods)} foods)")
    return redraw + 1


def add_ingredient(ingredients, redraw):
    return ingredients + [dict(EMPTY_INGREDIENT)], redraw + 1


# ── UI ─────────────────────────────────────────────────────────────────────
# ── Config handlers (shared by the setup page and the Settings panel) ───────
def _secret_ph(key: str) -> str:
    """Placeholder for a secret field — secrets are never pre-filled into the
    form (that would ship them to the browser); blank means 'keep current'."""
    return "leave blank to keep current" if config.get(key) else "required"


def _normalize_url(url: str) -> str:
    """Tidy a user-entered URL: trim, collapse an accidental doubled scheme
    (http://http://… → http://…), and add http:// if no scheme was given.
    Prevents a malformed URL parsing its host as 'http' and failing on push."""
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


def do_test_mealie(url, token):
    # blank fields fall back to stored config, so you can test saved creds as-is
    url = _normalize_url(url) or config.get("MEALIE_URL")
    token = (token or "").strip() or config.get("MEALIE_TOKEN")
    ok, msg = test_mealie(url, token)
    return ("✅ " if ok else "❌ ") + msg


def do_test_ai(base, model, key):
    base = (base or "").strip() or config.get("AI_BASE_URL")
    model = (model or "").strip() or config.get("AI_MODEL")
    key = (key or "").strip() or config.get("AI_API_KEY")
    ok, msg = test_ai(base, model, key)
    return ("✅ " if ok else "❌ ") + msg


def _apply_config(mealie_url, mealie_token, ai_key, ai_base, ai_model, auth_user, auth_pass, api_key=""):
    """Validate + persist config to the data volume. Mealie URL/token + AI key
    required; AI base/model fall back to defaults. Login and API key optional —
    blank username disables login; a blank password keeps the existing one;
    a blank API key keeps the existing one. Raises gr.Error on bad input."""
    mealie_url = _normalize_url(mealie_url)
    if mealie_url and not urlparse(mealie_url).hostname:
        raise gr.Error("Mealie URL looks invalid — use e.g. http://10.0.10.149:9925")
    updates = {
        "MEALIE_URL": mealie_url,
        # secrets: blank → keep the existing stored value (never pre-filled in UI)
        "MEALIE_TOKEN": (mealie_token or "").strip() or config.get("MEALIE_TOKEN"),
        "AI_API_KEY": (ai_key or "").strip() or config.get("AI_API_KEY"),
        "AI_BASE_URL": (ai_base or "").strip() or config.DEFAULTS["AI_BASE_URL"],
        "AI_MODEL": (ai_model or "").strip() or config.DEFAULTS["AI_MODEL"],
        # API key: blank keeps existing (same pattern as other secrets)
        "MIXER_API_KEY": (api_key or "").strip() or config.get("MIXER_API_KEY"),
    }
    missing = [k for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY") if not updates[k]]
    if missing:
        raise gr.Error("Please fill in: " + ", ".join(missing))

    auth_user = (auth_user or "").strip()
    auth_pass = auth_pass or ""
    if not auth_user:
        updates["MIXER_AUTH_USER"] = ""        # "" reads back as unset (no auth)
        updates["MIXER_AUTH_PASS_HASH"] = ""
    elif auth_pass:
        updates["MIXER_AUTH_USER"] = auth_user
        updates["MIXER_AUTH_PASS_HASH"] = config.hash_password(auth_pass)
    elif config.get("MIXER_AUTH_PASS_HASH"):
        updates["MIXER_AUTH_USER"] = auth_user  # keep existing password
    else:
        raise gr.Error("Set a password for the login, or clear the username to disable it.")

    try:
        config.save(updates)
    except OSError as e:
        raise gr.Error(f"Couldn't write config to {config.CONFIG_PATH}: {e}")


def do_save_setup(mealie_url, mealie_token, ai_key, ai_base, ai_model, auth_user, auth_pass, api_key):
    """Setup-page save: persist, then prompt a restart to start the app."""
    _apply_config(mealie_url, mealie_token, ai_key, ai_base, ai_model, auth_user, auth_pass, api_key)
    gr.Info("Saved — restart the container to start.")
    return (
        "✅ **Saved.** Now **restart the container** to start using Mealie Mixer:\n\n"
        "```\ndocker compose restart       # or:  podman restart mealie-mixer\n```"
    )


def do_save_settings(mealie_url, mealie_token, ai_key, ai_base, ai_model, auth_user, auth_pass, api_key, redraw):
    """Settings save: persist, apply Mealie/AI live, refresh the food list.
    (A login change still needs a restart — Gradio sets auth at launch.)"""
    _apply_config(mealie_url, mealie_token, ai_key, ai_base, ai_model, auth_user, auth_pass, api_key)
    _refresh_food_choices()  # Mealie may have changed
    gr.Info("Settings saved.")
    msg = "✅ Saved. Mealie/AI changes apply now. A **login** change needs a container restart."
    return msg, redraw + 1


with gr.Blocks(title="Mealie Mixer") as demo:
    gr.Markdown(
        "# 🍲 Mealie Mixer\n"
        "Screenshot → translated, structured recipe → **your review** → Mealie. "
        "Nothing is saved until you hit **Approve**."
    )

    ingredients_state = gr.State([])  # source of truth for ingredient rows
    redraw = gr.State(0)              # bump this to force the rows to re-draw
    image_url_state = gr.State("")    # scraped dish photo URL (URL imports only)
    queue_state = gr.State([])        # extra recipes awaiting review (multi-recipe extract)

    with gr.Row():
        # ── Left: input ──
        with gr.Column(scale=1):
            images = gr.File(
                label="Recipe screenshot(s)",
                file_count="multiple",
                file_types=["image"],
                type="filepath",
            )
            url_input = gr.Textbox(
                label="…or paste a recipe link",
                placeholder="https://example.com/recipe  (recipe sites — not Instagram/TikTok)",
            )
            user_prompt = gr.Textbox(
                label="Extra instructions (optional)",
                placeholder='e.g. "no mushrooms", "double the garlic"',
            )
            language = gr.Dropdown(
                LANGUAGES, value="English", allow_custom_value=True, label="Translate into"
            )
            extract_btn = gr.Button("Extract recipe", variant="primary")

        # ── Right: editable preview ──
        with gr.Column(scale=2):
            status = gr.Markdown("Upload a screenshot and click **Extract recipe** to begin.")
            image_preview = gr.Image(  # dish photo thumbnail (URL imports)
                show_label=False, height=200, interactive=False,
            )
            name = gr.Textbox(label="Name")
            description = gr.Textbox(label="Description", lines=2)
            with gr.Row():
                servings = gr.Number(label="Servings", precision=0, scale=1)
                recipe_yield = gr.Textbox(label="Yield", placeholder="e.g. 6 sandwiches", scale=2)

            gr.Markdown("### Ingredients")

            # Re-draws ONLY when `redraw` changes (extract / add / remove) — never
            # on a keystroke. Field edits update `ingredients_state` silently below.
            @gr.render(inputs=ingredients_state, triggers=[redraw.change])
            def render_ingredients(items):
                if not items:
                    gr.Markdown("_No ingredients yet — extract a recipe or add one._")
                for idx, ing in enumerate(items):
                    with gr.Row():
                        q = gr.Textbox(
                            value=("" if ing.get("quantity") in (None, "") else str(ing.get("quantity"))),
                            label="qty",
                            placeholder="—",
                            scale=1,
                        )
                        u = gr.Textbox(value=ing.get("unit") or "", label="unit", scale=1)
                        f = gr.Dropdown(
                            choices=FOOD_CHOICES,
                            value=ing.get("food"),
                            label="food",
                            allow_custom_value=True,
                            filterable=True,
                            scale=3,
                        )
                        n = gr.Textbox(value=ing.get("note") or "", label="note", scale=3)
                        rm = gr.Button("✕", scale=0, min_width=45)

                    def make_setter(i, field):
                        # update state silently — NOT a redraw trigger, so the row
                        # you're typing in is never recreated mid-edit
                        def _set(val, items):
                            items = list(items)
                            items[i] = {**items[i], field: val}
                            return items
                        return _set

                    q.change(make_setter(idx, "quantity"), [q, ingredients_state], ingredients_state)
                    u.change(make_setter(idx, "unit"), [u, ingredients_state], ingredients_state)
                    f.change(make_setter(idx, "food"), [f, ingredients_state], ingredients_state)
                    n.change(make_setter(idx, "note"), [n, ingredients_state], ingredients_state)

                    def make_remover(i):
                        def _rm(items, draw):
                            items = list(items)
                            items.pop(i)
                            return items, draw + 1  # bump redraw to re-draw rows
                        return _rm

                    rm.click(make_remover(idx), [ingredients_state, redraw], [ingredients_state, redraw])

            with gr.Row():
                add_btn = gr.Button("+ add ingredient", size="sm")
                refresh_btn = gr.Button("↻ refresh foods", size="sm")

            instructions = gr.Textbox(label="Instructions (one step per line)", lines=8)

            with gr.Row():
                approve_btn = gr.Button("✅ Approve & push to Mealie", variant="primary")
                discard_btn = gr.Button("🗑️ Discard")

    with gr.Accordion("⚙️ Settings", open=False):
        gr.Markdown("Update Mealie / AI / login. **Mealie & AI changes apply immediately**; "
                    "a **login** change needs a container restart. (If no login is set, "
                    "this panel is reachable by anyone on your LAN.)")
        set_mealie_url = gr.Textbox(label="Mealie URL", value=config.get("MEALIE_URL"))
        set_mealie_token = gr.Textbox(label="Mealie API token", type="password",
                                      placeholder=_secret_ph("MEALIE_TOKEN"))
        set_test_mealie = gr.Button("Test Mealie connection", size="sm")
        set_mealie_status = gr.Markdown()
        set_ai_key = gr.Textbox(label="AI API key", type="password", placeholder=_secret_ph("AI_API_KEY"))
        set_ai_base = gr.Textbox(label="AI base URL", value=config.get("AI_BASE_URL"))
        set_ai_model = gr.Textbox(label="AI model", value=config.get("AI_MODEL"))
        set_test_ai = gr.Button("Test AI connection (uses ~1 token)", size="sm")
        set_ai_status = gr.Markdown()
        set_auth_user = gr.Textbox(label="Login username (blank = no login)",
                                   value=config.get("MIXER_AUTH_USER"))
        set_auth_pass = gr.Textbox(label="Login password", type="password",
                                   placeholder="blank = keep current")
        gr.Markdown("---")
        gr.Markdown("### Agent API\nSet an API key to enable the `/api/extract` and "
                    "`/api/push` endpoints (used by external bots/agents). "
                    "Blank = API disabled.  Interactive docs at **[/docs](/docs)**.")
        set_api_key = gr.Textbox(label="API key (MIXER_API_KEY)", type="password",
                                 placeholder=_secret_ph("MIXER_API_KEY"))
        set_save = gr.Button("Save settings", variant="primary")
        set_status = gr.Markdown()

    if config.get("MIXER_AUTH_USER"):
        gr.Markdown("<sub>[Log out](/logout)</sub>")

    # ── Wiring ──
    # preview bundle: every handler that (re)loads or clears the form returns this
    preview = [name, description, servings, recipe_yield, ingredients_state, instructions,
               status, redraw, image_url_state, image_preview, queue_state]

    extract_btn.click(
        do_extract,
        inputs=[images, url_input, user_prompt, language, redraw],
        outputs=preview,
    )
    add_btn.click(add_ingredient, [ingredients_state, redraw], [ingredients_state, redraw])
    refresh_btn.click(do_refresh_foods, [redraw], [redraw])
    approve_btn.click(
        do_push,
        inputs=[name, description, servings, recipe_yield, ingredients_state, instructions,
                image_url_state, queue_state, redraw],
        outputs=preview + [images, url_input],  # also clear screenshot + link
    )
    discard_btn.click(do_discard, inputs=[redraw], outputs=preview + [images, url_input])

    # Settings panel
    set_test_mealie.click(do_test_mealie, [set_mealie_url, set_mealie_token], set_mealie_status)
    set_test_ai.click(do_test_ai, [set_ai_base, set_ai_model, set_ai_key], set_ai_status)
    set_save.click(
        do_save_settings,
        [set_mealie_url, set_mealie_token, set_ai_key, set_ai_base, set_ai_model,
         set_auth_user, set_auth_pass, set_api_key, redraw],
        [set_status, redraw],
    )


# ── First-run setup page (served when config is incomplete) ─────────────────
# (config handlers — do_test_*, do_save_setup, do_save_settings — are defined
#  above the main app so both the setup page and the Settings panel can use them.)


with gr.Blocks(title="Mealie Mixer — Setup") as setup_demo:
    gr.Markdown(
        "# 🍲 Mealie Mixer — first-time setup\n"
        "Enter your Mealie and AI details to get started. They're saved to the "
        "app's data volume, so you only do this once (editable later in Settings)."
    )
    with gr.Group():
        gr.Markdown("### Mealie")
        su_mealie_url = gr.Textbox(label="Mealie URL", value=config.get("MEALIE_URL"),
                                   placeholder="http://your-mealie-host:9925")
        su_mealie_token = gr.Textbox(label="Mealie API token", type="password",
                                     placeholder=f"{_secret_ph('MEALIE_TOKEN')} — Mealie → Profile → API Tokens")
        su_test_mealie = gr.Button("Test Mealie connection", size="sm")
        su_mealie_status = gr.Markdown()
    with gr.Group():
        gr.Markdown("### AI backend")
        su_ai_key = gr.Textbox(label="AI API key", type="password", placeholder=_secret_ph("AI_API_KEY"))
        su_ai_base = gr.Textbox(label="AI base URL", value=config.get("AI_BASE_URL"))
        su_ai_model = gr.Textbox(label="AI model", value=config.get("AI_MODEL"))
        su_test_ai = gr.Button("Test AI connection (uses ~1 token)", size="sm")
        su_ai_status = gr.Markdown()
    with gr.Group():
        gr.Markdown("### Login (optional)\nSet a username + password to require a login. "
                    "Leave the username blank for no login.")
        su_auth_user = gr.Textbox(label="Username", value=config.get("MIXER_AUTH_USER"))
        su_auth_pass = gr.Textbox(label="Password", type="password",
                                  placeholder="leave blank to keep the current password")
    with gr.Group():
        gr.Markdown("### Agent API (optional)\nSet an API key to enable the REST API "
                    "(`/api/extract`, `/api/push`) for external bots or agents. "
                    "Leave blank to disable the API.")
        su_api_key = gr.Textbox(label="API key", type="password",
                                placeholder=_secret_ph("MIXER_API_KEY"))
    su_save = gr.Button("Save", variant="primary")
    su_status = gr.Markdown()

    su_test_mealie.click(do_test_mealie, [su_mealie_url, su_mealie_token], su_mealie_status)
    su_test_ai.click(do_test_ai, [su_ai_base, su_ai_model, su_ai_key], su_ai_status)
    su_save.click(
        do_save_setup,
        [su_mealie_url, su_mealie_token, su_ai_key, su_ai_base, su_ai_model,
         su_auth_user, su_auth_pass, su_api_key],
        su_status,
    )


def _build_auth():
    """Gradio auth callable, or None if no login is configured. Supports the
    hashed password from the setup page and a legacy plaintext env password."""
    user = config.get("MIXER_AUTH_USER")
    pass_hash = config.get("MIXER_AUTH_PASS_HASH")
    pass_plain = config.get("MIXER_AUTH_PASS")  # legacy: env only
    if not user or not (pass_hash or pass_plain):
        return None

    def check(u, p):
        if u != user:
            return False
        if pass_hash:
            return config.verify_password(p, pass_hash)
        return p == pass_plain

    return check


def create_app():
    from fastapi import FastAPI
    from api import router as api_router

    # ── FastAPI shell — hosts both the API and the Gradio UI ──────────────
    app = FastAPI(
        title="Mealie Mixer",
        description="Recipe extraction + push API.  Interactive docs at /docs.",
    )
    app.include_router(api_router)

    # ── Gate on config: same logic as before ──────────────────────────────
    if config.is_configured():
        auth = _build_auth()
        if auth:
            print("Auth enabled (login required).")
        target = demo
    else:
        print("Config incomplete — serving the first-run setup page.")
        auth = None
        target = setup_demo

    # Mount Gradio at root — preserves setup-page gating + browser login.
    # API routes (/api/*) live above this mount, so they're always reachable
    # (they have their own fail-closed auth via MIXER_API_KEY).
    gr.mount_gradio_app(app, target, path="/", auth=auth)
    return app

fastapi_app = create_app()

if __name__ == "__main__":
    import uvicorn
    # LAN only — this app can write to Mealie. Do NOT expose to the internet.
    print("──────────────────────────────────────────────")
    print("  Mealie Mixer running on http://0.0.0.0:7860")
    print("  API docs:  http://0.0.0.0:7860/docs")
    print("  Health:    http://0.0.0.0:7860/api/health")
    print("──────────────────────────────────────────────")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
