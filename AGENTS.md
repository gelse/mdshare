# AGENT.md — AI Agent Project Reference

> **Purpose**: Provide AI coding agents with comprehensive project structure knowledge to prevent filesystem exploration for basic information. Read this first before working on this project.

---

## Project Purpose

mdshare is a minimal, self-hosted Markdown sharing service. Users upload Markdown with optional images via `PUT /api/share` (multipart/form-data) and get a URL that renders clean, GitHub-style HTML with Mermaid diagrams, syntax highlighting, KaTeX math, and optional password protection. Built for sharing AI-generated Markdown quickly.

---

## CRITICAL: Directory Structure & Casing

**The filesystem is CASE-SENSITIVE (Linux). All directory names are lowercase.**

VS Code may display these directories with uppercase first letters, but the actual filesystem paths are **all lowercase**. Using uppercase in paths WILL cause failures.

```
mdshare/                          # Project root
├── AGENT.md                      # AI agent project reference (this file)
├── Dockerfile                    # Single-stage production image
├── Dockerfile.test               # CI unit test image (pytest + JUnit XML)
├── docker-compose.yml            # Single service definition
├── Makefile                      # Test/coverage targets
├── pytest.ini                    # Pytest configuration
├── README.md                     # Project overview, API docs, configuration
├── requirements-dev.txt          # Dev dependencies (pytest, coverage, httpx)
├── LICENSE                       # Apache License 2.0
├── NOTICE                        # Apache 2.0 attribution notice
├── backend/                      # Python/Flask API server + static viewer
│   ├── __init__.py               # Package initialization
│   ├── app.py                    # Flask application (5 routes, auth, upload)
│   ├── config.py                 # Configuration dataclass (env vars)
│   ├── display_config.py         # Display defaults (YAML → frozen dataclass)
│   ├── requirements.txt          # Python dependencies (5 packages)
│   ├── static/                   # Static assets served by Flask
│   │   ├── viewer.html           # Client-side Markdown viewer (CDN libraries)
│   │   └── themes.css            # Dark/light mode CSS custom properties
│   ├── storage/                  # SQLite storage backend
│   │   ├── __init__.py           # Factory: returns SqliteStorage
│   │   ├── abstract.py           # StorageBackend ABC (3 abstract methods)
│   │   └── sqlite.py             # Single-table SQLite storage
│   └── __tests__/                # Backend test suite (pytest)
│       ├── conftest.py           # Pytest fixtures and config
│       ├── test_health.py        # Health endpoint tests
│       ├── test_upload.py        # PUT /api/share tests
│       ├── test_view.py          # View/raw/image endpoint tests
│       └── helpers/              # Test utilities
│           ├── __init__.py
│           ├── fixtures.py       # Document factory functions
│           └── setup.py          # Test environment setup
├── tests/                        # Integration tests (Docker-based)
│   └── integration/              # End-to-end container tests
│       ├── __init__.py           # Package marker
│       ├── conftest.py           # Docker container lifecycle fixtures
│       └── test_api.py           # HTTP API integration tests
├── .devcontainer/                # VS Code devcontainer config
└── .gitignore
```

**Key casing rules:**
- Python imports: `from backend.storage import get_storage` (lowercase)
- Dockerfile COPY: `COPY backend/ /app/backend/` (lowercase)
- All file references in documentation, configs, and code MUST use lowercase directory names

---

## Architecture Overview

```
┌──────────┐  PUT /api/share       ┌──────────────────┐
│  curl /  │ ──────────────────────▶│  Flask app        │
│  API     │ ◀── { url, [pw] } ─── │  (Python 3.13)    │
└──────────┘                        │  Port 5000        │
                                    │  + SQLite storage  │
                                    │  + static viewer   │
                                    └──────────────────┘
```

### Single Container Design

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| `mdshare` | `mdshare` | 8080→5000 | Flask API + static viewer + SQLite |

