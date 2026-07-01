# Plan: Layer Separation Refactor

## 1. Goals

1. Eliminate code duplication between [`backend/app.py`](backend/app.py) and [`backend/mcp_server.py`](backend/mcp_server.py)
2. Introduce a central `ShareService` class that all entry points call
3. Centralize configuration into a single `config.py` module
4. Centralize authentication into a single `auth.py` module
5. Slim Flask routes and MCP tools to thin adapters (request parsing / response formatting only)
6. Zero behavioral changes — identical API responses, identical MCP tool results, identical storage behavior

## 2. Target File Structure

```
backend/
├── __init__.py                    # (unchanged)
├── asgi.py                        # (unchanged — import paths for mcp_app/flask_app updated)
├── config.py                      # NEW — Config dataclass
├── app.py                         # REWRITTEN — thin Flask routes (~100 lines)
├── mcp_server.py                  # REWRITTEN — thin MCP tools (~80 lines)
├── services/                      # NEW directory
│   ├── __init__.py                # NEW
│   ├── share_service.py           # NEW — ShareService class (all domain logic)
│   ├── auth.py                    # NEW — authentication helpers
│   └── image_handler.py           # NEW — image saving/URL rewriting
├── storage/                       # (unchanged structure)
│   ├── __init__.py                # MODIFIED — accept config for data_dir
│   ├── abstract.py                # (unchanged)
│   └── sqlite.py                  # MINOR — use injected data_dir
├── static/                        # (unchanged)
├── requirements.txt               # (unchanged — no new dependencies)
└── __tests__/                     # (unchanged structure)
    ├── conftest.py                # MODIFIED — import config, patch accordingly
    ├── test_health.py             # (unchanged)
    ├── test_upload.py             # MINOR — import path updates if any
    ├── test_view.py               # (unchanged)
    ├── test_mcp_server.py         # MINOR — import path updates
    └── helpers/
        ├── __init__.py            # (unchanged)
        ├── fixtures.py            # (unchanged)
        └── setup.py               # (unchanged)
```

## 3. Implementation Steps (in strict order)

### Step 1: Create [`backend/config.py`](backend/config.py) — Centralized Configuration

**Purpose**: Single source of truth for all environment-variable-based configuration. Replaces the scattered `os.environ.get()` calls in `app.py` and `storage/sqlite.py`.

**Content**:
```python
"""Centralized configuration for mdshare — single source of truth."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    """Application configuration, loaded from environment variables.

    All values have sensible defaults matching the current behavior.
    """

    master_password: str = field(
        default_factory=lambda: os.environ.get("MDSHARE_MASTER_PASSWORD", "changeme")
    )
    data_dir: str = field(
        default_factory=lambda: os.environ.get("MDSHARE_DATA_DIR", "/app/data")
    )
    max_size: int = field(
        default_factory=lambda: int(
            os.environ.get("MDSHARE_MAX_SIZE", "16777216")
        )
    )
    base_url: str | None = field(
        default_factory=lambda: os.environ.get("MDSHARE_BASE_URL") or None
    )

    @property
    def images_dir(self) -> str:
        """Absolute path to the images storage directory."""
        return os.path.join(self.data_dir, "images")

    @property
    def db_path(self) -> str:
        """Absolute path to the SQLite database file."""
        return os.path.join(self.data_dir, "mdshare.db")


# Singleton instance — created once at import time (preserves current
# module‑level init pattern that conftest.py depends on).
config = Config()
```

**Design decisions**:
- `frozen=True` prevents accidental mutation
- `@property` for derived paths keeps them consistent with `data_dir`
- Singleton `config` instance preserves the existing import-time initialization pattern (critical for `conftest.py` which sets `MDSHARE_DATA_DIR` before imports)

### Step 2: Create [`backend/services/__init__.py`](backend/services/__init__.py) — Package Marker

**Content**: Empty file or docstring only.

### Step 3: Create [`backend/services/auth.py`](backend/services/auth.py) — Authentication Helpers

**Purpose**: Single authentication module used by both Flask routes and MCP tools. Replaces the duplicate auth logic in `app.py._check_master_auth()` and `mcp_server.py`'s inline password comparison.

