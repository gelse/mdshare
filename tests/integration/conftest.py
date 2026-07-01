"""Pytest fixtures for mdshare integration tests.

Provides a session-scoped ``integration_base_url`` fixture that:

1. Reads the ``DEPLOYMENT_HOST`` environment variable
2. Polls ``/api/health`` until the deployment is ready
3. Yields the base URL

Tests are skipped if ``DEPLOYMENT_HOST`` is not set.

The deployment is expected to be managed externally (e.g. manually deployed
to a server or started via docker-compose). This fixture **never** builds
Docker images or manages containers — it only waits for the deployment to
become healthy.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

HEALTH_POLL_INTERVAL = 0.5  # seconds
HEALTH_TIMEOUT = 30.0  # seconds


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _wait_for_health(base_url: str, timeout: float = HEALTH_TIMEOUT) -> None:
    """Poll ``GET /api/health`` until the endpoint responds with 200.

    Raises ``RuntimeError`` if the endpoint does not become healthy within
    *timeout* seconds.
    """
    deadline = time.monotonic() + timeout
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/health", timeout=2.0)
            if resp.status_code == 200:
                return  # deployment is ready
            last_error = f"HTTP {resp.status_code}"
        except httpx.RequestError as exc:
            last_error = str(exc)
        time.sleep(HEALTH_POLL_INTERVAL)

    raise RuntimeError(
        f"Deployment did not become healthy within {timeout}s. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# session-scoped fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def integration_base_url() -> str:
    """Return the base URL of an externally-deployed mdshare instance.

    The URL is read from the ``DEPLOYMENT_HOST`` environment variable.
    If the variable is not set, all integration tests are skipped.

    Before yielding, the fixture polls ``/api/health`` to confirm the
    deployment is reachable and responding correctly.
    """
    host = os.environ.get("DEPLOYMENT_HOST")
    if not host:
        pytest.skip(
            "DEPLOYMENT_HOST is not set \u2014 skipping integration tests. "
            "Set DEPLOYMENT_HOST to the base URL of a running mdshare instance "
            "(e.g. http://192.168.1.100:5000)."
        )

    # Strip trailing slash for consistency
    host = host.rstrip("/")

    # Wait until the deployment is actually healthy
    _wait_for_health(host)

    return host
