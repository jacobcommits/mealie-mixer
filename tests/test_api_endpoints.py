"""Integration tests for the HTTP REST API endpoints."""

import pytest
from fastapi.testclient import TestClient

import config
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
