# Backend: FastAPI (Sovereign AI Workbench). Single slim stage + non-root.
# No secrets baked in — everything arrives via environment at runtime.
# No model downloads at build time (Ollama stays optional/external).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Preserves the repo layout the code expects:
#   /app/backend/{app,seed_docs}, /app/roles.yaml, /app/requirements.txt
COPY requirements.txt roles.yaml ./
COPY backend ./backend

# tesseract: local on-prem OCR engine for the scanned-document workflow
# (paired with pytesseract from requirements.txt). No cloud OCR, ever.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# psycopg is intentionally NOT in requirements.txt (SQLite default); the
# container enables the optional PostgreSQL path, so it is added here only.
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]" \
    && rm -rf /root/.cache

# Runtime data (sqlite fallbacks, uploads, chroma, vectors) lives on /data.
RUN useradd -m -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

ENV SOV_DATA_DIR=/data \
    SOV_HOST=0.0.0.0 \
    SOV_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=14).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
