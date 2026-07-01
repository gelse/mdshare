# Plan: List Active Shares (Admin Endpoint)

## 1. Goals

Add an administrator endpoint to list all active (non-expired) shares, including
their publish date (`created_at`) and expiration date (`valid_until`). Expose
the same functionality as an MCP tool. Follows the project pattern: **API first,
then MCP wrapper**.

## 2. Design Overview

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  curl / MCP  │────▶│  Flask / MCP     │────▶│  SqliteStorage   │
│  client      │     │  server          │     │  .list_active()  │
└──────────────┘     └──────────────────┘     └─────────────────┘
```

### 2.1 Data Flow

1. **Storage layer**: New `list_active()` method on `StorageBackend` ABC
   → `SqliteStorage` implementation queries `shares` table filtering out
   expired rows (`valid_until IS NULL OR valid_until > datetime('now')`).
2. **Flask route**: `GET /api/admin/shares` with Bearer token auth
   → calls `storage.list_active()` → returns JSON array with metadata.
3. **MCP tool**: `list_shares` with `master_password` parameter
   → same auth pattern as `create_share` → delegates to storage.

### 2.2 What "Active" Means

A share is **active** if it is **not expired**. Expired shares are those where
`valid_until` is not null and its timestamp is in the past. Shares with
`valid_until = NULL` (grandfathered or `ttl=0`) never expire and are always
considered active.

This aligns with the existing lazy-expiry pattern: expired shares are deleted
on access via `storage.get()`, but `list_active()` only filters the query —
it does **not** delete anything.

### 2.3 Response Shape

Both the Flask endpoint and the MCP tool return the same data structure:

```json
{
  "shares": [
    {
      "id": "abc123def456",
      "url": "https://mdshare.example.com/v/abc123def456",
      "created_at": "2026-07-01T12:00:00",
      "valid_until": "2026-07-08T12:00:00",
      "protected": true
    }
  ],
  "count": 1
}
```

| Field        | Type             | Description                                    |
|-------------|------------------|------------------------------------------------|
| `id`         | `string`         | 12-character URL-safe share identifier         |
| `url`        | `string`         | Full viewer URL (using configured `BASE_URL`)  |
| `created_at` | `string`         | ISO 8601 UTC publish timestamp                 |
| `valid_until`| `string \| null` | ISO 8601 UTC expiry timestamp, `null` = never  |
| `protected`  | `boolean`        | `true` if the share has a password hash set    |

## 3. Implementation Details

### 3.1 New Abstract Method: [`StorageBackend.list_active()`](backend/storage/abstract.py)

Add after the `delete()` abstract method (after line 62):

```python
@abstractmethod
def list_active(self) -> list[dict]:
    """Return metadata for all non-expired shares.

    Each dict contains:
        - ``id`` (str): Share identifier.
        - ``created_at`` (str): ISO 8601 UTC creation timestamp.
        - ``valid_until`` (str | None): Expiry timestamp or None.
        - ``protected`` (bool): True if password-protected.

    Expired shares (valid_until not null and in the past) are
    excluded. Shares with valid_until = NULL never expire.
    """
    ...
```

Location: insert as new abstract method between `delete()` (line 62) and `close()` (line 64).

### 3.2 SqliteStorage Implementation: [`SqliteStorage.list_active()`](backend/storage/sqlite.py)

Add a new method in the `SqliteStorage` class (after `exists()` at line 136):

```python
def list_active(self) -> list[dict]:
    """Return metadata for all non-expired shares."""
    rows = self._conn.execute(
        "SELECT id, created_at, valid_until, password "
        "FROM shares "
        "WHERE valid_until IS NULL OR valid_until > datetime('now') "
        "ORDER BY created_at DESC"
    ).fetchall()

    result: list[dict] = []
    for id_, created_at, valid_until, password_hash in rows:
        result.append({
            "id": id_,
            "created_at": created_at,
            "valid_until": valid_until,
            "protected": password_hash is not None,
        })
    return result