**Content**:
```python
"""Authentication helpers — single module used by all entry points."""
from __future__ import annotations

import secrets

import bcrypt

from backend.config import config


def verify_master_password(provided: str) -> bool:
    """Timing-safe comparison of the provided master password.

    Used by Flask (Bearer token) and MCP (tool parameter).
    """
    return secrets.compare_digest(provided, config.master_password)


def verify_view_password(provided: str | None, stored_hash: str | None) -> bool:
    """Check a view password against a stored bcrypt hash.

    Parameters
    ----------
    provided:
        The password the user supplied (may be None for no password).
    stored_hash:
        The bcrypt hash from the database (None means share is public).

    Returns
    -------
    ``True`` if the share is public OR the password matches.
    """
    if stored_hash is None:
        return True
    if provided is None:
        return False
    return bcrypt.checkpw(provided.encode(), stored_hash.encode())
```

### Step 4: Create [`backend/services/image_handler.py`](backend/services/image_handler.py) — Image Handling

**Purpose**: Extracts all image-related logic currently duplicated between `app.py.share()` and `mcp_server.py.create_share()`. This includes saving image files, decoding base64, validating filenames, and rewriting Markdown image references.

**Content**:
```python
"""Image handling — saving and URL rewriting for share images."""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from backend.config import config


# Maximum size for a single image file (10 MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def save_uploaded_image(doc_id: str, filename: str, data: bytes) -> str:
    """Save an uploaded image to the filesystem.

    Parameters
    ----------
    doc_id:
        Share identifier (used as directory name).
    filename:
        Original filename (sanitized with os.path.basename).
    data:
        Raw image bytes.

    Returns
    -------
    The sanitized filename as saved.
    """
    safe_name = os.path.basename(filename)
    img_dir = os.path.join(config.images_dir, doc_id)
    os.makedirs(img_dir, exist_ok=True)
    filepath = os.path.join(img_dir, safe_name)
    with open(filepath, "wb") as f:
        f.write(data)
    return safe_name


def save_base64_image(doc_id: str, filename: str, b64_data: str) -> str:
    """Decode and save a base64-encoded image.

    Parameters
    ----------
    doc_id:
        Share identifier.
    filename:
        Original filename.
    b64_data:
        Base64-encoded image data (data URL or raw base64).

    Returns
    -------
    The sanitized filename as saved.

    Raises
    ------
    ValueError
        If the base64 data is invalid or the decoded size exceeds MAX_IMAGE_SIZE.
    """
    safe_name = os.path.basename(filename)

    # Handle data URL format: data:image/png;base64,<data>
    if "," in b64_data:
        _, b64_data = b64_data.split(",", 1)

    try:
        data = base64.b64decode(b64_data)
    except Exception:
        raise ValueError(f"Invalid base64 data for image '{safe_name}'")

    if len(data) > MAX_IMAGE_SIZE:
        raise ValueError(
            f"Image '{safe_name}' exceeds maximum size of {MAX_IMAGE_SIZE} bytes"
        )

    return save_uploaded_image(doc_id, safe_name, data)


def rewrite_image_urls(content: str, doc_id: str, filenames: set[str]) -> str:
    """Rewrite local image references to viewer URLs.

    Converts ``![alt](image.png)`` to ``![alt](/v/<doc_id>/img/image.png)``
    for all filenames that were actually uploaded.

    Parameters
    ----------
    content:
        Markdown content to process.
    doc_id:
        Share identifier.
    filenames:
        Set of uploaded image filenames.

    Returns
    -------
    Markdown content with rewritten image URLs.
    """
    def _replace(match):
        alt = match.group(1)
        fname = match.group(2)
        if fname in filenames:
            return f"![{alt}](/v/{doc_id}/img/{fname})"
        return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^\)]+)\)", _replace, content)
```

### Step 5: Create [`backend/services/share_service.py`](backend/services/share_service.py) — Domain Logic

**Purpose**: The central `ShareService` class that encapsulates ALL business logic. Both Flask routes and MCP tools become thin adapters that call this class.

