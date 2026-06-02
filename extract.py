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
import sys

from openai import OpenAI
from PIL import Image

import config

# Backend is swappable via the config layer: AI_BASE_URL / AI_MODEL can point at
# OpenRouter, OpenAI, a local model, whatever. Gemini 3.1 Flash Lite (free tier)
# is just the default value, not a hardcoded dependency. Values are read live
# from config.get() at call time (see _structure) so a saved config applies.

MAX_IMAGE_PX = 1200   # longest edge after resize — keeps payloads small/fast
JPEG_QUALITY = 85

# ── The prompt (this is what we'll tune together) ──────────────────────
SYSTEM_PROMPT = (
    "You are a precise recipe extraction engine. You read recipe images and "
    "output clean, structured data. You never invent ingredients or steps "
    "that aren't shown in the image."
)


def build_user_prompt(target_language: str, user_note: str, source: str = "the image(s)", known_categories=()) -> str:
    extra = f"\n\nExtra instructions from the user: {user_note}" if user_note.strip() else ""
    cat_rule = (
        "\n- For \"categories\", PREFER an existing category from this list when one "
        f"fits: {', '.join(known_categories)}. Only invent a new category name if "
        "none of them fit."
        if known_categories else ""
    )
    return f"""Extract every recipe in {source}.

If several images are provided, they may be parts of the SAME recipe (e.g.
ingredients on one screenshot, the method on another) — combine them into ONE
recipe. Only return multiple recipes if the images clearly show genuinely
different dishes.

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
- instructions: a list of step strings, in order
- tags: a short list of tags ("dinner", "vegetarian", ...), or []
- categories: a short list of category names that classify the dish ("Main Course", "Dessert", "Soup", "Breakfast", ...), translated into {target_language}, or []

Rules:
- Translate EVERYTHING (name, ingredients, steps, tags) into {target_language}.
- Convert amounts measured by weight or volume (flour, sugar, butter, liquids, meat, grains, ...) to metric (grams, millilitres). Keep tbsp/tsp/pinch as-is.
- Keep naturally COUNTABLE whole items as a count, never a weight — eggs, onions, lemons, peppers, bananas, potatoes, etc.: quantity = the number, unit = null, food = the item, with any size/prep in note ("2 large onions, diced" → quantity 2, unit null, food "onion", note "large, diced"). Do NOT convert a whole countable item to grams.
- If a countable item has a natural counting word, use it as the unit and keep food clean: "2 cloves garlic" → quantity 2, unit "clove", food "garlic"; likewise slices, cans, sprigs, heads, sticks, rashers.
- Put the ingredient name in "food" and descriptors in "note", so "food" stays clean and reusable.
- Keep "food" to a SINGLE ingredient. If the source offers alternatives ("X or Y"), put X in "food" and "or Y" in "note".
- NEVER merge two different foods into one ingredient (e.g. "salt and pepper", "oil or lard" is fine as alternatives but "salt and pepper" is two foods). Emit a separate ingredient for each, even if they share an amount or are both "to taste".
- If there is no clear amount (e.g. "salt to taste"), set quantity to null and put the descriptor in "note".
- For a range like "1.2 to 1.4 kg", pick the higher number and note the range.
- Do NOT invent anything not shown in the source.{cat_rule}{extra}

Respond with ONLY a JSON object in exactly this shape — no markdown, no commentary:
{{"recipes": [{{"name": "...", "description": "...", "servings": 4, "yield": "4 servings", "ingredients": [{{"quantity": 1.4, "unit": "kg", "food": "ground beef", "note": "80/20"}}, {{"quantity": 2, "unit": null, "food": "egg", "note": null}}, {{"quantity": 2, "unit": "clove", "food": "garlic", "note": null}}], "instructions": ["..."], "tags": ["..."], "categories": ["Main Course"]}}]}}"""


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


