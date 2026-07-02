# Version Information Feature Plan

## Summary

Inject the current git commit hash at Docker build time as `MDSHARE_VERSION`, expose it via a new public API endpoint and a new MCP tool. Refactor CI workflows to use Makefile targets for build/test.

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Build-time `ARG` + `ENV` | Works identically for `git rev-parse` in dev and `v1.2.3` in releases — the user overrides the arg |
| Fallback value `"unknown"` | Graceful for builds outside a git checkout (e.g., CI with shallow/no-git) |
| Public endpoint (no auth) | Version info is not sensitive — same reasoning as `/api/health` |
| MCP tool (Bearer auth) | Consistent with all existing MCP tools; auth enforced by middleware |
| Single env var, single result field | For now version = git hash. The `version` key leaves room to add `commit`/`build_date` later |
| `make build` uses `docker compose` | Consistent with the project's docker-compose.yml; no plain `docker build` |
| Single `build` target, no `build-dev` | All builds treated the same — the `MDSHARE_VERSION` arg controls what gets injected |

## Affected Files

```
Dockerfile                         # ARG MDSHARE_VERSION + ENV
docker-compose.yml                 # build.args (defaults to env var)
Makefile                           # build target (docker compose), ci-unit-test, ci-test-direct
backend/config.py                  # new version: str field
backend/app.py                     # GET /api/version route
backend/mcp_server.py              # get_version() tool
.forgejo/workflows/unittest.yml   # replace inline docker with make ci-unit-test
.github/workflows/unittest.yml    # replace pip install+pytest with make ci-test-direct
backend/__tests__/test_health.py   # version endpoint tests
backend/__tests__/test_mcp_server.py # get_version tool tests
tests/integration/test_api.py      # integration test for /api/version
```

## Implementation Steps

### Step 1 — Dockerfile: Accept and persist `MDSHARE_VERSION`

```dockerfile
# After the EXPOSE line (line 15), before ENV PYTHONUNBUFFERED:

ARG MDSHARE_VERSION=unknown
ENV MDSHARE_VERSION=$MDSHARE_VERSION
```

The `ARG` default `"unknown"` covers builds that don't pass `--build-arg`. The `ENV` persists it into the runtime environment, where `Config.version` picks it up.

### Step 2 — docker-compose.yml: Wire build arg

```yaml
services:
  mdshare:
    build:
      context: .
      args:
        MDSHARE_VERSION: ${MDSHARE_VERSION:-unknown}
    # ... rest unchanged ...
```

`docker compose build` reads `$MDSHARE_VERSION` from the host environment. When not set, it defaults to `"unknown"`.

### Step 3 — Makefile: Add `build` target (docker compose) + CI targets

```makefile
.PHONY: build test test-watch test-coverage test-integration ci-unit-test ci-test-direct

VENV = venv/bin/

# Auto-detect git hash; falls back to "unknown" when .git/ is absent.
VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")

build:
	docker compose build --build-arg MDSHARE_VERSION=$(VERSION)

# ---------------------------------------------------------------------------
# Existing test targets (unchanged)
# ---------------------------------------------------------------------------

test:
	$(VENV)python -m pytest

test-watch:
	$(VENV)python -m pytest-watch -- --testmon

test-coverage:
	$(VENV)python -m pytest --cov=backend --cov-report=term-missing --cov-report=html

test-integration:
	$(VENV)python -m pytest tests/integration/ -v -m integration

# ---------------------------------------------------------------------------
# CI targets
# ---------------------------------------------------------------------------

# Forgejo CI — Docker-based test image (includes version injection).
ci-unit-test:
	docker build \
		--build-arg MDSHARE_VERSION=$(VERSION) \
		-t mdshare:test -f- . <<'EOF'
	FROM python:3.13-slim
	ARG MDSHARE_VERSION=unknown
	ENV MDSHARE_VERSION=$$MDSHARE_VERSION
	COPY backend/ /app/backend/
	COPY requirements-dev.txt /app/
	RUN pip install --no-cache-dir -r /app/backend/requirements.txt -r /app/requirements-dev.txt
	WORKDIR /app
	CMD ["python", "-m", "pytest", "backend", "--junitxml=/app/junit.xml"]
	EOF
	mkdir -p test-results
	docker run --name mdshare-test mdshare:test
	docker cp mdshare-test:/app/junit.xml test-results/junit.xml
	docker rm -f mdshare-test 2>/dev/null || true

# GitHub CI — direct pytest (no Docker, no venv).
ci-test-direct:
	pip install --no-cache-dir -r backend/requirements.txt -r requirements-dev.txt
	mkdir -p test-results
	python -m pytest backend --junitxml=test-results/junit.xml
```

### Step 4 — Forgejo CI: Switch to `make ci-unit-test`

[`.forgejo/workflows/unittest.yml`](.forgejo/workflows/unittest.yml:1) — replace the inline heredoc `docker build` + `docker run` + `docker cp` blocks with:

