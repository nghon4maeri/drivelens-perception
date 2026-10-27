# Multi-stage Dockerfile cho deployment pilot RAV-26 (DEP1)
# Ho tro 3 target:
#   - api (mac dinh): Render Free Web Service (FastAPI nhe, khong Torch/CUDA)
#   - worker: Contabo AI Worker (day du ML, PyAV, YOLO, transformers)
#   - combined: FE build + API pilot, mot process, khong ML

# ---- Base Stage: Python 3.12 ----
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/venv && useradd -m -u 1000 appuser

# ========================================================
# Stage: Worker Builder (Contabo AI Worker)
# ========================================================
FROM base AS worker-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-kaggle.txt .
COPY requirements-worker.txt .
# Wheel worker lon (torch/transformers); mang build cham de vuot timeout 15s mac dinh cua pip.
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-worker.txt

# ========================================================
# Target: worker (Contabo AI Worker)
# ========================================================
FROM base AS worker
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=worker-builder /opt/venv /opt/venv
COPY --chown=appuser:appuser src /app/src
COPY --chown=appuser:appuser scripts /app/scripts
RUN mkdir -p /app/data /app/output && chown -R appuser:appuser /app

USER appuser
CMD ["python", "-m", "src.rav.worker", "--mode", "pilot"]

# ========================================================
# Stage: API Builder (Render Free Web Service)
# ========================================================
FROM base AS api-builder
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# ========================================================
# Target: api (Render Free Web Service - mac dinh)
# ========================================================
FROM base AS api-runtime
COPY --from=api-builder /opt/venv /opt/venv
COPY --chown=appuser:appuser src /app/src
RUN mkdir -p /app/data /app/output && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + (os.environ.get('PORT') or '8000') + '/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn src.rav.api.pilot:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

# Node is build-only; the combined runtime has one lightweight Python process.
FROM node:22-alpine AS web-build
WORKDIR /web
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web/ ./
ENV VITE_RAV_MODE=pilot
RUN npx tsc --noEmit && npm test && npm run build

FROM api-runtime AS combined
COPY --from=web-build --chown=appuser:appuser /web/dist /app/web-dist
ENV RAV_WEB_DIST=/app/web-dist VITE_API_URL=""

# Keep the existing default API-only build compatible with earlier releases.
FROM api-runtime AS api
