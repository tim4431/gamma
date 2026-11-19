# --- Stage 1: build the frontend ---
# Pinned to the build host's platform: the vite output is platform-independent
# static files, and running npm ci under QEMU for the arm64 image hangs.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: runtime ---
FROM python:3.12-slim
WORKDIR /app

# CJK glyphs in the "notes on page" PDF export are drawn as outlines from this
# font (gamma/vector_text.py) — a plain 4 MB .ttf, unlike the .ttc collections
# most CJK font packages ship, which ziafont can't open.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-droid-fallback \
    && rm -rf /var/lib/apt/lists/*

# requirements-s3.txt: boto3, for the off-site copies to an S3-compatible
# bucket (docs/dev/debugging.md "Off-site copies in a bucket"); imported only
# when the bucket is used.
COPY backend/requirements.txt backend/requirements-s3.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-s3.txt

# The models Tesseract recognizes scanned pages with (gamma/ocr_engine.py;
# the engine itself is the tesserocr wheel above, which needs no apt
# package), with their licence and notices. A layer of their own before the
# code's, so a new commit does not push and pull these 7 MB again.
COPY backend/tessdata/ ./tessdata/

COPY backend/app.py backend/manage.py ./
COPY backend/gamma/ ./gamma/
COPY --from=frontend /build/dist ./static/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The build stamp the admin dashboard shows and compares against the latest
# GitHub release (gamma/version.py): docker.yml passes the release version
# (or <tag>-dev.<n> plus the branch for any other build) and the commit sha.
ARG GAMMA_VERSION=""
ARG GAMMA_BRANCH=""
ARG GAMMA_COMMIT=""
# OMP_THREAD_LIMIT: one thread per recognized page; the OCR lane
# (GAMMA_OCR_WORKERS) decides how many pages are read at once, and a
# Tesseract built with OpenMP would otherwise start a thread a core, which
# measured slower (docs/research/ocr.md).
ENV GAMMA_DATA_DIR=/data \
    GAMMA_STATIC_DIR=/app/static \
    GAMMA_TESSDATA_DIR=/app/tessdata \
    OMP_THREAD_LIMIT=1 \
    GAMMA_VERSION=$GAMMA_VERSION \
    GAMMA_BRANCH=$GAMMA_BRANCH \
    GAMMA_COMMIT=$GAMMA_COMMIT \
    PYTHONUNBUFFERED=1

# The peers whose X-Forwarded-For uvicorn believes (it then takes the
# client's address from the header — rate limits key on it): loopback only,
# uvicorn's own default, so no one can pick the address a limit counts by
# sending the header. Behind a reverse proxy set it to the proxy's address
# (or its compose network's subnet) in the compose file, else every visitor
# shares the proxy's rate-limit bucket; "*" only when nothing but the proxy
# can reach the port.
ENV FORWARDED_ALLOW_IPS="127.0.0.1"

VOLUME /data
EXPOSE 9001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9001/api/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
