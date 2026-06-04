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
