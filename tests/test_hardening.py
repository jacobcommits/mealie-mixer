"""Tests for the SSRF guard (extract._assert_safe_url) and the upload cap
(api._max_upload_bytes / _collect_sources)."""

import asyncio
import socket

import pytest
from fastapi import HTTPException

import api
import extract


class TestSsrfGuard:
    def _patch_resolve(self, monkeypatch, ips):
        def fake(host, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in ips]
        monkeypatch.setattr(socket, "getaddrinfo", fake)

    def test_blocks_loopback(self, monkeypatch):
        self._patch_resolve(monkeypatch, ["127.0.0.1"])
        with pytest.raises(ValueError, match="SSRF"):
            extract._assert_safe_url("http://localhost/secret")

    def test_blocks_cloud_metadata_link_local(self, monkeypatch):
        self._patch_resolve(monkeypatch, ["169.254.169.254"])
        with pytest.raises(ValueError):
            extract._assert_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_private_range(self, monkeypatch):
        self._patch_resolve(monkeypatch, ["10.0.0.5"])
        with pytest.raises(ValueError):
            extract._assert_safe_url("http://internal.lan/admin")

    def test_allows_public_address(self, monkeypatch):
        self._patch_resolve(monkeypatch, ["93.184.216.34"])
        extract._assert_safe_url("http://example.com/recipe")  # must not raise

    def test_blocks_if_any_resolved_address_is_internal(self, monkeypatch):
        # round-robin DNS with one internal address → still blocked (conservative)
        self._patch_resolve(monkeypatch, ["93.184.216.34", "127.0.0.1"])
        with pytest.raises(ValueError):
            extract._assert_safe_url("http://example.com/recipe")

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError):
            extract._assert_safe_url("not a url")


class _FakeUpload:
    def __init__(self, data, name="img.jpg"):
        self.data = data
        self.filename = name

    async def read(self):
        return self.data


class TestUploadCap:
    def test_collect_sources_returns_413_over_cap(self, monkeypatch):
        monkeypatch.setattr(api, "_max_upload_bytes", lambda: 5)

        async def go():
            return await api._collect_sources([_FakeUpload(b"x" * 10)], None, [])

        with pytest.raises(HTTPException) as ei:
            asyncio.run(go())
        assert ei.value.status_code == 413

    def test_collect_sources_under_cap_ok(self, monkeypatch):
        monkeypatch.setattr(api, "_max_upload_bytes", lambda: 100)

        async def go():
            # .txt routes through the document-text path (no temp file)
            return await api._collect_sources([_FakeUpload(b"abc", "doc.txt")], None, [])

        img_paths, doc_texts, audio = asyncio.run(go())
        assert doc_texts == ["abc"]
        assert img_paths == [] and audio == ""

    def test_default_cap_is_100mb(self, monkeypatch):
        # unconfigured → built-in 100 MB default
        monkeypatch.setattr(api.config, "_file_cfg", {})
        monkeypatch.delenv("MIXER_MAX_UPLOAD_MB", raising=False)
        assert api._max_upload_bytes() == 100 * 1024 * 1024
