# Plan: MCP Endpoint Fixes

Fixes for 4 prioritized issues found during MCP endpoint audit (2026-07-02).

---

## Fix F: `list_shares` Protected Field Always `False` (Bug)

### Root Cause

[`mcp_server.py:207`](backend/mcp_server.py:207) accesses `share.get("password")`, but [`sqlite.list_active():155`](backend/storage/sqlite.py:155) returns the key `"protected"`, not `"password"`. The `password` key does not exist in the dict, so `share.get("password")` always returns `None`, making `protected` always `False`.

### Changes

**1. Fix the key reference** — [`mcp_server.py:207`](backend/mcp_server.py:207):

```diff
-                "protected": share.get("password") is not None,
+                "protected": share.get("protected", False),
```

**2. Strengthen the existing test** — [`test_mcp_server.py:363-379`](backend/__tests__/test_mcp_server.py:363):

After asserting both share IDs appear in the listing, add assertions that verify the `protected` field value:

```python
# Find each share in result and verify protected field
by_id = {s["id"]: s for s in result["shares"]}
assert by_id[pub["id"]]["protected"] is False
assert by_id[prot["id"]]["protected"] is True
```

---

## Fix A: Duplicate `ShareService` Instances

### Root Cause

Three separate `ShareService` instances are created, but the module-level singleton at [`share_service.py:226`](backend/services/share_service.py:226) is never imported by consumers:

| File | Line | Code |
|------|------|------|
| [`mcp_server.py`](backend/mcp_server.py:36) | 36 | `service = ShareService(get_storage())` |
| [`app.py`](backend/app.py:65) | 65 | `share_service = ShareService(storage)` |
| [`share_service.py`](backend/services/share_service.py:226) | 226 | `share_service = ShareService(get_storage())` |

All share the same `SqliteStorage` singleton, so behavior is correct, but the architecture is misleading.

### Changes

**1.** [`mcp_server.py`](backend/mcp_server.py:19) — change import and remove local instance:

```diff
-from backend.services.share_service import ShareService
+from backend.services.share_service import share_service as service
```

Remove line 36:
```diff
-service = ShareService(get_storage())
```

The variable name `service` is used throughout the file (lines 43, 73, 113, 142, 168, 176, 197), so the `as service` alias preserves all downstream usage unchanged.

**2.** [`app.py`](backend/app.py:14,65) — change import and remove local instance:

```diff
-from backend.services.share_service import ShareService
+from backend.services.share_service import share_service
```

Remove line 65:
```diff
-share_service = ShareService(storage)
```

Also remove the now-unused `storage` variable at line 64 if no other consumer uses it (verify — `storage` is only used to construct `ShareService`; `get_storage()` is imported but may be needed elsewhere).

Remove lines 64:
```diff
-storage = get_storage()
```

And remove `from backend.storage import get_storage` at line 13 if it was only imported for the storage variable. Check [`app.py`](backend/app.py:13) — `get_storage` is imported at line 13. After the fix, if no other code path uses it, remove the import.

Actually, `app.py` may not even import `get_storage` directly — looking at lines 12-16, the imports are:

```python
from backend.config import config
from backend.storage import get_storage
from backend.services.share_service import ShareService
from backend.services.auth import extract_bearer_token, verify_master_password, verify_view_password
from backend.services.image_handler import save_uploaded_image
```

So `get_storage` is only used at line 64. Remove the import along with line 64.

---

## Fix E: Image Processing Order Differs Between MCP and Flask

### Root Cause

The MCP tool saves images to disk **before** creating the database record, while Flask creates the database record **before** saving images. If image saving succeeds but `create_share()` fails, the MCP path leaves orphaned image files.

Additionally, `rewrite_image_urls()` is called **twice** in the MCP path: once at [`mcp_server.py:111`](backend/mcp_server.py:111) and again inside [`share_service.create_share():118`](backend/services/share_service.py:118). The second call is a no-op (URLs already rewritten), but it's wasteful.

### Changes

Restructure [`mcp_server.py:54-130`](backend/mcp_server.py:54) `create_share` to standardize on the Flask order: validate → create DB → save images.

**Current flow:**
```
1. Generate doc_id (line 73)
2. Parse & decode images (lines 76-97)
3. Validate total image size (lines 99-104)
4. Save images to disk ← IMAGE SAVED (lines 107-109)
5. Rewrite URLs (line 111)
6. service.create_share() ← DB CREATED (line 113)
```

**New flow:**
```
1. Parse & validate base64 images (collect names + raw data URI strings, DON'T save)
2. Build filenames set
3. service.create_share() ← DB CREATED (generates doc_id internally, rewrites URLs)
4. Get doc_id from result
5. Save images to disk ← IMAGE SAVED
```

**Detailed edit for** [`mcp_server.py:54-130`](backend/mcp_server.py:54):

