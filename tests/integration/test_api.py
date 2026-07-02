"""Integration tests for an externally-deployed mdshare instance.

These tests exercise a **running** mdshare instance over HTTP by connecting
to the URL provided in the ``DEPLOYMENT_HOST`` environment variable.  Each
test is self-contained \u2014 it uploads its own data and asserts against the
response.  No internal Python imports from ``backend/`` are used.

Prerequisites
-------------
* ``DEPLOYMENT_HOST`` must be set (e.g. ``http://192.168.1.100:5000``).
  See ``conftest.py`` for details.
* ``DEPLOYMENT_MASTER_PASSWORD`` is optional; defaults to
  ``test-integration-master-pw``.
"""

from __future__ import annotations

import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

MASTER_PW = os.environ.get("DEPLOYMENT_MASTER_PASSWORD", "test-integration-master-pw")
AUTH_HEADER = {"Authorization": f"Bearer {MASTER_PW}"}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _upload(
    base_url: str,
    *,
    content: str = "# Hello Integration",
    protected: str = "no",
    auth: dict | None = None,
    files: dict | None = None,
) -> httpx.Response:
    """Convenience wrapper for ``PUT /api/share``."""
    if auth is None:
        auth = AUTH_HEADER
    data: dict = {"content": content, "protected": protected}
    if files:
        # httpx merges data + files into multipart
        return httpx.put(
            f"{base_url}/api/share",
            data=data,
            files=files,
            headers=auth,
        )
    return httpx.put(
        f"{base_url}/api/share",
        data=data,
        headers=auth,
    )


# ===================================================================
# Test classes
# ===================================================================


@pytest.mark.integration
class TestHealthEndpoint:
    """``GET /api/health`` — always returns 200 with ``{"status": "ok"}``."""

    def test_health_returns_ok(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/api/health")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")
        assert resp.json() == {"status": "ok"}


