"""Authentication helpers — master-password (Bearer token) and view-password (bcrypt) verification."""

import secrets

import bcrypt

from backend.config import config


def verify_master_password(token: str | None) -> bool:
    """Verify a Bearer token against the configured master password.

    Uses ``secrets.compare_digest`` for timing-safe comparison.

    Returns ``True`` if the token matches, ``False`` otherwise (including
    when ``config.master_password`` is empty — unauthenticated requests
    are rejected).
    """
    if not config.master_password or not token:
        return False
    return secrets.compare_digest(token, config.master_password)


def verify_view_password(password: str | None, bcrypt_hash: str | None) -> bool:
    """Verify a view password against a bcrypt hash.

    * If the share has no password (``bcrypt_hash`` is ``None``) → no
      auth required → returns ``True``.
    * If a password is required but not provided → returns ``False``.
    * Otherwise uses ``bcrypt.checkpw`` to verify.
    """
    if bcrypt_hash is None:
        return True
    if not password:
        return False
    try:
        return bcrypt.checkpw(password.encode(), bcrypt_hash.encode())
    except (ValueError, AttributeError):
        return False
