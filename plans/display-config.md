# Display Configuration Plan

## Overview

Allow global display defaults (set via a YAML config file) and per-share overrides
(at upload time) for the viewer rendered page. The viewer fetches the merged config
and applies it dynamically via CSS custom properties.

---

## Display Options (8 total)

| Option             | Type    | Default                        | Description                                          |
|--------------------|---------|--------------------------------|------------------------------------------------------|
| `font_family`      | string  | `system-ui, -apple-system, ...`| CSS `font-family` for body text                      |
| `font_size`        | string  | `16px`                         | Base font size (any valid CSS value)                 |
| `line_height`      | string  | `1.6`                          | Line height multiplier                               |
| `max_width`        | string  | `900px`                        | Max content width                                    |
| `theme`            | enum    | `auto`                         | `light`, `dark`, or `auto` (OS preference)            |
| `code_font_size`   | string  | `14px`                         | Font size for code blocks                            |
| `code_line_numbers`| bool    | `false`                        | Show line numbers in code blocks                     |
| `custom_css`       | string  | `""`                           | Raw CSS injected into viewer (trusted — master-pw-gated) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Global defaults                                         │
│  ${MDSHARE_DATA_DIR}/display.yaml  (or env var override) │
└───────────────────┬──────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────┐
│  PUT /api/share  (optional display_config JSON field)     │
│  ─────────────────────────────────────────────────────  │
│  Parses display_config, validates, stores in SQLite       │
│  `shares.display_config` column (TEXT, JSON, nullable)    │
└───────────────────┬──────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────┐
│  GET /v/<id>/config   (new endpoint)                     │
│  ─────────────────────────────────────────────────────  │
│  Deep-merges global defaults ← per-share overrides       │
│  Returns JSON with all 8 keys (always complete object)    │
└───────────────────┬──────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────┐
│  viewer.html  (JS enhancement)                           │
│  ─────────────────────────────────────────────────────  │
│  1. Extracts share ID from pathname                      │
│  2. Fetches GET /v/<id>/config                           │
│  3. Applies config as CSS custom properties on <html>     │
│  4. Sets data-color-mode for theme                       │
│  5. Injects custom_css <style> if non-empty              │
│  6. Loads highlightjs-line-numbers if enabled            │
└──────────────────────────────────────────────────────────┘
```

---

## Implementation Steps

### 1. Global config file — `backend/display_config.py` (new file)

Create a `DisplayConfig` class that:
- Loads `${MDSHARE_DISPLAY_CONFIG}` env var (falls back to `${MDSHARE_DATA_DIR}/display.yaml`)
- If file missing, returns hardcoded sensible defaults
- Parses YAML (add `pyyaml` to `backend/requirements.txt`)
- Validates: `theme` must be `light|dark|auto`, `code_line_numbers` must be bool, etc.
- Exposes a singleton `display_config` for import by other layers
- Provides `get_defaults() → dict` returning all 8 keys with their values

**File**: [`backend/display_config.py`](backend/display_config.py)

### 2. Storage schema migration — `backend/storage/sqlite.py`

- Add `ALTER TABLE shares ADD COLUMN display_config TEXT` migration in `_init_schema()`
- Update `create()` to accept and store optional `display_config` (JSON string)
- Update `get()` to return `display_config` in the result dict (parsed back to dict or None)
- Private helper: `_serialize_config(d: dict | None) → str | None`, `_deserialize_config(s: str | None) → dict | None`

### 3. Service layer — `backend/services/share_service.py`

**`create_share()` changes:**
- New optional parameter: `display_config: dict | None = None`
- Validate keys against known options, strip unknown keys
- Validate value types (theme must be valid enum, code_line_numbers must be bool, etc.)
- Store validated config in the `doc` dict passed to storage

**New method `get_display_config(share_id: str) → dict`:**
- Fetch share from storage
- If share doesn't exist → return global defaults only
- If share has `display_config` → deep-merge global defaults with per-share overrides
- Always returns all 8 keys (fills in missing ones from global defaults)

### 4. API layer — `backend/app.py`

**`PUT /api/share` changes:**
- Accept new optional form field: `display_config` (JSON string)
- Parse and pass to `share_service.create_share()`
- On invalid JSON → return 400 with descriptive error
- On invalid config values → return 400 with descriptive error

**New route `GET /v/<doc_id>/config`:**
```python
@app.route("/v/<doc_id>/config")
def view_display_config(doc_id: str):
    """Return merged display configuration for a share."""
    config_dict = share_service.get_display_config(doc_id)
    return jsonify(config_dict)