```python
@mcp.tool()
async def create_share(
    content: str,
    master_password: str,
    protected: bool = False,
    images: list[str] | None = None,
    ttl_hours: int | None = None,
) -> dict[str, Any]:
    """Create new share from tool arguments."""
    if not verify_master_password(master_password):
        return {"error": "Invalid master password"}

    # ── Image validation (parse names, decode, check sizes) ──────────
    filenames: set[str] = set()
    parsed_images: list[tuple[str, str]] = []  # (safe_name, original_img_data_str)

    if images:
        for i, img_data in enumerate(images):
            if img_data.startswith("data:"):
                header, _, b64_data = img_data[5:].partition(",")
                params = header.split(";")
                safe_name: str | None = None
                for param in params:
                    if param.startswith("filename="):
                        safe_name = os.path.basename(param[len("filename="):])
                        break
                if not safe_name:
                    safe_name = f"image_{i}.png"
            else:
                safe_name = f"image_{i}.png"
                b64_data = img_data

            # Validate base64
            try:
                raw = base64.b64decode(b64_data)
            except Exception:
                return {"error": f"invalid base64 data for image '{safe_name}'"}

            parsed_images.append((safe_name, img_data))
            filenames.add(safe_name)

        # Validate total image data size
        total_img_size = sum(len(base64.b64decode(d.split(",", 1)[1] if d.startswith("data:") else d)) for _, d in parsed_images)
        if total_img_size > config.max_size:
            return {
                "error": f"total image data exceeds max size of {config.max_size} bytes"
            }

    # ── Create share via service (DB record first) ───────────────────
    try:
        result = service.create_share(
            content=content,
            protected=protected,
            ttl_hours=ttl_hours,
            filenames=filenames if filenames else None,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    doc_id = result["id"]

    # ── Save images to disk (after DB record exists) ─────────────────
    for safe_name, img_data in parsed_images:
        save_base64_image(img_data, doc_id, safe_name)

    # ── Build MCP response ───────────────────────────────────────────
    response: dict[str, Any] = {
        "id": doc_id,
        "url": _share_url(doc_id),
        "valid_until": result["valid_until"],
    }
    if protected and result.get("password"):
        response["password"] = result["password"]

    return response
```

**Key changes from the edit:**

1. Removed pre-generated `doc_id` — `create_share()` generates it internally (line 73 removed)
2. Removed `rewrite_image_urls()` call at line 111 — `create_share()` does it internally
3. Moved `save_base64_image()` calls to AFTER `create_share()` returns
4. `doc_id` is now obtained from `result["id"]` (line 123 equivalent)
5. Added `try/except ValueError` around `service.create_share()` (also addresses Fix C)

---

## Fix C: Content Validation Duplication + Unhandled ValueError

### Root Cause

[`mcp_server.py:66-71`](backend/mcp_server.py:66) validates content (empty + max size), returning error dicts. [`share_service.create_share():103-105`](backend/services/share_service.py:103) also validates (empty only), raising `ValueError`. If the mcp_server validation were ever bypassed or removed, the `ValueError` would be unhandled.

### Changes

**1.** Remove the redundant validation block from [`mcp_server.py:66-71`](backend/mcp_server.py:66):

```diff
-    if not content or not content.strip():
-        return {"error": "Content cannot be empty"}
-
-    content_size = len(content.encode("utf-8"))
-    if content_size > config.max_size:
-        return {"error": f"content exceeds max size of {config.max_size} bytes"}
```

**2.** Wrap the `service.create_share()` call in `try/except ValueError` (already included in Fix E above).

**3.** Verify the max-size check: `share_service.create_share()` does **not** validate max size — only the MCP tool does. So the max-size validation should remain in `mcp_server.py` but be placed just before the `service.create_share()` call rather than at the top:

```python
# Validate max content size (service does not enforce this)
content_size = len(content.encode("utf-8"))
if content_size > config.max_size:
    return {"error": f"content exceeds max size of {config.max_size} bytes"}
```

Note: max-size validation is intentionally kept in the tool layer since the Flask API also enforces it separately. The service layer only validates business logic (non-empty content).

---

## Test Impact Summary

### Tests affected by Fix F

| Test | Impact |
|------|--------|
| `test_field_keys_match_expected_schema` | No change — key names unchanged |
| `test_lists_public_and_protected_shares` | STRENGTHENED — now asserts `protected` value |
| `test_share_has_url_field` | No change |
| `test_excludes_expired_shares` | No change |
| `test_returns_empty_list_when_no_shares` | No change |

### Tests affected by Fix A

| Test | Impact |
|------|--------|
| All MCP tests import from `backend.mcp_server` | No change — only internal variable changes |
| All Flask tests import `app` | No change — `share_service` module path unchanged |

### Tests affected by Fix E

| Test | Impact |
|------|--------|
| `test_public_share_returns_url_and_id` | May need update — `doc_id` no longer pre-generated |
| `test_protected_share_returns_password` | May need update — `doc_id` no longer pre-generated |
| `test_images_parameter_accepted` | May need update — image save deferred |
| `test_invalid_base64_image_returns_error` | No change — validation still occurs first |
| `test_image_data_too_large` | No change — validation still occurs first |
| `test_default_ttl_returns_valid_until` | No change |
| `test_custom_ttl_hours` | No change |
| `test_zero_ttl_hours_no_expiry` | No change |

### Tests affected by Fix C

| Test | Impact |
|------|--------|
| `test_empty_content_returns_error` | Now raised via `ValueError` → `try/except` instead of explicit return. Error message unchanged. |
| `test_whitespace_only_content_returns_error` | Same as above |
| `test_content_exceeds_max_size` | No change — max-size check stays in tool |

### Existing test break risk

The `test_public_share_returns_url_and_id` test at lines 52-63 creates a share and checks `result["id"]` and `result["url"]`. The response format remains identical — only the internal flow changes, so no test break expected.

---

## Implementation Order

1. **Fix F first** — one-line bug fix, highest impact
2. **Fix C** — remove validation dup, add error handling, restore max-size at right location
3. **Fix E** — restructure image processing order (depends on Fix C's try/except)
4. **Fix A** — singleton consolidation (cosmetic but important for maintainability)

---

## Files Modified

| File | Fixes |
|------|-------|
| [`backend/mcp_server.py`](backend/mcp_server.py) | F, C, E, A |
| [`backend/app.py`](backend/app.py) | A |
| [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py) | F (strengthen tests) |
