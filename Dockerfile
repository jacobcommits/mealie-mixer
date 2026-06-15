# Stable base — 3.12 has rock-solid wheel coverage for all our deps
# (pillow, lxml/recipe-scrapers, gradio). The code uses no 3.13/3.14 features.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional voice-note transcription (B3) — heavy faster-whisper deps (~130 MB of wheels).
# OFF by default to keep the base image lean; build with --build-arg WITH_VOICE=1 (or set
# WITH_VOICE=1 for `docker compose build`) to include it. The app degrades gracefully when
# it's absent: transcribe.is_available() is False, /api/config reports voice=false, and the
# UI hides the 🎤 controls. (The Whisper model itself downloads to /data at runtime.)
ARG WITH_VOICE=0
COPY requirements-voice.txt .
RUN if [ "$WITH_VOICE" = "1" ]; then pip install --no-cache-dir -r requirements-voice.txt; fi

# Just the pipeline modules — secrets come from env/volume at runtime, never baked in
COPY config.py core.py extract.py push.py api.py app.py history.py cookbook.py jobs.py transcribe.py users.py ./
COPY static/ ./static/

# Config the setup page persists to (mount a volume here)
ENV MIXER_DATA_DIR=/data

# Run as a non-root user. /data is created + owned here so a FRESH named volume
# inherits that ownership. (An EXISTING root-owned volume must be recreated or
# chowned when switching to non-root — see README.)
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser
VOLUME ["/data"]

EXPOSE 7860

# Report healthy once the web server answers (no curl in slim — use Python,
# exec form so it survives buildah; urlopen raises -> non-zero exit on failure).
# start-period covers Gradio boot + the one-time food-list load.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:7860/api/health', timeout=4)"]

# LAN only — do NOT publish this port to the internet (it can write to Mealie).
CMD ["python", "app.py"]
