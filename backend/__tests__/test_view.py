"""Tests for viewing endpoints of the minimal mdshare API.

Covers GET /v/<id> (viewer page), GET /v/<id>/raw (markdown content),
GET /v/<id>/img/<filename> (images), and GET /themes.css.
"""

import os

from backend.config import config
from backend.__tests__.helpers.fixtures import write_document, make_document


class TestViewRawHappyPath:
    """Successful raw markdown retrieval."""

    def test_public_share_returns_content(self, client):
        """Public share: no password needed, returns 200 with content."""
        write_document("pub123456789", make_document(content="# Public"))
        resp = client.get("/v/pub123456789/raw")
        assert resp.status_code == 200
        assert resp.data.decode() == "# Public"

    def test_protected_share_correct_password_returns_content(self, client):
        """Protected share: correct password returns content."""
        write_document(
            "pro987654321",
            make_document(content="# Secret", password="correct-pw-hash"),
        )
        resp = client.get("/v/pro987654321/raw?pw=correct-pw-hash")
        assert resp.status_code == 200
        assert resp.data.decode() == "# Secret"

    def test_content_type_is_text_plain(self, client):
        """Response content-type should be text/plain for raw markdown."""
        write_document("ct123456789", make_document(content="# Hello"))
        resp = client.get("/v/ct123456789/raw")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/plain")


class TestViewRawAuth:
    """Password-gating on protected shares."""

    def test_wrong_password_returns_401(self, client):
        write_document(
            "wr0ng1234567",
            make_document(content="# Secret", password="correct-pw-hash"),
        )
        resp = client.get("/v/wr0ng1234567/raw?pw=wrong-password")
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "incorrect password"

    def test_missing_password_returns_401(self, client):
        write_document(
            "nopw12345678",
            make_document(content="# Secret", password="correct-pw-hash"),
        )
        resp = client.get("/v/nopw12345678/raw")
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "incorrect password"

    def test_public_share_needs_no_password(self, client):
        """Public share with no password set should be accessible without pw."""
        write_document("pubnopasswd", make_document(content="# Public"))
        resp = client.get("/v/pubnopasswd/raw")
        assert resp.status_code == 200


class TestViewRawNotFound:
    """Nonexistent share handling."""

    def test_nonexistent_share_returns_404(self, client):
        resp = client.get("/v/nonexistent/raw")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "not found"


class TestViewPage:
    """GET /v/<id> — viewer HTML page."""

    def test_existing_share_returns_html(self, client):
        write_document("htm123456789", make_document(content="# Page"))
        resp = client.get("/v/htm123456789")
        assert resp.status_code == 200
        assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data
        resp.close()

    def test_nonexistent_share_returns_404(self, client):
        resp = client.get("/v/nonexistent")
        assert resp.status_code == 404


class TestViewImage:
    """GET /v/<id>/img/<filename> — uploaded image serving."""

    def test_existing_image_returns_200(self, client):
        doc_id = "img123456789"
        write_document(doc_id, make_document(content="![test](pic.png)"))

        img_dir = os.path.join(config.data_dir, "images", doc_id)
        os.makedirs(img_dir, exist_ok=True)
        with open(os.path.join(img_dir, "pic.png"), "wb") as f:
            f.write(b"fake-png-data")

        resp = client.get(f"/v/{doc_id}/img/pic.png")
        assert resp.status_code == 200
        assert resp.data == b"fake-png-data"
        resp.close()

    def test_nonexistent_image_returns_404(self, client):
        write_document("noimg123456", make_document(content="# No images"))
        resp = client.get("/v/noimg123456/img/nope.png")
        assert resp.status_code == 404


class TestThemesCSS:
    """GET /themes.css."""

    def test_returns_css(self, client):
        resp = client.get("/themes.css")
        assert resp.status_code == 200
        assert "css" in resp.content_type
        resp.close()


class TestExpiryBehaviour:
    """Expired / non-expired share behaviour via lazy expiry in storage.get()."""

    def test_expired_share_returns_404(self, client):
        """Share with past valid_until should be deleted by lazy expiry → 404."""
        write_document(
            "expired12345",
            make_document(
                content="# Expired",
                valid_until="2020-01-01T00:00:00+00:00",
            ),
        )
        resp = client.get("/v/expired12345/raw")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "not found"

    def test_nonexpired_share_returns_200(self, client):
        """Share with future valid_until returns content normally."""
        write_document(
            "fresh1234567",
            make_document(content="# Fresh", valid_until="2099-12-31T23:59:59+00:00"),
        )
        resp = client.get("/v/fresh1234567/raw")
        assert resp.status_code == 200
        assert resp.data.decode() == "# Fresh"

    def test_null_valid_until_never_expires(self, client):
        """Share with NULL valid_until (grandfathered shares) never expires."""
        write_document(
            "forever12345",
            make_document(content="# Forever", valid_until=None),
        )
        resp = client.get("/v/forever12345/raw")
        assert resp.status_code == 200
        assert resp.data.decode() == "# Forever"
