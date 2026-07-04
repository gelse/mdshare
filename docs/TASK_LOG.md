# Task Log

## 2026-07-01T14:20:00Z — Fix GitHub test result recognition

- **Problem**: GitHub workflows generated JUnit XML files but only uploaded them as artifacts (via `actions/upload-artifact`). GitHub does not automatically parse artifact JUnit XML files, so test results never appeared in the "Tests" tab, check run summary, or PR annotations.
- **Root cause**: Missing `dorny/test-reporter@v1` step in both `unittest.yml` and `integration-test.yml` to publish JUnit results to GitHub's Checks API.
- **Changes made**:
  - `.github/workflows/unittest.yml`:
    - Added `mkdir -p test-results` before the pytest command (defensive measure, matching the Forgejo workflow pattern)
    - Added `dorny/test-reporter@v1` step to publish unit test results as `java-junit`
  - `.github/workflows/integration-test.yml`:
    - Added `dorny/test-reporter@v1` step to publish integration test results as `java-junit`

## 2026-07-01T15:54:00Z — Add auto-generated Swagger endpoint

- **Goal**: Provide a browsable, interactive API documentation page at `/api/docs/` so humans can discover, read about, and try out all REST endpoints directly from the browser.
- **Approach**: Flasgger (Swagger 2.0) — minimal, zero-DB, YAML docstring-driven, aligns with the project's "minimal" ethos.
- **Changes made**:
  - `backend/requirements.txt` — Added `flasgger>=0.9.5`
  - `backend/app.py`:
    - Added `from flasgger import Swagger` import
    - Added Swagger initialization with `securityDefinitions` for Bearer-token auth (public UI, "Authorize" button for protected ops)
    - Added OpenAPI YAML docstrings to 4 routes: `GET /api/health`, `GET /v/<doc_id>/raw`, `PUT /api/share`, `GET /api/admin/shares`
  - `README.md` — Added Swagger UI section documenting `/api/docs/` endpoint
- **What does NOT change**: Dockerfile, docker-compose.yml, asgi.py, config.py, services layer, MCP server, storage layer, frontend HTML/CSS, test suite.
- **Tests**: 83/83 pass (Docker-based test run).

## 2026-07-02T07:18:00Z — Remove redundant tool-level `master_password` auth from MCP tools

- **Problem**: Both `create_share` and `list_shares` MCP tools still accepted a `master_password` parameter and called `verify_master_password()` in-tool, despite the ASGI Bearer-auth middleware already enforcing auth at the transport layer.
- **Root cause**: The original design kept tool-level auth as "defense in depth," but it was dead code — the middleware already blocked unauthorized requests before any tool code executed.
- **Changes made**:
  - `backend/mcp_server.py`:
    - Removed `master_password: str` parameter from `create_share` tool signature (was lines 54-55)
    - Removed `master_password: str` parameter from `list_shares` tool signature (was lines 189-190)
    - Removed both `verify_master_password()` auth guard calls from inside each tool
    - Kept `verify_master_password` import (still needed by ASGI middleware at line 240)
  - `backend/__tests__/test_mcp_server.py`:
    - Deleted 4 obsolete auth-param tests: `test_missing_master_password_returns_error` and `test_wrong_master_password_returns_error` from both `TestCreateShare` and `TestListShares`
    - Stripped `master_password=_VALID_PW` from all remaining tool calls
    - Removed unused `_VALID_PW` constant
    - Fixed 5 tests in `TestListShares` where `result = await list_shares()` line was inadvertently removed
  - `plans/mcp-bearer-auth.md` — Updated status and documentation to reflect removal of tool-level auth
- **Tests**: 100/100 pass (local venv pytest run, full suite).

## 2026-07-02T11:18:00Z — Add version info (git hash) via API, MCP, and build-arg injection

- **Goal**: Expose the deployed version (git commit hash) through a new API endpoint (`GET /api/version`) and a new MCP tool (`get_version()`), injected at Docker build time via `--build-arg`.
- **Approach**: Docker `ARG MDSHARE_VERSION` → `ENV MDSHARE_VERSION` → `Config.version` dataclass field → Flask route + FastMCP tool. All CI builds delegate to Makefile targets so version injection is centralized.
- **Changes made**:
  - `Dockerfile` — Added `ARG MDSHARE_VERSION=unknown` and `ENV MDSHARE_VERSION=$MDSHARE_VERSION` between EXPOSE and ENV PYTHONUNBUFFERED
  - `docker-compose.yml` — Expanded `build` from a one-liner to a multi-line block with `context: .` and `args: MDSHARE_VERSION: ${MDSHARE_VERSION:-unknown}`
  - `Makefile` — Added `VERSION` auto-detection, `build` target with `docker compose build --build-arg MDSHARE_VERSION=$(VERSION)`, `ci-unit-test` target (Forgejo Docker-based), and `ci-test-direct` target (GitHub pip-based)
  - `.forgejo/workflows/unittest.yml` — Replaced inline heredoc docker build/run/cp with `make ci-unit-test`
  - `.github/workflows/unittest.yml` — Replaced separate pip install + pytest run with `make ci-test-direct`
  - `backend/config.py` — Added `version` frozen field with env-var default factory
  - `backend/app.py` — Added `GET /api/version` route with Swagger YAML docstring
  - `backend/mcp_server.py` — Added `get_version()` FastMCP tool function
  - `backend/__tests__/test_health.py` — Added `TestVersion` class (6 assertions)
  - `backend/__tests__/test_mcp_server.py` — Added `TestGetVersion` async class
  - `tests/integration/test_api.py` — Added `TestVersionEndpoint` integration class
