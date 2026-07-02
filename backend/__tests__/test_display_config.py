"""Tests for display configuration — GET /v/<id>/config and PUT display_config field."""

import json

MASTER_PW = "test-master-password"
AUTH = {"Authorization": f"Bearer {MASTER_PW}"}

_DEFAULT_THEME = "auto"
_DEFAULT_CODE_LN = False
_ALL_KEYS = frozenset(
    {
        "font_family",
        "font_size",
        "line_height",
        "max_width",
        "theme",
        "code_font_size",
        "code_line_numbers",
        "custom_css",
    }
)
_DEFAULTS = {
    "font_family": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "font_size": "16px",
    "line_height": "1.6",
    "max_width": "900px",
    "theme": "auto",
    "code_font_size": "14px",
    "code_line_numbers": False,
    "custom_css": "",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _create_share(client, *, display_config: dict | str | None = None) -> str:
    """Upload a share and return its doc ID.

    Parameters
    ----------
    display_config:
        If a dict, JSON-encoded and sent as the ``display_config`` form field.
        If a string, sent as-is (e.g. ``""`` for the empty-string test).
        If ``None``, no ``display_config`` field is included.
    """
    data = {"content": "# Test", "protected": "no"}
    if display_config is not None:
        if isinstance(display_config, dict):
            data["display_config"] = json.dumps(display_config)
        else:
            data["display_config"] = display_config
    resp = client.put("/api/share", data=data, headers=AUTH)
    assert resp.status_code == 201
    url: str = resp.get_json()["url"]
    return url.rsplit("/", 1)[-1]


# ── TestGetDisplayConfig ─────────────────────────────────────────────────────


class TestGetDisplayConfig:
    """Tests for ``GET /v/<id>/config``."""

    def test_nonexistent_share_returns_200_with_defaults(self, client):
        """Non-existent ID returns 200 with all 8 default keys."""
        resp = client.get("/v/nonexistent/config")
        assert resp.status_code == 200
        data = resp.get_json()
        for key in _ALL_KEYS:
            assert key in data
        assert data["theme"] == _DEFAULT_THEME
        assert data["code_line_numbers"] is _DEFAULT_CODE_LN

    def test_share_without_display_config_returns_defaults(self, client):
        """Share created without display_config returns defaults."""
        doc_id = _create_share(client)
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        data = resp.get_json()
        for key in _ALL_KEYS:
            assert key in data
        assert data == _DEFAULTS

    def test_share_with_partial_overrides_returns_merged(self, client):
        """Partial overrides merge with defaults."""
        overrides = {"theme": "dark", "code_line_numbers": True}
        doc_id = _create_share(client, display_config=overrides)
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["theme"] == "dark"
        assert data["code_line_numbers"] is True
        # Other keys should still be defaults
        for key in _ALL_KEYS - {"theme", "code_line_numbers"}:
            assert data[key] == _DEFAULTS[key]

    def test_share_with_full_overrides(self, client):
        """All 8 keys can be overridden and returned as set."""
        overrides = {
            "font_family": "monospace",
            "font_size": "18px",
            "line_height": "2.0",
            "max_width": "1200px",
            "theme": "dark",
            "code_font_size": "16px",
            "code_line_numbers": True,
            "custom_css": "body { color: red; }",
        }
        doc_id = _create_share(client, display_config=overrides)
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        data = resp.get_json()
        for key, value in overrides.items():
            assert data[key] == value

    def test_response_is_json(self, client):
        """Response Content-Type is application/json."""
        resp = client.get("/v/nonexistent/config")
        assert resp.is_json


# ── TestUploadDisplayConfig ──────────────────────────────────────────────────


class TestUploadDisplayConfig:
    """Tests for ``PUT /api/share`` with ``display_config`` field."""

    def test_valid_display_config_stored_and_retrievable(self, client):
        """Valid display_config is stored and returned by GET config."""
        overrides = {"theme": "dark", "line_height": "2.0"}
        doc_id = _create_share(client, display_config=overrides)
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["theme"] == "dark"
        assert data["line_height"] == "2.0"
        # Unset keys still at defaults
        assert data["code_line_numbers"] is False

    def test_invalid_json_returns_400(self, client):
        """Non-JSON display_config yields 400."""
        resp = client.put(
            "/api/share",
            data={"content": "# Test", "protected": "no", "display_config": "not-json"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        err = resp.get_json()["error"]
        assert "invalid display_config" in err.lower()

    def test_invalid_theme_returns_400(self, client):
        """Unknown theme value yields 400."""
        resp = client.put(
            "/api/share",
            data={
                "content": "# Test",
                "protected": "no",
                "display_config": json.dumps({"theme": "blue"}),
            },
            headers=AUTH,
        )
        assert resp.status_code == 400
        err = resp.get_json()["error"]
        assert "invalid display_config" in err.lower()
        assert "theme" in err.lower()

    def test_code_line_numbers_wrong_type_returns_400(self, client):
        """code_line_numbers as string yields 400."""
        resp = client.put(
            "/api/share",
            data={
                "content": "# Test",
                "protected": "no",
                "display_config": json.dumps({"code_line_numbers": "yes"}),
            },
            headers=AUTH,
        )
        assert resp.status_code == 400
        err = resp.get_json()["error"]
        assert "invalid display_config" in err.lower()
        assert "code_line_numbers" in err.lower()

    def test_string_field_wrong_type_returns_400(self, client):
        """String-typed field passed as int yields 400."""
        resp = client.put(
            "/api/share",
            data={
                "content": "# Test",
                "protected": "no",
                "display_config": json.dumps({"font_size": 16}),
            },
            headers=AUTH,
        )
        assert resp.status_code == 400
        err = resp.get_json()["error"]
        assert "invalid display_config" in err.lower()
        assert "font_size" in err.lower()

    def test_unknown_keys_silently_stripped(self, client):
        """Unknown keys in display_config are silently removed."""
        doc_id = _create_share(
            client,
            display_config={"theme": "dark", "bogus_field": "should be removed"},
        )
        # Upload should succeed (201) since bogus key is just stripped
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["theme"] == "dark"
        assert "bogus_field" not in data

    def test_omitted_display_config_uses_defaults(self, client):
        """Share without display_config field returns all defaults."""
        doc_id = _create_share(client)
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        assert resp.get_json() == _DEFAULTS

    def test_empty_display_config_uses_defaults(self, client):
        """Empty string display_config is treated as no config → defaults."""
        doc_id = _create_share(client, display_config="")
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        assert resp.get_json() == _DEFAULTS


# ── TestDisplayConfigValues ──────────────────────────────────────────────────


class TestDisplayConfigValues:
    """Edge cases and exact value preservation."""

    def test_custom_css_stored_and_returned(self, client):
        """custom_css with special characters is preserved exactly."""
        css = "body { background: red; }"
        doc_id = _create_share(client, display_config={"custom_css": css})
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        assert resp.get_json()["custom_css"] == css

    def test_font_family_with_multiple_fallbacks(self, client):
        """Font family with fallbacks is preserved exactly."""
        font = "'Fira Code', monospace"
        doc_id = _create_share(client, display_config={"font_family": font})
        resp = client.get(f"/v/{doc_id}/config")
        assert resp.status_code == 200
        assert resp.get_json()["font_family"] == font
