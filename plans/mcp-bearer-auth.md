# Plan: Add HTTP Bearer Authentication to MCP Endpoint

> **Status**: 📋 Planned — ready for implementation
> **Created**: 2026-07-01T14:50:00Z
> **Updated**: 2026-07-01T15:04:00Z — Refactored to reuse existing auth extraction logic
> **Target**: Enforce `Authorization: Bearer <MDSHARE_MASTER_PASSWORD>` at the HTTP transport level for all `/api/mcp` requests

---

## 1. Problem Statement

The MCP endpoint (`/api/mcp`) currently has **no HTTP-level authentication**. The master password is passed as a tool function argument (`master_password: str`) and each tool individually calls `verify_master_password()`. This differs from the Flask API which uses proper HTTP Bearer authentication via the `Authorization: Bearer <token>` header.

Additionally, the Bearer token extraction logic lives **only** in Flask's `_check_master_auth()` at [`backend/app.py:40-46`](backend/app.py:40) — it's not reusable outside a Flask request context.

---

## 2. Design: Extract Then Reuse

### 2.1 Refactor: Extract Shared `extract_bearer_token()` to `auth.py`

Currently the Bearer parsing is locked inside [`backend/app.py:42-45`](backend/app.py:42):

```python
auth = request.headers.get("Authorization", "")
if not auth.startswith("Bearer "):
    return False
token = auth[7:]
```

**Extract into a reusable function** in [`backend/services/auth.py`](backend/services/auth.py:10):

```python
def extract_bearer_token(header_value: str) -> str | None:
    """Parse a Bearer token from an Authorization header value.
    
    Returns the token string if the header is a valid Bearer token,
    or None if the header is missing, malformed, or uses a different scheme.
    """
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value[7:]  # strip "Bearer " prefix
    return token or None  # empty token → None
```

Then **refactor Flask's `_check_master_auth()`** to use it:

```python
def _check_master_auth() -> bool:
    """Verify the ``Authorization: Bearer`` header in the current request."""
    auth = request.headers.get("Authorization", "")
    token = extract_bearer_token(auth)
    return verify_master_password(token)
```

Result: | `app.py:_check_master_auth()` | → | calls `extract_bearer_token()` + `verify_master_password()` | Same as before |
| MCP middleware | → | calls `extract_bearer_token()` + `verify_master_password()` | New |

### 2.2 Add ASGI Bearer Middleware to `mcp_server.py`

```mermaid
flowchart TD
    A[HTTP Request to /api/mcp] --> B{ASGI Dispatch}
    B -->|/api/mcp| C[Bearer Auth Middleware]
    C --> D[extract_bearer_token from ASGI headers]
    D --> E{Token valid?}
    E -->|No| F[401: unauthorized]
    E -->|Yes| G[FastMCP streamable_http_app]
    G --> H[MCP Tool with master_password param]
```

The middleware is a pure ASGI wrapper:

```python
def _bearer_auth_middleware(inner_app):
    """Wrap an ASGI app with Bearer token authentication.
    
    Uses the shared extract_bearer_token() + verify_master_password()
    from backend.services.auth — same logic as the Flask API.
    """
    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await inner_app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode(errors="replace")
        token = extract_bearer_token(auth_header)

        if not verify_master_password(token):
            await _send_401(send)
            return

        await inner_app(scope, receive, send)

    return middleware
```

Integration point — line 218 of `backend/mcp_server.py`:

```python
# Before:
mcp_app = mcp.streamable_http_app()

# After:
mcp_app = _bearer_auth_middleware(mcp.streamable_http_app())
```

### 2.3 Why Keep Tool-Level `master_password` Parameter

| Reason | Detail |
|--------|--------|
| **Test compatibility** | Existing tests call MCP tool functions directly (not via HTTP) |
| **Defense in depth** | Two independent auth layers — HTTP middleware + tool verification |
| **Backward compatibility** | MCP clients already pass `master_password` — no breaking change |
| **Non-HTTP contexts** | If tools are ever used outside HTTP (stdio transport), auth still works |

