# 🍲 Mealie Mixer

Drop in a recipe from **anywhere** — a screenshot, a link, pasted text, a PDF, a voice note, a screen-recording, or **any mix** — and get a clean, translated, fully-structured recipe in your [Mealie](https://mealie.io/) collection. **You review and edit before anything is saved.**

> Built for self-hosters who collect recipes from foreign-language blogs, social posts, cookbooks, and handwritten cards and want them landing in Mealie cleanly, without hand-typing.

---

## What it does

### Add new recipes
- **Many ways in:** upload **screenshots** (great for Instagram/TikTok posts), paste a **recipe URL** (blogs/recipe sites), paste **text** (from any source), upload a **document** (PDF, markdown, .txt, .eml), record a **voice note** (dictate a recipe), or upload a **screen-recording** of a reel.
- **Combine sources:** drop in any mix — e.g. ingredients from a reel caption + steps narrated in the video — and they merge into **one recipe** in a single LLM call.
- **Translate + standardise:** everything is translated to your target language; measured amounts are converted to your chosen system — **metric or imperial** — applied across every path (single recipes, cookbooks, and re-standardize); `tbsp`/`tsp`/`pinch` kept as-is; countable items (eggs, onions) stay as counts.
- **Structured ingredients:** each ingredient becomes `quantity / unit / food / note`, so recipes **scale** in Mealie and foods stay clean and reusable.
- **Review before save:** an editable preview (name, description, servings, yield, ingredients with section headings, steps, categories, notes) — **nothing is written to Mealie until you approve.**
- **Food autocomplete:** food fields autocomplete from your existing Mealie foods, so you snap variants onto an existing food instead of creating near-duplicates.
- **Categories:** the AI classifies each recipe, **reusing your existing Mealie categories** when one fits. You edit them in review; they're resolved-or-created on push.
- **Useful notes:** culinary tips (storage, substitutions, make-ahead) are extracted from the source and pushed to Mealie's recipe notes — blog-fluff is filtered out.
- **Dish photo:** URL imports grab the recipe's photo automatically, and you can **pick or snap a photo** during review.
- **Cookbook import:** upload a recipe-book PDF → it finds each recipe (with its photo) → you pick which to import → the AI structures them → bulk review + push.

### Fix existing recipes
- **🔧 Fix existing recipe** (new in v0.11): read a messy recipe back out of Mealie, run it through the same AI pipeline to clean up ingredients, translate, convert to your chosen units (metric or imperial), and re-structure it. Review and save back onto the same recipe — **update in place**, not a new one. Great for cleaning up scraper-imported recipes with unparsed ingredient strings.

### Other features
- **Multi-user family accounts** (v0.15.0–v0.17.0) — per-user accounts with optional friendly display names (`Dad`, `Mom`), self-service **My Account** password and display-name management, isolated per-user import history, and **persistent 30-day session cookies** so family members stay signed in on mobile devices.
- **Import history** — a log of what you've imported (with dedupe warnings), and the ability to restore a discarded review.
- **Installable PWA** — a polished, mobile-first interface featuring a **segmented source switcher tab bar** (Link, Photo, Voice, Text) with visual indicator badges and a **sticky mobile action bar**. **Fully offline-capable**: all assets and fonts are self-hosted, so the UI makes **no third-party/CDN calls**. **Auto light/dark** (follows your device) and respects reduced-motion settings. Add it to your phone's home screen for a native-app feel.
- **REST API** for external bots/agents (Telegram, etc.).
- **Voice notes** (opt-in build) — dictate a recipe or upload a screen-recording; local transcription via faster-whisper.

It's **one LLM call** per recipe — extraction, translation, and structuring all happen in that single call.

---

## How it works

```
any source(s)  →  extract (LLM)  →  editable review  →  push to Mealie
  screenshot        translate +        (you edit /          create + structured
  link / text        structure          autocomplete)        ingredients, categories + photo
  PDF / voice
```

Modules, kept UI-agnostic so the pipeline is reusable:

| File | Role |
|------|------|
| `extract.py` | Extraction core — images, URL, text, documents, audio → structured recipe JSON |
| `push.py` | Mealie side — create/update recipe, resolve/create foods + units + categories, attach photo |
| `core.py` / `config.py` | Config layer (env → volume → default) + validation |
| `api.py` | FastAPI REST API — extract, push, re-standardize, config, history |
| `app.py` | Thin FastAPI server — mounts the API + the static web UI |
| `static/` | Mobile-first web UI (Alpine.js, no build step) + PWA |
| `cookbook.py` | PDF cookbook splitter (recipe-per-page layout) |
| `jobs.py` | Background job runner (cookbook structuring, audio transcription) |
| `transcribe.py` | Local audio transcription via faster-whisper |
| `history.py` | SQLite import log (dedupe, restore discards) |

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

#### Optional: voice notes (dictation)

Voice-note transcription (faster-whisper, runs locally) is **off by default** to keep the image lean (~130 MB of extra wheels). The app hides the 🎤 controls automatically when it's not built in. To enable it, build with `WITH_VOICE=1`:

```bash
# Docker Compose
WITH_VOICE=1 docker compose build && docker compose up -d

# Podman
podman build --format docker --build-arg WITH_VOICE=1 -t mealie-mixer .
```

The Whisper model (size set by `WHISPER_MODEL`, default `base`) downloads to the `/data` volume on first use — the review screen shows a progress bar while it transcribes.

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

### Adding a new recipe

1. Upload a recipe screenshot, paste a link, paste text, upload a document, or record/upload audio — or **any combination**.
2. (Optional) add instructions ("no mushrooms") and pick the output language + units (metric/imperial).
3. Click **Make recipe**.
4. Review and edit the structured preview — fix anything the model got wrong, snap foods onto existing ones, adjust categories, add notes, optionally add a dish photo.
5. Click **✅ Approve & push**. Done.

### Fixing an existing recipe

1. Open the **☰ menu → 🔧 Fix existing recipe**.
2. Search or browse your Mealie recipes, pick one.
3. The AI re-structures it (cleans ingredients, translates, converts to your chosen units).
4. Review the result — edit anything.
5. Click **💾 Save changes** to update in place.

### Cookbook import

1. Open the **☰ menu → 📚 Cookbook import**.
2. Upload a recipe-book PDF.
3. Pick which recipes to import, then let the AI structure them.
4. Bulk-review and push the lot.

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

### Key endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/health` | none | `{"status": "ok", "configured": true}` |
| `POST` | `/api/extract` | API key | Upload image(s), URL, text, audio → structured recipe JSON |
| `POST` | `/api/push` | API key | Recipe JSON → create in Mealie, return slug + URL |
| `GET` | `/api/mealie-recipes` | session/key | List all Mealie recipes (slug + name) |
| `POST` | `/api/restandardize` | session/key | Fetch + AI re-structure an existing recipe |
| `POST` | `/api/recipes/{slug}/update` | session/key | Update an existing recipe in place |
| `GET` | `/api/history` | session/key | Import history |

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
  -d '{"name": "Test", "categories": ["Breakfast"], "ingredients": [{"quantity": 2, "unit": null, "food": "eggs"}], "instructions": ["Boil."]}'
```

---

## ⚠️ Security — read this

This app **can write to your Mealie**, and has **no authentication by default**.

- **LAN only. Do NOT expose port 7860 to the internet.** Anyone who can reach it can create/modify recipes in your Mealie.
- Turn on the optional login (`MIXER_AUTH_USER` + `MIXER_AUTH_PASS`) if others share your network.
- **Link import fetches arbitrary URLs server-side** (an SSRF surface). Another reason not to expose it.
- Keep your `.env` out of version control (it's gitignored) and **rotate keys** if they're ever exposed.

---

## Known limits (by design)

- **Social posts** (Instagram/TikTok) can't be *scraped* by link — they need auth and the recipe lives in a caption. **Screenshot those** or use the multi-source combine (link for the caption + screen-recording for the steps).
- Link import relies on the site embedding structured data (schema.org). Most recipe blogs do; some don't.
- Extraction aims for **~90% accuracy + human review**, not perfection. The review step is where you fix the long tail — that's the whole point of it.

---

## Running on boot

- **Docker:** `restart: unless-stopped` (already in `compose.yaml`) + an enabled Docker service restarts the container after a host reboot — nothing else needed.
- **Podman (rootless):** use the Quadlet unit in [`deploy/mealie-mixer.container`](deploy/mealie-mixer.container) (install it, `loginctl enable-linger`, `systemctl --user start mealie-mixer`).

> **Non-root note:** the container runs as a non-root user. A *fresh* `/data` volume inherits the right ownership automatically; an **existing root-owned volume** (from an earlier root run) must be recreated (`docker volume rm mealie-mixer-data`) or `chown`ed.

## Development

```bash
pip install -r requirements-dev.txt
pytest            # tests
ruff check .      # lint (config in pyproject.toml)
```

## License

[MIT](LICENSE) © jacobcommits