- **Volume**: `mdshare_data` mounted at `/app/data` in container
- **Healthcheck**: `curl -f http://localhost:5000/api/health` every 30s

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.13+, Flask |
| Storage | SQLite (single `shares` table) |
| WSGI Server | Gunicorn (2 workers, 4 threads, gthread) |
| Markdown rendering | marked.js (client-side CDN) |
| Diagrams | Mermaid.js (client-side CDN) |
| Syntax highlighting | highlight.js (client-side CDN, auto-detection) |
| Math rendering | KaTeX (client-side CDN, auto-render) |
| XSS sanitization | DOMPurify (client-side CDN) |
| Password hashing | bcrypt |
| Testing | pytest |

---

## API Reference

### Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `PUT` | `/api/share` | `Authorization: Bearer <master>` | Upload Markdown + images (multipart) |
| `GET` | `/api/health` | None | Health check (`{"status": "ok"}`) |
| `GET` | `/v/<id>` | None | Serve viewer HTML page |
| `GET` | `/v/<id>/raw` | `?pw=<password>` if protected | Raw Markdown content |
| `GET` | `/v/<id>/img/<filename>` | None | Serve uploaded image |
| `GET` | `/v/<id>/config` | None | Return merged display config (global defaults + per-share overrides) |
| `GET` | `/themes.css` | None | Dark/light theme CSS |

### Upload API Details

**Request**: `PUT /api/share`
- Header: `Authorization: Bearer <MDSHARE_MASTER_PASSWORD>`
- Content-Type: `multipart/form-data`
- Fields:
  - `content` (required): Markdown text
  - `protected` (optional): `"yes"` or `"no"` (default: `"no"`)
  - `display_config` (optional): JSON string with display overrides (any subset of the 8 display keys). Example: `{"theme":"dark","code_line_numbers":true}`.
  - Image files: any additional parts are saved as images

**Response** (201 Created):
```json
{
  "url": "https://example.com/v/abc123def456/raw",
  "password": "xYz12AbC"  // only present if protected=true
}
```

**Image handling**: References like `![alt](image.png)` in Markdown are rewritten to `/v/<id>/img/image.png` paths. Upload image files as additional form parts with matching filenames.

### View Flow

1. User opens `/v/<id>` → gets viewer HTML
2. Viewer JavaScript fetches `/v/<id>/raw` with optional `?pw=` param
3. If protected and password matches bcrypt hash → returns raw Markdown
4. Client renders with marked.js, highlight.js, Mermaid, KaTeX

---

## Important Conventions

### Paths & Imports
- **Python imports always use lowercase**: `from backend.storage import get_storage`
- **Dockerfile COPY commands use lowercase**: `COPY backend/ /app/backend/`
- **Test files in `backend/__tests__/`** (lowercase, double-underscore pytest convention)
- **Storage backends in `backend/storage/`** (lowercase)

### Authentication
- **Upload**: `Authorization: Bearer <MDSHARE_MASTER_PASSWORD>` header
- **MCP (Streamable HTTP)**: `Authorization: Bearer <MDSHARE_MASTER_PASSWORD>` header on `/api/mcp` — same Bearer token as Upload; enforced via ASGI middleware wrapping `mcp.streamable_http_app()`
- **View (protected)**: `?pw=<password>` query parameter on `/v/<id>/raw`
- Master password verified with `secrets.compare_digest` (timing-safe)
- View password stored as bcrypt hash in SQLite

### Environment Variables
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MDSHARE_MASTER_PASSWORD` | **Yes** | `changeme` | Master password for upload auth |
| `MDSHARE_DATA_DIR` | No | `/app/data` | SQLite DB + image storage directory |
| `MDSHARE_MAX_SIZE` | No | `16777216` | Max upload size in bytes (16 MB) |
| `MDSHARE_BASE_URL` | No | *(auto-detected)* | Explicit base URL for share links (e.g. `https://mdshare.example.com`). Overrides auto-detection from `X-Forwarded-*` headers. |
| `MDSHARE_DISPLAY_CONFIG` | No | *(auto-resolved)* | Path to display defaults YAML file (default: `${MDSHARE_DATA_DIR}/display.yaml`) |

### Storage Schema

