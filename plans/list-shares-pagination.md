# Plan: Pagination for `list_shares` (API + MCP)

## 1. Motivation

The current `GET /api/admin/shares` and MCP `list_shares` tool return **all** active
shares in a single response. For installations with many shares, this becomes
unwieldy — large JSON payloads, slow response times, and no way for clients to
navigate results in pages.

This plan adds offset-based pagination with `page_size` and `page` parameters
across the entire stack: **storage → service → API → MCP**.

---

## 2. Response Format

### 2.1 Current (unpaginated)

```json
{
  "shares": [{...}, {...}, {...}],
  "count": 3
}
```

### 2.2 Proposed (paginated)

```json
{
  "shares": [{...}, {...}],
  "page": 1,
  "page_size": 50,
  "total_pages": 3,
  "total_count": 120
}
```

| Field | Type | Description |
|-------|------|-------------|
| `shares` | array | Shares for the requested page (sorted `created_at DESC`) |
| `page` | int | Current page number (1-based) |
| `page_size` | int | Items per page |
| `total_pages` | int | Total number of pages (`ceil(total_count / page_size)`) |
| `total_count` | int | Total number of active shares (not just this page) |

> **Note**: The old `count` field is renamed to `total_count` for clarity —
> it now represents the **full dataset size**, not the current page length.
> This is a breaking change, but acceptable for a self-hosted service.

### 2.3 Defaults

| Parameter | Default | Min | Max |
|-----------|---------|-----|-----|
| `page` | 1 | 1 | — |
| `page_size` | 50 | 1 | 200 |

When no query parameters are provided, the endpoint returns page 1 with 50 items
per page — identical behavior for callers who don't care about pagination, just
with a cap at 50 results.

---

## 3. Layer-by-Layer Changes

### 3.1 Storage ABC — [`backend/storage/abstract.py`](backend/storage/abstract.py)

**Current signature** (line 68):
```python
@abstractmethod
def list_active(self) -> list[dict]:
```

**New signature**:
```python
@abstractmethod
def list_active(self, page_size: int = 50, page: int = 1) -> tuple[list[dict], int]:
    """Return (shares_for_page, total_active_count)."""
```

Returns a 2-tuple: `(list_of_share_dicts, total_count)` where `total_count`
is the unfiltered count of all active shares (used to compute `total_pages`).

---

### 3.2 SQLite Storage — [`backend/storage/sqlite.py`](backend/storage/sqlite.py)

**Current implementation** (lines 172–193): single `SELECT` with no `LIMIT`/`OFFSET`.

**New implementation**:

```python
def list_active(self, page_size: int = 50, page: int = 1) -> tuple[list[dict], int]:
    # 1. Total count (single aggregate query)
    total = self._conn.execute(
        "SELECT COUNT(*) FROM shares "
        "WHERE valid_until IS NULL OR valid_until > datetime('now')"
    ).fetchone()[0]

    # 2. Page query with LIMIT/OFFSET
    offset = (page - 1) * page_size
    rows = self._conn.execute(
        "SELECT id, created_at, valid_until, password FROM shares "
        "WHERE valid_until IS NULL OR valid_until > datetime('now') "
        "ORDER BY created_at DESC "
        "LIMIT ? OFFSET ?",
        (page_size, offset)
    ).fetchall()

    result: list[dict] = []
    for id_, created_at, valid_until, password_hash in rows:
        result.append({
            "id": id_,
            "created_at": created_at,
            "valid_until": valid_until,
            "protected": password_hash is not None,
        })
    return result, total
```

**Design notes**:
- Two queries: one for count, one for data. A single query with `COUNT(*) OVER()`
  window function would avoid the second round-trip, but SQLite's `sqlite3` module
  doesn't expose window-function results cleanly through the cursor API. Two
  queries is simpler and the performance difference is negligible for expected
  share counts.
- `ORDER BY created_at DESC` preserved — newest shares first.
- Parameterized queries (`?` placeholders) — safe from SQL injection.

---

### 3.3 ShareService — [`backend/services/share_service.py`](backend/services/share_service.py)

**Current** (lines 271–273):
```python
def list_shares(self) -> list[dict]:
    return self.storage.list_active()
```

**New**:
```python
def list_shares(self, page_size: int = 50, page: int = 1) -> tuple[list[dict], int]:
    """Return (shares_for_page, total_active_count)."""
    return self.storage.list_active(page_size, page)
```

Pure pass-through — the service layer doesn't add business logic here, but
keeping it ensures the MCP tool doesn't bypass the service.

> **Note**: The API route (`app.py`) currently calls `storage.list_active()`
> directly rather than going through `share_service.list_shares()`. Fixing this
> inconsistency is out of scope for this pagination task. The API route will
> continue calling storage directly, while the MCP tool calls the service.
> Both paths get the same paginated signature.

---

### 3.4 API Route — [`backend/app.py`](backend/app.py)

**Current** (lines 360–394):

```python
@app.route("/api/admin/shares")
def list_shares():
    # ... auth check, Swagger docstring ...
    shares = storage.list_active()
    base = _base_url()
    for share in shares:
        share["url"] = f"{base}/v/{share['id']}"
    return jsonify({"shares": shares, "count": len(shares)})
```