```yaml
- name: Run unit tests (Docker)
  run: make ci-unit-test

- name: Cleanup docker
  if: always()
  run: docker rm -f mdshare-test 2>/dev/null || true
```

### Step 5 — GitHub CI: Switch to `make ci-test-direct`

[`.github/workflows/unittest.yml`](.github/workflows/unittest.yml:1) — replace the `pip install` + `python -m pytest` blocks with:

```yaml
- name: Run unit tests
  run: make ci-test-direct
```

### Step 6 — Config: Add `version` field

In [`backend/config.py`](backend/config.py:11), add to the `Config` dataclass:

```python
#: Current version string — git hash (dev) or semver tag (release).
version: str = field(
    default_factory=lambda: os.environ.get("MDSHARE_VERSION", "unknown"),
)
```

### Step 7 — Flask route: `GET /api/version`

In [`backend/app.py`](backend/app.py:113), add after the `/api/health` block:

```python
@app.route("/api/version")
def version():
    """Version information endpoint.
    ---
    tags: [Health]
    responses:
      200:
        description: Version information
        schema:
          type: object
          properties:
            version: {type: string, example: "a1b2c3d"}
    """
    return jsonify({"version": config.version})
```

### Step 8 — MCP tool: `get_version()`

In [`backend/mcp_server.py`](backend/mcp_server.py:169), add after `health_check()`:

```python
@mcp.tool()
async def get_version() -> dict[str, Any]:
    """Return the deployed mdshare version (git hash or release tag)."""
    return {"version": config.version}
```

### Step 9 — Unit tests: version endpoint

In [`backend/__tests__/test_health.py`](backend/__tests__/test_health.py:1), add:

```python
class TestVersion:
    """GET /api/version — returns version string."""

    def test_version_returns_200(self, client):
        response = client.get("/api/version")
        assert response.status_code == 200
        assert response.is_json
        data = response.get_json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0
```

### Step 10 — Unit tests: `get_version` MCP tool

In [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py:1), add import and test class:

```python
from backend.mcp_server import get_version  # add to existing import block

class TestGetVersion:
    """get_version() tool returns deployed version."""

    @pytest.mark.asyncio
    async def test_get_version_returns_string(self):
        result = await get_version()
        assert "version" in result
        assert isinstance(result["version"], str)
        assert len(result["version"]) > 0
```

### Step 11 — Integration test

In [`tests/integration/test_api.py`](tests/integration/test_api.py:1), add:

```python
class TestVersionEndpoint:
    """GET /api/version — public system info."""

    @pytest.mark.integration
    def test_version_endpoint(self, http_client):
        response = http_client.get("/api/version")
        assert response.status_code == 200
        assert response.is_json
        data = response.json()
        assert "version" in data
        assert isinstance(data["version"], str)
```

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  make build  (or docker compose build)                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  git rev-parse --short HEAD  ──►  VERSION env var     │   │
│  │  docker compose build --build-arg MDSHARE_VERSION=$VERSION │
│  │  (or manually: MDSHARE_VERSION=v2.0.0 docker compose build) │
│  └──────────────────────┬───────────────────────────────┘   │
│                         ▼                                     │
│            docker-compose.yml build.args:                     │
│            MDSHARE_VERSION: ${MDSHARE_VERSION:-unknown}       │
│                         │                                     │
│                         ▼                                     │
│            Dockerfile: ARG MDSHARE_VERSION                    │
│                         ENV MDSHARE_VERSION=$MDSHARE_VERSION  │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  Container Runtime                                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Config.version  ←  os.environ["MDSHARE_VERSION"]     │   │
│  └──────────┬───────────────────────────┬───────────────┘   │
│             │                           │                    │
│             ▼                           ▼                    │
│  ┌──────────────────┐    ┌──────────────────────────────┐   │
│  │ Flask route       │    │ MCP tool                     │   │
│  │ GET /api/version  │    │ get_version()                │   │
│  │ (public, no auth) │    │ (Bearer auth via middleware) │   │
│  │ → {"version":"…"} │    │ → {"version":"…"}            │   │
│  └──────────────────┘    └──────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘

  ┌─ CI (Forgejo) ──────────────────────────────────────────┐
  │  make ci-unit-test                                      │
  │  → builds test Docker image with version injected       │
  │  → runs pytest inside container                         │
  │  → extracts junit.xml                                   │
  └─────────────────────────────────────────────────────────┘

  ┌─ CI (GitHub) ───────────────────────────────────────────┐
  │  make ci-test-direct                                    │
  │  → pip install + pytest directly (no Docker)            │
  └─────────────────────────────────────────────────────────┘
```

## Future Adaptability

When actual version injection on release is needed:

1. **CI/CD pipeline** sets `MDSHARE_VERSION=v2.0.0` before calling `make build`
2. **Config** already reads the env var — no code changes needed
3. **Additional fields** (commit hash, build date, Go-style build info) can be added to the `Config` dataclass and exposed on the same endpoint without breaking the API contract
