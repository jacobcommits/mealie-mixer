import config
import jobs


def test_process_job_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    chunks = [
        {"text": "recipe A", "image": "imgA", "title": "A"},
        {"text": "BOOM", "image": "imgB", "title": "B"},      # this one fails
        {"text": "recipe C", "image": None, "title": "C"},
    ]

    def stub(text, language):
        if text == "BOOM":
            raise RuntimeError("nope")
        return [{"name": text.upper()}]

    job = {"id": "t1", "status": "running", "total": 3, "done": 0, "failed": 0,
           "label": "A", "created_at": "2026-01-01T00:00:00+00:00", "recipes": []}
    out = jobs._process_job(job, chunks, "English", structure_fn=stub)

    assert out["status"] == "done"
    assert out["done"] == 3 and out["failed"] == 1
    assert len(out["recipes"]) == 2                          # A and C, B failed
    assert out["recipes"][0]["recipe"]["name"] == "RECIPE A"
    assert out["recipes"][0]["image"] == "imgA"
    # progress was persisted and is reloadable (survives a restart)
    loaded = jobs._load("t1")
    assert loaded["status"] == "done" and len(loaded["recipes"]) == 2


def test_process_job_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    job = {"id": "c1", "status": "running", "total": 3, "done": 0, "failed": 0,
           "label": "A", "created_at": "t", "recipes": [], "cancelled": False}
    chunks = [{"text": "a", "image": None, "title": "a"} for _ in range(3)]
    calls = {"n": 0}

    def stub(text, language):
        calls["n"] += 1
        if calls["n"] == 1:
            job["cancelled"] = True            # Stop requested after the first recipe
        return [{"name": "R" + str(calls["n"])}]

    out = jobs._process_job(job, chunks, "English", structure_fn=stub)
    assert out["status"] == "cancelled"
    assert out["done"] == 1 and len(out["recipes"]) == 1   # loop broke before the 2nd


def test_cancel_job_sets_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    jobs.JOBS.clear()
    jobs.JOBS["x1"] = {"id": "x1", "status": "running", "cancelled": False}
    assert jobs.cancel_job("x1") is True
    assert jobs.JOBS["x1"]["cancelled"] is True
    assert jobs.cancel_job("missing") is False


def test_get_job_and_list_summaries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    jobs.JOBS.clear()
    job = {"id": "disk1", "status": "done", "total": 1, "done": 1, "failed": 0, "label": "X",
           "created_at": "2026-01-02T00:00:00+00:00", "recipes": [{"recipe": {"name": "X"}, "image": None}]}
    jobs._flush(job)   # disk-only (simulates a job from before a restart)

    got = jobs.get_job("disk1")
    assert got["status"] == "done" and got["recipes"][0]["recipe"]["name"] == "X"
    summaries = jobs.list_jobs()
    assert "disk1" in [s["id"] for s in summaries]
    assert "recipes" not in summaries[0]                     # summaries exclude payloads
    assert jobs.get_job("missing") is None


# ── voice-note transcription jobs (B3) ───────────────────────────────────

def _audio_job(job_id="a1"):
    return {"id": job_id, "kind": "audio", "status": "running", "phase": "transcribing",
            "progress": 0.0, "total": 1, "done": 0, "failed": 0, "recipes": [],
            "created_at": "2026-01-01T00:00:00+00:00"}


def test_process_audio_job_success(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    audio = tmp_path / "note.webm"
    audio.write_bytes(b"x")
    job = _audio_job()

    def stub(path, note, lang, cats, progress):
        progress(0.5)
        progress(1.0)                      # flips phase to "structuring"
        return [{"name": "Cake"}]

    out = jobs._process_audio_job(job, str(audio), "English", "", [], extract_fn=stub)

    assert out["status"] == "done" and out["done"] == 1
    assert out["progress"] == 1.0 and out["phase"] == "done"
    assert out["recipes"][0]["recipe"]["name"] == "Cake"
    assert not audio.exists()              # temp audio cleaned up
    # audio jobs persist under /data/audio, NOT the cookbook dir the banner scans
    assert "a1" not in [s["id"] for s in jobs.list_jobs()]


def test_process_audio_job_error_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    audio = tmp_path / "note.webm"
    audio.write_bytes(b"x")
    job = _audio_job("a2")

    def boom(*a):
        raise ValueError("no speech")

    out = jobs._process_audio_job(job, str(audio), "English", "", [], extract_fn=boom)

    assert out["status"] == "error" and out["failed"] == 1
    assert out["error"] == "no speech"
    assert not audio.exists()              # temp audio cleaned up even on failure
