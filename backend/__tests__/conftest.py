"""Pytest fixtures and configuration for mdshare backend tests."""

import os
import shutil
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Set environment variables BEFORE importing the app module.
# The config singleton reads env vars at import time — these must be
# in place before any backend imports happen.
# ---------------------------------------------------------------------------

os.environ["MDSHARE_MASTER_PASSWORD"] = "test-master-password"

_test_data_dir = tempfile.mkdtemp(prefix="mdshare_test_")
os.environ["MDSHARE_DATA_DIR"] = _test_data_dir

from backend.config import config  # noqa: E402
from backend.app import app as flask_app  # noqa: E402
from backend.services.share_service import share_service  # noqa: E402


# ---------------------------------------------------------------------------
# session‑scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def test_data_dir():
    """Clean up the temporary data directory after the test session ends."""
    yield _test_data_dir
    # Close the storage backend through the service layer
    share_service.storage.close()
    shutil.rmtree(_test_data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# function‑scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Flask test client configured for the minimal mdshare app."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as tc:
        yield tc


@pytest.fixture(autouse=True)
def reset_storage():
    """Wipe SQLite storage and image directories between tests."""
    storage = share_service.storage
    conn = storage._conn
    conn.execute("DELETE FROM shares")
    conn.commit()
    # Clean up any image directories left over from previous test
    images_root = os.path.join(config.data_dir, "images")
    if os.path.isdir(images_root):
        shutil.rmtree(images_root)
    yield
    # Also clean up after the test
    conn.execute("DELETE FROM shares")
    conn.commit()
    if os.path.isdir(images_root):
        shutil.rmtree(images_root)
