"""Tests for PUT /api/share — the minimal mdshare upload endpoint."""

MASTER_PW = "test-master-password"
AUTH = {"Authorization": f"Bearer {MASTER_PW}"}


class TestUploadHappyPath:
    """Successful upload scenarios."""

    def test_public_share_returns_201_with_url_only(self, client):
        """Upload with protected=no returns url, no password."""
        resp = client.put(
            "/api/share",
            data={"content": "# Hello", "protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "url" in data
        assert data["url"].startswith("http://localhost/v/")
        assert data.get("password") is None

    def test_protected_share_returns_201_with_url_and_password(self, client):
        """Upload with protected=yes returns url + 8-char password."""
        resp = client.put(
            "/api/share",
            data={"content": "# Secret", "protected": "yes"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "url" in data
        assert "password" in data
        assert len(data["password"]) == 8

    def test_protected_true_lowercase_works(self, client):
        """protected='true' is accepted as truthy."""
        resp = client.put(
            "/api/share",
            data={"content": "# Test", "protected": "true"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.get_json().get("password") is not None

    def test_protected_1_works(self, client):
        """protected='1' is accepted as truthy."""
        resp = client.put(
            "/api/share",
            data={"content": "# Test", "protected": "1"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.get_json().get("password") is not None

    def test_omitted_protected_defaults_to_public(self, client):
        """When protected field is absent, share is public."""
        resp = client.put(
            "/api/share",
            data={"content": "# Public"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.get_json().get("password") is None

    def test_uploaded_content_is_retrievable_via_raw(self, client):
        """End-to-end: upload then fetch via raw endpoint."""
        resp = client.put(
            "/api/share",
            data={"content": "# Persisted"},
            headers=AUTH,
        )
        url = resp.get_json()["url"]
        doc_id = url.rstrip("/").split("/")[-1]

        raw = client.get(f"/v/{doc_id}/raw")
        assert raw.status_code == 200
        assert raw.data.decode() == "# Persisted"

    def test_response_is_json(self, client):
        """Upload response has JSON content type."""
        resp = client.put(
            "/api/share",
            data={"content": "# Test"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert "application/json" in resp.content_type


class TestUploadAuth:
    """Authorization header handling."""

    def test_missing_auth_header_returns_401(self, client):
        resp = client.put(
            "/api/share",
            data={"content": "# Test"},
        )
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "unauthorized"

    def test_wrong_password_returns_401(self, client):
        resp = client.put(
            "/api/share",
            data={"content": "# Test"},
            headers={"Authorization": "Bearer wrong-password"},
        )
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "unauthorized"

    def test_no_bearer_prefix_returns_401(self, client):
        """Auth header without 'Bearer ' prefix is rejected."""
        resp = client.put(
            "/api/share",
            data={"content": "# Test"},
            headers={"Authorization": MASTER_PW},
        )
        assert resp.status_code == 401

    def test_empty_auth_header_returns_401(self, client):
        resp = client.put(
            "/api/share",
            data={"content": "# Test"},
            headers={"Authorization": ""},
        )
        assert resp.status_code == 401


class TestUploadValidation:
    """Input validation."""

    def test_missing_content_returns_400(self, client):
        resp = client.put(
            "/api/share",
            data={"protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "markdown content is required" in resp.get_json()["error"]

    def test_empty_content_returns_400(self, client):
        resp = client.put(
            "/api/share",
            data={"content": "", "protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_whitespace_only_content_returns_400(self, client):
        resp = client.put(
            "/api/share",
            data={"content": "   \n  \t  ", "protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_content_exceeds_max_size_returns_413(self, client, monkeypatch):
        from backend.config import Config
        monkeypatch.setattr("backend.app.config", Config(max_size=10))
        resp = client.put(
            "/api/share",
            data={"content": "x" * 100, "protected": "no"},
            headers=AUTH,
        )
        assert resp.status_code == 413


class TestUploadIdScheme:
    """ID generation."""

    def test_ids_are_unique_across_uploads(self, client):
        """Ten consecutive uploads get ten distinct IDs."""
        ids = set()
        for _ in range(10):
            resp = client.put(
                "/api/share",
                data={"content": "# Test"},
                headers=AUTH,
            )
            url = resp.get_json()["url"]
            doc_id = url.rstrip("/").split("/")[-1]
            ids.add(doc_id)
        assert len(ids) == 10


class TestUploadTTL:
    """TTL / retention time tests for PUT /api/share."""

    def test_default_ttl_returns_valid_until(self, client):
        """Default TTL produces a valid_until ISO 8601 datetime string."""
        resp = client.put(
            "/api/share",
            data={"content": "# Hello"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "valid_until" in data
        assert data["valid_until"] is not None
        # Verify it parses as ISO 8601
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(data["valid_until"])
        assert parsed.tzinfo is not None

    def test_custom_ttl_returns_correct_valid_until(self, client):
        """Custom TTL of 2 hours produces a valid_until ~2h in the future."""
        resp = client.put(
            "/api/share",
            data={"content": "# Hello", "ttl": "2"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "valid_until" in data
        from datetime import datetime, timezone, timedelta
        parsed = datetime.fromisoformat(data["valid_until"])
        now = datetime.now(timezone.utc)
        diff = parsed - now
        # Should be roughly 2 hours (allow 30s tolerance)
        assert timedelta(hours=1, minutes=59) < diff < timedelta(hours=2, minutes=1)

    def test_invalid_ttl_returns_400(self, client):
        """Non-integer TTL value returns 400."""
        resp = client.put(
            "/api/share",
            data={"content": "# Hello", "ttl": "abc"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "ttl" in resp.get_json()["error"].lower()

    def test_zero_ttl_returns_null_valid_until(self, client):
        """TTL=0 means no expiry (valid_until is null)."""
        resp = client.put(
            "/api/share",
            data={"content": "# Hello", "ttl": "0"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "valid_until" in data
        assert data["valid_until"] is None


class TestAdminListShares:
    """Tests for GET /api/admin/shares — admin share listing with pagination."""

    URL = "/api/admin/shares"

    # ------------------------------------------------------------------
    # Auth tests (unchanged)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Basic functionality with new pagination response shape
    # ------------------------------------------------------------------

    def test_returns_empty_list_when_no_shares(self, client):
        resp = client.get(self.URL, headers=AUTH)
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["shares"] == []
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total_pages"] == 1
        assert data["total_count"] == 0

    def test_response_is_json(self, client):
        resp = client.get(self.URL, headers=AUTH)
        assert resp.is_json

    def test_lists_public_and_protected_shares(self, client):
        # Create one public and one protected share
        client.put(
            "/api/share",
            data={"content": "public share"},
            headers=AUTH,
        )
        client.put(
            "/api/share",
            data={"content": "protected share", "protected": "yes"},
            headers=AUTH,
        )

        resp = client.get(self.URL, headers=AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_count"] == 2
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total_pages"] == 1

        # Find the protected share and verify its flag
        protected = [s for s in data["shares"] if s["protected"]]
        assert len(protected) == 1
        assert protected[0]["protected"] is True

    def test_each_share_has_required_fields(self, client):
        client.put(
            "/api/share",
            data={"content": "test"},
            headers=AUTH,
        )
        resp = client.get(self.URL, headers=AUTH)
        data = resp.get_json()
        share = data["shares"][0]
        assert set(share.keys()) == {
            "id", "url", "created_at", "valid_until", "protected",
        }
        assert share["id"] and isinstance(share["id"], str)
        assert share["url"].startswith("http")
        assert share["created_at"] is not None

    def test_excludes_expired_shares(self, client, monkeypatch):
        """Shares with valid_until in the past should not appear."""
        # Create a non-expiring share
        client.put(
            "/api/share",
            data={"content": "forever", "ttl": "0"},
            headers=AUTH,
        )

        # Insert an already-expired share directly
        from backend.storage import get_storage
        from datetime import datetime, timezone, timedelta
        import secrets
        import string

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

        resp = client.get(self.URL, headers=AUTH)
        data = resp.get_json()
        assert data["total_count"] == 1  # only the non-expired share
        assert data["total_pages"] == 1
        assert data["shares"][0]["valid_until"] is None

    # ------------------------------------------------------------------
    # Pagination parameter validation
    # ------------------------------------------------------------------

    def test_invalid_page_size_below_min_returns_400(self, client):
        resp = client.get(self.URL, query_string={"page_size": "0"}, headers=AUTH)
        assert resp.status_code == 400
        assert "page_size" in resp.get_json()["error"]

    def test_invalid_page_size_above_max_returns_400(self, client):
        resp = client.get(self.URL, query_string={"page_size": "201"}, headers=AUTH)
        assert resp.status_code == 400
        assert "page_size" in resp.get_json()["error"]

    def test_invalid_page_below_one_returns_400(self, client):
        resp = client.get(self.URL, query_string={"page": "0"}, headers=AUTH)
        assert resp.status_code == 400
        assert "page" in resp.get_json()["error"]

    def test_non_integer_page_size_returns_400(self, client):
        resp = client.get(self.URL, query_string={"page_size": "abc"}, headers=AUTH)
        assert resp.status_code == 400
        assert "integer" in resp.get_json()["error"]

    def test_non_integer_page_returns_400(self, client):
        resp = client.get(self.URL, query_string={"page": "abc"}, headers=AUTH)
        assert resp.status_code == 400
        assert "integer" in resp.get_json()["error"]

    # ------------------------------------------------------------------
    # Pagination behaviour
    # ------------------------------------------------------------------

    def test_default_pagination_metadata(self, client):
        """Default page_size=50, page=1 when no query params provided."""
        resp = client.get(self.URL, headers=AUTH)
        data = resp.get_json()
        assert data["page"] == 1
        assert data["page_size"] == 50

    def test_custom_page_size_reflected_in_response(self, client):
        resp = client.get(self.URL, query_string={"page_size": "10"}, headers=AUTH)
        data = resp.get_json()
        assert data["page_size"] == 10

    def test_page_parameter_reflected_in_response(self, client):
        resp = client.get(self.URL, query_string={"page": "3"}, headers=AUTH)
        data = resp.get_json()
        assert data["page"] == 3

    def test_page_beyond_end_returns_empty_shares(self, client):
        """Page past the last page returns empty share list with correct metadata."""
        # Create 3 shares
        for i in range(3):
            client.put(
                "/api/share",
                data={"content": f"share {i}"},
                headers=AUTH,
            )
        resp = client.get(
            self.URL, query_string={"page": "10", "page_size": "2"}, headers=AUTH
        )
        data = resp.get_json()
        assert data["shares"] == []
        assert data["page"] == 10
        assert data["page_size"] == 2
        assert data["total_pages"] == 2  # 3 shares at page_size=2 = ceil(3/2) = 2
        assert data["total_count"] == 3

    def test_total_pages_calculation(self, client):
        """total_pages = ceil(total_count / page_size)."""
        # Create 5 shares
        for i in range(5):
            client.put(
                "/api/share",
                data={"content": f"share {i}"},
                headers=AUTH,
            )
        resp = client.get(
            self.URL, query_string={"page_size": "3"}, headers=AUTH
        )
        data = resp.get_json()
        assert data["total_count"] == 5
        assert data["page_size"] == 3
        assert data["total_pages"] == 2  # ceil(5/3)

    def test_second_page_returns_remaining_shares(self, client):
        """With page_size=2 and 3 shares, page 2 has 1 share."""
        for i in range(3):
            client.put(
                "/api/share",
                data={"content": f"share {i}"},
                headers=AUTH,
            )
        resp = client.get(
            self.URL, query_string={"page": "2", "page_size": "2"}, headers=AUTH
        )
        data = resp.get_json()
        assert len(data["shares"]) == 1
        assert data["page"] == 2
        assert data["total_count"] == 3
        assert data["total_pages"] == 2


VALID_UNTIL_URL = "/api/admin/shares/validuntil"


class TestAdminSetValidUntil:
    """Tests for POST /api/admin/shares/validuntil — batch set valid_until."""

    def test_returns_401_without_auth(self, client):
        """Missing auth header returns 401."""
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": ["abc"], "valid_until": "2027-06-01T00:00:00"},
        )
        assert resp.status_code == 401

    def test_missing_ids_field_returns_400(self, client):
        """Request body without 'ids' returns 400."""
        resp = client.post(
            VALID_UNTIL_URL,
            json={"valid_until": "2027-06-01T00:00:00"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "missing required field: ids"

    def test_empty_ids_array_returns_400(self, client):
        """Empty ids array returns 400."""
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": [], "valid_until": "2027-06-01T00:00:00"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "ids must be a non-empty array"

    def test_non_string_id_returns_400(self, client):
        """Numeric element in ids array returns 400."""
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": [42], "valid_until": "2027-06-01T00:00:00"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "non-empty string" in resp.get_json()["error"]

    def test_invalid_date_format_returns_400(self, client):
        """Bad ISO 8601 date returns 400."""
        # Create at least one share first
        client.put("/api/share", data={"content": "test"}, headers=AUTH)
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": ["abc123def456"], "valid_until": "not-a-date"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "Invalid date format" in resp.get_json()["error"]

    def test_set_valid_until_updates_date(self, client):
        """Setting valid_until on existing shares returns updated count."""
        # Create two shares
        r1 = client.put("/api/share", data={"content": "a"}, headers=AUTH)
        r2 = client.put("/api/share", data={"content": "b"}, headers=AUTH)
        id1 = r1.get_json()["url"].rstrip("/").split("/")[-1]
        id2 = r2.get_json()["url"].rstrip("/").split("/")[-1]

        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": [id1, id2], "valid_until": "2027-06-01T00:00:00"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated"] == 2
        assert data["not_found"] == 0

    def test_clear_valid_until_sets_null(self, client):
        """Omitting valid_until (null) clears the expiry."""
        r = client.put("/api/share", data={"content": "x"}, headers=AUTH)
        share_id = r.get_json()["url"].rstrip("/").split("/")[-1]

        # First set a date
        client.post(
            VALID_UNTIL_URL,
            json={"ids": [share_id], "valid_until": "2027-06-01T00:00:00"},
            headers=AUTH,
        )
        # Then clear it
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": [share_id], "valid_until": None},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 1

    def test_non_existent_ids_return_not_found(self, client):
        """IDs that don't exist are counted in not_found."""
        resp = client.post(
            VALID_UNTIL_URL,
            json={"ids": ["nonexistent1", "nonexistent2"], "valid_until": None},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated"] == 0
        assert data["not_found"] == 2

    def test_mixed_existing_and_non_existent(self, client):
        """Mix of existing and non-existing IDs returns partial results."""
        r = client.put("/api/share", data={"content": "existing"}, headers=AUTH)
        existing_id = r.get_json()["url"].rstrip("/").split("/")[-1]

        resp = client.post(
            VALID_UNTIL_URL,
            json={
                "ids": [existing_id, "does-not-exist"],
                "valid_until": "2027-06-01T00:00:00",
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated"] == 1
        assert data["not_found"] == 1
