# Plan: Batch Set `valid_until` Date

## 1. Goals

- Add a new admin endpoint (`POST /api/admin/shares/validuntil`) that accepts an array of share IDs and a date value
- Set (or clear) the `valid_until` column for the specified shares in a single batch UPDATE
- Expose the same functionality as an MCP tool
- Preparation for future refined deletion and retention mechanisms

## 2. Design Overview

```mermaid
sequenceDiagram
    participant Client
    participant Flask [app.py]
    participant MCP [mcp_server.py]
    participant Service [share_service.py]
    participant Storage [sqlite.py]

    Client->>Flask: POST /api/admin/shares/validuntil
    Flask->>Flask: _check_master_auth()
    Flask->>Service: set_valid_until_date(ids, valid_until)
    Service->>Storage: update_valid_until(ids, valid_until)
    Storage-->>Service: updated count
    Service-->>Flask: {"updated": N, "not_found": M}
    Flask-->>Client: 200 JSON

    Client->>MCP: set_valid_until_date(ids, valid_until)
    MCP->>Service: set_valid_until_date(ids, valid_until)
    Service->>Storage: update_valid_until(ids, valid_until)
    Storage-->>Service: updated count
    Service-->>MCP: {"updated": N, "not_found": M}
    MCP-->>Client: dict result
```

Both the Flask route and the MCP tool delegate to a single `ShareService.set_valid_until_date()` method, which in turn calls a new `StorageBackend.update_valid_until()` method.

## 3. Schema — No Migration Needed

The `valid_until` column already exists in the `shares` table (added in the retention-time plan):

```sql
valid_until TEXT  -- ISO 8601 UTC datetime, NULL = never expires
```

No DDL changes are required. The existing schema supports this feature as-is.

## 4. Storage Layer

### 4.1 [`StorageBackend` ABC](backend/storage/abstract.py)

Add a new abstract method after `delete()` (line ~65):

```python
@abstractmethod
def update_valid_until(self, ids: list[str], valid_until: str | None) -> int:
    """Set the ``valid_until`` column for a batch of share IDs.

    Args:
        ids: List of share IDs to update.
        valid_until: ISO 8601 UTC datetime string, or ``None`` to clear expiry.

    Returns:
        Number of rows actually updated (IDs that existed).
    """
    ...
```

### 4.2 [`SqliteStorage`](backend/storage/sqlite.py)

Add the concrete implementation after `list_active()` (line ~206):

```python
def update_valid_until(self, ids: list[str], valid_until: str | None) -> int:
    """Set valid_until for a batch of shares. Returns count of updated rows."""
    if not ids:
        return 0

    placeholders = ", ".join("?" for _ in ids)
    sql = f"UPDATE shares SET valid_until = ? WHERE id IN ({placeholders})"
    cursor = self._conn.execute(sql, [valid_until] + ids)
    self._conn.commit()
    return cursor.rowcount
```

Key design notes:
- Uses parameterized queries (no SQL injection risk)
- `cursor.rowcount` returns the number of rows matched by the WHERE clause — this is the count of IDs that actually existed
- `valid_until = None` sets the column to SQL NULL, meaning "never expires"
- A single `COMMIT` wraps the entire batch

## 5. Service Layer

### 5.1 [`ShareService.set_valid_until_date()`](backend/services/share_service.py)

Add a new method after `delete_share()` (line ~289):

```python
def set_valid_until_date(self, ids: list[str], valid_until: str | None) -> dict[str, int]:
    """Batch-update the ``valid_until`` date for multiple shares.

    Args:
        ids: List of share IDs to update. Must be non-empty.
        valid_until: ISO 8601 UTC datetime string (e.g. ``"2026-07-10T00:00:00Z"``),
                     or ``None`` to remove the expiry date.

    Returns:
        ``{"updated": N, "not_found": M}`` where *N* is the count of
        successfully updated rows and *M* is IDs that did not match.

    Raises:
        ValueError: If *ids* is empty or *valid_until* is an invalid format.
    """
    if not ids:
        raise ValueError("ids must be a non-empty list")

    if valid_until is not None:
        # Validate that valid_until is a parseable ISO 8601 datetime
        try:
            datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ValueError(
                "valid_until must be an ISO 8601 datetime string or None"
            )

    updated = self.storage.update_valid_until(ids, valid_until)
    not_found = len(ids) - updated
    return {"updated": updated, "not_found": not_found}
```

Add the `datetime` import at the top of the file:
```python
from datetime import datetime
```

**Return type rationale**: Returning `{"updated": N, "not_found": M}` gives the caller full visibility into which IDs were found vs. not found, without revealing which specific IDs were missing (keeping the API response simple).

## 6. Flask API Layer

### 6.1 `POST /api/admin/shares/validuntil` — New Route

Add after the `list_shares` route (after line ~435 in [`backend/app.py`](backend/app.py)):