**Content**:
```python
"""ShareService — central domain logic for mdshare."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone, timedelta

from backend.config import config
from backend.storage import StorageBackend
from backend.services.auth import verify_master_password, verify_view_password
from backend.services.image_handler import (
    save_uploaded_image,
    save_base64_image,
    rewrite_image_urls,
)

# ---------------------------------------------------------------------------
# ID / password generation
# ---------------------------------------------------------------------------

_CHARS = string.ascii_letters + string.digits


def generate_id(length: int = 12) -> str:
    """Generate a random share identifier."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def generate_password(length: int = 12) -> str:
    """Generate a random view password."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))


# ---------------------------------------------------------------------------
# TTL helpers
# ---------------------------------------------------------------------------

def compute_valid_until(ttl_hours: int | None = None) -> str | None:
    """Compute ISO 8601 UTC expiry timestamp.

    Parameters
    ----------
    ttl_hours:
        Number of hours until expiry. 0 means no expiry.
        ``None`` uses a default of 24 hours.

    Returns
    -------
    ISO 8601 string or ``None`` for no expiry.
    """
    if ttl_hours is None:
        ttl_hours = 24  # default: 24 hours
    if ttl_hours <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()


# ---------------------------------------------------------------------------
# ShareService
# ---------------------------------------------------------------------------

class ShareService:
    """Central service for all share-related business logic.

    Both the Flask API routes and MCP tools delegate to this class,
    ensuring a single implementation of every operation.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # -- create ----------------------------------------------------------

    def create(
        self,
        content: str,
        *,
        protected: bool = False,
        uploaded_images: dict[str, bytes] | None = None,
        base64_images: dict[str, str] | None = None,
        ttl_hours: int | None = None,
    ) -> dict:
        """Create a new share.

        Parameters
        ----------
        content:
            Markdown content (must be non-empty after stripping).
        protected:
            Whether the share requires a view password.
        uploaded_images:
            Dict mapping filename → raw bytes.
        base64_images:
            Dict mapping filename → base64-encoded data.
        ttl_hours:
            Hours until expiry. ``None`` = 24h default, 0 = never.

        Returns
        -------
        Dict with keys: ``id``, ``url``, and optionally ``password``.

        Raises
        ------
        ValueError
            If content is empty or exceeds max size.
        """
        # Validate content
        stripped = content.strip()
        if not stripped:
            raise ValueError("Content must not be empty")
        if len(stripped.encode("utf-8")) > config.max_size:
            raise ValueError(
                f"Content exceeds maximum size of {config.max_size} bytes"
            )

        doc_id = generate_id()
        view_password: str | None = None

        # Password protection
        if protected:
            import bcrypt
            view_password = generate_password()
            pw_hash = bcrypt.hashpw(
                view_password.encode(), bcrypt.gensalt()
            ).decode()
        else:
            pw_hash = None

        # Save uploaded images (raw bytes)
        filenames: set[str] = set()
        if uploaded_images:
            for fname, data in uploaded_images.items():
                saved = save_uploaded_image(doc_id, fname, data)
                filenames.add(saved)

        # Save base64-encoded images
        if base64_images:
            for fname, b64_data in base64_images.items():
                saved = save_base64_image(doc_id, fname, b64_data)
                filenames.add(saved)

        # Rewrite image URLs
        final_content = rewrite_image_urls(stripped, doc_id, filenames)

        # Compute expiry
        valid_until = compute_valid_until(ttl_hours)

        # Persist
        doc = {"content": final_content, "valid_until": valid_until}
        if pw_hash is not None:
            doc["password"] = pw_hash
        self._storage.create(doc_id, doc)

        # Build response
        result: dict = {
            "id": doc_id,
            "url": f"/v/{doc_id}/raw",
        }
        if view_password is not None:
            result["password"] = view_password
        return result

    # -- get -------------------------------------------------------------

    def get(self, doc_id: str, *, view_password: str | None = None) -> dict:
        """Retrieve raw share content.

        Parameters
        ----------
        doc_id:
            Share identifier.
        view_password:
            Password for protected shares.

        Returns
        -------
        Dict with ``content`` key.

        Raises
        ------
        LookupError
            If share not found or password incorrect.
        """
        doc = self._storage.get(doc_id)
        if doc is None:
            raise LookupError(f"Share '{doc_id}' not found")

        if not verify_view_password(view_password, doc.get("password")):
            raise LookupError("Incorrect password")

        return {"content": doc["content"]}

    # -- get_info --------------------------------------------------------

    def get_info(self, doc_id: str) -> dict:
        """Retrieve share metadata without content.

        Parameters
        ----------
        doc_id:
            Share identifier.

        Returns
        -------
        Dict with keys ``exists``, ``protected``, ``url``, ``created_at``,
        and optionally ``valid_until``.
        """
        doc = self._storage.get(doc_id)
        if doc is None:
            return {"exists": False}

        return {
            "exists": True,
            "protected": doc.get("password") is not None,
            "url": f"/v/{doc_id}/raw",
            "created_at": doc.get("created_at"),
            "valid_until": doc.get("valid_until"),
        }

    # -- health_check ----------------------------------------------------

    def health_check(self) -> dict:
        """Check service health.

        Returns
        -------
        ``{"status": "ok"}`` if storage is reachable,
        ``{"status": "degraded"}`` otherwise.
        """
        try:
            self._storage.exists("__health_check__")
            return {"status": "ok"}
        except Exception:
            return {"status": "degraded"}

    # -- list_shares -----------------------------------------------------

    def list_shares(self) -> list[dict]:
        """List all active (non-expired) shares.

        Returns
        -------
        List of dicts with ``id``, ``protected``, ``url``, ``created_at``,
        and optionally ``valid_until``.
        """
        shares = self._storage.list_active()
        return [
            {
                "id": s["id"],
                "protected": s.get("password") is not None,
                "url": f"/v/{s['id']}/raw",
                "created_at": s.get("created_at"),
                "valid_until": s.get("valid_until"),
            }
            for s in shares
        ]

    # -- get_storage (for tests) -----------------------------------------

    @property
    def storage(self) -> StorageBackend:
        """Direct access to storage backend (for test cleanup only)."""
        return self._storage


# ---------------------------------------------------------------------------
# Singleton instance — created once at import time.
# Both app.py and mcp_server.py import this instance.
# ---------------------------------------------------------------------------

from backend.storage import get_storage as _get_storage

share_service = ShareService(_get_storage())
```

