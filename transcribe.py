"""
Mealie Mixer — local audio transcription (B3, dev).

Turns a voice note / dictation into text via faster-whisper (local, CPU), which then
feeds the SAME text structuring pipeline as everything else. faster-whisper is an
OPTIONAL dependency (heavy) — imported lazily so the lean base image still runs; the
voice feature simply isn't offered when it's absent (see is_available()).
"""

import importlib.util
import os

import config

_MODEL = None        # lazily-loaded WhisperModel singleton
_MODEL_NAME = None    # which model the singleton was loaded for


def is_available() -> bool:
    """True if faster-whisper is installed — cheap (no import/model load)."""
    return importlib.util.find_spec("faster_whisper") is not None


def _get_model():
    """Load (once) the faster-whisper model named by config WHISPER_MODEL, cached under
    /data so it persists across restarts. Raises a clear error if the dep is missing."""
    global _MODEL, _MODEL_NAME
    name = (config.get("WHISPER_MODEL") or "base").strip() or "base"
    if _MODEL is not None and _MODEL_NAME == name:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
    except Exception:
        raise RuntimeError(
            "Voice transcription isn't enabled in this build — it needs the optional "
            "'faster-whisper' dependency. Use the voice image/profile, or add the recipe "
            "as a screenshot or text instead."
        )
    cache = os.path.join(config.DATA_DIR, "whisper")
    os.makedirs(cache, exist_ok=True)
    # CPU + int8 keeps it light; the model downloads to /data on first use.
    _MODEL = WhisperModel(name, device="cpu", compute_type="int8", download_root=cache)
    _MODEL_NAME = name
    return _MODEL


def transcribe_audio(path: str, progress=None) -> str:
    """Transcribe an audio file to plain text (language auto-detected). If `progress` is
    given, it's called with a 0..1 fraction as segments are decoded — best-effort, used to
    drive the UI progress bar; it needs the clip duration, which faster-whisper reports up
    front. A failing callback never breaks transcription."""
    model = _get_model()
    segments, info = model.transcribe(path, vad_filter=False)
    duration = getattr(info, "duration", 0) or 0
    parts = []
    for seg in segments:
        parts.append(seg.text.strip())
        if progress and duration:
            try:
                progress(min(1.0, seg.end / duration))
            except Exception:
                pass
    return " ".join(p for p in parts if p).strip()
