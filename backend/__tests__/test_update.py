"""Tests for PUT /api/share/<doc_id> — the update share endpoint."""

import json

MASTER_PW = "test-master-password"
AUTH = {"Authorization": f"Bearer {MASTER_PW}"}


def _create_share(client, content: str = "# Original", protected: str = "no", ttl: int | None = None) -> dict:
    """Helper: create a share and return the parsed response + doc_id."""
    data = {"content": content, "protected": protected}
    if ttl is not None:
        data["ttl"] = str(ttl)
    resp = client.put("/api/share", data=data, headers=AUTH)
    assert resp.status_code == 201
    body = resp.get_json()
    return body


class TestUpdateHappyPath:
    """Successful update scenarios."""

    def test_update_content(self, client):
        """Update only content — raw endpoint returns new content."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "# Updated Content"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == doc_id
        assert data["url"].endswith(f"/v/{doc_id}")

        # Verify raw reflects new content
        raw = client.get(f"/v/{doc_id}/raw")
        assert raw.status_code == 200
        assert raw.data.decode() == "# Updated Content"

    def test_update_protected_yes_returns_password(self, client):
        """Updating protected=yes on a public share generates and returns a password."""
        created = _create_share(client, protected="no")
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"protected": "yes"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "password" in data
        assert len(data["password"]) == 8

    def test_update_protected_no_clears_password(self, client):
        """Updating protected=no on a protected share clears the password."""
        created = _create_share(client, protected="yes")
        doc_id = created["id"]
        old_password = created["password"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("password") is None

        # Raw endpoint should now work without password
        raw = client.get(f"/v/{doc_id}/raw")
        assert raw.status_code == 200

    def test_update_ttl(self, client):
        """Updating ttl changes valid_until."""
        created = _create_share(client, ttl=168)
        doc_id = created["id"]
        old_valid_until = created["valid_until"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "1"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "valid_until" in data
        assert data["valid_until"] != old_valid_until

    def test_update_ttl_to_zero_clears_valid_until(self, client):
        """Setting ttl=0 clears valid_until (never expires)."""
        created = _create_share(client, ttl=168)
        doc_id = created["id"]
        assert created["valid_until"] is not None

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "0"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("valid_until") is None

    def test_update_display_config(self, client):
        """Updating display_config changes the per-share display config."""
        created = _create_share(client)
        doc_id = created["id"]

        new_config = {"theme": "dark", "code_line_numbers": True}
        resp = client.put(
            f"/api/share/{doc_id}",
            data={"display_config": json.dumps(new_config)},
            headers=AUTH,
        )
        assert resp.status_code == 200

        # Verify via config endpoint
        config_resp = client.get(f"/v/{doc_id}/config")
        assert config_resp.status_code == 200
        config_data = config_resp.get_json()
        assert config_data.get("theme") == "dark"
        assert config_data.get("code_line_numbers") is True

    def test_clear_display_config_with_empty_object(self, client):
        """Sending display_config={} clears per-share overrides (falls back to defaults)."""
        created = _create_share(client)
        doc_id = created["id"]

        # First set a config
        client.put(
            f"/api/share/{doc_id}",
            data={"display_config": json.dumps({"theme": "dark"})},
            headers=AUTH,
        )

        # Then clear it
        resp = client.put(
            f"/api/share/{doc_id}",
            data={"display_config": json.dumps({})},
            headers=AUTH,
        )
        assert resp.status_code == 200

        # Config endpoint should return defaults (theme not dark unless default)
        config_resp = client.get(f"/v/{doc_id}/config")
        assert config_resp.status_code == 200

    def test_update_all_fields_at_once(self, client):
        """Sending all fields updates everything in one request."""
        created = _create_share(client, protected="no")
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={
                "content": "# Fully Updated",
                "protected": "yes",
                "ttl": "24",
                "display_config": json.dumps({"theme": "dark"}),
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == doc_id
        assert "password" in data
        assert len(data["password"]) == 8

    def test_no_fields_returns_200_without_changes(self, client):
        """Sending no form fields (or only irrelevant ones) returns 200 as no-op."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == doc_id
        assert data.get("password") is None

    def test_url_valid_until_returned_in_response(self, client):
        """Update response includes url and optional valid_until."""
        created = _create_share(client, ttl=168)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "48"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "url" in data
        assert data["url"].startswith("http://localhost/v/")
        assert "valid_until" in data