**Design decisions**:
- `ShareService` takes `StorageBackend` as constructor argument — enables testing with mock storage
- Singleton `share_service` at module level preserves the current import-time initialization pattern
- `generate_id()`, `generate_password()`, `compute_valid_until()` are module-level functions (stateless, no storage dependency) — they can be called directly or through the service
- Custom exceptions (`ValueError`, `LookupError`) provide consistent error handling across both entry points

### Step 6: Modify [`backend/storage/sqlite.py`](backend/storage/sqlite.py) — Use Injected Data Dir

**Change**: Replace the module-level `DATA_DIR` variable that reads `os.environ` directly with a value taken from `config`.

**Current code** (lines 18-22):
```python
DATA_DIR = os.environ.get("MDSHARE_DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "mdshare.db")
```

**Replace with**:
```python
from backend.config import config

DATA_DIR = config.data_dir
DB_PATH = config.db_path
```

**Rationale**: The `config` singleton reads `MDSHARE_DATA_DIR` from the environment, so behavior is identical. The benefit is that `conftest.py` sets `os.environ["MDSHARE_DATA_DIR"]` before imports, and the `Config` dataclass picks it up — same flow, centralized.

### Step 7: Rewrite [`backend/app.py`](backend/app.py) — Thin Flask Routes

**Goal**: Reduce from ~340 lines to ~100 lines. Every route becomes a thin adapter: parse request → call `share_service` → format HTTP response.