- **What does NOT change**: Storage layer, services layer, auth layer, ASGI routing, viewer HTML/CSS, themes, Docker image structure, API auth model, database schema.
- **Tests**: 87/87 pass (local venv pytest run, full suite).

## 2026-07-02T16:00:49Z — Add display configuration (global defaults + per-share overrides)

**Goal**: Let operators set global display defaults (font, theme, etc.) via YAML config file, and allow per-share overrides at upload time.

**Approach**: Created `DisplayConfig` frozen dataclass loaded from `${MDSHARE_DATA_DIR}/display.yaml`, stored per-share overrides as JSON in a new `display_config TEXT` SQLite column, deep-merged them server-side, and exposed the result via `GET /v/<id>/config`. Viewer fetches config and applies CSS custom properties, theme switching via `data-color-mode`, and optional highlight.js line numbers.

**Changes made**:
- New `backend/display_config.py` — frozen dataclass, YAML loading (pyyaml), validation, singleton
- New `GET /v/<id>/config` route — returns merged config as JSON (no auth)
- SQLite migration: `ADD COLUMN display_config TEXT` with lazy `try/except`
- `ShareService.create_share()` — validates/cleans `display_config` dict, stores as JSON
- `ShareService.get_display_config()` — deep-merges `{**global_defaults, **per_share_overrides}`
- `PUT /api/share` — parses `display_config` JSON form field
- MCP `create_share` tool — accepts optional `display_config` dict
- `viewer.html` CSS — replaced hardcoded styles with `--md-*` custom properties
- `viewer.html` JS — `applyDisplayConfig()`: fetch config, set CSS vars, theme, code line numbers, custom CSS injection
- `themes.css` — added explicit `[data-color-mode="light"]` / `[data-color-mode="dark"]` rules
- Added `pyyaml>=6.0` dependency
- `test_display_config.py` — 15 tests (upload validation, config retrieval, merge behavior)
- MCP test updates — 2 display_config tests
- Updated test fixtures with `display_config` param
- All 98 tests pass

**What does NOT change**:
- Existing shares with no `display_config` continue to use global defaults
- No breaking changes to API, storage, or viewer
- Upload without `display_config` field works as before

**Tests**: 98 backend tests pass, including 15 new display_config tests and 2 MCP tests.

## 2026-07-03T05:55:00Z — Add pagination to `list_shares` (REST API + MCP tool)

**Goal**: Support paginated responses from both `GET /api/admin/shares` and the MCP `list_shares` tool, preventing unbounded result sets and enabling clients to page through large share collections.

**Approach**: Offset-based pagination (`LIMIT ? OFFSET ?`) with two SQL queries per request — one `SELECT COUNT(*)` for total count, one data query for the page. Default `page_size=50`, capped at 200. Minimum page is 1.

**Changes made**:
- `backend/storage/abstract.py` — `StorageBackend.list_active()` signature changed from `() -> list[dict]` to `(page_size: int = 50, page: int = 1) -> tuple[list[dict], int]`
- `backend/storage/sqlite.py` — `list_active()` now runs `SELECT COUNT(*) FROM shares` + `SELECT ... LIMIT ? OFFSET ?`, returns `(list[dict], int)`
- `backend/services/share_service.py` — `list_shares(page_size, page)` pass-through to storage layer; added `# type: ignore[return-value]` to `health_check()` call site
- `backend/app.py` — `GET /api/admin/shares` parses `page_size`/`page` from query string, validates ranges (1–200, ≥1), returns `page`, `page_size`, `total_pages`, `total_count` metadata alongside `shares` array
- `backend/mcp_server.py` — `list_shares` tool accepts optional `page_size: int = 50, page: int = 1`, validates ranges, returns same pagination metadata
- `backend/__tests__/test_upload.py` — 7 HTTP tests updated for new response shape (`count` → `total_count`, added `page`/`page_size`/`total_pages` assertions); 13 new pagination tests added (validation, defaults, boundary, page-beyond-end, total_pages calculation, second page)
- `backend/__tests__/test_mcp_server.py` — 5 MCP tests updated for new response shape; 7 new pagination tests added
- `README.md` — `GET /api/admin/shares` docs updated with query parameters table, new response shape, 400 error; MCP tools table updated with pagination description

