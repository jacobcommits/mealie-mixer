"""
Mealie Mixer — recipe extraction core (step 2)

Takes one or more recipe images, sends them to a vision LLM, and prints
structured recipe data as JSON. This is the heart of the pipeline; the
Mealie push (steps 3-4) and the web UI (step 5) come later.

The extract_recipes() function is the reusable core — the UI will call it.

Run:
    export AI_API_KEY="your-google-ai-studio-key"
    python extract.py shot.jpg
    python extract.py shot1.jpg shot2.jpg --lang English --prompt "no mushrooms"
"""

import argparse
import base64
import io
import json
import os
import sys
import threading
import time

from openai import OpenAI
from PIL import Image

import config

# Backend is swappable via the config layer: AI_BASE_URL / AI_MODEL can point at
# OpenRouter, OpenAI, a local model, whatever. Gemini 3.1 Flash Lite (free tier)
# is just the default value, not a hardcoded dependency. Values are read live
# from config.get() at call time (see _structure) so a saved config applies.

MAX_IMAGE_PX = 1200   # longest edge after resize — keeps payloads small/fast
JPEG_QUALITY = 85

# LLM call resilience (see _call_with_retry):
_USE_JSON_OBJECT = True       # flipped off for the process if the backend 400s on response_format
_MAX_AI_RETRIES = 2           # extra attempts after the first (transient errors only)
_AI_RETRY_BACKOFF = 2.0       # seconds between retries, doubled each time

# ── The prompt (this is what we'll tune together) ──────────────────────
SYSTEM_PROMPT = (
    "You are a precise recipe extraction engine. You read recipe images and "
    "output clean, structured data. You never invent ingredients or steps "
    "that aren't shown in the image."
)