**Content**:
```python
"""mdshare — minimal Markdown sharing API (Flask routes)."""
from __future__ import annotations

import os
import mimetypes
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, abort, render_template_string

from backend.config import config
from backend.services.auth import verify_master_password
from backend.services.share_service import share_service, compute_valid_until

app = Flask(__name__, static_folder="static", static_url_path="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Determine the base URL for generating absolute share links."""
    if config.base_url:
        return config.base_url.rstrip("/")
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"


def _image_dir(doc_id: str) -> Path:
    """Filesystem path to the image directory for a share."""
    return Path(config.images_dir) / doc_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/api/health")
def health():
    result = share_service.health_check()
    status_code = 200 if result["status"] == "ok" else 503
    return jsonify(result), status_code


@app.route("/themes.css")
def themes_css():
    return send_from_directory("static", "themes.css", mimetype="text/css")


@app.route("/v/<doc_id>")
def view_page(doc_id: str):
    info = share_service.get_info(doc_id)
    if not info["exists"]:
        abort(404)
    return render_template_string(VIEWER_HTML, doc_id=doc_id)


@app.route("/v/<doc_id>/raw")
def view_raw(doc_id: str):
    view_pw = request.args.get("pw")
    try:
        result = share_service.get(doc_id, view_password=view_pw)
    except LookupError as exc:
        if "not found" in str(exc):
            abort(404)
        return jsonify({"error": str(exc)}), 403
    return result["content"], 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/v/<doc_id>/img/<filename>")
def view_image(doc_id: str, filename: str):
    safe_name = os.path.basename(filename)
    img_dir = _image_dir(doc_id)
    if not (img_dir / safe_name).is_file():
        abort(404)
    mime, _ = mimetypes.guess_type(safe_name)
    return send_from_directory(str(img_dir), safe_name, mimetype=mime or "application/octet-stream")


@app.route("/api/share", methods=["PUT"])
def share():
    # Auth
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or not verify_master_password(
        auth_header.removeprefix("Bearer ")
    ):
        return jsonify({"error": "Unauthorized"}), 401

    # Parse form
    content = request.form.get("content", "")
    protected = request.form.get("protected", "no").lower() in ("yes", "true", "1")
    ttl_str = request.form.get("ttl_hours")
    ttl_hours = int(ttl_str) if ttl_str else None

    # Collect uploaded images
    uploaded_images: dict[str, bytes] = {}
    for key in request.files:
        f = request.files[key]
        if f.filename:
            try:
                uploaded_images[f.filename] = f.read()
            except Exception:
                return jsonify({"error": f"Failed to read image '{f.filename}'"}), 400

    # Delegate to service
    try:
        result = share_service.create(
            content,
            protected=protected,
            uploaded_images=uploaded_images or None,
            ttl_hours=ttl_hours,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Format response
    base = _base_url()
    response = {
        "url": f"{base}{result['url']}",
    }
    if "password" in result:
        response["password"] = result["password"]
    return jsonify(response), 201


@app.route("/api/admin/shares")
def list_shares():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or not verify_master_password(
        auth_header.removeprefix("Bearer ")
    ):
        return jsonify({"error": "Unauthorized"}), 401

    shares = share_service.list_shares()
    base = _base_url()
    for s in shares:
        s["url"] = f"{base}{s['url']}"
    return jsonify({"shares": shares})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Request entity too large"}), 413


@app.errorhandler(500)
def server_error(_error):
    return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Viewer HTML (inline template — unchanged from current)
# ---------------------------------------------------------------------------

VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mdshare — {{ doc_id }}</title>
<link rel="stylesheet" href="/themes.css">
<!-- marked.js -->
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<!-- highlight.js -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/styles/github.min.css">
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/highlight.min.js"></script>
<!-- Mermaid -->
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<!-- KaTeX -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex/dist/contrib/auto-render.min.js"></script>
<!-- DOMPurify -->
<script src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"></script>
</head>
<body>
<div class="container markdown-body" id="content"></div>
<div class="password-overlay" id="password-overlay">
  <div class="password-box">
    <h2>Password Required</h2>
    <p>This share is password protected.</p>
    <input type="password" id="password-input" placeholder="Enter password...">
    <button id="password-submit">Submit</button>
    <p class="password-error" id="password-error"></p>
  </div>
</div>
<script>
const docId = "{{ doc_id }}";
const overlay = document.getElementById("password-overlay");
const contentDiv = document.getElementById("content");
const pwInput = document.getElementById("password-input");
const pwSubmit = document.getElementById("password-submit");
const pwError = document.getElementById("password-error");

function loadContent(password) {
  let url = "/v/" + docId + "/raw";
  if (password) url += "?pw=" + encodeURIComponent(password);
  fetch(url)
    .then(r => {
      if (r.status === 403) {
        overlay.style.display = "flex";
        pwError.textContent = "Incorrect password.";
        return null;
      }
      if (!r.ok) throw new Error("Failed to load");
      return r.text();
    })
    .then(text => {
      if (text === null) return;
      overlay.style.display = "none";
      const html = marked.parse(text);
      contentDiv.innerHTML = DOMPurify.sanitize(html);
      document.querySelectorAll("pre code").forEach(b => hljs.highlightElement(b));
      mermaid.run({ querySelector: ".mermaid" });
      renderMathInElement(contentDiv, { delimiters: [
        {left: "$$", right: "$$", display: true},
        {left: "$", right: "$", display: false}
      ]});
    })
    .catch(e => {
      contentDiv.innerHTML = "<p class='error'>Failed to load content.</p>";
    });
}

pwSubmit.addEventListener("click", () => loadContent(pwInput.value));
pwInput.addEventListener("keydown", e => { if (e.key === "Enter") loadContent(pwInput.value); });
loadContent(null);
</script>
</body>
</html>"""
```

