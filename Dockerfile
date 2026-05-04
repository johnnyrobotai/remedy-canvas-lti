# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + serve frontend
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies:
# - libqpdf-dev: required by pikepdf (PDF manipulation)
# - libmupdf-dev: required by PyMuPDF (PDF rendering)
# - build-essential, libffi-dev: required for C extension builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libqpdf-dev \
    libmupdf-dev \
    mupdf-tools \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 + LiteParse CLI for document parsing.
# liteparse Python package is a wrapper around @llamaindex/liteparse Node CLI.
# Node 20 (not 18) is required: @llamaindex/liteparse@1.4+ depends on
# file-type and p-limit which require node>=20. On Node 18 the install
# emits EBADENGINE warnings and the parser silently exit-codes 1 at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @llamaindex/liteparse \
    && npm cache clean --force \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# LibreOffice headless: required by liteparse for DOCX/PPTX/XLSX
# (LibreOffice converts Office formats to PDF, then liteparse parses the PDF).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-core \
        libreoffice-writer \
        libreoffice-impress \
        libreoffice-calc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy backend code + pyproject.toml
COPY pyproject.toml ./
COPY lti_app/ ./lti_app/
RUN touch README.md

# Install Python dependencies (non-editable for production)
RUN pip install --no-cache-dir .

# Smoke test: liteparse must be able to parse a real DOCX inside the image.
# This verifies Node, the @llamaindex/liteparse CLI, LibreOffice, and the
# Python wrapper are all installed and wired correctly. Hard build failure
# if any of them is broken — we never ship an image where document
# conversion would fail at runtime.
COPY tests/fixtures/smoke.docx /tmp/smoke.docx
RUN python -c "from liteparse import LiteParse; r = LiteParse().parse(open('/tmp/smoke.docx','rb').read(), ocr_enabled=False); assert r.text and 'liteparse should extract' in r.text, f'liteparse smoke test failed: pages={len(r.pages)}, text={r.text!r}'" \
    && rm /tmp/smoke.docx

# Install Playwright Chromium for rendered page scanning
RUN playwright install --with-deps chromium

# Vendor axe-core for offline injection into rendered pages
RUN mkdir -p /app/vendor && \
    python -c "import urllib.request; urllib.request.urlretrieve('https://cdn.jsdelivr.net/npm/axe-core@4.10.3/axe.min.js', '/app/vendor/axe.min.js')"

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Environment
ENV PORT=8080

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app /ms-playwright

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn lti_app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