Single SQLite table:
```sql
CREATE TABLE IF NOT EXISTS shares (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    password TEXT,           -- bcrypt hash, NULL for public shares
    display_config TEXT,      -- JSON-serialized per-share display overrides
    created_at TEXT DEFAULT datetime('now')
)
```

Images stored on filesystem at `{MDSHARE_DATA_DIR}/images/{doc_id}/{filename}`.

### Git Practices
- Commit after every completed task/subtask
- Short, concise commit messages in imperative mood
- Repository hosted at `https://github.com/gelse/mdshare`

---

## Common Commands

### Development (local venv)

```bash
# Run test suite
make test

# Run tests in watch mode (rerun on file changes)
make test-watch

# Run tests with HTML coverage report (outputs to htmlcov/)
make test-coverage

# Run pytest directly
python -m pytest
```

### Docker

```bash
# Build image
docker build -t mdshare .

# Start the stack
docker compose up -d

# Run tests in Docker (via compose profile)
docker compose --profile test up --build --abort-on-container-exit --exit-code-from test
docker compose --profile test down

# Or manually with the test Dockerfile:
docker build -t mdshare:test -f Dockerfile.test .
docker run --rm -v ./test-results:/app/test-results mdshare:test
```

---

## Testing Notes

Unless you are in a devcontainer (the current user in a devcontainer is 'vscode') NEVER run tests directly. Always use the dedicated docker image for that. Because the code is always copied inside of the container, for each run you have to build the image fresh.
If you are inside of a devcontainer, NEVER use docker.

### Integration Tests

Integration tests live in `tests/integration/` and test an externally-deployed mdshare instance over HTTP.

- **Location**: `tests/integration/`
- **Purpose**: Run all HTTP endpoint tests (health, upload, view, raw, images, themes) against a **separately deployed** mdshare container
- **How to run**: `make test-integration` (requires `DEPLOYMENT_HOST` env var)
- **Marker**: All integration tests use `@pytest.mark.integration` (already defined in `pytest.ini`)
- **Architecture**: Session-scoped fixture in `conftest.py` reads the `DEPLOYMENT_HOST` environment variable and polls `/api/health` until the deployment is ready. **No Docker CLI calls** — the container is expected to be managed externally.
- **Environment variables**:
  - `DEPLOYMENT_HOST` (required) — base URL of the running instance (e.g. `http://192.168.1.100:5000`)
  - `DEPLOYMENT_MASTER_PASSWORD` (optional, default: `test-integration-master-pw`) — master password configured on the deployment
- **Dependencies**: Only `httpx` and `pytest` — no backend imports. Tests interact purely via HTTP.
- **CI workflow**: Triggered manually with `deployment_host` and `deployment_master_password` inputs, which are passed as environment variables to the test runner.

---

## Filesystem Notes

1. **Case-sensitive filesystem**: Linux is case-sensitive. `backend/` ≠ `Backend/`. All paths in this project use lowercase directory names.

2. **VS Code display quirk**: VS Code may show directories as `Backend/`, `Docs/`, `Frontend/`, `Scripts/` in the explorer, but the actual filesystem paths are `backend/`, `docs/`, `frontend/`, `scripts/`. Always use lowercase in commands, imports, and configs.

3. **All paths relative to project root**

4. **Key config files**:
   - `docker-compose.yml` — single service definition
   - `Dockerfile` — single-stage production image
   - `Makefile` — test and coverage targets
   - `pytest.ini` — pytest configuration
   - `backend/requirements.txt` — runtime Python dependencies (Flask, gunicorn, bcrypt, python-multipart, pyyaml)
   - `requirements-dev.txt` — development dependencies (pytest, pytest-cov, httpx)
   - `tests/` — integration test suite (external deployment HTTP tests)

5. **Data storage**: SQLite database at `{MDSHARE_DATA_DIR}/mdshare.db`. Images at `{MDSHARE_DATA_DIR}/images/{doc_id}/{filename}`.

6. **Git**: Repository root is the project root. `.gitignore` excludes `venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `htmlcov/`, `data/`, `certs/`.