```
- No authentication needed (same as viewing the page)
- Returns 200 with full config object even if share doesn't exist (returns global defaults)
  — The viewer can handle the "not found" case separately

### 5. MCP server — `backend/mcp_server.py`

**`put_share` tool changes:**
- Accept optional `display_config` argument (dict)
- Pass through to `share_service.create_share()`

**New `get_share_config` tool (optional):**
- Takes `share_id`, returns merged display config
- Useful for MCP clients that want to preview the rendering

### 6. Viewer — `backend/static/viewer.html`

**Script additions (after existing `loadContent` logic):**

```
1. Extract share ID from window.location.pathname
2. Fetch /v/<id>/config
3. Apply config:
   - Set CSS custom properties on document.documentElement.style:
     --md-font-family, --md-font-size, --md-line-height,
     --md-max-width, --md-code-font-size
   - Set data-color-mode attribute for theme
   - If custom_css non-empty: create <style> element, append to <head>
   - If code_line_numbers true: load highlightjs-line-numbers CDN script,
     call hljs.initLineNumbersOnLoad() after rendering
```

**CSS changes in viewer.html `<style>` block:**
- Replace hardcoded `body { max-width: 900px; ... }` with CSS custom property references
- Add `--md-*` properties with fallback defaults in `:root`

### 7. Tests — `backend/__tests__/`

**New file: `backend/__tests__/test_display_config.py`**
- Test `DisplayConfig` singleton loads from YAML
- Test `DisplayConfig` falls back to defaults when file missing
- Test `DisplayConfig` validates theme values
- Test `share_service.create_share()` with valid/invalid display_config
- Test `share_service.get_display_config()` deep-merges correctly
- Test `GET /v/<id>/config` returns 200 with full config
- Test `PUT /api/share` with display_config JSON in form data
- Test `PUT /api/share` rejects invalid display_config values

**Update existing tests:**
- `test_upload.py`: Add cases for upload with display_config
- `helpers/fixtures.py`: Update `make_document()` to accept optional `display_config`

### 8. Dependencies

- Add `pyyaml` to [`backend/requirements.txt`](backend/requirements.txt) for YAML config parsing

### 9. Documentation

**`README.md` — New "Display Customization" section** (after the existing Configuration section):

Must cover:
1. **Global defaults**: How to create `${MDSHARE_DATA_DIR}/display.yaml`, all 8 options
   with their defaults, and the `MDSHARE_DISPLAY_CONFIG` env var override
2. **Per-share overrides**: The `display_config` JSON form field in `PUT /api/share`,
   with curl examples showing single and multiple overrides
3. **How merging works**: Global defaults are the base; per-share fields override
   individual keys; unspecified keys fall back to global defaults
4. **Viewer behavior**: How the viewer fetches `/v/<id>/config` and applies CSS
   custom properties — non-technical explanation
5. **Example recipes**: Common customizations
   - Dark mode for all shares: set `theme: dark` in `display.yaml`
   - Custom font per share: `-F 'display_config={"font_family":"Georgia, serif"}'`
   - Code line numbers: `-F 'display_config={"code_line_numbers":true}'`
   - Full custom branding: set font + max-width + custom_css

**`AGENTS.md`**:
- Add `backend/display_config.py` to the directory structure
- Add DisplaConfig to the technology stack table
- Add `display_config` column to the SQLite schema docs
- Add `GET /v/<id>/config` to the route table

**`docs/TASK_LOG.md`**: Log the change chronologically

**New file `backend/display_defaults.yaml`**: Example config file shipped in the repo
as a reference (not loaded at runtime — the runtime file is at `${MDSHARE_DATA_DIR}/display.yaml`)

---

## Example `display.yaml`

```yaml
# mdshare global display defaults
# Place at ${MDSHARE_DATA_DIR}/display.yaml or set MDSHARE_DISPLAY_CONFIG env var

font_family: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
font_size: "16px"
line_height: "1.6"
max_width: "900px"
theme: "auto"           # light | dark | auto
code_font_size: "14px"
code_line_numbers: false
custom_css: ""
```

## Example Upload with Overrides

```bash
# Override just the theme and font size for this share
curl -X PUT http://localhost:8080/api/share \
  -H "Authorization: Bearer <master>" \
  -F "content=# Dark Mode Doc" \
  -F 'display_config={"theme":"dark","font_size":"18px"}'
```

---

## Security Considerations

- **custom_css**: Injected directly as a `<style>` tag in the viewer. This is intentional
  — the upload endpoint is gated behind the master password, so the operator
  (or anyone they share the master password with) is trusted. CSS injection
  cannot access the SQLite DB or server filesystem; it only affects the
  viewer's browser rendering.
- **Validation**: All config values are validated server-side. Unknown keys are
  silently stripped. Invalid values (e.g., `theme: "neon"`) return 400.
- **No XSS vector**: The config is applied as CSS custom properties, not as
  innerHTML. custom_css goes into a `<style>` element, not an inline style
  attribute, but even so, CSS alone (without `expression()` which is dead)
  cannot execute JavaScript in modern browsers.
