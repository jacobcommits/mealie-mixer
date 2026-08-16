"""
Mealie Mixer — Mealie push layer (steps 3 + 4)

Takes a recipe dict (the shape extract.py produces) and creates it in Mealie.
By default ingredients are pushed STRUCTURED (step 4): each food + unit is
resolved against Mealie — created if missing — and real quantities are sent so
the recipe scales. Pass structured=False (CLI: --plain) for the step-3 mode
that pushes ingredients as plain strings, useful for debugging the bare flow.

push_recipe() is the reusable core — the UI (step 5) will call it.

Landmine-safe flow (see CLAUDE.md):
  1. POST  /api/recipes          -> create shell; returns slug as a plain string
  2. PATCH /api/recipes/{slug}   -> description      (one field per PATCH)
  3. PATCH /api/recipes/{slug}   -> recipeIngredient (structured, or plain w/ --plain)
  4. PATCH /api/recipes/{slug}   -> recipeInstructions (each step gets a UUID v4)
  Tags are skipped on purpose (v3 "Recipe already exists" PATCH bug).

Structured ingredients resolve foods via GET/POST /api/foods and units via
GET/POST /api/units, matched by lowercased name (both only need `name` to
create), then attach the full objects to each recipeIngredient.

Standalone usage:
    # secrets come from a gitignored .env (MEALIE_URL, MEALIE_TOKEN, AI_*)
    python extract.py shot.jpg > recipe.json
    python push.py recipe.json            # structured (default); dict OR list
    python push.py recipe.json --plain    # step-3 plain-string mode
    python push.py recipe.json --index 1  # pick a recipe when file is a list
"""

import argparse
import json
import sys
import uuid

import httpx

import config


def _mealie():
    """Current Mealie (base_url, token) — read live from the config layer."""
    return config.get("MEALIE_URL").rstrip("/"), config.get("MEALIE_TOKEN")


def _fmt_quantity(q) -> str:
    """1.0 -> '1', 1.4 -> '1.4'. Keeps whole numbers clean."""
    if isinstance(q, float) and q.is_integer():
        return str(int(q))
    return str(q)


def ingredient_to_text(ing: dict) -> str:
    """Flatten a structured ingredient dict into one readable line.

    Step 3 pushes ingredients as plain text, so we collapse
    quantity/unit/food/note into a single string here.
    """
    parts: list[str] = []
    if ing.get("quantity") is not None:
        parts.append(_fmt_quantity(ing["quantity"]))
    if ing.get("unit"):
        parts.append(str(ing["unit"]))
    if ing.get("food"):
        parts.append(str(ing["food"]))
    line = " ".join(parts)
    if ing.get("note"):
        line = f"{line}, {ing['note']}" if line else str(ing["note"])
    return line.strip()


def _extract_items(data) -> list:
    """Extract item list safely whether Mealie returns a paginated dict
    {"items": [...]} or a raw list [...]."""
    if isinstance(data, dict):
        return data.get("items", []) or []
    if isinstance(data, list):
        return data
    return []


# ── Step 4: structured ingredients (resolve-or-create foods + units) ─────
def _load_lookup(client: httpx.Client, endpoint: str) -> dict[str, dict]:
    """Fetch every food/unit (perPage=-1) and map lowercased name -> object."""
    r = client.get(endpoint, params={"perPage": -1})
    r.raise_for_status()
    items = _extract_items(r.json())
    return {it["name"].strip().lower(): it for it in items if isinstance(it, dict) and it.get("name")}


