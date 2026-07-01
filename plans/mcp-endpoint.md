# Plan: MCP Endpoint for mdshare

> **Status**: ✅ Final — implemented
> **Created**: 2026-06-29T06:31:00Z
> **Updated**: 2026-07-01T06:38:00Z — Added `master_password` auth to `create_share`
> **Target**: Allow AI agents (Claude, Roo Code, etc.) to interact with mdshare via the Model Context Protocol over HTTP

---

## 1. Goals

- Expose an **MCP Streamable HTTP endpoint** at `/api/mcp` inside the existing mdshare container
- Provide typed MCP **tools** for AI agents to upload, retrieve, and inspect Markdown shares
- Keep the implementation **minimal** — one new module, no second process, no new port
- Share the **same storage layer** and **configuration** as the existing Flask API

---

## 2. Architecture

### 2.1 Integration Model: ASGI Sub-Application

The MCP server is an **ASGI sub-application** mounted alongside Flask in the same process. A thin ASGI dispatch layer routes `/api/mcp` requests to the MCP handler and everything else to Flask:

```
                          POST /api/mcp
                    ┌──────────────────────┐
 AI Agent ──────────│  mdshare Container    │
 (HTTP client)      │                      │
                    │  uvicorn :5000        │
                    │  ┌──────────────────┐ │
                    │  │  asgi.py          │ │
                    │  │  (dispatch)       │ │
                    │  └───┬──────────┬───┘ │
                    │      │          │     │
                    │  ┌───▼────┐ ┌───▼───┐ │
                    │  │ Flask   │ │ MCP   │ │
                    │  │ (WSGI→  │ │ (ASGI)│ │
                    │  │  ASGI)  │ │       │ │
                    │  └───┬────┘ └───┬───┘ │
                    │      │          │     │
                    │      └────┬─────┘     │
                    │           ▼           │
                    │  ┌────────────────┐   │
                    │  │ SqliteStorage   │   │
                    │  │ + Image FS      │   │
                    │  └────────────────┘   │
                    └──────────────────────┘
```

**Key decisions**:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport | **Streamable HTTP** (not stdio) | Bundled with Docker; AI agents connect over network |
| Server | **uvicorn** (replaces gunicorn) | Native ASGI support; same process serves Flask (via `WsgiToAsgi`) + MCP |
| MCP integration | **ASGI sub-app** via simple dispatch | Minimal code; no second process or port |
| Storage access | **Direct import** of `backend.storage` | Same DB, same image FS, no HTTP overhead between Flask and MCP |

### 2.2 Why Not stdio?

The user explicitly requested Streamable HTTP bundled with Docker. stdio transport would require the AI agent to spawn the MCP server as a child process — this doesn't fit a Docker deployment where the container is already running. Streamable HTTP allows any MCP-compatible client to connect to `https://mdshare.example.com/api/mcp`.

---

## 3. Proposed MCP Tools

### 3.1 `create_share` — Upload Markdown Content

```
Tool: create_share
Description: Upload Markdown content to mdshare. Requires master password for
             authentication. Optionally password-protect the share and include
             embedded images as base64-encoded data.

Parameters:
  master_password  (string, required)  — Master password for upload auth (MDSHARE_MASTER_PASSWORD)
  content          (string, required)  — Markdown text to share
  protected        (boolean, optional) — Whether the share requires a view password (default: false)
  images           (object, optional)  — Map of filename → base64-encoded image data
                     e.g. {"diagram.png": "iVBORw0KGgo...", "photo.jpg": "/9j/4AAQ..."}
  ttl_hours        (integer, optional) — Time-to-live in hours (0 = no expiry, default: 168)

Returns:
  {
    "id": "abc123def456",
    "url": "https://mdshare.example.com/v/abc123def456/raw",
    "password": "xYz12AbC",   // only when protected=true
    "valid_until": "2026-07-08T06:00:00+00:00"  // ISO 8601, only when ttl_hours > 0
  }
```

**Authentication**: Caller must provide the `master_password` matching the `MDSHARE_MASTER_PASSWORD` environment variable. Verified with `secrets.compare_digest()` (timing-safe). Both empty and incorrect passwords return `"unauthorized — invalid master password"`.

