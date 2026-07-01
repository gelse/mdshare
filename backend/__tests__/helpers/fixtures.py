"""Test helper factories for creating shares in test scenarios."""

import bcrypt
from pathlib import Path


def make_document(
    *,
    doc_id: str = "test123456789",
    content: str = "# Test Document",
    password: str | None = None,
    valid_until: str | None = None,
) -> dict:
    """Build a document dict matching the SQLite storage schema.

    Parameters
    ----------
    doc_id:
        The share identifier (12-char random string in production).
    content:
        Markdown content of the share.
    password:
        If provided, the *cleartext* view password. Will be bcrypt-hashed
        before storage. Pass ``None`` for a public share.
    valid_until:
        ISO 8601 UTC datetime string indicating when the share expires.
        Pass ``None`` (default) for a share that never expires.
    """
    doc: dict = {
        "content": content,
    }
    if password is not None:
        doc["password"] = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()
    if valid_until is not None:
        doc["valid_until"] = valid_until
    return doc


def write_document(doc_id: str, doc: dict) -> None:
    """Persist a document dict directly into the SQLite storage backend.

    Uses the same ``get_storage()`` factory that the application uses,
    so tests operate against the real storage layer.
    """
    from backend.storage import get_storage

    storage = get_storage()
    storage.create(doc_id, doc)


def make_share_form(
    *,
    content: str = "# Hello",
    protected: str = "no",
    content_type: str = "multipart/form-data",
) -> tuple[dict, str]:
    """Return (data, content_type) suitable for a PUT /api/share request.

    Parameters
    ----------
    content:
        Markdown content string.
    protected:
        ``"yes"`` or ``"no"``.
    content_type:
        The HTTP Content-Type to use for the request body.
    """
    data = {
        "content": content,
        "protected": protected,
    }
    return data, content_type
