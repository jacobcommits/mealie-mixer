"""
Mealie Mixer — cookbook PDF splitter (B7, dev/experimental).

Splits a multi-recipe cookbook PDF into per-recipe chunks for the bulk import flow:
each recipe page → its text + its hero dish photo. Pure pypdf + PIL, NO LLM — the
browser then structures each chunk through the existing /api/extract text path.

Heuristics (validated against two real cookbooks — a clean 1-recipe-per-page book and
a messy 92-page one with a table of contents, section dividers and decorative chrome):
  - a page is a recipe only if its text has BOTH "ingredient" and "direction"
    (drops table-of-contents, section dividers, food-safety / reference pages)
  - the dish photo is the LARGEST image on the page by pixel area (pages carry
    decorative images — bars, faded backgrounds — that we skip via a min-edge floor)
"""

import base64
import io

from PIL import Image  # noqa: F401  (kept explicit; pages give us PIL images)

MIN_IMAGE_EDGE = 200   # px — ignore decorative chrome smaller than this
MAX_RECIPES = 200      # safety cap on a single cookbook


def _page_text(page) -> str:
    """Page text in READING ORDER. pypdf's default mode reads multi-column recipe
    pages out of order (ingredients column before the title), which wrecks both the
    title guess and the AI's recipe name; `extraction_mode="layout"` keeps the visual
    order (title first). Falls back to default mode if layout yields nothing."""
    try:
        t = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        t = ""
    if not t.strip():
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
    return t


def _is_recipe_text(text: str) -> bool:
    t = text.lower()
    return "ingredient" in t and "direction" in t


def _guess_title(text: str) -> str:
    """Rough title for the selection list — best-effort; the LLM gets the real name
    later. pypdf's text order on multi-column pages can surface ingredient bullets or
    a nutrition line before the title, so we skip those and take the first title-like
    line. The thumbnail is the reliable cue; this is just a helpful label."""
    skip_starts = (
        "ingredient", "direction", "prep", "cook", "total", "yield", "serving",
        "region", "country", "table of contents", "nutrition", "recipe notes",
        "for more recipes", "www.", "makes ", "sodium", "calories",
    )
    bad_words = ("calorie", "sodium", "carbohydrate", "saturated fat", "per serving", "www.")
    bullet_chars = "•-–·*0123456789½¼¾⅓⅔⅛"
    for ln in [line.strip() for line in text.splitlines() if line.strip()][:40]:
        low = ln.lower()
        if low.startswith(skip_starts):
            continue
        if ln[0] in bullet_chars:                 # ingredient bullets / amounts
            continue
        if any(b in low for b in bad_words):
            continue
        if not (3 <= len(ln) <= 80) or not any(c.isalpha() for c in ln):
            continue
        return ln
    return "Untitled recipe"


def _largest_image_data_url(page) -> str | None:
    """The page's largest embedded image as a base64 JPEG data URL, or None."""
    best, best_area = None, 0
    try:
        images = list(page.images)
    except Exception:
        return None
    for img in images:
        try:
            pil = img.image          # PIL.Image
            w, h = pil.size
        except Exception:
            continue
        if w < MIN_IMAGE_EDGE or h < MIN_IMAGE_EDGE:
            continue
        if w * h > best_area:
            best, best_area = pil, w * h
    if best is None:
        return None
    if best.mode != "RGB":
        best = best.convert("RGB")
    buf = io.BytesIO()
    best.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def split_cookbook(pdf_bytes: bytes) -> list[dict]:
    """Split a cookbook PDF into per-recipe chunks: [{index, title, text, image}].
    `image` is a base64 JPEG data URL (or None). Only recipe pages are returned."""
    from pypdf import PdfReader  # lazy, like the other adapters

    reader = PdfReader(io.BytesIO(pdf_bytes))
    out: list[dict] = []
    for i, page in enumerate(reader.pages):
        text = _page_text(page).strip()
        if not _is_recipe_text(text):
            continue
        out.append({
            "index": i,
            "title": _guess_title(text),
            "text": text,
            "image": _largest_image_data_url(page),
        })
        if len(out) >= MAX_RECIPES:
            break
    return out