@pytest.mark.integration
class TestVersionEndpoint:
    """``GET /api/version`` — public system info."""

    def test_version_endpoint(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/api/version")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)


@pytest.mark.integration
class TestSwaggerDocs:
    """``GET /api/docs/`` and ``GET /apispec.json`` — Swagger UI and OpenAPI spec."""

    def test_swagger_ui_returns_200(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/api/docs/")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/html")
        assert "swagger" in resp.text.lower()

    def test_apispec_json_returns_200(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/apispec.json")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")
        data = resp.json()
        assert data["swagger"] == "2.0"
        assert data["info"]["title"] == "mdshare API"


@pytest.mark.integration
class TestThemesCSS:
    """``GET /themes.css`` — serves the theme stylesheet."""

    def test_returns_css_content_type(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/themes.css")
        assert resp.status_code == 200
        assert "css" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Upload — happy path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUploadHappyPath:
    """Successful ``PUT /api/share`` scenarios."""

    def test_public_share_returns_url_only(self, integration_base_url: str) -> None:
        """``protected=no`` → 201, ``url`` present, no ``password``."""
        resp = _upload(integration_base_url, content="# Public", protected="no")
        assert resp.status_code == 201
        data = resp.json()
        assert "url" in data
        assert data["url"].startswith(f"{integration_base_url}/v/")
        assert data.get("password") is None

    def test_protected_share_returns_url_and_password(
        self, integration_base_url: str,
    ) -> None:
        """``protected=yes`` → 201, ``url`` + ``password`` both present."""
        resp = _upload(integration_base_url, content="# Secret", protected="yes")
        assert resp.status_code == 201
        data = resp.json()
        assert "url" in data
        assert "password" in data
        assert len(data["password"]) >= 4  # sanity: not empty/trivial


# ---------------------------------------------------------------------------
# Upload — auth & validation errors
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUploadAuthErrors:
    """Authentication and input-validation error responses."""

    def test_no_auth_returns_401(self, integration_base_url: str) -> None:
        resp = _upload(integration_base_url, auth={})
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_wrong_token_returns_401(self, integration_base_url: str) -> None:
        resp = _upload(
            integration_base_url,
            auth={"Authorization": "Bearer wrong-password"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_empty_content_returns_400(self, integration_base_url: str) -> None:
        resp = _upload(integration_base_url, content="")
        assert resp.status_code == 400
        assert resp.json()["error"] == "markdown content is required"


# ---------------------------------------------------------------------------
# View — raw markdown retrieval
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestViewRaw:
    """``GET /v/<id>/raw`` — raw markdown content delivery."""

    def test_public_share_returns_content(self, integration_base_url: str) -> None:
        """Public share can be fetched without a password."""
        upload_resp = _upload(integration_base_url, content="**bold**")
        doc_id = upload_resp.json()["url"].rstrip("/").split("/")[-1]

        raw = httpx.get(f"{integration_base_url}/v/{doc_id}/raw")
        assert raw.status_code == 200
        assert raw.text == "**bold**"

    def test_protected_correct_password(self, integration_base_url: str) -> None:
        """Protected share with correct ``?pw=`` returns content."""
        upload_resp = _upload(
            integration_base_url, content="# Protected", protected="yes",
        )
        data = upload_resp.json()
        doc_id = data["url"].rstrip("/").split("/")[-1]
        password = data["password"]

        raw = httpx.get(
            f"{integration_base_url}/v/{doc_id}/raw",
            params={"pw": password},
        )
        assert raw.status_code == 200
        assert raw.text == "# Protected"

    def test_protected_no_password_returns_401(
        self, integration_base_url: str,
    ) -> None:
        """Protected share without ``?pw=`` → 401."""
        upload_resp = _upload(
            integration_base_url, content="# No PW", protected="yes",
        )
        doc_id = upload_resp.json()["url"].rstrip("/").split("/")[-1]

        raw = httpx.get(f"{integration_base_url}/v/{doc_id}/raw")
        assert raw.status_code == 401
        assert raw.json()["error"] == "password required"

    def test_protected_wrong_password_returns_401(
        self, integration_base_url: str,
    ) -> None:
        """Protected share with wrong ``?pw=`` → 401."""
        upload_resp = _upload(
            integration_base_url, content="# Wrong PW", protected="yes",
        )
        doc_id = upload_resp.json()["url"].rstrip("/").split("/")[-1]

        raw = httpx.get(
            f"{integration_base_url}/v/{doc_id}/raw",
            params={"pw": "definitely-wrong"},
        )
        assert raw.status_code == 401
        assert raw.json()["error"] == "incorrect password"

    def test_nonexistent_share_returns_404(self, integration_base_url: str) -> None:
        """Random ID that doesn't exist → 404."""
        resp = httpx.get(f"{integration_base_url}/v/nonexistent99/raw")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not found"


# ---------------------------------------------------------------------------
# View — viewer HTML page
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestViewerPage:
    """``GET /v/<id>`` — serves the static viewer HTML."""

    def test_valid_share_returns_html(self, integration_base_url: str) -> None:
        upload_resp = _upload(integration_base_url, content="# Viewer")
        doc_id = upload_resp.json()["url"].rstrip("/").split("/")[-1]

        page = httpx.get(f"{integration_base_url}/v/{doc_id}")
        assert page.status_code == 200
        assert "html" in page.headers.get("content-type", "").lower()

    def test_nonexistent_share_returns_404(self, integration_base_url: str) -> None:
        resp = httpx.get(f"{integration_base_url}/v/nope12345678")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestImages:
    """Image upload and retrieval via ``GET /v/<id>/img/<filename>``."""

    def test_upload_and_retrieve_image(self, integration_base_url: str) -> None:
        """Upload markdown referencing an image, then fetch the image."""
        resp = httpx.put(
            f"{integration_base_url}/api/share",
            data={"content": "![pic](test.png)", "protected": "no"},
            files={"test.png": ("test.png", b"fake-png-bytes", "image/png")},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        doc_id = resp.json()["url"].rstrip("/").split("/")[-1]

        # Verify the image is served back
        img = httpx.get(f"{integration_base_url}/v/{doc_id}/img/test.png")
        assert img.status_code == 200
        assert img.content == b"fake-png-bytes"

    def test_nonexistent_image_returns_404(
        self, integration_base_url: str,
    ) -> None:
        """Requesting an image that was never uploaded → 404."""
        upload_resp = _upload(integration_base_url, content="# No images here")
        doc_id = upload_resp.json()["url"].rstrip("/").split("/")[-1]

        resp = httpx.get(f"{integration_base_url}/v/{doc_id}/img/ghost.png")
        assert resp.status_code == 404
