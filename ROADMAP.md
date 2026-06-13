# Mealie Mixer — Roadmap & Decisions

**For Claude Code.** Read `CLAUDE.md` first (architecture + Mealie API landmines). This file is the prioritised next-work list, with code locations, **and the things we deliberately ruled OUT — do not revisit those.**

## Where we are (v0.7)
Shipped: image + URL + social-caption extraction (one shared LLM call), structured push (foods/units/categories resolve-or-create, servings/yield, orgURL, dish photo), sub-recipe sectioning (B1), useful-notes extraction (B2), editable review UI, PWA, REST API, rollback-on-failure. The core is solid. Everything below is refinement.

---

## Phase A — fixes + quick wins (target 0.7.x)

### A1. Re-add the count-vs-weight prompt rule  ✅ DONE (v0.7.1)  *(was a REGRESSION)*
`extract.py` → `build_user_prompt` rules. The current prompt says "convert all measurements to metric" with no exception, so countable items come back as weights. Add:
- Naturally countable items (eggs, onions, garlic cloves, peppers, lemons, slices…) → keep as a **count**: `quantity` = the number, `unit` = null, size/prep in `note`. Do NOT convert a whole countable item to a weight.
- Only use weight/volume for things normally measured that way (flour, sugar, butter, liquids, meat, grains).

### A2. Brand-name stripper  ✅ DONE (v0.7.2)  *(prompt rule, DB hygiene)*
`extract.py` rules. Strip sponsor/brand names out of `food`, move them to `note`. Example: "1 package Philadelphia cream cheese" → `food: "cream cheese"`, `note: "Philadelphia, 1 package"`. Keeps the food DB clean and reusable.

### A3. Raw-text / clipboard ingestion  ✅ DONE (v0.7.2)  *(highest ROI, nearly free)*
`extract.py` + UI. Add a third input path alongside image/URL: a textarea whose text goes straight to `build_user_prompt(source="the recipe text below")` + a text-only content block → `_structure()`. No image, no scrape. Also covers "paste an Instagram caption yourself" when yt-dlp hits a login wall.

### A4. Fix the social/video thumbnail  ✅ DONE (v0.7.3)  *(bug)*
`extract.py` video path (`info["thumbnail"]`) and URL path (`scraper.image()`) auto-attach a dish photo. For Instagram these are unreliable — first video frame, profile pic, or generic og:image, not the dish. **Don't silently attach it on the social path.** Surface it in the review preview as a *suggestion the human can clear or replace* (review already supports adding/snapping a photo). Optionally keep auto-attach only for proper recipe-site URL imports where the og:image is usually correct.

