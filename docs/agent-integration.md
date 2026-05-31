# Agent integration

Mealie Mixer exposes a small REST API so an LLM agent or bot (e.g. a Telegram
bot) can drive it: send a screenshot/link, get a structured recipe back, let the
**user review it in chat**, then push the approved recipe to Mealie.

Replace `MIXER_HOST` with your instance, e.g. `http://192.168.1.50:7860`.

## Setup
1. In the app's **setup page** or **⚙️ Settings**, set an **API key**
   (`MIXER_API_KEY`) — click **🎲 Generate key** for a strong random one, copy
   it, and **Save**. (Blank key = API disabled, returns `503`.)
2. Give your agent the key + base URL, and the workflow below.

## Suggested agent workflow
1. User sends a recipe **screenshot** (or **link**).
2. Agent calls `POST /api/extract` → structured recipe JSON.
3. Agent **shows the recipe to the user** (name, servings, ingredients with
   quantities, suggested categories) and asks if it's right — it can edit
   quantities, foods, categories, etc.
4. **Only after the user confirms**, agent calls `POST /api/push`.
5. Agent shares the returned Mealie recipe URL.

> The chat is the review step — the agent must not push without the user's OK.

## Endpoints
Auth header on the two POSTs: `Authorization: Bearer <MIXER_API_KEY>`
(or `X-API-Key: <key>`). Interactive docs: `MIXER_HOST/docs` · OpenAPI:
`MIXER_HOST/openapi.json` (importable by many agent frameworks).

### `POST /api/extract`  (`multipart/form-data`)
- `files` — image(s), **or** `url` — a recipe page URL
- `language` *(optional, default `English`)*, `prompt` *(optional)*
- → `{ "recipes": [ <Recipe>, ... ] }`

```bash
curl -X POST MIXER_HOST/api/extract \
  -H "Authorization: Bearer $MIXER_API_KEY" \
  -F "files=@photo.jpg" -F "language=English"
```

### `POST /api/push`  (JSON body = one Recipe)
→ `{ "slug": "...", "url": "http://YOUR-MEALIE-HOST:9925/g/home/r/<slug>" }`

```bash
curl -X POST MIXER_HOST/api/push \
  -H "Authorization: Bearer $MIXER_API_KEY" \
  -H "Content-Type: application/json" -d @recipe.json
```

## Recipe shape
```json
{
  "name": "Classic Egg Spread",
  "description": "",
  "servings": 6,
  "yield": "6 sandwiches",
  "ingredients": [
    { "quantity": 2, "unit": null, "food": "eggs", "note": null },
    { "quantity": null, "unit": null, "food": "salt", "note": "to taste" }
  ],
  "instructions": ["Boil the eggs.", "Mix and season."],
  "tags": ["breakfast"],
  "categories": ["Breakfast"],
  "image_url": "https://..."
}
```
`quantity: null` = "to taste" (no amount). The recipe scales in Mealie, so
quantities matter — confirm them with the user.

`categories` — `/api/extract` suggests these, **reusing the user's existing
Mealie categories** where one fits and proposing a new name only when none do.
Show them to the user to confirm/edit; whatever you send in the `/api/push` body
is resolved-or-created and attached. (`tags` are accepted but currently **not**
written to Mealie — a Mealie v3 PATCH bug.)

## Errors
- `401` — missing/invalid API key
- `503` — API disabled (no key) or app not configured
- `502` — extraction or push failed (show the `detail`)

## Note
Screenshots work for anything (incl. social posts); `url` only works for recipe
sites that embed structured data, not Instagram/TikTok.
