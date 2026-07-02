"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Minimal storage interface for the mdshare service.

    Four methods form the core interface: create a share, retrieve a share,
    delete a share, and check if a share ID exists.
    """

    @abstractmethod
    def create(self, doc_id: str, doc: dict) -> None:
        """Create a new share.

        Args:
            doc_id: Random URL-safe identifier (12 characters).
            doc: Dict with keys:
                 - 'content' (str): Raw markdown text.
                 - 'password' (str | None): bcrypt hash, or None if public.
                 - 'valid_until' (str | None): ISO 8601 datetime after which
                   the share should be treated as expired, or None for
                   shares that never expire (grandfathered rows).
                 - 'display_config' (dict | None): Per-share display
                   overrides (e.g. ``{"lineNumbers": true}``), or None to
                   use global defaults.
        """
        ...

    @abstractmethod
    def get(self, doc_id: str) -> dict | None:
        """Retrieve a share by ID.

        This method is the single choke-point for the lazy-expiry pattern.
        If the share exists but its ``valid_until`` timestamp has passed,
        the implementation MUST delete the row (and any associated image
        files) and return None.

        Args:
            doc_id: The share identifier.

        Returns:
            Dict with keys ``id``, ``content``, ``password``,
        ``created_at``, ``valid_until``, or None if not found (or expired).
        """
        ...

    @abstractmethod
    def exists(self, doc_id: str) -> bool:
        """Return True if a share with the given ID exists."""
        ...

    @abstractmethod
    def delete(self, doc_id: str) -> None:
        """Delete a share and its associated resources (internal use only).

        This method is NOT exposed via HTTP or MCP endpoints. It exists
        solely so the storage backend can clean up expired rows from
        within :meth:`get`.

        Args:
            doc_id: Share identifier.
        """
        ...

    @abstractmethod
    def list_active(self) -> list[dict]:
        """Return metadata for all non-expired shares.

        Expired shares (valid_until not null and in the past) are
        excluded. Shares with valid_until = NULL never expire.

        Returns:
            List of dicts, each with keys ``id``, ``created_at``,
            ``valid_until``, and ``protected`` (bool — True if a
            password hash is present).
        """
        ...

    def close(self) -> None:
        """Release any resources held by the storage backend.

        Subclasses may override to perform cleanup (e.g. closing
        database connections). The default implementation is a no-op.
        """