**Implementation**: Reuses `_generate_id()`, `_generate_password()`, `_rewrite_image_urls()` from [`backend/app.py`](backend/app.py). Calls `storage.create()`. Writes decoded images to `{DATA_DIR}/images/{doc_id}/`.

### 3.2 `get_share` — Retrieve Raw Markdown Content

```
Tool: get_share
Description: Retrieve the raw Markdown content of a share by its ID.
             Provide the view password if the share is protected.

Parameters:
  share_id (string, required)  — The 12-char share identifier
  password (string, optional)  — View password for protected shares

Returns:
  {
    "content": "# Hello World\n\n...",
    "protected": false,
    "created_at": "2026-06-29T06:00:00Z"
  }
```

**Errors**: Share not found → descriptive MCP error. Wrong/missing password → MCP error with clear message.

### 3.3 `get_share_info` — Metadata Without Content

```
Tool: get_share_info
Description: Get metadata about a share without retrieving its full content.
             Useful for checking existence or protection status before fetching.

Parameters:
  share_id (string, required) — The 12-char share identifier

Returns:
  {
    "id": "abc123def456",
    "exists": true,
    "protected": true,
    "created_at": "2026-06-29T06:00:00Z",
    "url": "https://mdshare.example.com/v/abc123def456/raw"
  }
```

**Rationale**: AI agents can check if a share exists and whether they need a password *before* attempting to fetch content — avoiding the awkward "try get_share, catch error, ask user for password" flow.

### 3.4 `health_check` — Service Health

```
Tool: health_check
Description: Verify that the mdshare MCP endpoint and storage backend are
             operational.

Parameters: none

Returns:
  {
    "status": "ok",
    "storage": "sqlite",
    "data_dir": "/data"
  }
```

---

## 4. Tools NOT Included (and Why)

| Tool | Reason for exclusion |
|------|---------------------|
| `list_shares` | `StorageBackend` ABC has no `list()` method. Adding one means a full table scan or schema change. **Deferred** to a future enhancement. |
| `delete_share` | No delete endpoint exists in the Flask API or storage ABC today. **Out of scope** for v1. |
| `update_share` | Shares are immutable after upload. **Out of scope**. |
| `get_viewer_html` | The viewer is client-side rendered; AI agents want raw Markdown, which `get_share` already provides. |

---

## 5. Implementation Plan

### 5.1 New File: [`backend/mcp_server.py`](backend/mcp_server.py) (~225 lines)

Uses the `FastMCP` API (`mcp.server.fastmcp`) with `@mcp.tool()` decorators and typed function signatures — no manual JSON-RPC handling:

```python
# Actual implementation pattern
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mdshare", streamable_http_path="/api/mcp",
              stateless_http=True, json_response=True)

@mcp.tool(description="Upload Markdown content as a new share...")
async def create_share(
    content: str, protected: bool = False,
    images: dict[str, str] | None = None,
) -> dict:
    # generate ID, hash password if protected, rewrite image URLs,
    # write images to disk, call storage.create(), return result
    return {"id": doc_id, "url": "..."}

@mcp.tool(description="Retrieve raw Markdown content...")
async def get_share(share_id: str, password: str = "") -> dict:
    # call storage.get(), check password if protected, return content
    return {"content": ..., "protected": ..., "created_at": ...}

@mcp.tool(description="Get metadata about a share...")
async def get_share_info(share_id: str) -> dict:
    # call storage.get(), return metadata only (no content)
    return {"id": ..., "exists": ..., "protected": ..., "url": ...}

@mcp.tool(description="Verify service is operational...")
async def health_check() -> dict:
    # probe storage
    return {"status": "ok", "storage": "sqlite", ...}

mcp_app = mcp.streamable_http_app()
```

### 5.2 New File: [`backend/asgi.py`](backend/asgi.py) (~25 lines)

Minimal ASGI dispatch that routes MCP requests to the MCP app and everything else to Flask (wrapped as ASGI):

```python
"""mdshare — combined ASGI application (Flask + MCP)."""
from asgiref.wsgi import WsgiToAsgi
from backend.app import app as flask_app
from backend.mcp_server import mcp_app

flask_asgi = WsgiToAsgi(flask_app)

async def app(scope, receive, send):
    if scope["type"] == "http" and scope["path"].startswith("/api/mcp"):
        await mcp_app(scope, receive, send)
    else:
        await flask_asgi(scope, receive, send)
```