**What does NOT change**:
- Authentication model (Bearer token still required)
- Database schema (no migrations)
- Viewer, themes, display config, image serving
- Integration tests (they don't exercise `list_shares`)
- Existing share creation, retrieval, and deletion flows

**Tests**: 116/116 backend tests pass (local venv pytest run, full suite).

## 2026-07-03T15:21:00Z — Refactor CI unit test infrastructure (Dockerfile.test + compose profile)

**Problem**: The `ci-unit-test` Makefile target used an inline heredoc Dockerfile with raw `docker build`, `docker run`, `docker cp`, and `docker rm` commands. This was fragile — `docker cp` for JUnit XML extraction could fail if the container didn't write the file, there was no proper exit code propagation, and the inline heredoc was hard to maintain.

**Approach**: Extract the test build into a proper `Dockerfile.test`, orchestrate via `docker compose --profile test` with volume-mounted test results and exit code propagation.

**Changes made**:
- `Dockerfile.test` (new) — standalone test image based on `python:3.13-slim`, installs `requirements-dev.txt` (which includes `backend/requirements.txt` via `-r`), runs `pytest` with `--junitxml=/app/test-results/junit.xml`
- `docker-compose.yml` — added `test` service under `profiles: ["test"]`, builds from `Dockerfile.test`, mounts `./test-results:/app/test-results`, overrides command with `-v` verbose flag
- `Makefile` — replaced `ci-unit-test` inline heredoc Dockerfile with `docker compose --profile test up --build --abort-on-container-exit --exit-code-from test` followed by `docker compose --profile test down`
- `.forgejo/workflows/unittest.yml` — removed standalone `Cleanup docker` step (handled by `docker compose down` now)
- `AGENTS.md` — updated Docker test commands section to reference new `Dockerfile.test` + compose approach

**What does NOT change**:
- GitHub CI workflow (`.github/workflows/unittest.yml`) still uses `make ci-test-direct` — unaffected
- Local dev workflow (`make test`, `make test-watch`, `make test-coverage`) — unchanged
- Production `Dockerfile` — unchanged
- Any source code, routes, or application logic

**Tests**: `make ci-unit-test` now uses compose profiles; exit code propagates correctly; JUnit XML extracted via volume mount instead of `docker cp`.

## 2026-07-03T20:25:00Z — Add file-watching DisplayConfigCache singleton

- **Goal**: Cache `display.yaml` settings in a singleton that detects file changes on read, reloads valid YAML seamlessly, preserves old values on invalid YAML, and logs all transitions.
- **Approach**: `DisplayConfigCache` singleton class in `backend/display_config.py` with mtime-based change detection on every `get_defaults()` call. Uses `threading.Lock` for thread safety under Gunicorn's threaded worker model. Keeps the existing frozen `DisplayConfig` dataclass and `DisplayConfig.get_defaults()` interface unchanged.
- **Changes made**:
  - `backend/display_config.py` — complete refactor:
    - Added `import logging`, `import threading`, `from pathlib import Path`, `import os`
    - Added `_DISPLAY_CONFIG_ENV_VAR = "MDSHARE_DISPLAY_CONFIG"` constant
    - Added `_read_yaml_file()` helper that catches `yaml.YAMLError` and `OSError`, logging an ERROR for invalid YAML and returning `None`
    - Added `_resolve_config_path()`, `_get_mtime()`, `_load_yaml()` helpers
    - Added `DisplayConfigCache` singleton class with `__new__`/`__init__`, `_initialize()`, `_load()`, `_check_and_reload()`, and `get_defaults()` methods
    - Replaced direct `DisplayConfig.from_defaults()` call with singleton pattern on every `get_defaults()` call
    - Fixed ordering: `DisplayConfig` defined before helper functions before `DisplayConfigCache`
  - `backend/__tests__/test_display_config_cache.py` (new, 12 tests) — comprehensive test suite:
    - `TestDefaults` — defaults when no YAML, INFO log on startup
    - `TestCustomYaml` — loads custom YAML, logs overrides, ignores unknown keys, singleton identity
    - `TestFileWatching` — detects file changes, keeps old on invalid YAML/values, ERROR log on invalid YAML, reverts on file removal, INFO log on revert
    - `TestThreadSafety` — 10 threads × 50 iterations with random sleep, verifies no partial state
  - `plans/display-config-file-watcher.md` (new) — architecture and implementation plan
- **What does NOT change**:
  - `DisplayConfig` frozen dataclass struct — unchanged
  - `DisplayConfig.from_defaults()` static method — unchanged
  - `display_defaults.yaml` — unchanged
  - `config.py`, `share_service.py`, `app.py`, `mcp_server.py`, `viewer.html`, `themes.css` — unchanged
  - `requirements.txt` or `requirements-dev.txt` — no new dependencies (uses stdlib `logging`, `threading`)
  - Existing test `test_display_config.py` — no changes needed
- **Tests**: 144/144 backend tests pass (`make ci-unit-test`).

## 2026-07-04T06:14:00Z — Dockerfile hardening

- **Goal**: Fix missing OCI metadata, security gaps, layer-caching issues, and redundant COPY in both `Dockerfile` and `Dockerfile.test`.
- **Changes made**:
  - `.dockerignore` (new, ~45 entries) — excludes `.git/`, `__pycache__/`, `Plans/`, `docs/`, `test-results/`, CI configs, IDE files, and other build-time-only artifacts from the Docker build context, reducing daemon payload and improving cache consistency.
  - `Dockerfile`:
    - **Removed** redundant `COPY backend/display_defaults.yaml /app/backend/display_defaults.yaml` (line 14) — already covered by `COPY backend/ /app/backend/` (no-op layer removed).
    - **Added** 8 OCI-standard LABELs: `title`, `description`, `authors`, `url`, `source`, `documentation`, `licenses`, `vendor`.
    - **Added** non-root `appuser` (`groupadd`/`useradd`/`chown`/`USER appuser`) — container no longer runs as root.
    - **Added** `HEALTHCHECK` with 30s interval, 5s timeout, 3 retries, 10s start period — mirrors the `docker-compose.yml` healthcheck so the image is self-contained.
    - **Added** `ENV MDSHARE_WORKERS=4` and switched `CMD` from exec form to `sh -c` form for variable expansion — workers count is now overridable at runtime via `-e MDSHARE_WORKERS=2`.
  - `Dockerfile.test`:
    - **Reordered** layers for cache efficiency: `COPY backend/requirements.txt` and `requirements-dev.txt` → `RUN pip install` → `COPY backend/` code last (previously copied all code before pip install, invalidating cache on every code change).
    - **Added** 5 OCI-standard LABELs: `title`, `description`, `authors`, `source`, `licences`.
    - **Added** non-root `appuser` (same pattern as production image).
    - **Aligned** `CMD` with `docker-compose.yml` override by adding `-v` flag (verbosity).
- **Risk**: Non-root user may cause write-permission issues with the host-mounted `test-results/` volume in CI. `mkdir -p test-results` in `Makefile` runs before Docker, so the directory is owned by the CI runner's UID. If CI fails, mitigation is `chmod 777 test-results` in the Makefile target or passing host UID as build arg.

## 2026-07-04T07:52:00Z — Fix Forgejo CI non-root permission failures

- **Problem**: Forgejo CI failed with `sqlite3.OperationalError: attempt to write readonly database` (production container) and `PermissionError: /app/test-results/junit.xml` (test container) after the `USER appuser` hardening was introduced.
- **Root cause**: Neither Dockerfile pre-created the volume mount directories with `appuser` ownership before the `USER` switch.
  - Production: Named volume `mdshare_data:/data` — Docker creates the mount point as `root`, so `appuser` can't write the SQLite DB.
  - Test: Bind mount `./test-results:/app/test-results` — same issue; Docker creates the mount point as `root`.
- **Changes made**:
  - `Dockerfile`: Added `RUN mkdir -p /data && chown appuser:appuser /data` before `USER appuser` so the named volume inherits correct ownership on first mount.
  - `Dockerfile.test`: Removed the `USER appuser` block entirely (test image is ephemeral, no security benefit from non-root).

## 2026-07-04T10:08:00Z — Fix fresh-deployment "unable to open database file"

- **Problem**: First startup with a fresh Docker named volume failed with `sqlite3.OperationalError: unable to open database file` — the volume mount point did not have write permissions for `appuser`.
- **Root cause**: `useradd -r` (system user) assigned UID 999 to `appuser`, which did not match the host user UID (1000). Combined with Docker's named volume ownership, this caused a write access failure.
- **Changes made**:
  - `Dockerfile`: Removed `-r` flag from `useradd -r` so `appuser` gets a regular user UID (≥1000, typically 1000), matching host user UIDs for better volume permission compatibility.
  - `backend/storage/sqlite.py`: Added `os.access(config.data_dir, os.W_OK)` check in `SqliteStorage.__init__()` before `_init_schema()`, raising a clear `PermissionError` with an actionable message instead of the cryptic SQLite error.
