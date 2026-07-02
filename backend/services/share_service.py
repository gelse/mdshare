"""Domain service — business logic for share creation, retrieval, expiry, and listing.

This is the single layer that both the Flask API (``backend/app.py``) and the
MCP server (``backend/mcp_server.py``) delegate to, eliminating the previous
code duplication.
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from backend.config import config
from backend.services.image_handler import rewrite_image_urls
from backend.storage import get_storage
from backend.storage.abstract import StorageBackend
from backend.display_config import display_config


class ShareService:
    """Encapsulates all share-related domain logic.

    * ID / password generation
    * Content validation
    * TTL / expiry computation
    * Storage delegation (create, read, delete, list)
    * Image URL rewriting (delegates to ``image_handler``)

    Callers are responsible for presentation-layer concerns such as building
    absolute URLs, formatting response dicts, and handling request context.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage

    # ------------------------------------------------------------------
    # Generation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def generate_id() -> str:
        """Generate a 12-character URL-safe document ID."""
        return secrets.token_urlsafe(9)

    @staticmethod
    def generate_password() -> str:
        """Generate an 8-character URL-safe view password."""
        return secrets.token_urlsafe(6)

    @staticmethod
    def compute_valid_until(ttl_hours: int | None = None) -> str | None:
        """Return an ISO-8601 expiry timestamp or ``None`` for non-expiring shares.

        When *ttl_hours* is ``None`` the configured ``default_ttl_hours`` is
        used.  A value of ``0`` means *no expiry*.
        """
        if ttl_hours is None:
            ttl_hours = config.default_ttl_hours
        if ttl_hours == 0:
            return None
        return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()

    # ------------------------------------------------------------------
    # Domain operations
    # ------------------------------------------------------------------

    def create_share(
        self,
        content: str,
        protected: bool = False,
        ttl_hours: int | None = None,
        filenames: set[str] | None = None,
        doc_id: str | None = None,
        display_config: dict | None = None,
    ) -> dict:
        """Create a new share and persist it.

        Parameters
        ----------
        content : str
            Raw Markdown content.
        protected : bool
            If ``True`` a random view password is generated and bcrypt-hashed.
        ttl_hours : int | None
            Time-to-live in hours.  ``None`` → use the configured default.
            ``0`` → never expires.
        filenames : set[str] | None
            Set of uploaded image filenames whose references in *content*
            will be rewritten to serving paths.
        doc_id : str | None
            Pre-generated document ID.  If ``None`` (the default) a new one
            is generated internally.
        display_config : dict | None
            Optional per-share display-config overrides.  Keys are validated
            against the 8 known display-config keys.  Invalid keys are
            stripped; invalid values raise :class:`ValueError`.

        Returns
        -------
        dict
            Domain result with keys ``id``, ``password`` (or ``None`` for
            public shares), ``valid_until``, and ``content`` (URL-rewritten).

        Raises
        ------
        ValueError
            If *content* is empty after stripping, or *display_config* contains
            invalid values.
        """
        content = content.strip()
        if not content:
            raise ValueError("Content cannot be empty")

        content_size = len(content.encode("utf-8"))
        if content_size > config.max_size:
            raise ValueError(
                f"content exceeds max size of {config.max_size} bytes"
            )

        doc_id = doc_id or self.generate_id()
        password = self.generate_password() if protected else None
        password_hash: str | None = (
            bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            if password
            else None
        )
        valid_until = self.compute_valid_until(ttl_hours)

        # Validate and normalise per-share display-config overrides.
        if display_config is not None:
            if not isinstance(display_config, dict):
                raise ValueError("invalid display_config: must be a dict")
            _known = {
                "font_family", "font_size", "line_height", "max_width",
                "theme", "code_font_size", "code_line_numbers", "custom_css",
            }
            validated = {k: v for k, v in display_config.items() if k in _known}
            for k, v in validated.items():
                if k == "theme" and v not in ("light", "dark", "auto"):
                    raise ValueError(
                        f"invalid display_config: theme must be 'light', 'dark',"
                        f" or 'auto', got {v!r}"
                    )
                if k == "code_line_numbers" and not isinstance(v, bool):
                    raise ValueError(
                        f"invalid display_config: code_line_numbers must be"
                        f" bool, got {type(v).__name__}"
                    )
                if k in ("font_family", "font_size", "line_height",
                         "max_width", "code_font_size", "custom_css") \
                        and not isinstance(v, str):
                    raise ValueError(
                        f"invalid display_config: {k} must be a string,"
                        f" got {type(v).__name__}"
                    )
            display_config = validated

        # Rewrite image references in the content *before* persisting.
        if filenames:
            content = rewrite_image_urls(content, doc_id, filenames)

        doc = {
            "id": doc_id,
            "content": content,
            "password": password_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "valid_until": valid_until,
            "display_config": display_config,
        }
        self.storage.create(doc_id, doc)

        return {
            "id": doc_id,
            "password": password,
            "valid_until": valid_until,
        }

    def get_share(
        self,
        share_id: str,
        view_password: str | None = None,
    ) -> dict | None:
        """Retrieve share content with optional password verification.

        * If the share does not exist (or expired) returns ``None``.
        * If the share is protected and *view_password* is wrong/missing
          returns an error dict ``{"error": "password_required"}`` or
          ``{"error": "incorrect_password"}``.
        * Otherwise returns the share document with an extra ``url`` field.

        This mirrors the existing 401 vs 401 distinction for the Flask route.
        """
        doc = self.storage.get(share_id)
        if doc is None:
            return None

        doc_password = doc.get("password")
        if doc_password is not None:
            if not view_password:
                return {"error": "password_required"}
            try:
                if not bcrypt.checkpw(view_password.encode(), doc_password.encode()):
                    return {"error": "incorrect_password"}
            except (ValueError, AttributeError):
                return {"error": "incorrect_password"}

        return doc

    def get_share_info(self, share_id: str) -> dict:
        """Return metadata for a share (no content, no password check).

        Keys: ``id``, ``exists``, ``protected``, ``created_at``, ``url``
        (path only — caller adds scheme/host).
        """
        doc = self.storage.get(share_id)
        if doc is None:
            return {
                "id": share_id,
                "exists": False,
                "protected": False,
                "created_at": None,
            }

        return {
            "id": share_id,
            "exists": True,
            "protected": doc.get("password") is not None,
            "created_at": doc.get("created_at"),
        }

    def get_display_config(self, share_id: str) -> dict:
        """Return display-config for a share with global fallback.

        Starts from global defaults and overlays any per-share overrides.
        If the share does not exist, global defaults are returned (the
        caller — the viewer — handles the 404 case separately).

        Returns
        -------
        dict
            All eight display-config keys filled from global defaults
            plus any per-share overrides.
        """
        defaults = display_config.get_defaults()
        doc = self.storage.get(share_id)
        if doc is None:
            return dict(defaults)
        per_share = doc.get("display_config")
        if not isinstance(per_share, dict):
            return dict(defaults)
        return {**defaults, **per_share}

    def health_check(self) -> dict:
        """Verify service is operational.

        Returns status, storage health indicator, and the configured data
        directory path.
        """
        try:
            self.storage.list_active()
            storage_status = "ok"
        except Exception:  # noqa: BLE001
            storage_status = "degraded"

        return {
            "status": "ok",
            "storage": storage_status,
            "data_dir": config.data_dir,
        }

    def list_shares(self) -> list[dict]:
        """Return all active (non-expired) shares."""
        return self.storage.list_active()

    def delete_share(self, share_id: str) -> bool:
        """Remove a share from storage.  Returns ``True`` on success."""
        try:
            self.storage.delete(share_id)
            return True
        except Exception:  # noqa: BLE001
            return False

    def exists(self, share_id: str) -> bool:
        """Check whether a share ID exists (ignores expiry)."""
        return self.storage.exists(share_id)


# Singleton instance — used by app.py, mcp_server.py, and the test suite.
# get_storage() returns a shared SqliteStorage singleton, so all consumers
# (including app.py's own ShareService instance) share the same backend.
share_service = ShareService(get_storage())