def build_user_prompt(
    target_language: str,
    user_note: str,
    source: str = "the image(s)",
    known_categories=(),
    known_tags=(),
    units_system: str = "metric",
    ai_rules: str | None = None,
) -> str:
    active_rules = (config.get("AI_RULES") if ai_rules is None else ai_rules) or ""
    preset_rule = (
        f"\n- Household & dietary rules (ALWAYS follow these): {active_rules.strip()}"
        if active_rules and active_rules.strip() else ""
    )
    extra = f"\n\nExtra instructions from the user: {user_note}" if user_note.strip() else ""
    cat_rule = (
        "\n- For \"categories\", PREFER an existing category from this list when one "
        f"fits: {', '.join(known_categories)}. Only invent a new category name if "
        "none of them fit."
        if known_categories else ""
    )
    tag_rule = (
        "\n- For \"tags\", PREFER an existing tag from this list when one "
        f"fits: {', '.join(known_tags)}. Only invent a new tag name if "
        "none of them fit."
        if known_tags else ""
    )
    if units_system.lower() == "imperial":
        units_rule = "- Convert amounts measured by weight or volume to Imperial/US customary units (ounces, pounds, cups, fluid ounces). Convert temperatures to Fahrenheit. Keep tbsp/tsp/pinch as-is."
    else:
        units_rule = "- Convert amounts measured by weight or volume (flour, sugar, butter, liquids, meat, grains, ...) to metric (grams, millilitres). Convert temperatures to Celsius. Keep tbsp/tsp/pinch as-is."
    return f"""Extract every recipe in {source}.

If several sources are provided — multiple images, and/or a caption, pasted text,
or a transcript of spoken audio/video — they may describe the SAME recipe from
different angles (e.g. ingredients listed in a caption, the method narrated in the
audio, a photo of the dish). Combine them into ONE coherent recipe. Only return
multiple recipes if the sources clearly show genuinely different dishes.

For each recipe, output:
- name
- description: one short line, or ""
- servings: a number — how many portions/servings the recipe makes (the base for scaling), or null if not stated
- yield: a short human-readable yield like "6 sandwiches" or "4 servings", or "" if not stated
- ingredients: a list, each with:
    - quantity: a number, or null if there is no clear amount
    - unit: a string ("g", "ml", "tbsp", "pack", "clove", ...) or null
    - food: the ingredient name ONLY, kept clean (e.g. "ground beef")
    - note: anything extra ("finely chopped", "80/20", "to taste") or null
    - title: a short SECTION HEADING for this ingredient's group ("For the sauce", "For the dough"), set ONLY on the FIRST ingredient of a new section; null for every other ingredient. Most recipes have no sections → leave it null everywhere.
- instructions: a list of step strings, in order
- tags: a short list of tags ("dinner", "vegetarian", ...), or []
- categories: a short list of category names that classify the dish ("Main Course", "Dessert", "Soup", "Breakfast", ...), translated into {target_language}, or []
- notes: a list of genuinely useful culinary tips found in the source — storage/freezing, make-ahead, substitutions, troubleshooting, serving suggestions — each as {{"title": <short label>, "text": <the tip>}}, translated into {target_language}. EXCLUDE the author's personal story, blog intro, SEO padding, ads, and anything already covered by the ingredients or steps. Most images/recipe cards have none → []

Rules:
- Translate EVERYTHING (name, ingredients, steps, tags) into {target_language}.
{units_rule}
- Keep naturally COUNTABLE whole items as a count, never a weight — eggs, onions, lemons, peppers, bananas, potatoes, etc.: quantity = the number, unit = null, food = the item, with any size/prep in note ("2 large onions, diced" → quantity 2, unit null, food "onion", note "large, diced"). Do NOT convert a whole countable item to grams.
- If a countable item has a natural counting word, use it as the unit and keep food clean: "2 cloves garlic" → quantity 2, unit "clove", food "garlic"; likewise slices, cans, sprigs, heads, sticks, rashers.
- Put the ingredient name in "food" and descriptors in "note", so "food" stays clean and reusable.
- Strip brand / sponsor / trademark names out of "food" and move them to "note", keeping "food" generic: "1 package Philadelphia cream cheese" → food "cream cheese", note "Philadelphia, 1 package"; "Hellmann's mayonnaise" → food "mayonnaise", note "Hellmann's". If the brand IS the common name with no real generic, keep it as the food.
- Keep "food" to a SINGLE ingredient. If the source offers alternatives ("X or Y"), put X in "food" and "or Y" in "note".
- NEVER merge two different foods into one ingredient (e.g. "salt and pepper", "oil or lard" is fine as alternatives but "salt and pepper" is two foods). Emit a separate ingredient for each, even if they share an amount or are both "to taste".
- If there is no clear amount (e.g. "salt to taste"), set quantity to null and put the descriptor in "note".
- For a range like "1.2 to 1.4 kg", pick the higher number and note the range.
- Do NOT invent anything not shown in the source.{preset_rule}{cat_rule}{tag_rule}{extra}

Respond with ONLY a JSON object in exactly this shape — no markdown, no commentary:
{{"recipes": [{{"name": "...", "description": "...", "servings": 4, "yield": "4 servings", "ingredients": [{{"quantity": 1.4, "unit": "kg", "food": "ground beef", "note": "80/20", "title": "For the sauce"}}, {{"quantity": 2, "unit": null, "food": "egg", "note": null, "title": null}}, {{"quantity": 2, "unit": "clove", "food": "garlic", "note": null, "title": null}}], "instructions": ["..."], "tags": ["..."], "categories": ["Main Course"], "notes": [{{"title": "Storage", "text": "Keeps 3 days in the fridge."}}]}}]}}"""


# ── Image handling ─────────────────────────────────────────────────────
def image_to_data_url(path: str) -> str:
    """Open, resize, re-encode as JPEG, return a base64 data URL."""
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))  # in place, keeps aspect ratio
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


# ── AI rate limiting ───────────────────────────────────────────────────
_rpm_lock = threading.Lock()
_rpm_last = [0.0]