```

Design notes:
- The `ORDER BY created_at DESC` sorts newest shares first — most useful for
  an admin listing.
- `protected` is derived from `password IS NOT NULL` — no bcrypt verification
  needed.
- The query filters expired shares at the SQL level, matching the lazy-expiry
  semantics.
- No image directory scanning — image metadata is not needed for listing.

### 3.3 Flask Route: `GET /api/admin/shares` ([`backend/app.py`](backend/app.py))

Insert a new route before the `# error handlers` section (before line 297):

```python
@app.route("/api/admin/shares")
def list_shares():
    """List all active (non-expired) shares.

    **Auth**: ``Authorization: Bearer <master-password>`` header.

    **Response** (200):
        ``{"shares": [...], "count": N}`` — each entry has id, url,
        created_at, valid_until, and protected flag.

    **Errors**:
        - 401 — missing or invalid master password
    """
    if not _check_master_auth():
        return jsonify({"error": "unauthorized"}), 401

    storage = get_storage()
    shares = storage.list_active()

    # Add computed url field to each share
    base = _base_url()
    for share in shares:
        share["url"] = f"{base}/v/{share['id']}"

    return jsonify({"shares": shares, "count": len(shares)})
```

Auth pattern: reuses the existing [`_check_master_auth()`](backend/app.py:45) helper —
identical to the upload route. No new auth code needed.

URL construction: reuses [`_base_url()`](backend/app.py:98), consistent with the
upload response.

### 3.4 MCP Tool: [`list_shares`](backend/mcp_server.py)

Insert after the `health_check` tool (after line 237, before `mcp_app = ...` at line 241):

```python
@mcp.tool(
    description=(
        "List all active (non-expired) shares.  Requires the master "
        "password for authentication.  Returns metadata including id, "
        "url, created_at, valid_until, and protected status."
    ),
)
async def list_shares(master_password: str) -> dict[str, Any]:
    """List all active (non-expired) shares (admin only)."""
    if not MASTER_PASSWORD:
        return {"error": "master password not configured"}
    if not secrets.compare_digest(master_password, MASTER_PASSWORD):
        return {"error": "unauthorized — invalid master password"}

    storage = get_storage()
    shares = storage.list_active()

    base = BASE_URL
    for share in shares:
        share["url"] = f"{base}/v/{share['id']}"

    return {"shares": shares, "count": len(shares)}
```

Auth pattern: identical to [`create_share`](backend/mcp_server.py:65-68) — uses
`secrets.compare_digest` for timing-safe comparison.

No need to handle `MASTER_PASSWORD` being empty differently — the `create_share`
tool already sets this precedent.

## 4. Test Plan

### 4.1 Flask HTTP Tests ([`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py))

New test class `TestAdminListShares` appended to the file (after `TestUploadTTL` at line 241):