def fetch_food_names() -> list[str]:
    """All existing Mealie food names, sorted — feeds the UI's food autocomplete
    so the reviewer can snap variants ("black pepper") onto an existing food
    ("pepper") instead of spawning near-dupes. Returns [] if Mealie is unset."""
    url, token = _mealie()
    if not (url and token):
        return []
    with httpx.Client(
        base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        r = client.get("/api/foods", params={"perPage": -1})
        r.raise_for_status()
        items = _extract_items(r.json())
        return sorted({it["name"] for it in items if isinstance(it, dict) and it.get("name")})


def fetch_category_names() -> list[str]:
    """All existing Mealie category names, sorted. Feeds the recipe-categories
    autocomplete in review AND the extraction prompt (so the AI reuses existing
    categories instead of spawning near-dupes). Returns [] if Mealie is unset."""
    url, token = _mealie()
    if not (url and token):
        return []
    try:
        with httpx.Client(
            base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0
        ) as client:
            r = client.get("/api/organizers/categories", params={"perPage": -1})
            r.raise_for_status()
            items = _extract_items(r.json())
            return sorted({it["name"] for it in items if isinstance(it, dict) and it.get("name")})
    except Exception:
        return []


def fetch_tag_names() -> list[str]:
    """All existing Mealie tag names, sorted. Feeds the recipe-tags
    autocomplete in review AND the extraction prompt (so the AI reuses existing
    tags instead of spawning near-dupes). Returns [] if Mealie is unset."""
    url, token = _mealie()
    if not (url and token):
        return []
    try:
        with httpx.Client(
            base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0
        ) as client:
            r = client.get("/api/organizers/tags", params={"perPage": -1})
            r.raise_for_status()
            items = _extract_items(r.json())
            return sorted({it["name"] for it in items if isinstance(it, dict) and it.get("name")})
    except Exception:
        return []


def fetch_recipe_names() -> list[str]:
    """All existing Mealie recipe names, sorted. Feeds the review-step
    duplicate-name warning so the reviewer notices a name already in Mealie
    (which would otherwise create a 'recipe-2'). Returns [] if Mealie is unset."""
    return sorted(r["name"] for r in fetch_recipes())


def fetch_recipes() -> list[dict]:
    """All Mealie recipes as [{slug, name}], sorted by name. Powers the
    B9 'fix existing recipe' browse list."""
    url, token = _mealie()
    if not (url and token):
        return []
    with httpx.Client(
        base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        r = client.get("/api/recipes", params={"perPage": -1})
        r.raise_for_status()
        items = _extract_items(r.json())
        return sorted(
            [{"slug": it["slug"], "name": it["name"]} for it in items if isinstance(it, dict) and it.get("slug") and it.get("name")],
            key=lambda x: x["name"].lower(),
        )


def fetch_recipe(slug: str) -> dict:
    """GET /api/recipes/{slug} — full Mealie recipe JSON. Used by B9 to
    read an existing recipe before re-standardizing it."""
    url, token = _mealie()
    if not url or not token:
        raise RuntimeError("Mealie is not configured.")
    with httpx.Client(
        base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        r = client.get(f"/api/recipes/{slug}")
        r.raise_for_status()
        return r.json()


def recipe_to_text(mealie_recipe: dict) -> str:
    """Render a Mealie recipe JSON into a plain-text blob the LLM can
    re-structure. Extracts name, ingredient display strings, instruction
    texts, and existing category names."""
    parts: list[str] = []

    name = mealie_recipe.get("name") or ""
    if name:
        parts.append(f"Name: {name}")

    desc = mealie_recipe.get("description") or ""
    if desc:
        parts.append(f"Description: {desc}")

    servings = mealie_recipe.get("recipeServings")
    yield_str = mealie_recipe.get("recipeYield") or ""
    if servings or yield_str:
        parts.append(f"Servings: {servings or '?'}{(' · ' + yield_str) if yield_str else ''}")

    # Ingredients: prefer the display string (human-readable, present on
    # scraper-imported recipes). Fall back to structured food/unit/quantity.
    ings = mealie_recipe.get("recipeIngredient") or []
    if ings:
        ing_lines: list[str] = []
        for ing in ings:
            display = (ing.get("display") or "").strip()
            if display:
                ing_lines.append(display)
            else:
                # Build from structured fields
                p: list[str] = []
                q = ing.get("quantity")
                if q is not None and q != 0:
                    p.append(_fmt_quantity(q))
                u = ing.get("unit")
                if isinstance(u, dict):
                    u = u.get("name") or ""
                if u:
                    p.append(str(u))
                f = ing.get("food")
                if isinstance(f, dict):
                    f = f.get("name") or ""
                if f:
                    p.append(str(f))
                note = (ing.get("note") or "").strip()
                line = " ".join(p)
                if note:
                    line = f"{line}, {note}" if line else note
                if line.strip():
                    ing_lines.append(line.strip())
        if ing_lines:
            parts.append("Ingredients:\n" + "\n".join(ing_lines))

    # Instructions
    steps = mealie_recipe.get("recipeInstructions") or []
    if steps:
        step_texts = [s.get("text", "") for s in steps if s.get("text")]
        if step_texts:
            parts.append("Instructions:\n" + "\n".join(step_texts))

    # Categories
    cats = mealie_recipe.get("recipeCategory") or []
    if cats:
        cat_names = [c.get("name") or c for c in cats if (c.get("name") if isinstance(c, dict) else c)]
        if cat_names:
            parts.append("Categories: " + ", ".join(cat_names))

    # Tags
    tags = mealie_recipe.get("tags") or []
    if tags:
        tag_names = [t.get("name") or t for t in tags if (t.get("name") if isinstance(t, dict) else t)]
        if tag_names:
            parts.append("Tags: " + ", ".join(tag_names))

    # Notes
    notes = mealie_recipe.get("notes") or []
    if notes:
        note_lines = []
        for n in notes:
            title = (n.get("title") or "").strip()
            text = (n.get("text") or "").strip()
            if text:
                note_lines.append(f"{title}: {text}" if title else text)
        if note_lines:
            parts.append("Notes:\n" + "\n".join(note_lines))

    return "\n\n".join(parts)


def test_mealie(url: str, token: str) -> tuple[bool, str]:
    """Check a Mealie URL + token by hitting /api/users/self. Returns
    (ok, message). Used by the setup/settings page's Test button."""
    url = (url or "").strip().rstrip("/")
    token = (token or "").strip()
    if not url or not token:
        return False, "Enter both the Mealie URL and the token first."
    try:
        r = httpx.get(
            url + "/api/users/self",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return False, f"Couldn't reach Mealie at {url} ({type(e).__name__})."
    if r.status_code == 200:
        d = r.json()
        return True, f"Connected as {d.get('username', '?')} (group {d.get('group', '?')})."
    if r.status_code in (401, 403):
        return False, f"Reached Mealie, but the token was rejected (HTTP {r.status_code})."
    return False, f"Mealie returned HTTP {r.status_code}."


def upload_recipe_image(slug: str, content: bytes, filename: str = "photo.jpg") -> None:
    """Upload an image file as a recipe's photo. Mealie resizes + thumbnails it.
    PUT /api/recipes/{slug}/image — multipart: `image` file + `extension` field."""
    url, token = _mealie()
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else "jpg"
    ext = ext or "jpg"
    r = httpx.put(
        f"{url}/api/recipes/{slug}/image",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": (filename or f"photo.{ext}", content)},
        data={"extension": ext},
        timeout=30,
    )
    r.raise_for_status()


def _resolve(client: httpx.Client, endpoint: str, kind: str, name: str, cache: dict[str, dict]) -> dict:
    """Return the food/unit object for `name`, creating it in Mealie if absent.

    Matched by lowercased name. Caches hits AND freshly created objects, so the
    same food/unit is never created twice within a push. Both endpoints only
    require `name` to create.
    """
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    r = client.post(endpoint, json={"name": name.strip()})
    r.raise_for_status()
    obj = r.json()
    cache[key] = obj
    print(f"    + new {kind}: {name.strip()}", file=sys.stderr)
    return obj


def build_structured_ingredients(client: httpx.Client, recipe: dict) -> list[dict]:
    """Resolve each ingredient's food + unit to real Mealie objects (creating
    any that don't exist) and return structured recipeIngredient dicts.

    disableAmount is False here so amounts show and scale — the whole point of
    structuring. food/unit are sent as the full objects Mealie returned.
    """
    foods = _load_lookup(client, "/api/foods")
    units = _load_lookup(client, "/api/units")
    out: list[dict] = []
    for ing in recipe.get("ingredients", []):
        food = _resolve(client, "/api/foods", "food", ing["food"], foods) if ing.get("food") else None
        unit = _resolve(client, "/api/units", "unit", ing["unit"], units) if ing.get("unit") else None
        out.append(
            {
                "quantity": ing.get("quantity"),
                "unit": unit,
                "food": food,
                "note": ing.get("note") or "",
                "title": ing.get("title") or None,   # section heading (Mealie renders it above the row)
                "disableAmount": False,
            }
        )
    return out


def _clean_category(cat: dict) -> dict:
    """Sanitize a Mealie category object for PATCH payloads.
    Strips internal read-only fields (e.g. recipes, createdAt, updatedAt, userId)
    that can cause Mealie Pydantic validation errors (422 / 400)."""
    clean = {}
    if isinstance(cat, dict):
        if cat.get("id"):
            clean["id"] = str(cat["id"])
        if cat.get("name"):
            clean["name"] = str(cat["name"])
        if cat.get("slug"):
            clean["slug"] = str(cat["slug"])
    return clean


def _clean_tag(tag: dict) -> dict:
    """Sanitize a Mealie tag object for PATCH payloads."""
    clean = {}
    if isinstance(tag, dict):
        if tag.get("id"):
            clean["id"] = str(tag["id"])
        if tag.get("name"):
            clean["name"] = str(tag["name"])
        if tag.get("slug"):
            clean["slug"] = str(tag["slug"])
    return clean


def _format_mealie_error(e: Exception) -> Exception:
    """Format an httpx.HTTPStatusError to include Mealie's response status and body."""
    if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
        body = (e.response.text or "").strip()
        msg = f"Mealie HTTP {e.response.status_code}"
        if body:
            msg += f": {body[:300]}"
        return RuntimeError(msg)
    return e


def _patch_fields(client: httpx.Client, slug: str, recipe: dict, structured: bool = True) -> None:
    """PATCH an existing Mealie recipe's editable fields — one PATCH per field
    (Mealie landmine: combining fields causes vague 400s). Shared by both
    push_recipe (new) and update_recipe (existing). Does NOT create or delete
    the recipe shell — the caller owns that."""

    # Description
    if recipe.get("description"):
        r = client.patch(f"/api/recipes/{slug}", json={"description": recipe["description"]})
        r.raise_for_status()

    # Servings + yield
    yield_patch = {}
    if recipe.get("servings") is not None:
        yield_patch["recipeServings"] = recipe["servings"]
    if recipe.get("yield"):
        yield_patch["recipeYield"] = recipe["yield"]
    if yield_patch:
        r = client.patch(f"/api/recipes/{slug}", json=yield_patch)
        r.raise_for_status()

    # Ingredients
    if structured:
        ingredients = build_structured_ingredients(client, recipe)
    else:
        ingredients = [
            {
                "note": ingredient_to_text(ing),
                "quantity": None,
                "unit": None,
                "food": None,
                "title": None,
                "disableAmount": True,
            }
            for ing in recipe.get("ingredients", [])
        ]
    if ingredients:
        r = client.patch(f"/api/recipes/{slug}", json={"recipeIngredient": ingredients})
        r.raise_for_status()

    # Instructions — each step needs the full object with a UUID v4 id.
    instructions = [
        {
            "id": str(uuid.uuid4()),
            "title": "",
            "summary": "",
            "text": step,
            "ingredientReferences": [],
        }
        for step in recipe.get("instructions", [])
    ]
    if instructions:
        r = client.patch(f"/api/recipes/{slug}", json={"recipeInstructions": instructions})
        r.raise_for_status()

    # Categories — resolve-or-create each name.
    if "categories" in recipe:
        category_names = [c for c in (recipe.get("categories") or []) if c and str(c).strip()]
        cat_objs = []
        if category_names:
            cat_lookup = _load_lookup(client, "/api/organizers/categories")
            cat_objs = [
                _clean_category(_resolve(client, "/api/organizers/categories", "category", name, cat_lookup))
                for name in category_names
            ]
        r = client.patch(f"/api/recipes/{slug}", json={"recipeCategory": cat_objs})
        r.raise_for_status()

    # Tags — resolve-or-create each tag name.
    if "tags" in recipe:
        tag_names = [t for t in (recipe.get("tags") or []) if t and str(t).strip()]
        tag_objs = []
        if tag_names:
            tag_lookup = _load_lookup(client, "/api/organizers/tags")
            tag_objs = [
                _clean_tag(_resolve(client, "/api/organizers/tags", "tag", name, tag_lookup))
                for name in tag_names
            ]
        r = client.patch(f"/api/recipes/{slug}", json={"tags": tag_objs})
        r.raise_for_status()

    # Notes
    notes = [
        {"title": str(n.get("title") or "").strip(), "text": str(n.get("text") or "").strip()}
        for n in (recipe.get("notes") or [])
        if str(n.get("text") or "").strip()
    ]
    if notes:
        r = client.patch(f"/api/recipes/{slug}", json={"notes": notes})
        r.raise_for_status()

    # Source URL
    if recipe.get("source_url"):
        r = client.patch(f"/api/recipes/{slug}", json={"orgURL": recipe["source_url"]})
        r.raise_for_status()

    # Dish photo (URL imports): Mealie downloads from the URL. Non-fatal.
    if recipe.get("image_url"):
        try:
            r = client.post(f"/api/recipes/{slug}/image", json={"url": recipe["image_url"]})
            r.raise_for_status()
            print("  + attached dish photo", file=sys.stderr)
        except httpx.HTTPError as e:
            print(f"  ! couldn't attach photo (recipe still saved): {e}", file=sys.stderr)


def push_recipe(recipe: dict, client: httpx.Client | None = None, structured: bool = True) -> str:
    """Push one recipe dict to Mealie. Returns the created recipe's slug.

    Pass an existing httpx.Client to reuse a connection (e.g. batch pushes);
    otherwise one is created and closed for this call.
    """
    mealie_url, mealie_token = _mealie()
    if not mealie_url:
        raise RuntimeError("MEALIE_URL is not set — configure it in the setup page or .env.")
    if not mealie_token:
        raise RuntimeError("MEALIE_TOKEN is not set — configure it in the setup page or .env.")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            base_url=mealie_url,
            headers={"Authorization": f"Bearer {mealie_token}"},
            timeout=30,
        )

    slug = None       # set once the shell is created; used to roll back on failure
    finished = False  # flips true only after every step succeeds
    try:
        # 1. Create the shell with just the name.
        #    LANDMINE: the response body is the slug as a bare JSON string
        #    ("my-recipe"), NOT an object — so r.json() gives us the slug
        #    directly. Never .get("slug") on it.
        r = client.post("/api/recipes", json={"name": recipe["name"]})
        r.raise_for_status()
        slug = r.json()
        print(f"  created -> {slug}", file=sys.stderr)

        # 2–6. PATCH each field onto the shell.
        _patch_fields(client, slug, recipe, structured=structured)

        # Tags intentionally skipped — see CLAUDE.md landmine.

        finished = True
        print(f"  done -> {mealie_url}/g/home/r/{slug}", file=sys.stderr)
        return slug
    except Exception as e:
        # A failure mid-push leaves a half-created recipe (shell + some fields).
        # Roll it back so a failed push leaves nothing behind.
        if slug and not finished:
            print("  ! push failed — rolling back the partial recipe", file=sys.stderr)
            try:
                client.delete(f"/api/recipes/{slug}")
            except Exception:
                pass
        raise _format_mealie_error(e)
    finally:
        if owns_client:
            client.close()


def update_recipe(slug: str, recipe: dict, client: httpx.Client | None = None) -> str:
    """Update an EXISTING Mealie recipe in place (B9 re-standardize). Returns
    the slug. Uses the same _patch_fields logic as push_recipe but:
    - does NOT create a new recipe shell (the recipe already exists)
    - does NOT delete on failure (that would destroy the user's real recipe)
    - patches only the fields the app models; untouched fields (nutrition,
      assets, comments, ratings, tools, tags) survive because we never
      include them in the PATCH.
    """
    mealie_url, mealie_token = _mealie()
    if not mealie_url:
        raise RuntimeError("MEALIE_URL is not set — configure it in the setup page or .env.")
    if not mealie_token:
        raise RuntimeError("MEALIE_TOKEN is not set — configure it in the setup page or .env.")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            base_url=mealie_url,
            headers={"Authorization": f"Bearer {mealie_token}"},
            timeout=30,
        )

    try:
        # Update the name if it changed (its own PATCH, like description).
        if recipe.get("name"):
            r = client.patch(f"/api/recipes/{slug}", json={"name": recipe["name"]})
            r.raise_for_status()

        _patch_fields(client, slug, recipe, structured=True)
        print(f"  updated -> {mealie_url}/g/home/r/{slug}", file=sys.stderr)
        return slug
    except Exception as e:
        # CRITICAL: do NOT delete-on-failure — this is a real recipe the user
        # already has. Leave it as-is and surface the error.
        print(f"  ! update failed for {slug} — recipe left as-is", file=sys.stderr)
        raise _format_mealie_error(e)
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Push a recipe JSON into Mealie (step 3).")
    ap.add_argument("recipe_file", help="JSON file: a single recipe dict, or a list of them")
    ap.add_argument(
        "--index",
        type=int,
        default=0,
        help="which recipe to push when the file holds a list (0-based, default 0)",
    )
    ap.add_argument(
        "--plain",
        action="store_true",
        help="push ingredients as plain strings (step-3 mode) instead of structured",
    )
    args = ap.parse_args()

    with open(args.recipe_file, encoding="utf-8") as f:
        data = json.load(f)

    recipe = data[args.index] if isinstance(data, list) else data

    try:
        slug = push_recipe(recipe, structured=not args.plain)
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    except httpx.HTTPStatusError as e:
        # Surface Mealie's error body — it's where the useful detail lives.
        print(f"\nMealie rejected the request ({e.response.status_code}):", file=sys.stderr)
        print(e.response.text, file=sys.stderr)
        sys.exit(1)

    print(slug)
