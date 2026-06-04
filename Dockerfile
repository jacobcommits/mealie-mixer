# Stable base — 3.12 has rock-solid wheel coverage for all our deps
# (pillow, lxml/recipe-scrapers, gradio). The code uses no 3.13/3.14 features.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Just the pipeline modules — secrets come from env/volume at runtime, never baked in
COPY config.py core.py extract.py push.py api.py app.py history.py cookbook.py jobs.py ./
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
