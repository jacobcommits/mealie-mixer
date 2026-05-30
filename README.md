# 🍲 Mealie Mixer

Turn a recipe **screenshot** or **link** into a clean, translated, fully-structured recipe in your [Mealie](https://mealie.io/) collection — with a **human review step before anything is saved**.

Point it at a photo of a recipe (even in another language) or a recipe URL. A vision/text LLM reads it, translates it, converts amounts to metric, and splits every ingredient into `quantity / unit / food / note`. You review and edit the result in a web UI, then push it to Mealie with one click — foods and units resolved into real, scalable Mealie entries, and the dish photo attached.

> Built for self-hosters who collect recipes from foreign-language blogs and social posts and want them landing in Mealie cleanly, without hand-typing.

---

## What it does

- **Two ways in:** upload one or more **screenshots** (great for Instagram/TikTok posts), or paste a **recipe URL** (blogs/recipe sites).
- **Translate + standardise:** everything is translated to your target language; measured amounts are converted to metric; `tbsp`/`tsp`/`pinch` kept as-is.
- **Structured ingredients:** each ingredient becomes `quantity / unit / food / note`, so recipes **scale** in Mealie and foods stay clean and reusable.
- **Review before save:** an editable preview (name, description, servings, yield, ingredients, steps) — **nothing is written to Mealie until you click Approve.**
- **Food autocomplete:** food fields autocomplete from your existing Mealie foods, so you snap variants ("black pepper") onto an existing food ("pepper") instead of creating near-duplicates.
- **Dish photo:** URL imports grab the recipe's photo and attach it to the Mealie recipe.
- **Multiple recipes:** if a screenshot/link contains several recipes, they queue up and you review them one at a time.

It's **one LLM call** per recipe — extraction, translation, and structuring all happen in that single call.

---

## How it works

```
screenshot / link  →  extract (LLM)  →  editable review  →  push to Mealie
                       translate +        (you edit /          create + structured
                       structure          autocomplete)        ingredients + photo
```

Three modules, kept UI-agnostic so the pipeline is reusable:

| File | Role |
|------|------|
| `extract.py` | Extraction core — images or URL → structured recipe JSON (vision LLM / `recipe-scrapers`) |
| `push.py` | Mealie side — create recipe, resolve/create foods + units, attach photo |
| `app.py` | Gradio review UI — imports the two above |

---

## Requirements

- A running **Mealie** instance and a **long-lived API token** (Mealie → *Profile → API Tokens*).
- An **OpenAI-compatible vision LLM** endpoint + API key. The default is **Google Gemini** (free tier via [Google AI Studio](https://aistudio.google.com/)), but the backend is swappable — point it at OpenAI, OpenRouter, or a local model.

---

## Setup

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

```ini
# AI backend (extraction) — swappable; Gemini via Google AI Studio is the default
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
AI_MODEL=gemini-3.1-flash-lite
AI_API_KEY=your-google-ai-studio-key

# Mealie (push)
MEALIE_URL=http://your-mealie-host:9925
MEALIE_TOKEN=your-long-lived-mealie-token

# Optional UI login — set BOTH to require a username/password; blank = no auth
MIXER_AUTH_USER=
MIXER_AUTH_PASS=

# Optional: enable the REST API for external bots/agents; blank = API disabled
MIXER_API_KEY=
```

### Run with Docker / Podman (recommended)

**Docker Compose:**
```bash
docker compose up -d --build
```

**Podman** (Fedora's default — no compose needed):
```bash
podman build --format docker -t mealie-mixer .   # --format docker keeps the HEALTHCHECK
podman run -d --name mealie-mixer \
  --env-file .env -v mealie-mixer-data:/data \
  -p 7860:7860 --restart unless-stopped mealie-mixer
```
*(Docker users: plain `docker build` — the HEALTHCHECK works automatically.)*

Open **http://localhost:7860** (or the host's LAN IP). Config comes from your `.env` if present, otherwise from the in-app setup page (persisted to the `/data` volume).

### Run locally

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

### First run (setup page)

You don't need a `.env` — if config is missing, the app opens straight to a **setup page**:

1. Open **http://localhost:7860**.
2. Enter your **Mealie URL + API token** and your **AI key** (base URL/model default to Gemini, but are editable). Hit **Test** on each to verify before saving.
3. Optionally set a **login** (username + password) to require sign-in. Leave blank for none — but then keep it LAN-only.
4. **Save**, then **restart the container** to apply.

Settings are stored on the **`/data` volume**, so you do this once. Change them later anytime via the **⚙️ Settings** panel inside the app (Mealie/AI changes apply immediately; a login change needs a restart). Env vars / `.env` still work and take precedence if you prefer that.

---

## Usage

1. Upload a recipe screenshot **or** paste a recipe link.
2. (Optional) add instructions ("no mushrooms") and pick the output language.
3. Click **Extract recipe**.
4. Review and edit the structured preview — fix anything the model got wrong, snap foods onto existing ones via the dropdowns, clear a quantity to leave it blank.
5. Click **Approve & push to Mealie**. Done.

**CLI** (no UI):

```bash
./venv/bin/python extract.py shot.jpg --lang English > recipe.json
./venv/bin/python extract.py --url https://example.com/recipe --lang English > recipe.json
./venv/bin/python push.py recipe.json
```

---

## API (for bots / agents)

Mealie Mixer exposes a REST API so an external agent (e.g. a Telegram bot) can drive extraction and push programmatically — no browser needed.

**Enable it** by setting `MIXER_API_KEY` (env var, `.env`, or the Settings panel — there's a **🎲 Generate key** button). The API is **disabled by default** (fail-closed: empty key → 503).

Interactive docs (Swagger UI) are at **`/docs`** once the server is running.

📄 **Building an agent/bot?** See **[docs/agent-integration.md](docs/agent-integration.md)** for a ready-to-use integration guide (workflow + endpoints + recipe shape).

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/health` | none | `{"status": "ok", "configured": true}` |
| `POST` | `/api/extract` | API key | Upload image(s) or a URL → structured recipe JSON |
| `POST` | `/api/push` | API key | Recipe JSON → create in Mealie, return slug + URL |

Auth: send `Authorization: Bearer <MIXER_API_KEY>` or `X-API-Key: <key>`.

### Examples

```bash
# Health check (no auth)
curl http://localhost:7860/api/health

# Extract from an image
curl -X POST http://localhost:7860/api/extract \
  -H "Authorization: Bearer $MIXER_API_KEY" \
  -F "files=@recipe-screenshot.jpg" \
  -F "language=English"

# Extract from a URL
curl -X POST http://localhost:7860/api/extract \
  -H "Authorization: Bearer $MIXER_API_KEY" \
  -F "url=https://example.com/recipe" \
  -F "language=English"

# Push a recipe to Mealie
curl -X POST http://localhost:7860/api/push \
  -H "Authorization: Bearer $MIXER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test", "ingredients": [{"quantity": 2, "unit": null, "food": "eggs"}], "instructions": ["Boil."]}'
```

---

## ⚠️ Security — read this

This app **can write to your Mealie**, and has **no authentication by default**.

- **LAN only. Do NOT expose port 7860 to the internet.** Anyone who can reach it can create recipes in your Mealie.
- Turn on the optional login (`MIXER_AUTH_USER` + `MIXER_AUTH_PASS`) if others share your network.
- **Link import fetches arbitrary URLs server-side** (an SSRF surface). Another reason not to expose it.
- Keep your `.env` out of version control (it's gitignored) and **rotate keys** if they're ever exposed.

---

## Known limits (by design)

- **Social posts** (Instagram/TikTok) can't be *scraped* by link — they need auth and the recipe lives in a caption. **Screenshot those instead** (that's what the image path is for).
- Link import relies on the site embedding structured data (schema.org). Most recipe blogs do; some don't.
- Extraction aims for **~90% accuracy + human review**, not perfection. The review step is where you fix the long tail (odd quantities, food name variants, etc.) — that's the whole point of it.

---

## Running on boot

- **Docker:** `restart: unless-stopped` (already in `compose.yaml`) + an enabled Docker service restarts the container after a host reboot — nothing else needed.
- **Podman (rootless):** use the Quadlet unit in [`deploy/mealie-mixer.container`](deploy/mealie-mixer.container) (install it, `loginctl enable-linger`, `systemctl --user start mealie-mixer`).

> **Non-root note:** the container runs as a non-root user. A *fresh* `/data` volume inherits the right ownership automatically; an **existing root-owned volume** (from an earlier root run) must be recreated (`docker volume rm mealie-mixer-data`) or `chown`ed.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## License

[MIT](LICENSE) © jacobcommits