**Key points**:
- The `_base_url()` helper stays in `app.py` — it's a presentation-layer concern (reads Flask request headers)
- `VIEWER_HTML` template stays in `app.py` — it's a Flask-specific presentation concern
- All routes are thin: parse → call service → format response
- Error handling maps service exceptions to HTTP status codes

### Step 8: Rewrite [`backend/mcp_server.py`](backend/mcp_server.py) — Thin MCP Tools

**Goal**: Reduce from ~270 lines to ~80 lines. Every tool becomes a thin adapter: parse arguments → call `share_service` → format tool result.

**Content**:
```python
"""MCP Streamable HTTP server for mdshare — thin tool definitions."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.config import config
from backend.services.auth import verify_master_password
from backend.services.share_service import share_service

mcp = FastMCP("mdshare")

mcp_app = mcp.streamable_http_app()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="create_share",
    description="Create a new Markdown share. Pass master_password for auth. "
                "Set protected=true for password-protected shares. "
                "Optionally provide images as a dict of filename → base64 data. "
                "Set ttl_hours to control expiry (0 = never, default 24h).",
)
async def create_share(
    content: str,
    master_password: str,
    protected: bool = False,
    images: dict[str, str] | None = None,
    ttl_hours: int | None = None,
) -> dict[str, Any]:
    if not verify_master_password(master_password):
        return {"error": "Unauthorized — invalid master password"}

    try:
        result = share_service.create(
            content,
            protected=protected,
            base64_images=images,
            ttl_hours=ttl_hours,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    result["url"] = f"{config.base_url or 'http://localhost:5000'}{result['url']}"
    return result


@mcp.tool(
    name="get_share",
    description="Retrieve raw Markdown content of a share. "
                "Provide view_password for protected shares.",
)
async def get_share(
    doc_id: str,
    view_password: str | None = None,
) -> dict[str, Any]:
    if not doc_id.strip():
        return {"error": "Share ID must not be empty"}

    try:
        result = share_service.get(doc_id, view_password=view_password)
    except LookupError as exc:
        msg = str(exc)
        if "not found" in msg:
            return {"error": f"Share '{doc_id}' not found"}
        return {"error": msg}

    return result


@mcp.tool(
    name="get_share_info",
    description="Get metadata about a share without retrieving its content.",
)
async def get_share_info(doc_id: str) -> dict[str, Any]:
    if not doc_id.strip():
        return {"error": "Share ID must not be empty"}

    info = share_service.get_info(doc_id)
    if info.get("exists"):
        info["url"] = f"{config.base_url or 'http://localhost:5000'}{info['url']}"
    return info


@mcp.tool(
    name="health_check",
    description="Verify that the mdshare service is operational.",
)
async def health_check() -> dict[str, Any]:
    return share_service.health_check()


@mcp.tool(
    name="list_shares",
    description="List all active (non-expired) shares. Requires master password.",
)
async def list_shares(master_password: str) -> dict[str, Any]:
    if not verify_master_password(master_password):
        return {"error": "Unauthorized — invalid master password"}

    shares = share_service.list_shares()
    base = config.base_url or "http://localhost:5000"
    for s in shares:
        s["url"] = f"{base}{s['url']}"
    return {"shares": shares}
```

