"""SQLite storage backend for mdshare — single-table design."""

import json
import sqlite3
import threading
import os
import shutil
from .abstract import StorageBackend
from backend.config import config


class SqliteStorage(StorageBackend):
    """SQLite-backed share storage.

    Schema::

        CREATE TABLE shares (
            id              TEXT PRIMARY KEY,
            content         TEXT NOT NULL,
            password        TEXT,           -- bcrypt hash, NULL if public
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            valid_until     TEXT,           -- ISO 8601 UTC expiry, NULL = never
            display_config  TEXT            -- JSON dict of per-share display overrides, NULL = use global defaults
        );
    """

    def __init__(self) -> None:
        os.makedirs(config.data_dir, exist_ok=True)
        # Verify write access to the data directory before attempting DB init.
        # Docker named volumes may not inherit build-time ownership, causing
        # a cryptic "unable to open database file" from sqlite3.connect().
        if not os.access(config.data_dir, os.W_OK):
            raise PermissionError(
                f"Cannot write to data directory '{config.data_dir}'. "
                f"The application user (UID {os.getuid()}) does not have "
                f"write permission. Ensure the volume mount is owned by "
                f"this user, or set MDSHARE_DATA_DIR to a writable path."
            )
        self._local = threading.local()
        self._init_schema()

    # ------------------------------------------------------------------
    # database connection
    # ------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(config.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                password    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                valid_until TEXT
            )
            """
        )
        # Migration: add valid_until column to existing databases.
        # This is a no-op if the column already exists.
        try:
            self._conn.execute("ALTER TABLE shares ADD COLUMN valid_until TEXT")
        except sqlite3.OperationalError:
            pass
        # Migration: add display_config column to existing databases.
        try:
            self._conn.execute("ALTER TABLE shares ADD COLUMN display_config TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_display_config(d: dict | None) -> str | None:
        """Serialize a display_config dict to JSON string (or None)."""
        if d is None:
            return None
        return json.dumps(d, separators=(",", ":"))

    @staticmethod
    def _deserialize_display_config(s: str | None) -> dict | None:
        """Deserialize a JSON string to a display_config dict (or None)."""
        if s is None:
            return None
        return json.loads(s)

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def create(self, doc_id: str, doc: dict) -> None:
        """Insert a new share."""
        self._conn.execute(
            "INSERT INTO shares (id, content, password, valid_until, display_config) VALUES (?, ?, ?, ?, ?)",
            (
                doc_id,
                doc["content"],
                doc.get("password"),
                doc.get("valid_until"),
                self._serialize_display_config(doc.get("display_config")),
            ),
        )
        self._conn.commit()

    def get(self, doc_id: str) -> dict | None:
        """Retrieve a share by ID, or None.

        Implements the lazy-expiry pattern: if the share exists but its
        ``valid_until`` timestamp has passed, the row and any associated
        image files are deleted and None is returned.
        """
        row = self._conn.execute(
            "SELECT id, content, password, created_at, valid_until, display_config FROM shares WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None

        result = dict(row)

        # Parse display_config from JSON if present
        result["display_config"] = self._deserialize_display_config(
            result.get("display_config")
        )

        # Lazy expiry check
        valid_until = result.get("valid_until")
        if valid_until is not None:
            from datetime import datetime, timezone

            expiry = datetime.fromisoformat(valid_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expiry:
                self.delete(doc_id)
                return None

        return result

    def delete(self, doc_id: str) -> None:
        """Delete a share and its associated resources (internal use only).

        Removes the database row and the image directory if it exists.
        """
        self._conn.execute("DELETE FROM shares WHERE id = ?", (doc_id,))
        self._conn.commit()
        images_dir = os.path.join(config.data_dir, "images", doc_id)
        if os.path.isdir(images_dir):
            shutil.rmtree(images_dir)

    def update_valid_until(self, ids: list[str], valid_until: str | None) -> int:
        """Batch-update ``valid_until`` for the given share IDs.

        Uses a single parameterized ``UPDATE`` with an ``IN (... )`` clause.
        Returns the number of rows affected.
        """
        placeholders = ", ".join("?" for _ in ids)
        sql = f"UPDATE shares SET valid_until = ? WHERE id IN ({placeholders})"
        cursor = self._conn.execute(sql, [valid_until] + ids)
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the database connection for the current thread.

        Safe to call even if no connection has been opened yet.
        """
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def exists(self, doc_id: str) -> bool:
        """Check whether a share ID exists."""
        row = self._conn.execute(
            "SELECT 1 FROM shares WHERE id = ?", (doc_id,)
        ).fetchone()
        return row is not None

    def list_active(self, page_size: int = 50, page: int = 1) -> tuple[list[dict], int]:
        """Return a page of metadata for all non-expired shares.

        Expired shares (valid_until IS NOT NULL and <= now) are
        excluded. Shares with valid_until = NULL never expire.

        Returns:
            A tuple of (list of share dicts, total count of matching rows).
        """
        # Total count first
        (total_count,) = self._conn.execute(
            "SELECT COUNT(*) FROM shares "
            "WHERE valid_until IS NULL OR valid_until > datetime('now')"
        ).fetchone()

        # Paginated data query
        offset = (page - 1) * page_size
        rows = self._conn.execute(
            "SELECT id, created_at, valid_until, password "
            "FROM shares "
            "WHERE valid_until IS NULL OR valid_until > datetime('now') "
            "ORDER BY created_at DESC "
            "LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()

        result: list[dict] = []
        for id_, created_at, valid_until, password_hash in rows:
            result.append({
                "id": id_,
                "created_at": created_at,
                "valid_until": valid_until,
                "protected": password_hash is not None,
            })
        return result, total_count
