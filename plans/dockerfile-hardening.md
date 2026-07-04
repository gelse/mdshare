# Dockerfile Hardening Plan

## Overview

Fix missing metadata, security gaps, and layer-caching issues in both `Dockerfile` (production) and `Dockerfile.test` (CI test), plus add a `.dockerignore` to keep the build context lean.

---

## Fix 1: `.dockerignore` (NEW file)

**File:** `.dockerignore` (project root)

Exclude everything not needed at runtime inside the container:

```
# Version control
.git/

# Python artifacts
__pycache__/
*.pyc
*.pyo
venv/
.env
.pytest_cache/
htmlcov/
.coverage
.eggs/
*.egg-info/

# Test artifacts
test-results/

# Documentation and planning (not needed in image)
Plans/
docs/
*.md
!README.md

# Docker / CI files (not needed inside image)
Dockerfile
Dockerfile.test
docker-compose.yml
.dockerignore

# Development tooling
Makefile
pytest.ini
requirements-dev.txt
.github/
.forgejo/
.devcontainer/
.roo/
.roo.*
.agent/

# Scripts (build-time only, not runtime)
scripts/

# SSL / certs
certs/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Misc
.gitignore
.gitattributes
LICENSE
NOTICE

# OS junk
.DS_Store
Thumbs.db
```

---

## Fix 2: Production `Dockerfile` — Remove Redundant COPY

**File:** `Dockerfile`, line 14

**Current:**
```dockerfile
COPY backend/ /app/backend/
COPY backend/display_defaults.yaml /app/backend/display_defaults.yaml
```

**Fix:** Delete line 14. The preceding `COPY backend/ /app/backend/` already copies everything in `backend/`, including `display_defaults.yaml`. This is a pure no-op that wastes a layer.

---

## Fix 3: Production `Dockerfile` — Add OCI Labels

**File:** `Dockerfile`, after `FROM` line (line 4)

**Add:**
```dockerfile
LABEL org.opencontainers.image.title="mdshare" \
      org.opencontainers.image.description="Minimal, self-hosted Markdown sharing service with Mermaid, syntax highlighting, KaTeX math, and password protection" \
      org.opencontainers.image.authors="Werner Gelse <https://forgejo.gelse.local/werner>" \
      org.opencontainers.image.url="https://github.com/gelse/mdshare" \
      org.opencontainers.image.source="https://github.com/gelse/mdshare" \
      org.opencontainers.image.documentation="https://github.com/gelse/mdshare/blob/main/README.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Gelse"
```

**Note:** `org.opencontainers.image.revision`, `.version`, and `.created` are already passed dynamically by `make docker-build` (see [`Makefile`](Makefile:17-20)). They are intentionally omitted from the static Dockerfile since they are build-time values. The `make docker-build` CLI flags take precedence over any Dockerfile `LABEL` with the same key — so this is safe and complementary.

---

## Fix 4: Production `Dockerfile` — Non-Root User

**File:** `Dockerfile`, before `EXPOSE`

**Add:**
```dockerfile
# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser
```

This follows the principle of least privilege. The `appuser` owns `/app` but has no login shell and no sudo.

---

## Fix 5: Production `Dockerfile` — HEALTHCHECK

**File:** `Dockerfile`, after `EXPOSE`, before `CMD`

**Add:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/api/health').getcode() == 200 else 1)"
```

**Rationale:** Mirrors the existing healthcheck in [`docker-compose.yml`](docker-compose.yml:20-23) so the image is self-contained. Users running with `docker run` or in Kubernetes get health monitoring out of the box. When used with `docker-compose.yml`, the compose-level healthcheck takes precedence — no conflict.

---

## Fix 6: Production `Dockerfile` — Configurable Workers

**File:** `Dockerfile`, after `ENV PYTHONUNBUFFERED=1` (line 21)

**Add:**
```dockerfile
ENV MDSHARE_WORKERS=4
```

**Change `CMD`** (line 23) **to:**
```dockerfile
CMD ["sh", "-c", "uvicorn backend.asgi:app --host 0.0.0.0 --port 5000 --workers ${MDSHARE_WORKERS} --timeout-keep-alive 30"]
```

**Rationale:** Defaults to 4 workers but allows override at runtime via `-e MDSHARE_WORKERS=2` or in docker-compose. The switch from `exec` form to `shell` form is necessary because `CMD ["uvicorn", ..., "--workers", "$MDSHARE_WORKERS"]` does not expand variables — only the shell form does.

---

## Fix 7: `Dockerfile.test` — Reorder Layers for Caching

**File:** `Dockerfile.test`

**Current order:**
```dockerfile
COPY backend/ /app/backend/          # line 11
COPY pytest.ini /app/                 # line 12
COPY requirements-dev.txt /app/       # line 13
RUN pip install ...                   # line 15
```

**Fix — reorder to:**
```dockerfile
# Install dependencies first (cached until requirements change)
COPY backend/requirements.txt /app/backend/requirements.txt
COPY requirements-dev.txt /app/
RUN pip install --no-cache-dir -r /app/requirements-dev.txt