```python
@app.route("/api/admin/shares/validuntil", methods=["POST"])
def set_valid_until():
    """Batch-set the valid_until date for multiple shares.
    ---
    tags: [Admin]
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [ids, valid_until]
          properties:
            ids:
              type: array
              items:
                type: string
              description: List of share IDs to update
              minItems: 1
            valid_until:
              type: string
              nullable: true
              description: ISO 8601 UTC datetime (e.g. 2026-07-10T00:00:00Z) or null to clear
    responses:
      200:
        description: Batch update result
        schema:
          type: object
          properties:
            updated: {type: integer, description: Count of shares updated}
            not_found: {type: integer, description: Count of IDs that did not exist}
      400:
        description: Invalid request (empty ids, bad date format, missing fields)
      401:
        description: Missing or invalid master password
    """
    if not _check_master_auth():
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "request body must be valid JSON"}), 400

    ids = body.get("ids")
    valid_until = body.get("valid_until")

    # Validate ids
    if not ids or not isinstance(ids, list):
        return jsonify({"error": "ids must be a non-empty array of share IDs"}), 400
    if not all(isinstance(i, str) for i in ids):
        return jsonify({"error": "all ids must be strings"}), 400

    # valid_until can be None (clear expiry) or a datetime string
    # The key "valid_until" must be present in the body (explicit about intent)
    if "valid_until" not in body:
        return jsonify({"error": "valid_until field is required"}), 400
    if valid_until is not None and not isinstance(valid_until, str):
        return jsonify({"error": "valid_until must be an ISO 8601 string or null"}), 400

    try:
        result = share_service.set_valid_until_date(ids, valid_until)
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    return jsonify(result), 200
```

**Route naming**: `POST /api/admin/shares/validuntil` follows the existing admin namespace pattern (`GET /api/admin/shares`). POST is the appropriate HTTP method since this is a state-changing batch operation (not idempotent in the traditional REST sense).

**Body design**: The `valid_until` key is always required in the JSON body. Set it to `null` to clear expiry. This makes the caller's intent explicit — there's no ambiguity between "I forgot to include the field" and "I want to clear the expiry."

**Validation flow**:
1. Auth check (Bearer token)
2. JSON body presence
3. `ids` is a non-empty array of strings
4. `valid_until` key is present in body
5. `valid_until` is either `null` or a string
6. Service layer validates ISO 8601 format

## 7. MCP Server Layer

### 7.1 `set_valid_until_date` — New Tool

Add after the `list_shares` tool (after line ~232 in [`backend/mcp_server.py`](backend/mcp_server.py)):

```python
@mcp.tool()
async def set_valid_until_date(
    ids: list[str],
    valid_until: str | None = None,
) -> dict[str, Any]:
    """Batch-set the valid_until date for multiple shares. Auth via Bearer token.

    Args:
        ids: List of share IDs to update. Must be non-empty.
        valid_until: ISO 8601 UTC datetime string (e.g. ``2026-07-10T00:00:00Z``)
                     or null/omit to clear the expiry date.
    """
    if not ids or len(ids) == 0:
        return {"error": "ids must be a non-empty list"}

    try:
        result = service.set_valid_until_date(ids, valid_until)
    except ValueError as err:
        return {"error": str(err)}

    return result
```

**Auth note**: The MCP Bearer-auth middleware (lines 253–272 in [`backend/mcp_server.py`](backend/mcp_server.py)) already protects all MCP tools globally. No additional per-tool auth check is needed — the middleware handles it before the tool is invoked.

**`valid_until` default of `None`**: Similar to how `ttl_hours` defaults to `None` in `create_share`, this makes clearing the expiry the default behavior when the parameter is omitted — a safe default.

## 8. Test Strategy

### 8.1 Storage Tests (new)

Add tests for [`SqliteStorage.update_valid_until()`](backend/storage/sqlite.py) — can be added to [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) or a new test file. Test cases:

| Test | Description |
|------|-------------|
| Batch update existing shares | Upload 3 shares, update 2 of them, verify only those 2 changed |
| Set `valid_until` to a future date | Upload share, set valid_until, verify `get()` returns the new date |
| Clear `valid_until` (set to None) | Upload share with TTL, clear valid_until, verify it becomes `None` |
| Empty IDs list | Should return 0 updated, 0 not_found (or raise ValueError at service layer) |
| IDs that don't exist | Update non-existent IDs, verify `not_found` count is correct |
| Mix of existing and non-existing | Verify `updated` + `not_found` = len(ids) |

### 8.2 API Endpoint Tests

Add to the `TestAdminListShares` class in [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) (or a new class):