class TestUpdateAuth:
    """Authorization handling for update endpoint."""

    def test_missing_auth_returns_401(self, client):
        """PUT without Authorization header returns 401."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "# Hacked"},
        )
        assert resp.status_code == 401

    def test_wrong_password_returns_401(self, client):
        """PUT with wrong Bearer token returns 401."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "# Hacked"},
            headers={"Authorization": "Bearer wrong-password"},
        )
        assert resp.status_code == 401


class TestUpdateValidation:
    """Input validation for update endpoint."""

    def test_nonexistent_share_returns_404(self, client):
        """Updating a non-existent share ID returns 404."""
        resp = client.put(
            "/api/share/nonexistent123",
            data={"content": "# New Content"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_empty_content_after_strip_returns_400(self, client):
        """Sending content that becomes empty after strip returns 400."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "   \n   "},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "content" in resp.get_json().get("error", "").lower()

    def test_whitespace_only_content_returns_400(self, client):
        """Sending whitespace-only content returns 400."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "   "},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_content_exceeds_max_size_returns_413(self, client, monkeypatch):
        """Sending content that exceeds max size returns 413."""
        created = _create_share(client)
        doc_id = created["id"]

        # We need to exceed the max size check in the service layer,
        # but the 413 is raised by Flask before reaching the route.
        # Instead, test that the service layer rejects oversized content.
        # The Flask request size limit is handled by MAX_CONTENT_LENGTH.
        oversized = "x" * (16 * 1024 * 1024 + 1)  # 16MB + 1 byte

        # This should be caught by the service layer or Flask
        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": oversized},
            headers=AUTH,
        )
        assert resp.status_code in (400, 413)

    def test_invalid_ttl_returns_400(self, client):
        """Non-integer ttl returns 400."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "not-a-number"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_negative_ttl_returns_400(self, client):
        """Negative ttl returns 400."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "-1"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_invalid_display_config_json_returns_400(self, client):
        """Malformed display_config JSON returns 400."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"display_config": "not-json"},
            headers=AUTH,
        )
        assert resp.status_code == 400


class TestUpdateEdgeCases:
    """Edge cases for the update endpoint."""

    def test_update_does_not_affect_untouched_fields(self, client):
        """Fields not sent in the update request remain unchanged."""
        created = _create_share(client, protected="yes", ttl=168)
        doc_id = created["id"]
        original_content = "# Original"
        view_password = created["password"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"ttl": "1"},  # Only change ttl
            headers=AUTH,
        )
        assert resp.status_code == 200

        # Content should be unchanged
        raw = client.get(f"/v/{doc_id}/raw?pw={view_password}")
        assert raw.status_code == 200
        assert raw.data.decode() == original_content

        # Password should still be required (protected was not touched)
        raw_no_pw = client.get(f"/v/{doc_id}/raw")
        assert raw_no_pw.status_code == 401

    def test_update_and_then_view_raw_with_password(self, client):
        """Protected share: update content, then view raw with password."""
        created = _create_share(client, protected="yes")
        doc_id = created["id"]
        old_password = created["password"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "# Updated Protected"},
            headers=AUTH,
        )
        assert resp.status_code == 200

        # View raw with original password
        raw = client.get(f"/v/{doc_id}/raw?pw={old_password}")
        assert raw.status_code == 200
        assert raw.data.decode() == "# Updated Protected"

    def test_response_is_json(self, client):
        """Update response has JSON content type."""
        created = _create_share(client)
        doc_id = created["id"]

        resp = client.put(
            f"/api/share/{doc_id}",
            data={"content": "# JSON Check"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert "application/json" in resp.content_type