# ── The core call (this is what the UI will import and use) ────────────
def _structure(content: list) -> list[dict]:
    """Send a user-content payload (text prompt + images, or just text) to the
    LLM and return normalised structured recipes. Shared by the image and URL
    paths — same single call, same JSON shape out, regardless of input."""
    api_key = config.get("AI_API_KEY")
    if not api_key:
        raise RuntimeError("AI_API_KEY is not set — configure it in the setup page or .env.")

    client = OpenAI(base_url=config.get("AI_BASE_URL"), api_key=api_key)
    resp = client.chat.completions.create(
        model=config.get("AI_MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    raw = resp.choices[0].message.content or ""
    return _normalize(parse_recipes(raw))


def extract_recipes(
    image_paths: list[str],
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
) -> list[dict]:
    """Extract recipe(s) from one or more images."""
    # Multimodal message: the text prompt + every image.
    content = [{"type": "text", "text": build_user_prompt(target_language, user_note, known_categories=known_categories)}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
    return _structure(content)


def extract_recipes_from_url(
    url: str,
    user_note: str = "",
    target_language: str = "English",
    known_categories=(),
) -> list[dict]:
    """Extract recipe(s) from a recipe-website URL.

    We scrape the page into rough source text (recipe-scrapers, with wild_mode
    so it falls back to schema.org for sites it doesn't explicitly support),
    then hand that text to the SAME LLM structuring step — so translation and
    the quantity/unit/food split work exactly as they do for images.
    Won't work on social posts (Instagram/TikTok) — screenshot those instead.
    """
    source_text, image_url = _scrape_url(url)
    prompt = build_user_prompt(target_language, user_note, source="the recipe text below", known_categories=known_categories)
    content = [{"type": "text", "text": f"{prompt}\n\n--- RECIPE SOURCE ---\n{source_text}"}]
    recipes = _structure(content)
    # carry the page's dish photo + source link through so push.py can attach them
    for r in recipes:
        if image_url:
            r["image_url"] = image_url
        r["source_url"] = url
    return recipes


def _scrape_url(url: str) -> tuple[str, str]:
    """Fetch a URL and pull a rough recipe text block + the dish photo URL out
    of it. Raises a clear error if no structured recipe is found on the page."""
    import httpx
    from recipe_scrapers import scrape_html

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MealieMixer/1.0"}
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
    resp.raise_for_status()

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

    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError(
            "Couldn't find a recipe at that link — no structured recipe data on the page. "
            "(For social posts use the video path; if the recipe is only in the video, screenshot it.)"
        )
    return text, image_url


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

    opts = {"quiet": True, "skip_download": True, "noplaylist": True, "no_warnings": True}
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
    )
    content = [{"type": "text", "text": f"{prompt}\n\n--- POST TEXT ---\n{source_text}"}]
    recipes = _structure(content)
    for r in recipes:
        if meta["thumbnail"]:
            r["image_url"] = meta["thumbnail"]
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
        # categories: coerce to a clean, de-duplicated list of non-empty strings
        cats = r.get("categories") or []
        if isinstance(cats, str):
            cats = [cats]
        clean: list[str] = []
        for c in cats:
            c = str(c).strip()
            if c and c.lower() not in {x.lower() for x in clean}:
                clean.append(c)
        r["categories"] = clean
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
    ap.add_argument("--url", default="", help="extract from a recipe-website URL instead of images")
    ap.add_argument("--prompt", default="", help='extra instructions, e.g. "no mushrooms"')
    ap.add_argument("--lang", default="English", help="output language (default: English)")
    args = ap.parse_args()

    if not args.url and not args.images:
        ap.error("provide image file(s) or --url")
    try:
        if args.url:
            if is_video_url(args.url):
                recipes = extract_recipes_from_video(args.url, user_note=args.prompt, target_language=args.lang)
            else:
                recipes = extract_recipes_from_url(args.url, user_note=args.prompt, target_language=args.lang)
        else:
            recipes = extract_recipes(args.images, user_note=args.prompt, target_language=args.lang)
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(recipes, indent=2, ensure_ascii=False))
    print(f"\n\u2713 extracted {len(recipes)} recipe(s)", file=sys.stderr)