| Test | Description |
|------|-------------|
| `test_set_validuntil_requires_auth` | Returns 401 without Bearer header |
| `test_set_validuntil_wrong_password` | Returns 401 with wrong password |
| `test_set_validuntil_success` | Upload 2 shares, set valid_until on both, verify 200 with `{"updated": 2, "not_found": 0}` |
| `test_set_validuntil_partial_not_found` | Upload 1 share, try to update 2 IDs, verify `{"updated": 1, "not_found": 1}` |
| `test_set_validuntil_clear_expiry` | Upload share, set valid_until, then clear it with `null`, verify via raw endpoint |
| `test_set_validuntil_empty_ids` | Returns 400 |
| `test_set_validuntil_missing_validuntil_key` | Returns 400 when `valid_until` key is absent from body |
| `test_set_validuntil_invalid_date` | Returns 400 for non-ISO-8601 string |
| `test_set_validuntil_missing_body` | Returns 400 for non-JSON body |
| `test_set_validuntil_ids_not_strings` | Returns 400 if ids contain non-string values |

**Auth header pattern** (from existing tests):
```python
auth_headers = {"Authorization": "Bearer test-master-password"}
```

**Fixture helper** — the existing `make_document` / `write_document` helpers in [`backend/__tests__/helpers/fixtures.py`](backend/__tests__/helpers/fixtures.py) and the `client` fixture from [`backend/__tests__/conftest.py`](backend/__tests__/conftest.py) can be used directly. The `reset_storage` autouse fixture ensures clean state between tests.

### 8.3 MCP Tool Tests

Add a new test class to [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py):

| Test | Description |
|------|-------------|
| `test_set_validuntil_batch_update` | Create 2 shares, call `set_valid_until_date` with both IDs, verify result |
| `test_set_validuntil_clear_expiry` | Create share with TTL, clear valid_until, verify via `get_share_info` |
| `test_set_validuntil_empty_ids` | Returns error dict |
| `test_set_validuntil_invalid_date` | Returns error dict |
| `test_set_validuntil_partial_not_found` | Some IDs exist, some don't — verify counts |

**MCP test pattern** (from existing tests):
```python
@pytest.mark.asyncio
async def test_set_validuntil_batch_update(self):
    from backend.mcp_server import set_valid_until_date
    # Create test shares first...
    result = await set_valid_until_date(
        ids=["id1", "id2"],
        valid_until="2026-12-31T23:59:59Z",
    )
    assert result == {"updated": 2, "not_found": 0}
```

## 9. Summary of File Changes

| File | Change | Description |
|------|--------|-------------|
| [`backend/storage/abstract.py`](backend/storage/abstract.py) | Add `update_valid_until` | New abstract method on StorageBackend ABC |
| [`backend/storage/sqlite.py`](backend/storage/sqlite.py) | Add `update_valid_until` | SQLite implementation using parameterized UPDATE |
| [`backend/services/share_service.py`](backend/services/share_service.py) | Add `set_valid_until_date` | Domain logic with input validation, add `datetime` import |
| [`backend/app.py`](backend/app.py) | Add `POST /api/admin/shares/validuntil` | Flask route with Swagger docs + Bearer auth |
| [`backend/mcp_server.py`](backend/mcp_server.py) | Add `set_valid_until_date` tool | Async MCP tool delegating to service layer |
| [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) | Add `TestSetValidUntil` class | API endpoint tests (~10 test cases) |
| [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py) | Add `TestSetValidUntilDate` class | MCP tool tests (~5 test cases) |

**No changes needed**:
- [`backend/__tests__/conftest.py`](backend/__tests__/conftest.py) — existing fixtures suffice
- [`backend/__tests__/helpers/fixtures.py`](backend/__tests__/helpers/fixtures.py) — existing helpers suffice
- [`backend/config.py`](backend/config.py) — no new configuration needed
- Any HTML/CSS/static files — viewer is unaffected

## 10. Design Decisions Log

| Decision | Rationale |
|----------|-----------|
| **Batch update (not single-ID)** | User explicitly requested "array of ids" — future deletion/retention mechanisms will operate on sets. A single UPDATE with `IN (...)` is more efficient than N individual queries. |
| **`POST` not `PUT` or `PATCH`** | `PUT` implies full resource replacement; `PATCH` implies partial update of a single resource. `POST` on a collection endpoint is the standard pattern for batch operations that don't fit CRUD semantics. |
| **`valid_until` key always required in body** | Explicit intent. Setting `null` explicitly clears expiry; omitting the key entirely is an error (no silent defaults for destructive operations). |
| **Return `updated` and `not_found` counts** | Gives the caller enough information to determine success without leaking which specific IDs were missing. Simpler than returning per-ID status arrays. |
| **ISO 8601 validation at service layer** | Keeps validation logic centralized. Both Flask and MCP paths get the same validation. Uses `datetime.fromisoformat()` which is strict about format. |
| **No separate `clear_validuntil` endpoint** | A single endpoint with `valid_until: null` is cleaner than two separate endpoints. The API surface stays minimal. |
| **MCP tool `valid_until` defaults to `None`** | Safe default — omitting the parameter clears expiry rather than requiring an explicit null. Matches the `ttl_hours` pattern in `create_share`. |
