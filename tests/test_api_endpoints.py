"""Integration tests for the HTTP REST API endpoints."""

from fastapi.testclient import TestClient

import api
import config
import jobs
import transcribe
from app import fastapi_app

client = TestClient(fastapi_app)

def _isolate(monkeypatch, tmp_path, extras=None):
    """Isolate config from the live .env and data directory."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_file_cfg", {})
    for k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY", "MIXER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    if extras:
        monkeypatch.setattr(config, "_file_cfg", extras)

class TestHealthEndpoint:
    def test_health_unconfigured(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path)
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "configured": False}

    def test_health_configured(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925",
            "MEALIE_TOKEN": "t",
            "AI_API_KEY": "k"
        })
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "configured": True}

class TestExtractEndpoint:
    def test_extract_api_disabled(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"
        })
        response = client.post("/api/extract", headers={"Authorization": "Bearer key"})
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()

    def test_extract_missing_auth(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k", "MIXER_API_KEY": "secret"
        })
        response = client.post("/api/extract")
        assert response.status_code == 401

    def test_extract_invalid_auth(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k", "MIXER_API_KEY": "secret"
        })
        response = client.post("/api/extract", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

class TestPushEndpoint:
    def test_push_api_disabled(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"
        })
        payload = {"name": "Test", "yield": "", "ingredients": [], "instructions": []}
        response = client.post("/api/push", json=payload, headers={"Authorization": "Bearer key"})
        assert response.status_code == 503

    def test_push_missing_auth(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k", "MIXER_API_KEY": "secret"
        })
        payload = {"name": "Test", "yield": "", "ingredients": [], "instructions": []}
        response = client.post("/api/push", json=payload)
        assert response.status_code == 401

    def test_push_invalid_auth(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k", "MIXER_API_KEY": "secret"
        })
        payload = {"name": "Test", "yield": "", "ingredients": [], "instructions": []}
        response = client.post("/api/push", json=payload, headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401


class TestExtractJobEndpoints:
    def test_job_start_missing_auth(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k", "MIXER_API_KEY": "secret"
        })
        # no session, no key → fail-closed, before any work
        assert client.post("/api/extract/job").status_code == 401

    def test_job_start_combines_sources(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"
        })
        monkeypatch.setattr(transcribe, "is_available", lambda: True)
        monkeypatch.setattr(api, "fetch_category_names", lambda: [])
        seen = {}

        def fake_start(sources, **k):
            seen.update(sources)
            return "JID123"

        monkeypatch.setattr(jobs, "start_extract_job", fake_start)
        c = TestClient(fastapi_app)
        c.post("/api/login", json={})        # no login required → mints an open session
        r = c.post("/api/extract/job",
                   files={"audio": ("note.webm", b"xx", "audio/webm")},
                   data={"url": "http://x/reel", "text": "salt", "language": "English"})
        assert r.status_code == 200 and r.json()["job_id"] == "JID123"
        # all provided sources reached the job together (combine, not "one wins")
        assert seen["url"] == "http://x/reel" and seen["text"] == "salt" and seen["audio_path"]

    def test_job_start_disabled_when_audio_and_dep_missing(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"
        })
        monkeypatch.setattr(transcribe, "is_available", lambda: False)
        c = TestClient(fastapi_app)
        c.post("/api/login", json={})
        r = c.post("/api/extract/job",
                   files={"audio": ("note.webm", b"xx", "audio/webm")})
        assert r.status_code == 503

    def test_job_status_returns_job_and_404(self, monkeypatch, tmp_path):
        _isolate(monkeypatch, tmp_path, {
            "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t", "AI_API_KEY": "k"
        })
        done = {"id": "abc", "status": "done", "progress": 1.0,
                "recipes": [{"recipe": {"name": "Cake"}, "image": None}]}
        monkeypatch.setattr(jobs, "get_job", lambda jid, user=None: done if jid == "abc" else None)
        c = TestClient(fastapi_app)
        c.post("/api/login", json={})
        r = c.get("/api/extract/job/abc")
        assert r.status_code == 200 and r.json()["recipes"][0]["recipe"]["name"] == "Cake"
        assert c.get("/api/extract/job/missing").status_code == 404