**New**:

```python
@app.route("/api/admin/shares")
def list_shares():
    """List all active (non-expired) shares with pagination.
    ---
    tags: [Admin]
    security:
      - Bearer: []
    parameters:
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        description: Page number (1-based)
      - name: page_size
        in: query
        type: integer
        required: false
        default: 50
        description: Items per page (1–200)
    responses:
      200:
        description: Paginated list of active shares
        schema:
          type: object
          properties:
            shares: {type: array, items: {type: object}}
            page: {type: integer}
            page_size: {type: integer}
            total_pages: {type: integer}
            total_count: {type: integer}
      400: {description: Invalid pagination parameters}
      401: {description: Missing or invalid master password}
    """
    if not _check_master_auth():
        return jsonify({"error": "unauthorized"}), 401

    # Parse and validate pagination parameters
    try:
        page = int(request.args.get("page", "1"))
        page_size = int(request.args.get("page_size", "50"))
    except (ValueError, TypeError):
        return jsonify({"error": "invalid pagination parameters"}), 400

    if page < 1:
        return jsonify({"error": "page must be >= 1"}), 400
    if page_size < 1 or page_size > 200:
        return jsonify({"error": "page_size must be between 1 and 200"}), 400

    shares, total_count = storage.list_active(page_size, page)
    total_pages = max(1, math.ceil(total_count / page_size))

    base = _base_url()
    for share in shares:
        share["url"] = f"{base}/v/{share['id']}"

    return jsonify({
        "shares": shares,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total_count,
    })
```

**New import needed**: `import math` at the top of `app.py`.

**Validation summary**:

| Condition | HTTP Status | Error message |
|-----------|-------------|---------------|
| `page` not an integer | 400 | `invalid pagination parameters` |
| `page_size` not an integer | 400 | `invalid pagination parameters` |
| `page < 1` | 400 | `page must be >= 1` |
| `page_size < 1` or `page_size > 200` | 400 | `page_size must be between 1 and 200` |

**Edge case — page beyond range**: If `page > total_pages`, the SQL query returns
an empty list. The response still includes correct `total_count` and `total_pages`,
so clients can detect they've gone past the last page. No 404 — an empty page is
not an error.

---

### 3.5 MCP Tool — [`backend/mcp_server.py`](backend/mcp_server.py)

**Current** (lines 195–213):

```python
@mcp.tool()
async def list_shares() -> dict[str, Any]:
    shares = service.list_shares()
    share_list = [...]
    return {"shares": share_list, "count": len(share_list)}
```

**New**:

```python
@mcp.tool()
async def list_shares(
    page_size: int = 50,
    page: int = 1,
) -> dict[str, Any]:
    """List all active shares with pagination.

    Args:
        page_size: Items per page (1–200, default 50).
        page: Page number (1-based, default 1).

    Returns:
        Paginated share list with metadata.
    """
    shares, total_count = service.list_shares(page_size, page)
    total_pages = max(1, math.ceil(total_count / page_size))

    share_list: list[dict[str, Any]] = []
    for share in shares:
        share_list.append({
            "id": share["id"],
            "url": _share_url(share["id"]),
            "created_at": share["created_at"],
            "valid_until": share["valid_until"],
            "protected": share.get("protected", False),
        })

    return {
        "shares": share_list,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total_count,
    }
```

**New import needed**: `import math` at the top of `mcp_server.py`.

**Validation**: The MCP framework/FastMCP handles type coercion for tool
parameters. Since `page_size` and `page` are typed as `int`, FastMCP will
reject non-integer values before the function is called. We still need to
validate the range (≥1 for both, ≤200 for `page_size`) inside the function:

```python
if page < 1:
    raise ValueError("page must be >= 1")
if page_size < 1 or page_size > 200:
    raise ValueError("page_size must be between 1 and 200")
```

> Actually, FastMCP's type validation + Python's default values handle the
> common cases. Explicit range checks add defense-in-depth but may be
> unnecessary if FastMCP already validates. The MCP test suite will confirm.

---

## 4. Test Plan

### 4.1 HTTP API Tests — [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py)

**Existing tests to update** (7 tests in `TestAdminListShares`, lines 245–347):

All existing tests check the response structure. They need updating for the new
fields (`page`, `page_size`, `total_pages`, `total_count` instead of `count`).

| Test | Change needed |
|------|---------------|
| `test_returns_empty_list_when_no_shares` | Add assertion for `page=1`, `page_size=50`, `total_pages=1`, `total_count=0` |
| `test_returns_401_without_auth` | No change (401 before pagination logic) |
| `test_returns_401_with_wrong_password` | No change |
| `test_lists_public_and_protected_shares` | Add pagination metadata assertions |
| `test_each_share_has_required_fields` | Add pagination metadata assertions |
| `test_excludes_expired_shares` | Add pagination metadata assertions |
| *(implicit JSON content type test)* | Add pagination metadata assertions |