def _rpm_wait() -> None:
    """Enforce the configured AI requests-per-minute cap (AI_RPM_LIMIT). "" or 0 means
    no limit. Serializes LLM calls app-wide (including the cookbook background job) so
    a bulk import doesn't trip the provider's rate limit. Read live from config."""
    try:
        rpm = float(config.get("AI_RPM_LIMIT") or 0)
    except (TypeError, ValueError):
        rpm = 0
    if rpm <= 0:
        return
    gap = 60.0 / rpm
    with _rpm_lock:
        wait = _rpm_last[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _rpm_last[0] = time.monotonic()


# ── The core call (this is what the UI will import and use) ────────────
def _is_transient_err(err: Exception) -> bool:
    """Retry on provider rate-limit, network/timeout, and any HTTP 5xx."""
    import openai
    if isinstance(err, openai.RateLimitError):
        return True
    if isinstance(err, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    code = getattr(err, "status_code", None)
    return isinstance(code, int) and code >= 500


def _is_bad_request_err(err: Exception) -> bool:
    """A 400 — usually the backend rejecting response_format (json mode)."""
    import openai
    return isinstance(err, openai.BadRequestError)


def _call_with_retry(make_call):
    """Run ``make_call(use_json: bool) -> response`` with two safety nets:

    1. response_format fallback — if the backend 400s while json mode is on,
       disable it for the whole process and retry this call without it (so a
       non-Gemini backend that doesn't support response_format still works;
       a genuine 400 then surfaces on the second attempt).
    2. transient retry — rate-limit / connection / 5xx retried up to
       _MAX_AI_RETRIES times with exponential backoff.
    """
    global _USE_JSON_OBJECT
    for attempt in range(_MAX_AI_RETRIES + 1):
        try:
            return make_call(_USE_JSON_OBJECT)
        except Exception as e:
            if _is_bad_request_err(e) and _USE_JSON_OBJECT:
                _USE_JSON_OBJECT = False
                print("  ! backend rejected response_format=json_object — retrying without", file=sys.stderr)
                continue
            if not _is_transient_err(e) or attempt == _MAX_AI_RETRIES:
                raise
            time.sleep(_AI_RETRY_BACKOFF * (2 ** attempt))
    raise RuntimeError("AI call failed after retries")  # defensive — unreachable


def _structure(content: list, log=None) -> list[dict]:
    """Send a user-content payload (text prompt + images, or just text) to the
    LLM and return normalised structured recipes. Shared by every input path —
    same single call, same JSON shape out, regardless of input."""
    api_key = config.get("AI_API_KEY")
    if not api_key:
        raise RuntimeError("AI_API_KEY is not set — configure it in the setup page or .env.")

    base_url = config.get("AI_BASE_URL")
    model = config.get("AI_MODEL")
    if log:
        try: log(f"🤖 Connecting to AI Provider ({model} @ {base_url[:35]}...)")
        except Exception: pass

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=45.0)
    _rpm_wait()   # honour the configured AI requests/min cap (bulk imports)

    def make_call(use_json: bool):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
        }
        if use_json:
            # Constrain the model to valid JSON — hardens parse_recipes against the
            # occasional prose-wrapped reply. Gemini's OpenAI-compat endpoint supports
            # it; if a backend doesn't, _call_with_retry turns it off for the process.
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    if log:
        try: log("⏳ Waiting for AI completion...")
        except Exception: pass

    try:
        resp = _call_with_retry(make_call)
        raw = resp.choices[0].message.content or ""
        if log:
            try: log(f"📥 Received AI response ({len(raw)} chars). Structuring...")
            except Exception: pass
        recipes = _normalize(parse_recipes(raw))
        if log:
            try: log(f"✨ Successfully structured {len(recipes)} recipe(s)!")
            except Exception: pass
        return recipes
    except Exception as e:
        if log:
            try: log(f"❌ AI Error ({type(e).__name__}): {str(e)[:250]}")
            except Exception: pass
        raise