# Copy application code last (changes frequently)
COPY backend/ /app/backend/
COPY pytest.ini /app/
```

**Rationale:** `requirements-dev.txt` includes `-r backend/requirements.txt` (see [`requirements-dev.txt`](requirements-dev.txt:1)), so both must be copied before `pip install`. This mirrors the production Dockerfile's layer strategy: expensive operations first, frequently-changing code last.

---

## Fix 8: `Dockerfile.test` — Add OCI Labels

**File:** `Dockerfile.test`, after `FROM` line

**Add:**
```dockerfile
LABEL org.opencontainers.image.title="mdshare-test" \
      org.opencontainers.image.description="CI test image for mdshare unit tests" \
      org.opencontainers.image.authors="Werner Gelse <https://forgejo.gelse.local/werner>" \
      org.opencontainers.image.source="https://github.com/gelse/mdshare" \
      org.opencontainers.image.licenses="Apache-2.0"
```

(Minimal labels since this image never leaves CI.)

---

## Fix 9: `Dockerfile.test` — Non-Root User

**File:** `Dockerfile.test`, before `CMD`

**Add:**
```dockerfile
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser
```

**Important:** The test-results volume mount in [`docker-compose.yml`](docker-compose.yml:36) (`./test-results:/app/test-results`) needs write access. The `mkdir -p test-results` call in [`Makefile`](Makefile:50) runs as the host user. With UID 1000 (default for `useradd -r`), this should work on most Linux hosts. If permissions issues arise, the host `test-results/` directory can be `chmod 777` or the user UID can be matched to the host user via `--build-arg`.

---

## Fix 10: `Dockerfile.test` — Align CMD with Compose Override

**File:** `Dockerfile.test`, line 17

**Current:**
```dockerfile
CMD ["python", "-m", "pytest", "backend", "--junitxml=/app/test-results/junit.xml"]
```

**Fix:**
```dockerfile
CMD ["python", "-m", "pytest", "backend", "-v", "--junitxml=/app/test-results/junit.xml"]
```

**Rationale:** [`docker-compose.yml`](docker-compose.yml:37) already overrides CMD with `-v`. Making them consistent means the image is usable standalone (`docker build -f Dockerfile.test . && docker run ...`) with the same behavior, and the compose override becomes redundant (though harmless to keep).

---

## Impact Assessment

| Change | Risk | Rollback |
|--------|------|----------|
| `.dockerignore` | **Low** — only excludes files not needed at runtime | Delete the file or remove entries |
| Remove redundant COPY | **None** — pure no-op removal | N/A |
| Add LABELs | **None** — metadata only, no runtime effect | N/A |
| Non-root USER | **Medium** — volume mounts may need permission adjustments | Remove `USER` line |
| HEALTHCHECK | **Low** — mirrors existing compose healthcheck, compose takes precedence | Remove HEALTHCHECK block |
| Configurable workers | **Low** — defaults to 4 (existing behavior), overridable via env | Remove ENV, revert CMD to exec form |
| Reorder test layers | **Low** — same files end up in image, just different ordering | Revert to original order |
| Align test CMD | **None** — compose already overrides it | N/A |

### Non-root user risk detail

The main risk is the `test-results/` volume mount in CI. On Linux, `useradd -r` typically assigns UID 999 or the next available system UID (< 1000). If the host's `test-results/` directory is owned by a different UID (e.g., the CI runner's UID 1000), the container won't be able to write the JUnit XML. Two mitigations:

1. **CI workflow** already runs `mkdir -p test-results` before docker compose — this creates the directory owned by the CI runner. The volume mount inherits those permissions.
2. **Fallback**: If this fails, we can use `chmod 777 test-results` in the Makefile target, or pass the host UID as a build arg.

This will need verification in CI after deployment.

---

## Files Changed Summary

| File | Action | Lines |
|------|--------|-------|
| `.dockerignore` | **CREATE** | ~45 lines |
| `Dockerfile` | **MODIFY** — remove line 14, add LABELs, USER, HEALTHCHECK, workers ENV, change CMD | ~+20 / -2 |
| `Dockerfile.test` | **MODIFY** — reorder COPY/RUN, add LABELs, USER, adjust CMD | ~+15 / -6 |
| `docs/TASK_LOG.md` | **UPDATE** — document the hardening | ~+5 |
| `docker-compose.yml` | Possibly simplify — HEALTHCHECK and CMD `-v` become redundant | Optional |