**New tests to add**:

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_default_pagination` | No query params → page=1, page_size=50 implied |
| 2 | `test_custom_page_size` | `?page_size=10` returns exactly 10 items |
| 3 | `test_second_page` | `?page=2&page_size=5` returns items 6–10 |
| 4 | `test_page_beyond_range_returns_empty` | `?page=999` returns empty shares but correct `total_count` |
| 5 | `test_page_zero_returns_400` | `?page=0` → 400 |
| 6 | `test_page_size_zero_returns_400` | `?page_size=0` → 400 |
| 7 | `test_page_size_exceeds_max_returns_400` | `?page_size=201` → 400 |
| 8 | `test_non_integer_page_returns_400` | `?page=abc` → 400 |
| 9 | `test_non_integer_page_size_returns_400` | `?page_size=abc` → 400 |
| 10 | `test_total_pages_correct` | Create 55 shares, `?page_size=10` → `total_pages=6` |
| 11 | `test_total_count_matches_actual` | `total_count` equals number of active shares |
| 12 | `test_large_page_size_returns_all` | `?page_size=200` with 150 shares → returns 150 |
| 13 | `test_order_preserved_desc` | Shares sorted by `created_at` descending |

**Test helpers**: Use the existing `make_document`/`write_document` factories
from [`backend/__tests__/helpers/fixtures.py`](backend/__tests__/helpers/fixtures.py).
For bulk creation, a loop calling `write_document` with controlled timestamps
(via `monkeypatch` on `datetime`) ensures deterministic ordering.

### 4.2 MCP Tests — [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py)

**Existing tests to update** (5 tests in `TestListShares`, lines 336–411):

Same pattern as HTTP tests — update response structure assertions.
Key change: `result["count"]` → `result["total_count"]`.

**New tests to add**:

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_default_pagination` | Called with no args → defaults applied |
| 2 | `test_custom_page_size` | `page_size=10` returns 10 items |
| 3 | `test_second_page` | `page=2` returns correct offset |
| 4 | `test_total_pages_correct` | Metadata math is correct |
| 5 | `test_page_beyond_range_returns_empty` | Past last page → empty shares |

---

## 5. Implementation Order

```
1. Storage ABC      ── change signature, return tuple
2. SQLite Storage   ── LIMIT/OFFSET + COUNT query
3. ShareService     ── pass-through with new params
4. API Route        ── query params, validation, new response
5. MCP Tool         ── optional params, new response
6. Update HTTP Tests ── fix existing + add 13 new
7. Update MCP Tests  ── fix existing + add 5 new
8. Update README     ── document pagination params
9. Update TASK_LOG   ── log the change
```

Each layer builds on the previous one, so this order minimizes broken
intermediate states. Tests are updated last because they need the final
response format to be settled.

---

## 6. Design Decisions & Rationale

1. **Offset-based, not cursor-based**: For an admin listing endpoint with
   expected share counts in the hundreds/low thousands, offset pagination is
   simple, well-understood, and sufficient. Cursor-based pagination adds
   complexity without meaningful benefit at this scale.

2. **`total_count` + `total_pages` in every response**: Enables UIs to render
   page navigation ("Page 3 of 12") without an extra request. The cost is one
   extra `COUNT(*)` query per request — negligible for SQLite on this dataset.

3. **Max `page_size` of 200**: Prevents accidental or malicious requests for
   thousands of items. 200 is generous enough for bulk operations while keeping
   response sizes reasonable.

4. **No `total_count` suppression**: Some APIs omit `total_count` for performance
   on huge tables. Not needed here — SQLite `COUNT(*)` on a single table with
   a simple `WHERE` clause is effectively instant.

5. **Empty page, not 404, when past last page**: An empty `shares` array with
   correct metadata is more useful than a 404 — clients can distinguish "no
   shares at all" (page 1, total_count=0) from "you've scrolled past the end"
   (page 5, total_count=12).

6. **`math.ceil` for `total_pages`**: Clearer than integer arithmetic tricks.
   `import math` is already available in the Python standard library.

7. **Two queries, not window function**: SQLite supports `COUNT(*) OVER()` but
   the `sqlite3` module doesn't expose window function results in a way that's
   easy to consume without iterating all rows. Two simple queries are more
   maintainable.

---

## 7. Affected Files Summary

| File | Change Type |
|------|-------------|
| [`backend/storage/abstract.py`](backend/storage/abstract.py) | Signature change: `list_active()` returns `tuple[list, int]` |
| [`backend/storage/sqlite.py`](backend/storage/sqlite.py) | Implementation: `LIMIT`/`OFFSET` + `COUNT` |
| [`backend/services/share_service.py`](backend/services/share_service.py) | Signature change: pass-through with new params |
| [`backend/app.py`](backend/app.py) | Query param parsing, validation, new response + `import math` |
| [`backend/mcp_server.py`](backend/mcp_server.py) | Optional params, new response + `import math` |
| [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) | Update 7 existing tests, add ~13 new tests |
| [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py) | Update 5 existing tests, add ~5 new tests |
| [`README.md`](README.md) | Document pagination query parameters |
| [`docs/TASK_LOG.md`](docs/TASK_LOG.md) | Log the change |
