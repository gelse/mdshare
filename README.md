# mdshare

Minimal, self-hosted Markdown sharing service. Upload Markdown via API, get a clean URL with GitHub-style rendering — Mermaid diagrams, syntax highlighting, KaTeX math, and optional password protection.

## Why?

Share AI-generated Markdown instantly. One `curl` command, one URL. No accounts, no databases, no complexity.

## Features

- **PUT API upload** — Single endpoint with Bearer token auth, accepts `multipart/form-data`
- **Password-protected shares** — Optional `protected` flag generates a random view password
- **Image uploads** — Inline images uploaded alongside Markdown, rewritten to correct paths
- **Client-side rendering** — marked.js, highlight.js, Mermaid, KaTeX, DOMPurify — all via CDN
- **SQLite storage** — Single `shares` table, no external database needed
- **Single container** — Flask + Gunicorn, no nginx, no microservices

## Quick Start

```bash
# Clone and start
git clone https://forgejo.gelse.local/werner/mdshare.git
cd mdshare
echo "MDSHARE_MASTER_PASSWORD=your-secret-password" > .env
docker compose up -d

# Upload Markdown
curl -X PUT http://localhost:8080/api/share \
  -H "Authorization: Bearer your-secret-password" \
  -F "content=# Hello World" \
  -F "protected=no"

# Response: {"url": "http://localhost:8080/v/abc123def456"}

# Upload with password protection
curl -X PUT http://localhost:8080/api/share \
  -H "Authorization: Bearer your-secret-password" \
  -F "content=# Secret Document" \
  -F "protected=yes"

# Response: {"url": "http://localhost:8080/v/xyz789abc012", "password": "aB3dEfGh"}

# View: open http://localhost:8080/v/xyz789abc012
# Enter password "aB3dEfGh" when prompted
```

## API

### `PUT /api/share`

Upload Markdown content with optional images and password protection.

**Headers**:
- `Authorization: Bearer <master-password>` (required)

**Form fields**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | text | Yes | Markdown content |
| `protected` | text | No | `"yes"` or `"no"` (default: `"no"`) |
| `ttl` | int | No | TTL in hours; `0` means no expiry (default: `168` = 7 days) |
| *any file* | file | No | Images referenced in Markdown |

**Response** (201):
```json
{
  "url": "https://mdshare.example.com/v/<12-char-id>",
  "valid_until": "2026-07-08T05:55:10.346000+00:00"
}
```

**Response with password** (201, when `protected=yes`):
```json
{
  "url": "https://mdshare.example.com/v/<12-char-id>",
  "password": "<8-char-random>",
  "valid_until": "2026-07-08T05:55:10.346000+00:00"
}
```

**Response with `ttl=0`** (201, no expiry):
```json
{
  "url": "https://mdshare.example.com/v/<12-char-id>"
}
```

**Errors**:
| Status | Meaning |
|--------|---------|
| 400 | Missing/empty content or invalid `ttl` value |
| 401 | Missing/wrong master password |
| 404 | Share expired or not found |
| 413 | Content exceeds size limit |

### `GET /v/<id>`

Serves the viewer HTML page. Always accessible — the viewer handles password prompting for protected content.

### `GET /v/<id>/raw`

Returns raw Markdown content. For protected shares, requires `?pw=<password>` query parameter.

**Errors**:
| Status | Meaning |
|--------|---------|
| 401 | Protected share, no password provided |
| 401 | Wrong password |
| 404 | Share not found |

### `GET /v/<id>/img/<filename>`

Serves uploaded images for a share. No authentication required.

### `GET /api/health`

Health check endpoint. Returns `{"status": "ok"}`.

### `GET /api/admin/shares`

List all active (non-expired) shares with pagination. Requires master password authentication.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | `1` | 1-based page number (must be ≥ 1) |
| `page_size` | int | `50` | Number of shares per page (1–200) |

