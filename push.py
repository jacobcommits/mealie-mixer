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


# ── Step 4: structured ingredients (resolve-or-create foods + units) ─────
def _load_lookup(client: httpx.Client, endpoint: str) -> dict[str, dict]:
    """Fetch every food/unit (perPage=-1) and map lowercased name -> object."""
    r = client.get(endpoint, params={"perPage": -1})
    r.raise_for_status()
    items = r.json().get("items", [])
    return {it["name"].strip().lower(): it for it in items if it.get("name")}


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
        return sorted({it["name"] for it in r.json().get("items", []) if it.get("name")})


def fetch_category_names() -> list[str]:
    """All existing Mealie category names, sorted. Feeds the recipe-categories
    autocomplete in review AND the extraction prompt (so the AI reuses existing
    categories instead of spawning near-dupes). Returns [] if Mealie is unset."""
    url, token = _mealie()
    if not (url and token):
        return []
    with httpx.Client(
        base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        r = client.get("/api/organizers/categories", params={"perPage": -1})
        r.raise_for_status()
        return sorted({it["name"] for it in r.json().get("items", []) if it.get("name")})


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
                "title": None,
                "disableAmount": False,
            }
        )
    return out


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

    try:
        # 1. Create the shell with just the name.
        #    LANDMINE: the response body is the slug as a bare JSON string
        #    ("my-recipe"), NOT an object — so r.json() gives us the slug
        #    directly. Never .get("slug") on it.
        r = client.post("/api/recipes", json={"name": recipe["name"]})
        r.raise_for_status()
        slug = r.json()
        print(f"  created -> {slug}", file=sys.stderr)

        # 2. Description — its own PATCH (combining fields causes vague 400s).
        if recipe.get("description"):
            r = client.patch(
                f"/api/recipes/{slug}",
                json={"description": recipe["description"]},
            )
            r.raise_for_status()

        # 2b. Servings + yield — the base Mealie scales from. Its own PATCH.
        #     recipeServings is the numeric base; recipeYield is the readable
        #     label. Human can adjust both in the review step.
        yield_patch = {}
        if recipe.get("servings") is not None:
            yield_patch["recipeServings"] = recipe["servings"]
        if recipe.get("yield"):
            yield_patch["recipeYield"] = recipe["yield"]
        if yield_patch:
            r = client.patch(f"/api/recipes/{slug}", json=yield_patch)
            r.raise_for_status()

        # 3. Ingredients — its own PATCH.
        if structured:
            # Step 4: resolve/create foods + units, push real amounts so the
            # recipe scales.
            ingredients = build_structured_ingredients(client, recipe)
        else:
            # Step 3 fallback (--plain): flatten to plain strings, amounts off.
            # disableAmount only renders verbatim at the recipe level, but with
            # all amount fields null Mealie shows the note text anyway.
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
            r = client.patch(
                f"/api/recipes/{slug}",
                json={"recipeIngredient": ingredients},
            )
            r.raise_for_status()

        # 4. Instructions — its own PATCH.
        #    LANDMINE: each step must be the FULL object with a unique UUID v4
        #    id, or the PATCH 400s.
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
            r = client.patch(
                f"/api/recipes/{slug}",
                json={"recipeInstructions": instructions},
            )
            r.raise_for_status()

        # 5. Categories — its own PATCH. Unlike tags, recipeCategory PATCHes
        #    cleanly (verified live). Resolve-or-create each name against
        #    /api/organizers/categories — same pattern as foods/units.
        category_names = [c for c in (recipe.get("categories") or []) if c and str(c).strip()]
        if category_names:
            cat_lookup = _load_lookup(client, "/api/organizers/categories")
            cat_objs = [
                _resolve(client, "/api/organizers/categories", "category", name, cat_lookup)
                for name in category_names
            ]
            r = client.patch(f"/api/recipes/{slug}", json={"recipeCategory": cat_objs})
            r.raise_for_status()

        # Tags intentionally skipped — see CLAUDE.md landmine.

        # Dish photo (URL imports only): Mealie downloads it from the URL.
        # Non-fatal — some hosts block hotlinking; the recipe still lands.
        if recipe.get("image_url"):
            try:
                r = client.post(f"/api/recipes/{slug}/image", json={"url": recipe["image_url"]})
                r.raise_for_status()
                print("  + attached dish photo", file=sys.stderr)
            except httpx.HTTPError as e:
                print(f"  ! couldn't attach photo (recipe still saved): {e}", file=sys.stderr)

        print(f"  done -> {mealie_url}/g/home/r/{slug}", file=sys.stderr)
        return slug
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