def extract_recipes(
    image_paths: list[str],
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    units_system: str = "metric",
) -> list[dict]:
    """Extract recipe(s) from one or more images."""
    # Multimodal message: the text prompt + every image.
    content = [{"type": "text", "text": build_user_prompt(target_language, user_note, known_categories=known_categories, units_system=units_system)}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
    return _structure(content)


def extract_recipes_from_text(
    text: str,
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    units_system: str = "metric",
) -> list[dict]:
    """Extract recipe(s) from pasted raw text — no image, no scrape. The text goes
    straight to the same structuring call. Handy as a manual fallback: paste an
    Instagram caption yourself when yt-dlp hits a login wall."""
    prompt = build_user_prompt(
        target_language, user_note,
        source="the recipe text below", known_categories=known_categories,
        units_system=units_system,
    )
    content = [{"type": "text", "text": f"{prompt}\n\n--- RECIPE TEXT ---\n{text}"}]
    return _structure(content)


# ── Document import (pdf / md / txt / eml) — Phase B6 ───────────────────
DOCUMENT_EXTS = (".pdf", ".md", ".markdown", ".txt", ".eml")


def is_document(filename: str) -> bool:
    """True for upload types we route through the text pipeline instead of vision."""
    return os.path.splitext(filename or "")[1].lower() in DOCUMENT_EXTS


def file_to_text(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded document so it can feed the SAME text
    structuring path as pasted text (one LLM call, same JSON out). Raises ValueError
    with a clear message when a PDF has no extractable text (i.e. it's scanned images)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return _pdf_to_text(data)
    if ext == ".eml":
        return _eml_to_text(data)
    # .md / .markdown / .txt / anything else text-like
    return data.decode("utf-8", errors="replace")


def _pdf_to_text(data: bytes) -> str:
    """Text from a PDF's text layer via pypdf (lazy import). Scanned/image-only PDFs
    have no text layer → clear ValueError telling the user to screenshot the pages."""
    from pypdf import PdfReader  # lazy: only needed on the PDF path

    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(
            f"Couldn't read this PDF ({type(e).__name__}). If it's a scanned/photo PDF, "
            "screenshot the pages and upload those instead."
        )
    if not text:
        raise ValueError(
            "Couldn't read text from this PDF — it may be scanned images. "
            "Screenshot the pages and upload those instead."
        )
    return text


def _eml_to_text(data: bytes) -> str:
    """Subject + body text from an .eml email (stdlib). Prefers the text/plain part;
    falls back to lightly stripping a text/html part."""
    import email
    import html as html_mod
    import re
    from email import policy

    msg = email.message_from_bytes(data, policy=policy.default)
    subject = (msg.get("subject", "") or "").strip()
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            if part.get_content_subtype() == "html":
                content = html_mod.unescape(re.sub(r"<[^>]+>", " ", content))
            body = content
    except Exception:
        body = ""
    if not body.strip():
        body = data.decode("utf-8", errors="replace")  # best-effort fallback
    parts = ([f"Subject: {subject}"] if subject else []) + [body]
    return "\n\n".join(parts).strip()


def extract_recipes_from_audio(
    audio_path: str,
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    units_system: str = "metric",
    progress=None,
) -> list[dict]:
    """Transcribe a voice note / dictation locally (faster-whisper), then structure the
    transcript through the SAME text pipeline. Handy for reading a recipe card aloud.
    `progress` (0..1 callback) is forwarded to the transcriber for the UI progress bar.
    Raises RuntimeError if transcription isn't enabled in this build."""
    import transcribe

    text = transcribe.transcribe_audio(audio_path, progress=progress)
    if not text.strip():
        raise ValueError(
            "Couldn't make out any speech in that audio — try again, a bit closer to the mic."
        )
    return extract_recipes_from_text(
        text, user_note=user_note, target_language=target_language,
        known_categories=known_categories, units_system=units_system,
    )


def extract_recipes_from_url(
    url: str,
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    known_tags=(),
    units_system: str = "metric",
) -> list[dict]:
    """Extract recipe(s) from a recipe-website URL.

    We scrape the page into rough source text (recipe-scrapers, with wild_mode
    so it falls back to schema.org for sites it doesn't explicitly support),
    then hand that text to the SAME LLM structuring step — so translation and
    the quantity/unit/food split work exactly as they do for images.
    Won't work on social posts (Instagram/TikTok) — screenshot those instead.
    """
    source_text, image_url = _scrape_url(url)
    prompt = build_user_prompt(
        target_language, user_note, source="the recipe text below",
        known_categories=known_categories, known_tags=known_tags, units_system=units_system
    )
    content = [{"type": "text", "text": f"{prompt}\n\n--- RECIPE SOURCE ---\n{source_text}"}]
    recipes = _structure(content)
    # carry the page's dish photo + source link through so push.py can attach them
    for r in recipes:
        if image_url:
            r["image_url"] = image_url
        r["source_url"] = url
    return recipes


def _assert_safe_url(url: str) -> None:
    """SSRF guard: reject a URL whose host resolves to a private / loopback /
    link-local / unspecified address (cloud-metadata endpoints like
    169.254.169.254, internal admin UIs, other LAN services). Called before any
    server-side fetch of a user-supplied URL. Raises ValueError on anything
    unsafe or unresolvable; conservative — blocks if ANY resolved address is
    internal (handles round-robin DNS)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        raise ValueError("That link has no hostname — can't fetch it.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"Couldn't resolve the host '{host}'.")
    for info in infos:
        ip = info[4][0]
        if "%" in ip:                       # strip a scoped IPv6 zone index (fe80::1%eth0)
            ip = ip.split("%", 1)[0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_unspecified or addr.is_multicast):
            raise ValueError(
                "Refusing to fetch that link — its host points at a private/internal "
                "address (SSRF guard). If it's genuinely a public recipe site, check the URL."
            )


def _extract_raw_html_text(html: str) -> str:
    """Fallback text extractor when recipe-scrapers finds no structured recipe.
    Extracts readable text from HTML for the LLM."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[:250])
    except Exception:
        clean = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", "\n", clean)
        lines = [line.strip() for line in clean.splitlines() if line.strip()]
        return "\n".join(lines[:250])


def _scrape_url(url: str) -> tuple[str, str]:
    """Fetch a URL and pull a rough recipe text block + the dish photo URL out
    of it. Raises a clear error if no structured recipe is found on the page."""
    import httpx
    from recipe_scrapers import scrape_html

    _assert_safe_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise ValueError("That site blocked automated access (403 Forbidden). Screenshot the recipe page and use the Photo tab instead.")
        raise ValueError(f"Site returned HTTP {e.response.status_code} ({e.response.reason_phrase}).")
    except Exception as e:
        raise ValueError(f"Could not reach site ({str(e)[:100]}).")

    if "Enable JavaScript and cookies to continue" in resp.text or "Access Denied" in resp.text:
        raise ValueError("That site requires JavaScript / Cloudflare verification. Screenshot the recipe page and use the Photo tab instead.")

    page_text = ""
    image_url = ""
    try:
        scraper = scrape_html(resp.text, org_url=url, wild_mode=True)
        parts: list[str] = []
        def grab(label, fn):
            try:
                val = fn()
            except Exception:
                return
            if not val:
                return
            if isinstance(val, (list, tuple)):
                val = "\n".join(str(v) for v in val)
            parts.append(f"{label}:\n{val}")

        grab("Title", scraper.title)
        grab("Yields", scraper.yields)
        grab("Ingredients", scraper.ingredients)
        grab("Instructions", scraper.instructions)

        try:
            image_url = scraper.image() or ""
        except Exception:
            image_url = ""

        page_text = "\n\n".join(parts).strip()
    except Exception:
        page_text = ""

    if not page_text.strip():
        page_text = _extract_raw_html_text(resp.text)

    if not page_text.strip():
        raise ValueError("No recipe text found on that web page — try screenshotting the recipe instead.")

    return page_text, image_url


# ── Social / video import (yt-dlp) — Phase 1: caption only ──────────────
VIDEO_HOSTS = (
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
    "facebook.com", "fb.watch",
)


def is_video_url(url: str) -> bool:
    """True if the URL is a social/video host we route through yt-dlp instead of
    the schema.org recipe scraper."""
    u = (url or "").lower()
    return any(host in u for host in VIDEO_HOSTS)


def _video_metadata(url: str) -> dict:
    """A social post's caption/description + thumbnail via yt-dlp — metadata only,
    no video download, no ffmpeg."""
    import yt_dlp  # lazy: only needed on the video path

    _assert_safe_url(url)
    opts = {"quiet": True, "skip_download": True, "noplaylist": True, "no_warnings": True, "socket_timeout": 15}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "thumbnail": info.get("thumbnail") or "",
    }


def extract_recipes_from_video(
    url: str,
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    units_system: str = "metric",
) -> list[dict]:
    """Extract a recipe from a social VIDEO post (TikTok / Reel / Short / FB).

    Phase 1: read the post's CAPTION (+ title) via yt-dlp and structure it with
    the SAME LLM call as every other path. Works when the creator wrote the recipe
    in the caption; if it's only spoken/shown in the video the caption is empty and
    we raise a clear 'screenshot it instead' error. The post thumbnail becomes the
    dish photo; the link is saved as the source.
    """
    try:
        meta = _video_metadata(url)
    except Exception as e:
        raise ValueError(
            "Couldn't read that social link — it may be private/login-walled or "
            f"unsupported. Screenshot the post and share the image instead. ({str(e)[:150]})"
        )

    title, caption = meta["title"], meta["description"]
    if not caption.strip():
        raise ValueError(
            "No recipe text in this post's caption (it may only be in the video). "
            "Screenshot the post and share the image instead."
        )

    parts = [f"Title: {title}"] if title else []
    parts.append(f"Caption:\n{caption}")
    source_text = "\n\n".join(parts)

    prompt = build_user_prompt(
        target_language, user_note,
        source="the social-media post text below", known_categories=known_categories,
        units_system=units_system,
    )
    content = [{"type": "text", "text": f"{prompt}\n\n--- POST TEXT ---\n{source_text}"}]
    recipes = _structure(content)
    for r in recipes:
        if meta["thumbnail"]:
            r["image_url"] = meta["thumbnail"]
        r["source_url"] = url
    return recipes


def extract_recipes_from_sources(
    image_paths: list[str] = (),
    url: str = "",
    text: str = "",
    doc_texts: list[str] = (),
    audio_path: str = "",
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
    known_tags=(),
    units_system: str = "metric",
    progress=None,
    log=None,
) -> list[dict]:
    """Combine any mix of sources (photos, link, pasted text, PDFs, voice note)
    into structured recipe(s). Single unified pipeline used by B3 combine jobs."""
    def _log(msg):
        if log:
            try: log(msg)
            except Exception: pass

    _log("🚀 Starting recipe extraction...")
    if progress:
        try:
            progress(0.1)
        except Exception:
            pass

    text_blocks: list[str] = []
    page_image_url = ""
    url_error: str | None = None

    if url:
        if is_video_url(url):
            try:
                _log(f"🎬 Reading social video post: {url}")
                meta = _video_metadata(url)
                cap = (meta.get("description") or meta.get("title") or "").strip()
                if cap.strip():
                    text_blocks.append(f"--- LINKED POST CAPTION ---\n{cap}")
                page_image_url = meta.get("thumbnail") or ""
                _log("✅ Read video metadata & thumbnail.")
            except Exception as e:
                url_error = str(e)
                _log(f"⚠️ Video metadata warning: {str(e)[:150]}")
        else:
            try:
                _log(f"🌐 Scraping web page: {url}")
                source_text, page_image_url = _scrape_url(url)
                if source_text.strip():
                    text_blocks.append(f"--- LINKED PAGE ---\n{source_text}")
                    _log(f"✅ Scraped recipe text ({len(source_text)} chars).")
            except Exception as e:
                url_error = str(e)
                _log(f"❌ URL Scrape error: {str(e)[:200]}")

    if text and text.strip():
        text_blocks.append(f"--- PASTED TEXT ---\n{text.strip()}")
        _log(f"📝 Added pasted text ({len(text)} chars).")

    for dt in doc_texts:
        if dt.strip():
            text_blocks.append(f"--- ATTACHED DOCUMENT ---\n{dt.strip()}")
            _log("📄 Added attached document text.")

    if audio_path:
        import transcribe
        _log("🎙 Transcribing audio dictation...")
        transcript = transcribe.transcribe_audio(audio_path)
        if transcript and transcript.strip():
            text_blocks.append(
                f"--- SPOKEN (transcribed from audio/video) ---\n{transcript.strip()}"
            )
            _log("✅ Audio transcribed successfully.")

    if not text_blocks and not image_paths:
        if url_error:
            raise ValueError(url_error)
        raise ValueError(
            "Nothing to extract from — add a photo, a link, some text, or a voice note / video."
        )

    prompt = build_user_prompt(
        target_language, user_note,
        source="the provided sources", known_categories=known_categories,
        known_tags=known_tags, units_system=units_system,
    )
    text_payload = prompt + ("\n\n" + "\n\n".join(text_blocks) if text_blocks else "")
    content = [{"type": "text", "text": text_payload}]

    import os
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
        _log(f"🖼 Attached photo payload ({os.path.basename(p)})")

    if progress:
        try:
            progress(0.4)
        except Exception:
            pass

    try:
        recipes = _structure(content, log=_log)
    except TypeError:
        recipes = _structure(content)
    for r in recipes:
        if page_image_url:
            r.setdefault("image_url", page_image_url)
        if url:
            r["source_url"] = url
    return recipes


def test_ai(base_url: str, model: str, api_key: str) -> tuple[bool, str]:
    """Validate the AI base/model/key with a tiny 1-token completion. Returns
    (ok, message). Validates the key AND the model name together. Used by the
    setup/settings page's Test button (costs ~1 token)."""
    base_url = (base_url or "").strip() or config.DEFAULTS["AI_BASE_URL"]
    model = (model or "").strip() or config.DEFAULTS["AI_MODEL"]
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Enter the AI API key first."
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
    except Exception as e:
        return False, f"AI call failed: {str(e)[:200]}"
    return True, f"AI reachable — model '{model}' responded."


def _normalize(recipes: list[dict]) -> list[dict]:
    """Default a 0 quantity to 1. 0 is never a real recipe quantity — the model
    uses it as a placeholder for 'no amount' (e.g. a topping listed by name),
    but 0 scales to 0 and shows "0 bell pepper" on shopping lists. 1 is a
    useful, scalable default; the human bumps or clears it in review."""
    for r in recipes:
        for ing in r.get("ingredients", []):
            if ing.get("quantity") == 0:
                ing["quantity"] = 1
            # section heading: clean string or None (most ingredients have none)
            t = (ing.get("title") or "").strip()
            ing["title"] = t or None
        # categories: coerce to a clean, de-duplicated list of non-empty strings
        cats = r.get("categories") or []
        if isinstance(cats, str):
            cats = [cats]
        clean: list[str] = []
        if isinstance(cats, (list, tuple)):
            for c in cats:
                c = str(c or "").strip()
                if c and c.lower() not in {x.lower() for x in clean}:
                    clean.append(c)
        r["categories"] = clean
        # notes: a clean list of {title, text} dicts. Drop entries with no text
        # (a bare title is useless), coerce both fields to stripped strings.
        notes = r.get("notes") or []
        if isinstance(notes, dict):
            notes = [notes]
        clean_notes: list[dict] = []
        if isinstance(notes, list):
            for n in notes:
                if not isinstance(n, dict):
                    continue
                title = str(n.get("title") or "").strip()
                text = str(n.get("text") or "").strip()
                if text:
                    clean_notes.append({"title": title, "text": text})
        r["notes"] = clean_notes
    return recipes


def parse_recipes(raw: str) -> list[dict]:
    """Robustly pull the JSON object out of the model's reply."""
    text = raw.strip()

    # strip ```json ... ``` fences if the model added them
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    # if there's stray prose around it, grab the outermost { ... }
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print("Could not parse JSON. Raw response was:\n", raw, file=sys.stderr)
        raise

    return data.get("recipes", [])


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Extract structured recipes from images or a URL.")
    ap.add_argument("images", nargs="*", help="one or more image files (same recipe or several)")
    ap.add_argument("--url", default="", help="extract from a recipe-website or social/video URL instead of images")
    ap.add_argument("--text", default="", help="extract from pasted recipe text instead of an image/URL")
    ap.add_argument("--file", default="", help="extract from a document file (.pdf/.md/.txt/.eml)")
    ap.add_argument("--audio", default="", help="extract from a voice note / audio file (faster-whisper)")
    ap.add_argument("--prompt", default="", help='extra instructions, e.g. "no mushrooms"')
    ap.add_argument("--lang", default="English", help="output language (default: English)")
    args = ap.parse_args()

    if not args.url and not args.images and not args.text and not args.file and not args.audio:
        ap.error("provide image file(s), --url, --text, --file, or --audio")
    try:
        if args.url:
            if is_video_url(args.url):
                recipes = extract_recipes_from_video(args.url, user_note=args.prompt, target_language=args.lang)
            else:
                recipes = extract_recipes_from_url(args.url, user_note=args.prompt, target_language=args.lang)
        elif args.audio:
            recipes = extract_recipes_from_audio(args.audio, user_note=args.prompt, target_language=args.lang)
        elif args.file:
            with open(args.file, "rb") as fh:
                doc_text = file_to_text(args.file, fh.read())
            recipes = extract_recipes_from_text(doc_text, user_note=args.prompt, target_language=args.lang)
        elif args.text:
            recipes = extract_recipes_from_text(args.text, user_note=args.prompt, target_language=args.lang)
        else:
            recipes = extract_recipes(args.images, user_note=args.prompt, target_language=args.lang)
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(recipes, indent=2, ensure_ascii=False))
    print(f"\n\u2713 extracted {len(recipes)} recipe(s)", file=sys.stderr)