**Headers**:
- `Authorization: Bearer <MDSHARE_MASTER_PASSWORD>` — master password for authentication

**Response** (200 OK):
```json
{
  "shares": [
    {
      "id": "abc123def456",
      "url": "https://example.com/v/abc123def456",
      "created_at": "2026-06-30T12:00:00",
      "valid_until": "2026-07-01T12:00:00",
      "protected": false
    }
  ],
  "page": 1,
  "page_size": 50,
  "total_pages": 1,
  "total_count": 1
}
```

**Errors**:
| Status | Meaning |
|--------|---------|
| 400 | Invalid query parameter (page < 1, page_size out of range, non-integer value) |
| 401 | Missing or invalid `Authorization` header |

### `GET /api/docs/`

Auto-generated Swagger UI documentation for the entire REST API. Browse all endpoints, inspect request/response schemas, and use the **Authorize** button to set your Bearer token for interactive try-out.

The OpenAPI specification is rendered via Flasgger (Swagger 2.0) and includes:

- **Try it out** for all endpoints — click **Authorize** and paste `Bearer <MDSHARE_MASTER_PASSWORD>` to enable authenticated requests
- **Request schemas** — JSON bodies, query parameters, path parameters, and multipart form fields documented
- **Response schemas** — Status codes and response body structures for every endpoint

No authentication required to view the documentation.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MDSHARE_MASTER_PASSWORD` | Yes | `changeme` | Master password for upload API auth |
| `MDSHARE_DATA_DIR` | No | `/app/data` | SQLite DB + image storage directory |
| `MDSHARE_MAX_SIZE` | No | `16777216` | Max upload size in bytes (16 MB) |
| `MDSHARE_BASE_URL` | No | *(auto-detected)* | Explicit base URL for share links (e.g. `https://mdshare.example.com`). Overrides auto-detection from request headers. |

## Display Customization

mdshare supports flexible display customization through **global defaults** (optional YAML file) and **per-share overrides** (upload-time JSON). The viewer applies these settings as CSS custom properties on the rendered page.

### Display Options

| Option | Key | Type | Default | Description |
|--------|-----|------|---------|-------------|
| Font Family | `font_family` | string | `"system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"` | CSS `font-family` for body text |
| Font Size | `font_size` | string | `"16px"` | Base text size (any valid CSS size) |
| Line Height | `line_height` | string | `"1.6"` | Text line height |
| Max Width | `max_width` | string | `"900px"` | Maximum content width (any valid CSS width) |
| Theme | `theme` | string | `"auto"` | `"light"`, `"dark"`, or `"auto"` (follows system preference) |
| Code Font Size | `code_font_size` | string | `"14px"` | Font size for code blocks |
| Code Line Numbers | `code_line_numbers` | boolean | `false` | Show line numbers in code blocks |
| Custom CSS | `custom_css` | string | `""` | Raw CSS string injected into viewer page |

### Global Configuration via YAML

Place a `display.yaml` file in your data directory to set system-wide display defaults:

```
${MDSHARE_DATA_DIR}/display.yaml
```

The path can be overridden with the `MDSHARE_DISPLAY_CONFIG` environment variable.

The file is **optional** — if missing, hardcoded defaults apply. Invalid or malformed files are silently ignored (falling back to defaults).

```yaml
# /app/data/display.yaml
font_family: "Georgia, serif"
theme: "dark"
code_line_numbers: true
```

### Per-Share Override at Upload

Pass a `display_config` JSON form field to override any subset of the 8 display options for a single share:

```bash
# Dark theme with code line numbers
curl -X PUT https://mdshare.example.com/api/share \
  -H "Authorization: Bearer $MDSHARE_MASTER_PASSWORD" \
  -F 'content=# Hello World' \
  -F 'display_config={"theme":"dark","code_line_numbers":true}'

# Custom font, size, and max width
curl -X PUT https://mdshare.example.com/api/share \
  -H "Authorization: Bearer $MDSHARE_MASTER_PASSWORD" \
  -F 'content=# Narrow Article' \
  -F 'display_config={"font_family":"Georgia, serif","font_size":"18px","max_width":"800px"}'
```