```python
class TestAdminListShares:
    """Tests for GET /api/admin/shares — admin share listing."""

    AUTH = {"Authorization": f"Bearer {MASTER_PW}"}
    URL = "/api/admin/shares"

    def test_returns_empty_list_when_no_shares(self, client):
        resp = client.get(self.URL, headers=self.AUTH)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data == {"shares": [], "count": 0}

    def test_returns_401_without_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401
        assert resp.get_json() == {"error": "unauthorized"}

    def test_returns_401_with_wrong_password(self, client):
        resp = client.get(
            self.URL,
            headers={"Authorization": "Bearer wrong-password"},
        )
        assert resp.status_code == 401

    def test_lists_public_and_protected_shares(self, client):
        # Create one public and one protected share
        client.put(
            "/api/share",
            data={"content": "public share"},
            headers=self.AUTH,
        )
        client.put(
            "/api/share",
            data={"content": "protected share", "protected": "yes"},
            headers=self.AUTH,
        )

        resp = client.get(self.URL, headers=self.AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2

        # Find the protected share and verify its flag
        protected = [s for s in data["shares"] if s["protected"]]
        assert len(protected) == 1
        assert protected[0]["protected"] is True

    def test_each_share_has_required_fields(self, client):
        client.put(
            "/api/share",
            data={"content": "test"},
            headers=self.AUTH,
        )
        resp = client.get(self.URL, headers=self.AUTH)
        data = resp.get_json()
        share = data["shares"][0]
        assert set(share.keys()) == {
            "id", "url", "created_at", "valid_until", "protected"
        }
        assert share["id"] and isinstance(share["id"], str)
        assert share["url"].startswith("http")
        assert share["created_at"] is not None

    def test_excludes_expired_shares(self, client, monkeypatch):
        """Shares with valid_until in the past should not appear."""
        # Override _default_ttl_hours to 0 so first share never expires
        # Then create a second share with TTL already in the past via
        # direct storage manipulation.
        from backend.app import _DEFAULT_TTL_HOURS

        monkeypatch.setattr("backend.app._DEFAULT_TTL_HOURS", 0)
        client.put(
            "/api/share",
            data={"content": "forever"},
            headers=self.AUTH,
        )

        # Insert an already-expired share directly
        from backend.storage import get_storage
        from datetime import datetime, timezone, timedelta
        import secrets, string

        expired_id = "".join(
            secrets.choice(string.ascii_lowercase + string.digits)
            for _ in range(12)
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        storage = get_storage()
        storage.create(
            expired_id,
            {
                "content": "expired",
                "password": None,
                "valid_until": past,
            },
        )

        resp = client.get(self.URL, headers=self.AUTH)
        data = resp.get_json()
        assert data["count"] == 1  # only the non-expired share
        assert data["shares"][0]["valid_until"] is None

    def test_response_is_json(self, client):
        resp = client.get(self.URL, headers=self.AUTH)
        assert resp.is_json
```

The `AUTH` constant reuses [`MASTER_PW`](backend/__tests__/test_upload.py) which
is already defined at module level (line 5 of test_upload.py).

### 4.2 MCP Direct-Call Tests ([`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py))

New test class appended after `TestHealthCheck` (after line 334):

```python
class TestListShares:
    """Tests for :func:`list_shares` tool function."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_shares(self):
        result = await list_shares(master_password=_VALID_PW)
        assert result == {"shares": [], "count": 0}

    @pytest.mark.asyncio
    async def test_missing_master_password_returns_error(self):
        result = await list_shares(master_password="")
        assert result == {"error": "unauthorized — invalid master password"}

    @pytest.mark.asyncio
    async def test_wrong_master_password_returns_error(self):
        result = await list_shares(master_password="wrong-password")
        assert result == {"error": "unauthorized — invalid master password"}

    @pytest.mark.asyncio
    async def test_lists_public_and_protected_shares(self):
        # Create a public share
        await create_share(
            content="public test",
            master_password=_VALID_PW,
        )
        # Create a protected share
        await create_share(
            content="protected test",
            master_password=_VALID_PW,
            protected=True,
        )

        result = await list_shares(master_password=_VALID_PW)
        assert result["count"] == 2

        ids = [s["id"] for s in result["shares"]]
        assert len(ids) == 2
        assert len(set(ids)) == 2

        protected = [s for s in result["shares"] if s["protected"]]
        assert len(protected) == 1

    @pytest.mark.asyncio
    async def test_share_has_url_field(self):
        await create_share(
            content="url test",
            master_password=_VALID_PW,
        )
        result = await list_shares(master_password=_VALID_PW)
        share = result["shares"][0]
        assert "url" in share
        assert share["url"].startswith("http")

    @pytest.mark.asyncio
    async def test_excludes_expired_shares(self, monkeypatch):
        """Shares with valid_until in the past should not be listed."""
        # Create non-expiring share
        await create_share(
            content="forever",
            master_password=_VALID_PW,
            ttl_hours=0,
        )

        # Insert an already-expired share directly
        from backend.storage import get_storage
        from datetime import datetime, timezone, timedelta
        import secrets, string

        expired_id = "".join(
            secrets.choice(string.ascii_lowercase + string.digits)
            for _ in range(12)
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        storage = get_storage()
        storage.create(
            expired_id,
            {
                "content": "expired",
                "password": None,
                "valid_until": past,
            },
        )

        result = await list_shares(master_password=_VALID_PW)
        assert result["count"] == 1
        assert result["shares"][0]["valid_until"] is None

    @pytest.mark.asyncio
    async def test_field_keys_match_expected_schema(self):
        await create_share(
            content="schema test",
            master_password=_VALID_PW,
        )
        result = await list_shares(master_password=_VALID_PW)
        share = result["shares"][0]
        assert set(share.keys()) == {
            "id", "url", "created_at", "valid_until", "protected"
        }
```