**Key changes from current**:
- No more `from backend.app import _generate_id, _generate_password, _rewrite_image_urls, compute_valid_until, _base_url`
- No direct storage access — all through `share_service`
- No inline image handling — delegated to service
- URL generation uses `config.base_url` instead of calling Flask helper
- ~190 lines removed, all business logic deduplicated

### Step 9: Modify [`backend/storage/__init__.py`](backend/storage/__init__.py) — Accept Config

**Minimal change**: Ensure the factory uses `config.data_dir` implicitly (since `sqlite.py` now imports `config`).

**Current content remains valid** — no changes needed unless you want to make the factory accept an explicit data_dir parameter. The singleton nature of `config` handles this.

If desired, add an optional parameter:
```python
def get_storage(data_dir: str | None = None) -> StorageBackend:
    ...
```
But this is optional — the current implementation works because `sqlite.py` imports `config`.

**Decision**: Keep `__init__.py` unchanged for minimal diff. The `sqlite.py` change in Step 6 is sufficient.

### Step 10: Modify [`backend/__tests__/conftest.py`](backend/__tests__/conftest.py) — Update Imports

**Change needed**: The `reset_storage` fixture accesses `storage._conn` directly. With the service layer, tests can still access the storage backend directly via `share_service.storage` for cleanup. However, since conftest.py sets env vars and imports `backend.app` at module level, we must ensure the import chain works.

**Current import pattern** (lines 15-21):
```python
os.environ["MDSHARE_MASTER_PASSWORD"] = "test-master-password"
_test_data_dir = tempfile.mkdtemp(prefix="mdshare_test_")
os.environ["MDSHARE_DATA_DIR"] = _test_data_dir

from backend.app import app as flask_app  # noqa: E402
from backend.storage import get_storage  # noqa: E402
```

**New pattern**:
```python
os.environ["MDSHARE_MASTER_PASSWORD"] = "test-master-password"
_test_data_dir = tempfile.mkdtemp(prefix="mdshare_test_")
os.environ["MDSHARE_DATA_DIR"] = _test_data_dir

# The config singleton reads env vars at import time — same behavior
from backend.config import config  # noqa: E402
from backend.app import app as flask_app  # noqa: E402
from backend.storage import get_storage  # noqa: E402
from backend.services.share_service import share_service  # noqa: E402
```

**`reset_storage` fixture update** — use `share_service.storage` instead of raw `get_storage()`:
```python
@pytest.fixture(autouse=True)
def reset_storage():
    """Wipe SQLite storage and image directories between tests."""
    storage = share_service.storage
    conn = storage._conn
    conn.execute("DELETE FROM shares")
    conn.commit()
    images_root = os.path.join(config.images_dir)
    if os.path.isdir(images_root):
        shutil.rmtree(images_root)
    yield
    conn.execute("DELETE FROM shares")
    conn.commit()
    if os.path.isdir(images_root):
        shutil.rmtree(images_root)
```

**`test_data_dir` fixture** — close storage through service:
```python
@pytest.fixture(autouse=True, scope="session")
def test_data_dir():
    yield _test_data_dir
    share_service.storage.close()
    shutil.rmtree(_test_data_dir, ignore_errors=True)
```

### Step 11: Update Test Files — Import Path Changes

**Files to check**:
- [`backend/__tests__/test_mcp_server.py`](backend/__tests__/test_mcp_server.py) — imports `from backend.mcp_server import create_share, get_share, get_share_info, health_check, list_shares` (unchanged — tools are still in mcp_server.py)
- [`backend/__tests__/test_upload.py`](backend/__tests__/test_upload.py) — uses `from backend.app import app` (unchanged)
- [`backend/__tests__/helpers/fixtures.py`](backend/__tests__/helpers/fixtures.py) — imports `from backend.storage import get_storage` (unchanged)