The viewer fetches the merged config (global defaults + per-share overrides) via `GET /v/<id>/config` and applies it automatically.

### CSS Custom Properties Reference

The viewer exposes all display values as CSS custom properties on the `<html>` element. You can target them in your own stylesheets or in the `custom_css` option:

```css
/* --md-font-family      — body font family   */
/* --md-font-size         — base text size     */
/* --md-line-height       — text line height   */
/* --md-max-width         — content max-width  */
/* --md-code-font-size    — code block size    */
```

### Example Recipes

#### Serif Blog Posts
```bash
curl -X PUT ... \
  -F 'display_config={"font_family":"Georgia, serif","font_size":"18px","max_width":"720px"}'
```
Georgia font, larger text, narrower reading width — ideal for long-form content.

#### Code-Focused Shares
```bash
curl -X PUT ... \
  -F 'display_config={"theme":"dark","code_line_numbers":true,"code_font_size":"16px"}'
```
Dark theme with line numbers and larger code font — great for sharing code snippets.

#### Branded Shares
```bash
curl -X PUT ... \
  -F 'display_config={"custom_css":".header{background:#333;color:#fff;padding:1em;text-align:center}"}'
```
Inject a company header/footer via custom CSS — perfect for client-facing shares.

#### Light-Only Share
```bash
curl -X PUT ... \
  -F 'display_config={"theme":"light"}'
```
Force light theme regardless of system preference.

## Reverse Proxy

mdshare is ready to run behind a reverse proxy. It respects
`X-Forwarded-Proto`, `X-Forwarded-Host`, and `X-Forwarded-Port` headers
(via Werkzeug's `ProxyFix` middleware).

### nginx example

```nginx
server {
    listen 443 ssl;
    server_name mdshare.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        client_max_body_size 16m;
    }
}
```

### Traefik labels (Docker Compose)

```yaml
labels:
  - "traefik.http.routers.mdshare.rule=Host(`mdshare.example.com`)"
  - "traefik.http.services.mdshare.loadbalancer.server.port=5000"
```

If auto-detection doesn't produce the correct URL, set
`MDSHARE_BASE_URL=https://mdshare.example.com` explicitly.

## Upload with Images

```bash
# Markdown file referencing local images
cat > post.md << 'EOF'
# Screenshot
![screenshot](screenshot.png)
EOF

# Upload with image
curl -X PUT http://localhost:8080/api/share \
  -H "Authorization: Bearer your-secret-password" \
  -F "content=@post.md" \
  -F "protected=no" \
  -F "screenshot.png=@screenshot.png"
```

Image references in Markdown (like `![alt](filename.png)`) are automatically rewritten to the correct `/v/<id>/img/filename.png` path.

## MCP Server

mdshare exposes a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) endpoint at `/api/mcp`, allowing AI agents (Claude, Roo Code, Cursor, etc.) to create, retrieve, and inspect Markdown shares directly.

### Endpoint

```
https://mdshare.example.com/api/mcp
```

The endpoint uses **Streamable HTTP** transport (stateless, JSON responses). No separate process or port — the MCP handler shares the same uvicorn process as the REST API.

### Available Tools

| Tool | Description |
|------|-------------|
| `create_share` | Upload Markdown content as a new share. Supports password protection, base64-encoded images, and optional `ttl_hours` (default: 168 = 7 days; `0` = no expiry). |
| `get_share` | Retrieve raw Markdown content. Provide password for protected shares. |
| `get_share_info` | Check if a share exists and whether it's password-protected, without returning content. |
| `health_check` | Verify the service is operational (storage backend reachable). |
| `list_shares` | List all active (non-expired) shares with pagination. Supports `page_size` (1–200, default 50) and `page` (default 1) parameters. Returns `page`, `page_size`, `total_pages`, and `total_count` metadata. |

