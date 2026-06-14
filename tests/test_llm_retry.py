"""Tests for the LLM-call resilience wrapper (extract._call_with_retry):
transient-error retry with exponential backoff, and the response_format=json_object
fallback that disables json mode if the backend rejects it."""

import time
from types import SimpleNamespace

import pytest

import extract


class _Err(Exception):
    pass


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


# ── the retry loop (uses monkeypatched classifiers so we don't need real openai errors) ──

def test_retries_transient_then_succeeds(monkeypatch, no_sleep):
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: True)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: False)
    calls = []

    def make_call(use_json):
        calls.append(use_json)
        if len(calls) < 3:
            raise _Err("transient")
        return "OK"

    assert extract._call_with_retry(make_call) == "OK"
    assert len(calls) == 3


def test_gives_up_after_max_retries(monkeypatch, no_sleep):
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: True)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: False)
    calls = []

    def make_call(use_json):
        calls.append(1)
        raise _Err("always transient")

    with pytest.raises(_Err):
        extract._call_with_retry(make_call)
    # initial attempt + _MAX_AI_RETRIES retries
    assert len(calls) == extract._MAX_AI_RETRIES + 1


def test_non_transient_not_retried(monkeypatch, no_sleep):
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: False)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: False)
    calls = []

    def make_call(use_json):
        calls.append(1)
        raise _Err("real 4xx")

    with pytest.raises(_Err):
        extract._call_with_retry(make_call)
    assert len(calls) == 1


def test_bad_request_disables_json_then_succeeds(monkeypatch, no_sleep):
    """A 400 while json mode is on → disable it for the process, retry without it."""
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: False)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: True)
    monkeypatch.setattr(extract, "_USE_JSON_OBJECT", True)   # reset, isolated by monkeypatch
    calls = []

    def make_call(use_json):
        calls.append(use_json)
        if use_json:
            raise _Err("response_format not supported")
        return "OK"

    assert extract._call_with_retry(make_call) == "OK"
    assert calls == [True, False]
    assert extract._USE_JSON_OBJECT is False


def test_real_bad_request_after_json_disabled_surfaces(monkeypatch, no_sleep):
    """Once json mode is off, a 400 is a genuine error → raised immediately."""
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: False)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: True)
    monkeypatch.setattr(extract, "_USE_JSON_OBJECT", False)  # already disabled
    calls = []

    def make_call(use_json):
        calls.append(1)
        raise _Err("genuine bad request")

    with pytest.raises(_Err):
        extract._call_with_retry(make_call)
    assert len(calls) == 1


def test_backoff_doubles_between_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(extract, "_is_transient_err", lambda e: True)
    monkeypatch.setattr(extract, "_is_bad_request_err", lambda e: False)

    def make_call(use_json):
        raise _Err("transient")

    with pytest.raises(_Err):
        extract._call_with_retry(make_call)
    # one sleep per non-final attempt: base * 2^0, base * 2^1, ...
    expected = [extract._AI_RETRY_BACKOFF * (2 ** i) for i in range(extract._MAX_AI_RETRIES)]
    assert sleeps == expected


# ── the real classifier (duck-typed status_code path) ────────────────────

def test_is_transient_via_status_code():
    # 5xx → retry; 4xx / plain errors → don't
    assert extract._is_transient_err(SimpleNamespace(status_code=500)) is True
    assert extract._is_transient_err(SimpleNamespace(status_code=503)) is True
    assert extract._is_transient_err(SimpleNamespace(status_code=400)) is False
    assert extract._is_transient_err(Exception("plain")) is False