**Expected**: No test file changes needed beyond `conftest.py`, because:
- MCP tests call tool functions directly (same imports)
- Flask tests use the test client (same interface)
- Fixture helpers use storage directly (same interface)

However, verify by running the test suite after refactoring.

### Step 12: No Changes Needed — These Files Stay Identical

| File | Reason |
|------|--------|
| [`backend/asgi.py`](backend/asgi.py) | Imports `from backend.app import app` and `from backend.mcp_server import mcp_app` — both still exist in same locations |
| [`backend/storage/abstract.py`](backend/storage/abstract.py) | Well-designed ABC, no changes needed |
| [`backend/static/viewer.html`](backend/static/viewer.html) | Static asset, untouched |
| [`backend/static/themes.css`](backend/static/themes.css) | Static asset, untouched |
| [`backend/requirements.txt`](backend/requirements.txt) | No new dependencies |
| [`Dockerfile`](Dockerfile) | Copies `backend/` recursively — new `services/` dir is included automatically |
| [`docker-compose.yml`](docker-compose.yml) | No configuration changes |
| [`Makefile`](Makefile) | Test commands unchanged |
| [`pytest.ini`](pytest.ini) | Unchanged |
| [`tests/integration/`](tests/integration/) | Tests HTTP API — internal refactoring is transparent |

## 4. Validation Checklist

After all steps are implemented, verify:

- [ ] `make test` passes all backend tests
- [ ] `make test-integration` passes all integration tests (requires DEPLOYMENT_HOST)
- [ ] `PUT /api/share` returns identical response format
- [ ] `GET /v/<id>/raw` returns identical content
- [ ] `GET /v/<id>/raw?pw=<pw>` auth behavior unchanged
- [ ] `GET /api/admin/shares` returns identical format
- [ ] `GET /api/health` returns identical format
- [ ] All 5 MCP tools return identical result shapes
- [ ] Expired shares are still lazily deleted on access
- [ ] Image upload and serving works identically

## 5. Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Import order issues in conftest.py | Medium | Preserve exact env-var-before-import pattern; test immediately after Step 10 |
| VIEWER_HTML template regression | Low | Copy-paste exact template from current app.py; verify with diff |
| MCP tool response format mismatch | Low | Compare test assertions before/after; test_mcp_server.py validates exact shapes |
| Storage singleton initialization order | Low | `config.py` is imported first by `sqlite.py`; conftest sets env vars before any imports |
| Docker build breakage | Very Low | No new dependencies, no path changes in Dockerfile |

## 6. Rollback Strategy

If the refactor causes issues:
1. `git stash` the changes
2. Re-run tests to confirm original code works
3. Fix and re-apply

The refactor is entirely additive at the file level (new `services/` and `config.py`) with rewrites of `app.py` and `mcp_server.py`. Git history preserves the originals.

## 7. Summary of File Changes

| File | Action | Lines Before | Lines After |
|------|--------|-------------|-------------|
| [`backend/config.py`](backend/config.py) | **New** | — | ~45 |
| [`backend/services/__init__.py`](backend/services/__init__.py) | **New** | — | ~1 |
| [`backend/services/auth.py`](backend/services/auth.py) | **New** | — | ~35 |
| [`backend/services/image_handler.py`](backend/services/image_handler.py) | **New** | — | ~90 |
| [`backend/services/share_service.py`](backend/services/share_service.py) | **New** | — | ~220 |
| [`backend/storage/sqlite.py`](backend/storage/sqlite.py) | **Modify** (3 lines) | 159 | 159 |
| [`backend/app.py`](backend/app.py) | **Rewrite** | 339 | ~100 |
| [`backend/mcp_server.py`](backend/mcp_server.py) | **Rewrite** | 270 | ~80 |
| [`backend/__tests__/conftest.py`](backend/__tests__/conftest.py) | **Modify** | 69 | ~72 |
| **Total** | | **~837** | **~800** |

Net: ~380 lines of business logic extracted from Flask/MCP files into the service layer, with total line count roughly the same (new files add structure, rewrites are much shorter).
