"""Centralized configuration for mdshare — single-source-of-truth for all env-var-based settings.

All defaults match the original module-level constants so that existing code
(including tests that set env vars before importing) works without changes.
"""

from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class Config:
    """Immutable configuration loaded from environment variables at import time.

    The singleton instance ``config`` is created at module level.  Its
    ``default_factory`` lambdas call ``os.environ.get()`` **lazily** (at
    instantiation time), so tests that set env vars *before* the first import
    (e.g. ``conftest.py``) work correctly.
    """

    #: Directory for SQLite DB + uploaded images.
    data_dir: str = field(
        default_factory=lambda: os.environ.get("MDSHARE_DATA_DIR", "./data"),
    )
    #: Master password for upload authentication.
    master_password: str = field(
        default_factory=lambda: os.environ.get("MDSHARE_MASTER_PASSWORD", ""),
    )
    #: Maximum upload size in bytes (default: 10 MB).
    max_size: int = field(
        default_factory=lambda: int(
            os.environ.get("MDSHARE_MAX_SIZE", str(10 * 1024 * 1024))
        ),
    )
    #: Explicit base URL override (auto-detected via X-Forwarded-* when empty).
    base_url: str = field(
        default_factory=lambda: os.environ.get("MDSHARE_BASE_URL", "").rstrip("/"),
    )
    #: Default TTL hours for new shares (168 = 7 days).
    default_ttl_hours: int = 168

    #: Computed path to the SQLite database file.
    db_path: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", os.path.join(self.data_dir, "mdshare.db"))


# Singleton instance — imported by all layers.
config = Config()
