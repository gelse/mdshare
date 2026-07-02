"""mdshare — minimal Markdown sharing API.

Thin Flask route layer — delegates business logic to ShareService,
auth helpers, and image handlers.
"""

import os

from flask import Flask, request, jsonify, send_from_directory, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.config import config
from backend.storage import get_storage
from backend.services.share_service import share_service
from backend.services.auth import extract_bearer_token, verify_master_password, verify_view_password
from backend.services.image_handler import save_uploaded_image
from flasgger import Swagger

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)  # we serve static files explicitly

app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1,
)

# ---------------------------------------------------------------------------
# Swagger / OpenAPI documentation
# ---------------------------------------------------------------------------

swagger_config = {
    "headers": [],
    "specs": [{"endpoint": "apispec", "route": "/apispec.json"}],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs/",
}

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "mdshare API",
        "description": "Minimal, self-hosted Markdown sharing service.",
        "version": "1.0.0",
    },
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Master password as Bearer token. Format: `Bearer <your-master-password>`",
        }
    },
}

Swagger(app, config=swagger_config, template=swagger_template)

# ---------------------------------------------------------------------------
# Storage & services
# ---------------------------------------------------------------------------

storage = get_storage()

# ---------------------------------------------------------------------------
# Request-context auth helpers
# ---------------------------------------------------------------------------


def _check_master_auth() -> bool:
    """Verify the ``Authorization: Bearer`` header in the current request."""
    auth = request.headers.get("Authorization", "")
    token = extract_bearer_token(auth)
    return verify_master_password(token)


