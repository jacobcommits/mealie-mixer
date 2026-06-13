import builtins

import pytest

import extract
import transcribe


def test_is_available_returns_bool():
    assert isinstance(transcribe.is_available(), bool)


def test_get_model_clear_error_when_dep_missing(monkeypatch):
    transcribe._MODEL = None
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError):
        transcribe._get_model()


def test_extract_from_audio_feeds_text_pipeline(monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda p, **kw: "two eggs and one cup flour, then bake")
    captured = {}

    def fake_text(text, **kw):
        captured["text"] = text
        return [{"name": "Cake"}]

    monkeypatch.setattr(extract, "extract_recipes_from_text", fake_text)
    out = extract.extract_recipes_from_audio("/tmp/x.webm", target_language="English")
    assert out == [{"name": "Cake"}]
    assert "two eggs" in captured["text"]


def test_extract_from_audio_empty_transcript_raises(monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda p, **kw: "   ")
    with pytest.raises(ValueError):
        extract.extract_recipes_from_audio("/tmp/x.webm")


def test_transcribe_audio_reports_progress(monkeypatch):
    class Seg:
        def __init__(self, text, end):
            self.text, self.end = text, end

    class Info:
        duration = 10.0

    class FakeModel:
        def transcribe(self, path, **kw):
            return iter([Seg(" hello ", 5.0), Seg(" world ", 10.0)]), Info()

    monkeypatch.setattr(transcribe, "_get_model", lambda: FakeModel())
    seen = []
    out = transcribe.transcribe_audio("/tmp/x.webm", progress=lambda f: seen.append(f))
    assert out == "hello world"
    assert seen == [0.5, 1.0]            # seg.end / duration, ending at 1.0