The import line at the top of the file (line 14-18) needs to add `list_shares`:

```python
from backend.mcp_server import (
    create_share,
    get_share,
    get_share_info,
    health_check,
    list_shares,        # <-- new
)
```

## 5. Documentation Update

### 5.1 README.md API Reference

Add a new subsection after the `/api/health` entry (after line 118):

```markdown
### `GET /api/admin/shares`

List all active (non-expired) shares with their metadata.

**Auth**: `Authorization: Bearer <master-password>` (required)

**Response** (200):
```json
{
  "shares": [
    {
      "id": "abc123def456",
      "url": "https://mdshare.example.com/v/abc123def456",
      "created_at": "2026-07-01T12:00:00",
      "valid_until": "2026-07-08T12:00:00",
      "protected": true
    }
  ],
  "count": 1
}
```

**Errors**:
| Status | Body | Cause |
|--------|------|-------|
| 401 | `{"error": "unauthorized"}` | Missing or invalid master password |
```

### 5.2 README.md MCP Tools

Add `list_shares` to the Available Tools table (after `health_check` entry, around line 203):

```markdown
| `list_shares` | List all active shares with metadata | `master_password` |
```

## 6. Summary of File Changes

| File | Change | Lines |
|------|--------|-------|
| [`backend/storage/abstract.py`](backend/storage/abstract.py) | Add `list_active()` abstract method | +13 (after L62) |
| [`backend/storage/sqlite.py`](backend/storage/sqlite.py) | Implement `list_active()` | +18 (after L136) |
| [`backend/app.py`](backend/app.py) | Add `GET /api/admin/shares` route | +22 (before L297) |
| [`backend/mcp_server.py`](backend/mcp_server.py) | Add `list_shares` tool, update docstring | +23 (after L237) |
| [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) | Add `TestAdminListShares` class | +115 (after L241) |
| [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py) | Add `TestListShares` class, update import | +100 (after L334) |
| [`README.md`](README.md) | Add API docs + MCP tool entry | ~+40 |

**Total estimated new lines**: ~330 across 7 files (all additions, no deletions).

## 7. Design Decisions Log

1. **Method name `list_active()` vs `list()`**: `list_active` is more descriptive
   and doesn't shadow the Python built-in `list`.

2. **Only listing active (non-expired) shares**: The user asked for "active
   publishings". Expired shares are intentionally excluded. There is no separate
   "all shares including expired" endpoint because expired shares are cleaned up
   lazily by `storage.get()`.

3. **`protected` derived from `password IS NOT NULL`**: No need to verify the
   bcrypt hash — the mere presence of a hash means the share was created with
   `protected=True`.

4. **No content/truncation in listing**: The admin endpoint shows metadata only.
   Full content retrieval remains the job of `GET /v/<id>/raw`.

5. **No pagination**: For a self-hosted service where share counts are expected
   to be low (hundreds, not millions), pagination adds unnecessary complexity.
   Can be added later if needed.

6. **URL field computed at API layer, not storage**: Storage returns raw data;
   URL construction (which depends on `BASE_URL` configuration) happens in the
   Flask/MCP layer. This keeps the storage backend URL-agnostic.

7. **Test file placement**: Admin route tests go in `test_upload.py` because
   that file already tests the authenticated upload endpoint and the `AUTH`
   constant is available. An alternative would be a new `test_admin.py` file,
   but adding to the existing file follows the "fewer files" philosophy.
