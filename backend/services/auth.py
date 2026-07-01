"""Authentication helpers — master-password (Bearer token) and view-password (bcrypt) verification."""

import secrets

import bcrypt

from backend.config import config


def extract_bearer_token(header_value: str) -> str | None:
    """Parse a Bearer token from an ``Authorization`` header value.

    Returns the token string if the header starts with ``"Bearer "``,
    or ``None`` if the header is missing, malformed, or uses a different
    scheme.  An empty token after the ``"Bearer "`` prefix is also
    treated as ``None``.
    """
    if not header_value or not header_value.startswith("Bearer "):
        return None
    token = header_value[7:]  # strip "Bearer " prefix
    return token or None


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
