# mdshare — minimal Markdown sharing service
# Single-container deployment (Flask + uvicorn, no nginx)

FROM python:3.13-slim

LABEL org.opencontainers.image.title="mdshare" \
      org.opencontainers.image.description="Minimal, self-hosted Markdown sharing service with Mermaid, syntax highlighting, KaTeX math, and password protection" \
      org.opencontainers.image.authors="Werner Gelse <https://forgejo.gelse.local/werner>" \
      org.opencontainers.image.url="https://github.com/gelse/mdshare" \
      org.opencontainers.image.source="https://github.com/gelse/mdshare" \
      org.opencontainers.image.documentation="https://github.com/gelse/mdshare/blob/main/README.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Gelse"

WORKDIR /app

# Install dependencies (cached until requirements.txt changes)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application (preserving the backend package structure)
COPY backend/ /app/backend/

EXPOSE 5000

ARG MDSHARE_VERSION=unknown
ENV MDSHARE_VERSION=$MDSHARE_VERSION

ENV PYTHONUNBUFFERED=1
ENV MDSHARE_WORKERS=4

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
# Pre-create data volume mount point so the non-root user can write the SQLite DB
RUN mkdir -p /data && chown appuser:appuser /data
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/api/health').getcode() == 200 else 1)"

CMD ["sh", "-c", "uvicorn backend.asgi:app --host 0.0.0.0 --port 5000 --workers ${MDSHARE_WORKERS} --timeout-keep-alive 30"]