def _check_view_auth(doc: dict) -> bool:
    """Return ``True`` if the viewer is authorized to see *doc*.

    Public documents (no password hash) are always authorized.
    Protected documents require ``?pw=`` with the correct password.
    """
    pw_hash = doc.get("password")
    if not pw_hash:
        return True  # public share
    given = request.args.get("pw", "")
    return verify_view_password(given or None, pw_hash)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Return the base URL of this service (no trailing slash).

    :envvar:`MDSHARE_BASE_URL` takes precedence when set.
    Otherwise auto-detect from the request (respecting ``X-Forwarded-*``
    headers via :class:`~werkzeug.middleware.proxy_fix.ProxyFix`).
    """
    if config.base_url:
        return config.base_url
    return request.host_url.rstrip("/")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/api/health")
def health():
    """Health check endpoint.
    ---
    tags: [Health]
    responses:
      200:
        description: Service is healthy
        schema:
          type: object
          properties:
            status: {type: string, example: ok}
    """
    return jsonify({"status": "ok"})


@app.route("/themes.css")
def themes():
    """Serve the CSS theme file."""
    return send_from_directory("static", "themes.css")


@app.route("/v/<doc_id>")
def view_page(doc_id: str):
    """Serve the viewer HTML page for a share."""
    if not storage.exists(doc_id):
        abort(404)
    return send_from_directory("static", "viewer.html")


@app.route("/v/<doc_id>/raw")
def view_raw(doc_id: str):
    """Return raw Markdown content for a share.
    ---
    tags: [View]
    parameters:
      - {name: doc_id, in: path, type: string, required: true, description: Share ID}
      - {name: pw, in: query, type: string, required: false, description: Password for protected shares}
    responses:
      200:
        description: Raw Markdown content
        schema: {type: string}
      401: {description: Password required or incorrect}
      404: {description: Share not found}
    """
    doc = storage.get(doc_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404

    # Differentiate between missing password and wrong password
    pw_hash = doc.get("password")
    if pw_hash:
        given = request.args.get("pw", "")
        if not given:
            return jsonify({"error": "password required"}), 401
        if not verify_view_password(given, pw_hash):
            return jsonify({"error": "incorrect password"}), 401

    return doc["content"], 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/v/<doc_id>/img/<filename>")
def view_image(doc_id: str, filename: str):
    """Serve an uploaded image for a share."""
    img_dir = os.path.join(config.data_dir, "images", doc_id)
    return send_from_directory(img_dir, filename)


@app.route("/api/share", methods=["PUT"])
def share():
    """Upload Markdown content with optional images.

    **Auth**: ``Authorization: Bearer <master-password>`` header.

    **Request body** (``multipart/form-data``):

    ==============  ======  ==========================================
    Field           Type    Description
    ==============  ======  ==========================================
    ``content``     string  Raw markdown text (required).
    ``protected``   string  ``"yes"`` / ``"no"`` (default ``"no"``).
    ``ttl``         integer Time-to-live in hours (default 168 = 7 days).
    *any file*      binary  Images referenced in the markdown.
    ==============  ======  ==========================================

    **Response** (201)::

        {
            "url": "https://host/v/<random-id>",
            "password": "<cleartext>",  // only when protected=true
            "valid_until": "2026-07-08T05:50:42+00:00"  // ISO 8601 UTC
        }

    =======  ==================================
    Status   Meaning
    =======  ==================================
    201      Share created.
    400      Markdown content is missing/empty.
    401      Invalid or missing master password.
    413      Content exceeds size limit.
    =======  ==================================
    ---
    tags: [Share]
    security:
      - Bearer: []
    consumes:
      - multipart/form-data
    parameters:
      - {name: content, in: formData, type: string, required: true, description: Raw Markdown text}
      - {name: protected, in: formData, type: string, enum: [yes, no], default: no, description: Password-protect the share}
      - {name: ttl, in: formData, type: integer, default: 168, description: Time-to-live in hours}
      - {name: file, in: formData, type: file, description: Image files referenced in the Markdown}
    responses:
      201:
        description: Share created
        schema:
          type: object
          properties:
            url: {type: string, example: https://example.com/v/abc123}
            password: {type: string}
            valid_until: {type: string, format: date-time}
      400: {description: Missing content or invalid TTL}
      401: {description: Invalid or missing master password}
      413: {description: Content exceeds size limit}
    """
    if not _check_master_auth():
        return jsonify({"error": "unauthorized"}), 401

    # Parse multipart form fields
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"error": "markdown content is required"}), 400

    if len(content.encode("utf-8")) > config.max_size:
        return jsonify({"error": "content too large"}), 413

    protected = request.form.get("protected", "no").lower() in (
        "yes", "true", "1"
    )

    # Parse TTL (time-to-live) in hours
    raw_ttl = request.form.get("ttl", "")
    if raw_ttl.strip():
        try:
            ttl_hours = int(raw_ttl)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid ttl — must be a positive integer"}), 400
        if ttl_hours < 0:
            return jsonify({"error": "invalid ttl — must be a non-negative integer"}), 400
    else:
        ttl_hours = None  # use default in compute_valid_until

    # Collect uploaded filenames for URL rewriting
    filenames: set[str] = set()
    for key in request.files:
        file = request.files[key]
        if file and file.filename:
            filenames.add(file.filename)

    # Create share via service layer (generates ID, rewrites URLs, stores)
    result = share_service.create_share(
        content=content,
        protected=protected,
        ttl_hours=ttl_hours,
        filenames=filenames,
    )

    doc_id = result["id"]
    view_password = result.get("password")

    # Persist uploaded image files
    for key in request.files:
        file = request.files[key]
        if file and file.filename:
            save_uploaded_image(file, doc_id, file.filename)

    return (
        jsonify(
            {
                "url": f"{_base_url()}/v/{doc_id}",
                "password": view_password,
                "valid_until": result.get("valid_until"),
            }
        ),
        201,
    )


@app.route("/api/admin/shares")
def list_shares():
    """List all active (non-expired) shares.
    ---
    tags: [Admin]
    security:
      - Bearer: []
    responses:
      200:
        description: List of active shares
        schema:
          type: object
          properties:
            shares:
              type: array
              items:
                type: object
                properties:
                  id: {type: string}
                  url: {type: string}
                  created_at: {type: string}
                  valid_until: {type: string}
                  protected: {type: boolean}
            count: {type: integer}
      401: {description: Missing or invalid master password}
    """
    if not _check_master_auth():
        return jsonify({"error": "unauthorized"}), 401

    shares = storage.list_active()
    base = _base_url()
    for share in shares:
        share["url"] = f"{base}/v/{share['id']}"

    return jsonify({"shares": shares, "count": len(shares)})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def _not_found(_error):
    return jsonify({"error": "not found"}), 404


@app.errorhandler(413)
def _too_large(_error):
    return jsonify({"error": "content too large"}), 413


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
