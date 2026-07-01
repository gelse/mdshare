"""Storage backend package — factory function.

get_storage() returns a cached singleton SqliteStorage instance so that
the underlying SQLite connection is reused across the application lifespan.
This avoids ResourceWarning from unclosed connections being garbage collected.
"""

from .abstract import StorageBackend
from .sqlite import SqliteStorage

__all__ = ["StorageBackend", "SqliteStorage", "get_storage"]

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the shared storage backend singleton (always SQLite).

    The instance is created once on first call and cached.  Callers MUST NOT
    close the returned object — the session-scoped teardown in the test suite
    (or application shutdown) handles cleanup via :meth:`StorageBackend.close`.
    """
    global _storage
    if _storage is None:
        _storage = SqliteStorage()
    return _storage