**Concrete UX (maintainer's ask):** on a social import, show the fetched thumbnail in the review preview with three clear choices — **keep it / replace it (pick or snap) / remove it (no photo)**. CURRENT GAP: the review can clear a *picked* photo (`photoFile`) via the ✕, but there's **no control to clear an auto-attached `recipe.image_url`** — only replace it. Fix = a remove (✕) on the hero/photorow that sets `recipe.image_url = ''` when no file is picked. (Could also mark the social thumbnail visually as "auto — check this is the dish".)

---

## Phase B — small features (target 0.8)

### B1. Sub-recipe sectioning  ✅ DONE (v0.7.4)  *(mostly already plumbed)*
`extract.py` prompt + `push.build_structured_ingredients`. The ingredient `title` field already exists in the structured dict but is hardcoded `None`, and **Mealie renders ingredient `title` as a section header.** So: have the AI emit a section title on the first ingredient of each part ("For the rub", "For the sauce"), and pass `ing.get("title")` through instead of `None`. Turns a wall of ingredients into clean sections.

### B2. Blog-fluff / useful-notes extraction  ✅ DONE (v0.7.5)  *(shipped broader: all text sources, editable in review)*
`extract.py` prompt + `push.py`. Pull genuine culinary tips (freezing instructions, substitutions, troubleshooting) out of the source text into a `notes` field, and push it to Mealie's recipe **notes** (a list of `{title, text}`). Skip the life-story padding; keep only useful notes.
**Shipped as:** added to the *shared* extraction prompt — URL, pasted text and social captions all produce notes; recipe-card images → `[]`. `_normalize` cleans them to clean `{title, text}` dicts (drops text-less entries). `push.py` PATCHes Mealie `notes` on its **own** PATCH (landmine-safe). `api.py` gained a `RecipeNote` model + `notes` on `Recipe`. The review screen has an **editable Notes section** (add/edit/remove) so blog fluff is pruned before push. Verified live in the container against a real recipe import.

### B3. Audio transcription via local faster-whisper  *(OPTIONAL — low priority)*
New transcription step feeding the existing `_structure()` pipeline. **Use faster-whisper locally** — do NOT migrate off the OpenAI SDK / native Gemini audio. Flow: audio → transcript text → existing pipeline.
- **Honest ROI:** spoken video/reel audio is the weakest source (vague amounts). The caption path already covers reels. The case that pays off is **dictation** — reading a recipe card aloud, where amounts are actually spoken. Justify this feature by dictation, not reels.
- Keep firmly behind the review step. Expect a rough draft the human completes.
- **Only after everything else (maintainer: "video pipeline maybe, after all").** If it happens: ship the heavy deps (ffmpeg + faster-whisper) in an **optional `mealie-mixer-video` image / compose profile** so the lean base image stays lean; **Whisper model-size selectable**, cached to `/data` at runtime; add **async jobs + a progress bar** (a slow import must not hang the UI). [folded in from the old social-import master plan]

**Status — core DONE; async+progress DONE; lean image DONE.** Core flow shipped last session: `transcribe.py` (lazy faster-whisper, model cached to `/data`, `is_available()` guard), `extract.extract_recipes_from_audio()`, `/api/extract` audio branch, `WHISPER_MODEL` config, health `voice` flag, CLI `--audio`, record/upload UI (gated on `cfgInfo.voice`). **Async + progress (this session):** voice notes now run as a background job (`jobs.start_audio_job` / `_process_audio_job`, persisted to `/data/audio` so they don't pollute the cookbook banner) — the browser starts the job via `POST /api/extract/audio`, polls `GET /api/extract/audio/{job_id}`, and shows a live progress bar (transcription fraction from faster-whisper segment end / clip duration) + phase (transcribing → structuring). The synchronous `/api/extract` audio branch stays for agents/CLI. **Lean image (this session):** faster-whisper is now gated behind a `WITH_VOICE` build arg (default `0`) — the base image is lean and voice is opt-in (`WITH_VOICE=1 docker compose build`, or `--build-arg WITH_VOICE=1` for podman). No runtime toggle / Settings button: a pip package can't be installed into a running container persistently, and the runtime already self-detects (`is_available()` → `voice` flag → UI auto-hides). The Whisper **model** still downloads to `/data` on first use (the progress bar covers it). **Not yet:** tab-close resume for audio jobs (cookbook has it; a single short voice note doesn't warrant it). **Needs live verification** (LAN-only): record/dictate a note end-to-end → progress bar → review → push.

### B4. Import history  *(small feature, /data)*  ← merged in, wanted
A SQLite log in the `/data` volume of what's been imported — source (url / slug / name), timestamp, status — with **dedupe** ("already imported") and a small **history screen** + `GET /api/history`. Standalone: useful for all imports, not just video. (A feature the other tools had and the maintainer liked. [folded in from the old master plan])

### B5. Nutrition estimation  *(MAYBE — low value)*
LLM estimates calories/macros → Mealie's `nutrition` fields (verify field names live). Applies to all recipes. Honest caveat: it's a guess, low accuracy. Only if wanted. [folded in from the old master plan]

### B8. Unified multi-source extraction  *(DONE — the capstone)*
Drop in ANY combination of sources — image(s), a recipe/social link, pasted text, documents, and a voice note / screen-recording — and forward all of it to ONE structuring call → a single recipe. Flips the old "one source wins" precedence (`url → files → text → audio`) into a **combine**.
- **The reel case it unlocks (and the answer to B3's "reel audio is the weakest source" caveat):** ingredients live in the **caption** (link), steps are the spoken **voice-over** (screen-recording). Neither alone is a full recipe; merged they are. So don't use the audio alone — pair it with the caption.
- **Video = audio only** (transcribed via the B3 path; PyAV decodes the `.mp4` audio track). No frame/OCR/on-screen-text reading — see RULED OUT.
- **DONE:** `extract.extract_recipes_from_sources()` builds one `_structure()` call from labeled provenance blocks (`--- LINKED POST CAPTION ---`, `--- PASTED TEXT ---`, `--- DOCUMENT ---`, `--- SPOKEN (transcribed) ---`) + image parts, with a merge hint in `build_user_prompt`. The async job generalized `start_audio_job → start_extract_job` (phases: fetching link → transcribing → structuring), persisted under `/data/extract`. Sync `/api/extract` now combines (back-compat: one field behaves as before); browser uses `POST /api/extract/job` + `GET /api/extract/job/{id}`. Additive input UI; upload accepts `video/*`. 86 tests green; verified locally (error paths + new UI). **Needs live combine check** (LAN): reel link + screen-recording → merged recipe → push.

### B9. Fix old Mealie recipes (AI re-standardize)  *(DONE)*
Read an existing recipe back out of Mealie → run it through the same structuring LLM to clean/split/translate/standardize → review/edit in the existing review screen → **save back onto the same recipe** (update in place, not a new one). The reverse direction of everything so far; ≈70% reuses existing code (Mealie client, review UI, `_structure`, `push_recipe`'s PATCH-per-field logic). Key risk: it's **destructive** — `update_recipe` must NOT delete-on-failure (unlike `push_recipe`) and must PATCH only edited fields. Full step-by-step plan: **`RESTANDARDIZE_PLAN.md`**.

---

## RULED OUT — do NOT build (decided; don't resurface these)

- **Divided-ingredient splitter** ("1 cup oil, divided" → two lines). The split amounts are usually *not stated*, so the model would hallucinate proportions. The review step + a "divided" note handles it fine. Skip.
- **Step-text scaling via bracketed numbers** ("Add the milk [2 cups]"). Mealie does **not** scale plain text inside instruction steps — this gives false confidence. The only correct mechanism is populating each step's `ingredientReferences`, which is too unreliable to auto-generate. Defer indefinitely; never do the bracket hack.
- **Full video download + visual-frame step extraction.** Caption-only (already built) is the tractable 80%. Downloading videos and analysing frames is high cost, low reliability. The only video-audio path worth any consideration is the optional faster-whisper item (B3).

---

## Standing reminders (from CLAUDE.md)
- Do NOT change the `AI_MODEL` default. Flag an unfamiliar name in text, but run with the configured value.
- Secrets via env/config only, never in files. LAN only (the app can write to Mealie).
- Target ~90% extraction + human review, not perfection — the review step absorbs the long tail.

## Suggested order
A1 + A2 (prompt edits, minutes) → A3 (raw text, the big quick win) → A4 (thumbnail fix) → B1 (sectioning) → B2 (notes) → **B4 (import history, next — wanted)** → B3 only if you want dictation; B5 only if wanted.