---

## 3. Files to Modify

| # | File | Change | Lines |
|---|------|--------|-------|
| 1 | [`backend/services/auth.py`](backend/services/auth.py:10) | Add `extract_bearer_token(header_value) -> str \| None` | +15 |
| 2 | [`backend/app.py`](backend/app.py:40) | Refactor `_check_master_auth()` to use `extract_bearer_token()` | ~3 changed |
| 3 | [`backend/mcp_server.py`](backend/mcp_server.py:218) | Add `_bearer_auth_middleware()`, `_send_401()`, wrap `mcp_app` export | +35 |
| 4 | [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py:25) | Add `TestMcpBearerAuth` class with HTTP-level auth tests | +60 |
| 5 | [`plans/mcp-endpoint.md`](plans/mcp-endpoint.md:322) | Update auth architecture section | ~5 changed |

### Files NOT Modified

| File | Reason |
|------|--------|
| [`backend/asgi.py`](backend/asgi.py:1) | Middleware applied inside `mcp_server.py`, not at dispatch level |
| [`backend/config.py`](backend/config.py:1) | `config.master_password` already used by `verify_master_password()` |
| Existing MCP tool tests | Tools still accept `master_password` parameter — unchanged |
| [`backend/__tests__/conftest.py`](backend/__tests__/conftest.py:15) | Flask test client already used; ASGI test client created in new test class |
| `Dockerfile` | No new dependencies needed |

---

## 4. Test Plan

### 4.1 New Tests: `TestMcpBearerAuth` in `test_mcp_server.py`

Using `httpx.ASGITransport` (already a dev dependency) against the wrapped `mcp_app`:

| Test | Verification |
|------|-------------|
| `test_no_auth_header_returns_401` | Request without `Authorization` → 401 |
| `test_wrong_scheme_returns_401` | `Authorization: Basic xxx` → 401 |
| `test_empty_bearer_token_returns_401` | `Authorization: Bearer ` → 401 |
| `test_wrong_token_returns_401` | `Authorization: Bearer wrong-pw` → 401 |
| `test_correct_token_passes_through` | `Authorization: Bearer test-master-password` → forwarded to MCP |
| `test_lifespan_events_not_blocked` | ASGI lifespan events bypass auth check |

### 4.2 Existing Tests

All existing tool-function tests in [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py:1) remain **unchanged** — they call tools directly with `master_password=_VALID_PW`.

---

## 5. Shared Auth Stack

```
                    ┌─────────────────────────────────┐
                    │  backend/services/auth.py        │
                    │                                  │
                    │  extract_bearer_token(header)     │ ← NEW: parses "Bearer <token>"
                    │  verify_master_password(token)    │ ← EXISTING: timing-safe compare
                    └──────────┬───────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                                 │
   ┌──────────▼──────────┐          ┌──────────▼──────────┐
   │  backend/app.py      │          │ backend/mcp_server  │
   │  (Flask WSGI)        │          │ (FastMCP ASGI)      │
   │                      │          │                     │
   │  _check_master_auth  │          │ _bearer_auth_       │
   │  → extract + verify  │          │   middleware        │
   │                      │          │ → extract + verify  │
   └──────────────────────┘          └─────────────────────┘
```

---

## 6. Error Response Format

Matches the existing Flask API 401 format:

```json
{"error": "unauthorized"}
```

HTTP status: `401`

---

## 7. Summary

| Aspect | Before | After |
|--------|--------|-------|
| HTTP auth on `/api/mcp` | ❌ None | ✅ Bearer token |
| Token extraction | Flask-only `_check_master_auth()` | Shared `extract_bearer_token()` in `auth.py` |
| Tool-level auth | ✅ `master_password` param | ✅ unchanged (defense in depth) |
| Flask API auth | ✅ Bearer | ✅ Bearer (refactored, same behavior) |
| New dependencies | — | None |
| Consistency | ❌ Flask=Bearer, MCP=none | ✅ Both use shared extraction + verification |