### Client Configuration

Add mdshare to your MCP client's configuration. Replace `mdshare.example.com` with your actual deployment host.

#### Roo Code / VS Code

```json
{
  "mcpServers": {
    "mdshare": {
      "url": "https://mdshare.example.com/api/mcp",
      "transport": "streamable-http"
    }
  }
}
```

#### Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mdshare": {
      "type": "streamableHttp",
      "url": "https://mdshare.example.com/api/mcp"
    }
  }
}
```

#### Generic (any MCP client with Streamable HTTP support)

```json
{
  "mcpServers": {
    "mdshare": {
      "url": "https://mdshare.example.com/api/mcp"
    }
  }
}
```

After configuration, the AI agent automatically discovers all five tools via the MCP handshake. No additional setup or API keys required.

## Development

### Prerequisites
- Python 3.13+
- Docker (for containerized deployment)

### Setup
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install runtime deps
pip install -r backend/requirements.txt

# Install dev deps
pip install -r requirements-dev.txt

# Run tests
make test

# Run with coverage
make test-coverage
```

### Running Locally
```bash
# Without Docker
MDSHARE_MASTER_PASSWORD=dev-password \
MDSHARE_DATA_DIR=./data \
python -m flask --app backend.app run --port 5000
```

### Docker Build
```bash
# Build image
docker build -t mdshare .

# Test image (includes test dependencies)
docker build -t mdshare:test -f- . <<'DOCKERFILE'
FROM python:3.13-slim
RUN pip install pytest pytest-cov httpx
COPY backend/ /app/backend/
COPY requirements-dev.txt /app/
RUN pip install -r /app/requirements-dev.txt
WORKDIR /app
CMD ["python", "-m", "pytest", "backend", "--junitxml=/app/junit.xml"]
DOCKERFILE
```

### Integration Tests

Integration tests in [`tests/integration/`](tests/integration/) exercise all HTTP endpoints (health, upload, view, raw, images, themes) against a **separately deployed** mdshare instance — no Docker containers are managed by the test suite itself.

**Prerequisites:**

- A running mdshare instance accessible over HTTP
- [`httpx`](https://www.python-httpx.org/) and `pytest` installed (`pip install -r requirements-dev.txt`)

**Running against a specific URL:**

```bash
# Set the target deployment
export DEPLOYMENT_HOST=http://192.168.1.100:5000

# Optional: set the master password (default: test-integration-master-pw)
export DEPLOYMENT_MASTER_PASSWORD=your-master-password

# Run via make
make test-integration

# Or run directly
python -m pytest tests/integration/ -v -m integration
```

The [`integration_base_url`](tests/integration/conftest.py) fixture reads `DEPLOYMENT_HOST` from the environment. If unset, all integration tests are skipped. Before yielding, the fixture polls `GET /api/health` (up to 30 seconds) to confirm the deployment is reachable, so you can run the command as soon as the container starts.

**CI usage:** The [`integration-test.yml`](.forgejo/workflows/integration-test.yml) workflow is triggered manually via `workflow_dispatch` with `deployment_host` and `deployment_master_password` inputs.

## Database Migration

The app auto-migrates the SQLite schema on startup via a try/except `ALTER TABLE` — no manual steps required for new or existing deployments. For reference, the migration adds a single column:

```sql
ALTER TABLE shares ADD COLUMN valid_until TEXT;
```

This column stores an ISO 8601 UTC timestamp. `NULL` means the share never expires (grandfathered shares or `ttl=0` uploads).

---

## Version

**2.0.0** — Retention time / TTL feature

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

Copyright 2026 Werner Schiller <github@gelse.net>. See [`NOTICE`](NOTICE) for attribution details.
