"""
Mealie Mixer — cookbook background jobs (B7 Phase B, dev).

Structures a batch of selected cookbook recipes server-side in a background thread, so
the user can close the tab and come back. Each job is persisted to the /data volume so
progress survives a restart. Thin layer over the existing text-structuring path
(extract.extract_recipes_from_text) — one call per recipe, throttled.

Single-user / LAN scope: a simple in-memory registry + one JSON file per job.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone

import config

# Rate limiting lives at the LLM choke point (extract._rpm_wait, config AI_RPM_LIMIT),
# so each structure_fn call self-throttles — no extra gap needed in this loop.
SUMMARY_KEYS = ("id", "status", "total", "done", "failed", "label", "created_at")

JOBS: dict[str, dict] = {}        # in-memory live state (the structuring thread mutates these)
_LOCK = threading.Lock()


def _jobs_dir(kind: str = "cookbook") -> str:
    # Jobs live under /data/<kind> so the different kinds (cookbook batch, audio note)
    # don't share a directory — list_jobs() scans only "cookbook" for the review banner.
    return os.path.join(config.DATA_DIR, kind)


def _job_path(job_id: str, kind: str = "cookbook") -> str:
    return os.path.join(_jobs_dir(kind), f"job-{job_id}.json")


def _flush(job: dict) -> None:
    """Persist a job to disk (atomic). Called only from the owning worker thread."""
    kind = job.get("kind", "cookbook")
    os.makedirs(_jobs_dir(kind), exist_ok=True)
    tmp = _job_path(job["id"], kind) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f)
    os.replace(tmp, _job_path(job["id"], kind))


def _load(job_id: str, kind: str = "cookbook") -> dict | None:
    try:
        with open(_job_path(job_id, kind), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _structure_default(text: str, language: str, units_system: str):
    from extract import extract_recipes_from_text
    return extract_recipes_from_text(text, target_language=language, units_system=units_system)


def _process_job(job: dict, chunks: list[dict], language: str, units_system: str, structure_fn=None) -> dict:
    """The structuring loop — pure + testable. Mutates `job` in place, flushing after each
    recipe so a crash/restart keeps progress. `structure_fn(text, language, units_system) -> [recipe...]`."""
    structure_fn = structure_fn or _structure_default
    for ch in chunks:
        if job.get("cancelled"):       # Stop button — checked between recipes
            break
        try:
            recs = structure_fn(ch.get("text", ""), language, units_system) or []
            with _LOCK:
                for rec in recs:
                    job["recipes"].append({"recipe": rec, "image": ch.get("image")})
                job["done"] += 1
        except Exception:
            with _LOCK:
                job["failed"] += 1
                job["done"] += 1
        _flush(job)
    with _LOCK:
        job["status"] = "cancelled" if job.get("cancelled") else "done"
    _flush(job)
    return job


def cancel_job(job_id: str) -> bool:
    """Ask a running job to stop (checked between recipes — can't interrupt a live LLM
    call). Returns True if the job was found in memory."""
    with _LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False
        job["cancelled"] = True
        job["status"] = "cancelled"
    return True


def start_job(chunks: list[dict], language: str = "English", units_system: str = "metric") -> str:
    """Create a job for the selected chunks and run it in a daemon thread. Returns the id."""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "status": "running", "total": len(chunks), "done": 0, "failed": 0,
        "label": (chunks[0].get("title") or "").strip() if chunks else "",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recipes": [],
    }
    with _LOCK:
        JOBS[job_id] = job
    _flush(job)
    threading.Thread(target=_process_job, args=(job, chunks, language, units_system), daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    """A snapshot of one job (deep-copied under the lock so it's safe to serialize while the
    thread is still appending)."""
    with _LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            return json.loads(json.dumps(job))
    return _load(job_id)


def list_jobs(limit: int = 10) -> list[dict]:
    """Recent job summaries (no recipe payloads), newest first — disk ∪ memory."""
    found: dict[str, dict] = {}
    try:
        for fn in os.listdir(_jobs_dir()):
            if fn.startswith("job-") and fn.endswith(".json"):
                j = _load(fn[len("job-"):-len(".json")])
                if j:
                    found[j["id"]] = j
    except FileNotFoundError:
        pass
    with _LOCK:
        found.update(JOBS)   # in-memory is fresher
    ordered = sorted(found.values(), key=lambda j: j.get("created_at", ""), reverse=True)[:limit]
    return [{k: j.get(k) for k in SUMMARY_KEYS} for j in ordered]


# ── unified extraction jobs (B3 voice → multi-source combine) ────────────────
# Some sources are slow — whisper transcription (esp. the first run, which downloads the
# model to /data) and link scraping/yt-dlp — so a combine runs in a background thread and
# the browser polls get_job(), rather than blocking the /api/extract request. `sources` is
# a dict {image_paths, url, text, doc_texts, audio_path, _tmp_paths}.

def _extract_sources_default(sources, user_note, language, known_categories, units_system, progress):
    from extract import extract_recipes_from_sources
    return extract_recipes_from_sources(
        image_paths=sources.get("image_paths", []),
        url=sources.get("url", ""),
        text=sources.get("text", ""),
        doc_texts=sources.get("doc_texts", []),
        audio_path=sources.get("audio_path", ""),
        user_note=user_note, target_language=language,
        known_categories=known_categories, units_system=units_system,
        progress=progress,
    )


def _process_extract_job(job, sources, language, user_note, known_categories, units_system,
                         extract_fn=None) -> dict:
    """Combine the given sources into recipe(s). Mutates `job` in place (status/phase/progress)
    and always removes the temp upload files in `sources['_tmp_paths']`. `extract_fn(sources,
    note, lang, cats, progress)` is injectable for tests."""
    extract_fn = extract_fn or _extract_sources_default

    def on_progress(frac):
        with _LOCK:
            job["progress"] = round(float(frac), 3)
            if job["phase"] in ("fetching link", "transcribing"):
                job["phase"] = "transcribing" if frac < 0.999 else "structuring"

    try:
        recs = extract_fn(sources, user_note, language, known_categories, units_system, on_progress) or []
        with _LOCK:
            for rec in recs:
                job["recipes"].append({"recipe": rec, "image": rec.get("image_url")})
            job["done"] = 1
            job["progress"] = 1.0
            job["phase"] = "done"
            job["status"] = "done"
    except Exception as e:
        with _LOCK:
            job["status"] = "error"
            job["failed"] = 1
            job["error"] = str(e)
    finally:
        for p in sources.get("_tmp_paths", []):
            try:
                os.remove(p)
            except OSError:
                pass
        _flush(job)
    return job


def start_extract_job(sources: dict, language: str = "English", user_note: str = "",
                     known_categories=(), units_system: str = "metric") -> str:
    """Kick off a combine extraction in a daemon thread; returns the id the browser polls."""
    job_id = uuid.uuid4().hex[:12]
    # Opening phase reflects the first slow step the job will hit (audio dominates; else a
    # link fetch; else straight to the LLM call).
    phase = ("transcribing" if sources.get("audio_path")
             else "fetching link" if sources.get("url")
             else "structuring")
    job = {
        "id": job_id, "kind": "extract", "status": "running", "phase": phase,
        "progress": 0.0, "total": 1, "done": 0, "failed": 0, "recipes": [],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        JOBS[job_id] = job
    _flush(job)
    threading.Thread(
        target=_process_extract_job,
        args=(job, sources, language, user_note, list(known_categories), units_system),
        daemon=True,
    ).start()
    return job_id