### 5.3 Modified: [`backend/requirements.txt`](backend/requirements.txt)

```diff
 flask>=3.0
-gunicorn>=22.0
 bcrypt>=4.0
 python-multipart>=0.0.12
+mcp>=1.0.0
+uvicorn>=0.30.0
+asgiref>=3.0
```

`gunicorn` is removed because `uvicorn` handles ASGI natively with equivalent `--workers` support.

### 5.4 Modified: [`Dockerfile`](Dockerfile) (line 19)

```diff
-CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "30", "backend.app:app"]
+CMD ["uvicorn", "backend.asgi:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "4", "--timeout-keep-alive", "30"]
```

No other Dockerfile changes needed — the same container, same port, same volume.

### 5.5 Modified: [`docker-compose.yml`](docker-compose.yml)

No structural changes required. The healthcheck already uses `python -c "import urllib.request..."` to hit `/api/health` — it works regardless of whether gunicorn or uvicorn is behind it.

### 5.6 New File: `backend/__tests__/test_mcp_server.py`

Tests using **direct function calls** (not HTTP) to the MCP tool functions, bypassing ASGI lifespan issues with Streamable HTTP:

- `TestCreateShare` — public/protected creation, empty content, image inclusion, size enforcement
- `TestGetShare` — retrieve public & protected shares, wrong/missing password, not found
- `TestGetShareInfo` — existing & non-existing shares, empty ID
- `TestHealthCheck` — verify `status: "ok"` response
- Uses `pytest-asyncio` with `@pytest.mark.asyncio` on each async test
- Reuses `reset_storage` autouse fixture from `conftest.py`
- No `httpx.ASGITransport` dependency — pure business-logic testing

### 5.7 MCP Client Configuration (for AI agents)

Example Claude Desktop / Roo Code MCP configuration (HTTP transport):

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

No child process — the AI agent connects to the already-running Docker container.

---

## 6. Configuration

The MCP endpoint reads the **same environment variables** as the Flask app:

| Variable | Required | Default | Used by MCP |
|----------|----------|---------|-------------|
| `MDSHARE_MASTER_PASSWORD` | Yes | `changeme` | Auth for `create_share` (read from env, not passed per-call) |
| `MDSHARE_DATA_DIR` | No | `/app/data` | SQLite DB + image storage location |
| `MDSHARE_BASE_URL` | No | *(auto)* | URL generation in responses |
| `MDSHARE_MAX_SIZE` | No | `16777216` | Max payload size (16 MB) |

**No new environment variables are introduced.**

---

## 7. Resolved Decisions

All open questions resolved (2026-06-29T09:05:00Z):

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Master password source | **Environment only** | Consistent with Flask API Bearer token pattern; MCP client config holds deployment credentials |
| 2 | Image size enforcement | **Apply `MDSHARE_MAX_SIZE`** | Same limit (16 MB default) as multipart uploads; validated before processing |
| 3 | `get_share_info` design | **Separate tool** | Clearer intent, no conditional return types, simpler signatures |
| 4 | gunicorn vs uvicorn | **uvicorn directly** | Simpler dependency chain, equivalent production behavior with `--workers` |
| 5 | MCP create_share auth | **Require `master_password` param** | Caller must pass master password; verified with timing-safe `secrets.compare_digest()` — same mechanism as Flask route |

## 8. Summary

| Aspect | Decision |
|--------|----------|
| Transport | **Streamable HTTP** at `/api/mcp` |
| Integration model | ASGI sub-app in same process (no second container or port) |
| WSGI → ASGI bridge | `asgiref.wsgi.WsgiToAsgi` wrapping Flask |
| Server | **uvicorn** (replaces gunicorn) |
| Tools | `create_share`, `get_share`, `get_share_info`, `health_check` |
| New dependencies | `mcp>=1.0.0`, `uvicorn>=0.30.0`, `asgiref>=3.0` |
| Removed dependencies | `gunicorn>=22.0` |
| New files | `backend/mcp_server.py` (~225 lines), `backend/asgi.py` (~15 lines), `backend/__tests__/test_mcp_server.py` |
| Modified files | `backend/requirements.txt`, `Dockerfile` (CMD line) |
| Docker changes | 1 line changed in Dockerfile; no docker-compose changes |
| Configuration | Same env vars as Flask app; `master_password` caller-supplied for MCP `create_share` |
