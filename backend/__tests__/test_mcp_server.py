"""Tests for MCP Streamable HTTP server — direct function call tests.

Uses the FastMCP tool functions directly (not via HTTP), bypassing
ASGI lifespan issues with Streamable HTTP. Reuses the same pytest
fixtures from conftest.py (reset_storage, etc.).

Note: The conftest.py already sets ``MDSHARE_MASTER_PASSWORD`` and
``MDSHARE_DATA_DIR`` before importing backend modules.
"""

import pytest

from backend.config import Config
from backend.mcp_server import (
    create_share,
    get_share,
    get_share_info,
    health_check,
    list_shares,
)

# conftest.py sets MDSHARE_MASTER_PASSWORD="test-master-password"
_VALID_PW = "test-master-password"


class TestCreateShare:
    """Tests for :func:`create_share` tool function."""

    @pytest.mark.asyncio
    async def test_missing_master_password_returns_error(self):
        """Empty master_password returns unauthorized error."""
        result = await create_share(
            content="# Hello",
            master_password="",
            protected=False,
        )
        assert "error" in result
        assert "invalid master password" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_wrong_master_password_returns_error(self):
        """Invalid master password returns unauthorized error."""
        result = await create_share(
            content="# Hello",
            master_password="wrong-password",
            protected=False,
        )
        assert "error" in result
        assert "invalid master password" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_public_share_returns_url_and_id(self):
        """Public share creation returns id and url, no password."""
        result = await create_share(
            content="# Hello World",
            master_password=_VALID_PW,
            protected=False,
        )
        assert "id" in result
        assert "url" in result
        assert "password" not in result
        assert result["url"].endswith(f"/v/{result['id']}")

    @pytest.mark.asyncio
    async def test_protected_share_returns_password(self):
        """Protected share creation returns a password field."""
        result = await create_share(
            content="Secret content",
            master_password=_VALID_PW,
            protected=True,
        )
        assert "id" in result
        assert "url" in result
        assert "password" in result
        # _generate_password produces 8 chars
        assert len(result["password"]) == 8

    @pytest.mark.asyncio
    async def test_empty_content_returns_error(self):
        """Empty content returns error dict, not success."""
        result = await create_share(
            content="",
            master_password=_VALID_PW,
            protected=False,
        )
        assert "error" in result
        assert "content" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_content_returns_error(self):
        """Whitespace-only content returns error dict."""
        result = await create_share(
            content="   \n  \t  ",
            master_password=_VALID_PW,
            protected=False,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_content_exceeds_max_size(self, monkeypatch):
        """Content exceeding MAX_CONTENT_SIZE returns error."""
        monkeypatch.setattr("backend.mcp_server.config", Config(max_size=10))
        result = await create_share(
            content="x" * 100,
            master_password=_VALID_PW,
            protected=False,
        )
        assert "error" in result
        assert "exceeds" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_images_parameter_accepted(self):
        """Image dict parameter is accepted and processed."""
        result = await create_share(
            content="Text with ![img](test.png)",
            master_password=_VALID_PW,
            protected=False,
            images=["data:image/png;filename=test.png;base64,cGxhY2Vob2xkZXI="],
        )
        assert "id" in result
        assert "url" in result
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_invalid_base64_image_returns_error(self):
        """Invalid base64 image data returns error."""
        result = await create_share(
            content="Text with ![img](bad.png)",
            master_password=_VALID_PW,
            protected=False,
            images=["data:image/png;filename=bad.png;base64,not-valid-base64!!!@@@"],
        )
        assert "error" in result
        assert "invalid base64 data" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_image_data_too_large(self, monkeypatch):
        """Image base64 data exceeding MAX_CONTENT_SIZE returns error."""
        monkeypatch.setattr("backend.mcp_server.config", Config(max_size=10))
        result = await create_share(
            content="Hi",  # 2 bytes, well under the 10-byte limit
            master_password=_VALID_PW,
            protected=False,
            # 1000 bytes base64 → triggers limit
            images=["data:image/png;filename=img.png;base64," + "A" * 1000],
        )
        assert "error" in result
        assert "exceeds max size" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_default_ttl_returns_valid_until(self):
        """Default TTL produces a valid_until ISO 8601 string."""
        result = await create_share(
            content="# TTL test",
            master_password=_VALID_PW,
            protected=False,
        )
        assert "valid_until" in result
        # Should be a parseable ISO 8601 datetime string
        from datetime import datetime
        parsed = datetime.fromisoformat(result["valid_until"])
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_custom_ttl_hours(self):
        """Custom ttl_hours=2 produces a valid_until ~2 hours in future."""
        result = await create_share(
            content="# Custom TTL",
            master_password=_VALID_PW,
            protected=False,
            ttl_hours=2,
        )
        assert "valid_until" in result
        from datetime import datetime, timezone, timedelta
        parsed = datetime.fromisoformat(result["valid_until"])
        now = datetime.now(timezone.utc)
        diff = parsed - now
        # Should be roughly 2 hours (allow 30s skew)
        assert timedelta(hours=1, minutes=59) < diff < timedelta(hours=2, minutes=1)

    @pytest.mark.asyncio
    async def test_zero_ttl_hours_no_expiry(self):
        """ttl_hours=0 means no expiry → valid_until is None."""
        result = await create_share(
            content="# No expiry",
            master_password=_VALID_PW,
            protected=False,
            ttl_hours=0,
        )
        # When valid_until is None, the key should not be present
        # or explicitly set to None (depends on response format)
        assert result.get("valid_until") is None


class TestGetShare:
    """Tests for :func:`get_share` tool function."""

    @pytest.mark.asyncio
    async def test_retrieve_public_share(self):
        """Public share returns content and metadata."""
        created = await create_share(
            content="Test content",
            master_password=_VALID_PW,
            protected=False,
        )
        result = await get_share(share_id=created["id"])
        assert "error" not in result
        assert result["content"] == "Test content"
        assert result["protected"] is False
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_retrieve_protected_share_with_correct_password(self):
        """Protected share returns content when correct password given."""
        created = await create_share(
            content="Secret",
            master_password=_VALID_PW,
            protected=True,
        )
        result = await get_share(share_id=created["id"], password=created["password"])
        assert "error" not in result
        assert result["content"] == "Secret"
        assert result["protected"] is True

    @pytest.mark.asyncio
    async def test_protected_share_without_password_returns_error(self):
        """Protected share without password returns password required error."""
        created = await create_share(
            content="Secret",
            master_password=_VALID_PW,
            protected=True,
        )
        result = await get_share(share_id=created["id"])
        assert "error" in result
        assert "password required" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_wrong_password_returns_error(self):
        """Protected share with wrong password returns error."""
        created = await create_share(
            content="Secret",
            master_password=_VALID_PW,
            protected=True,
        )
        result = await get_share(share_id=created["id"], password="wrongpass")
        assert "error" in result
        assert "incorrect" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_non_existent_share_returns_not_found(self):
        """Non-existent share returns not found error."""
        result = await get_share(share_id="nonexistent123456")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_share_id_returns_error(self):
        """Empty share_id returns error."""
        result = await get_share(share_id="")
        assert "error" in result
        assert "share id is required" in result["error"].lower()


class TestGetShareInfo:
    """Tests for :func:`get_share_info` tool function."""

    @pytest.mark.asyncio
    async def test_existing_public_share_returns_metadata(self):
        """Existing share returns metadata without content."""
        created = await create_share(
            content="Test",
            master_password=_VALID_PW,
            protected=False,
        )
        result = await get_share_info(share_id=created["id"])
        assert result["id"] == created["id"]
        assert result["exists"] is True
        assert result["protected"] is False
        assert "url" in result
        assert "created_at" in result
        assert "content" not in result

    @pytest.mark.asyncio
    async def test_existing_protected_share_shows_protected(self):
        """Protected share shows protected=True in info response."""
        created = await create_share(
            content="Test",
            master_password=_VALID_PW,
            protected=True,
        )
        result = await get_share_info(share_id=created["id"])
        assert result["protected"] is True

    @pytest.mark.asyncio
    async def test_non_existing_share_returns_exists_false(self):
        """Non-existing share returns exists=False, no error."""
        result = await get_share_info(share_id="idonotexist123")
        assert "error" not in result
        assert result["exists"] is False
        assert result["id"] == "idonotexist123"
        assert result["protected"] is False
        assert "url" in result
        assert result["created_at"] is None

    @pytest.mark.asyncio
    async def test_empty_share_id_returns_error(self):
        """Empty share_id returns error."""
        result = await get_share_info(share_id="")
        assert "error" in result
        assert "share id is required" in result["error"].lower()


class TestHealthCheck:
    """Tests for :func:`health_check` tool function."""

    @pytest.mark.asyncio
    async def test_health_returns_ok_with_working_storage(self):
        """Health check returns ok status with storage reachable."""
        result = await health_check()
        assert result["status"] == "ok"
        assert result["storage"] == "sqlite"
        assert "data_dir" in result

    @pytest.mark.asyncio
    async def test_health_degraded_when_storage_unreachable(self, monkeypatch):
        """Health returns degraded status when storage throws an exception."""
        from backend.storage.sqlite import SqliteStorage

        def _failing_list_active(self) -> list:
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(SqliteStorage, "list_active", _failing_list_active)
        result = await health_check()
        assert result["status"] == "degraded"
        assert result["storage"] == "unreachable"


class TestListShares:
    """Tests for :func:`list_shares` tool function."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_shares(self):
        """No shares returns empty list with count 0."""
        result = await list_shares(master_password=_VALID_PW)
        assert result["shares"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_missing_master_password_returns_error(self):
        """Empty master_password returns error."""
        result = await list_shares(master_password="")
        assert "error" in result
        assert "invalid master password" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_wrong_master_password_returns_error(self):
        """Invalid master password returns error."""
        result = await list_shares(master_password="wrong-password")
        assert "error" in result
        assert "invalid master password" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_lists_public_and_protected_shares(self):
        """Both public and protected shares appear in listing."""
        pub = await create_share(
            content="public content",
            master_password=_VALID_PW,
        )
        prot = await create_share(
            content="protected content",
            master_password=_VALID_PW,
            protected=True,
        )
        result = await list_shares(master_password=_VALID_PW)
        assert result["count"] >= 2
        ids = {s["id"] for s in result["shares"]}
        assert pub["id"] in ids
        assert prot["id"] in ids

    @pytest.mark.asyncio
    async def test_share_has_url_field(self):
        """Each share has a url field that is a non-empty string."""
        await create_share(
            content="# Hello",
            master_password=_VALID_PW,
        )
        result = await list_shares(master_password=_VALID_PW)
        assert len(result["shares"]) == 1
        share = result["shares"][0]
        assert "url" in share
        assert isinstance(share["url"], str)
        assert len(share["url"]) > 0

    @pytest.mark.asyncio
    async def test_excludes_expired_shares(self):
        """Shares with past valid_until are excluded from listing."""
        from backend.storage import get_storage

        # Create a valid share
        valid = await create_share(
            content="active share",
            master_password=_VALID_PW,
        )

        # Directly insert an expired share bypassing the API
        storage = get_storage()
        storage.create("expired-test-id", {
            "content": "expired content",
            "password": None,
            "valid_until": "2020-01-01T00:00:00",
        })

        result = await list_shares(master_password=_VALID_PW)
        share_ids = {s["id"] for s in result["shares"]}
        assert valid["id"] in share_ids
        assert "expired-test-id" not in share_ids

    @pytest.mark.asyncio
    async def test_field_keys_match_expected_schema(self):
        """Each share dict contains expected keys."""
        await create_share(
            content="schema test",
            master_password=_VALID_PW,
        )
        result = await list_shares(master_password=_VALID_PW)
        assert len(result["shares"]) == 1
        share = result["shares"][0]
        expected_keys = {"id", "url", "created_at", "valid_until", "protected"}
        assert set(share.keys()) == expected_keys
