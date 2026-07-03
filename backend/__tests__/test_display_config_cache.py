"""Unit tests for DisplayConfigCache — file-watching, thread-safe singleton."""

import os
import threading
import time
from pathlib import Path

import pytest
import yaml

from backend.config import config
from backend.display_config import DisplayConfigCache, _DEFAULTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> None:
    """Write YAML dict to *path* and wait for filesystem mtime to settle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))
    time.sleep(0.02)


def _clean_yaml(path: Path) -> None:
    """Remove the YAML file if it exists."""
    if path.exists():
        path.unlink()
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset singleton before each test so state doesn't leak between tests."""
    DisplayConfigCache._instance = None
    yield


@pytest.fixture
def yaml_path() -> Path:
    """Path to the display YAML inside the ephemeral test data directory."""
    return Path(config.data_dir) / "display.yaml"


@pytest.fixture
def cache(yaml_path):
    """Return a fresh ``DisplayConfigCache`` singleton instance.

    Ensures no YAML file exists at first-load time so the cache starts
    with hardcoded defaults.
    """
    _clean_yaml(yaml_path)
    return DisplayConfigCache()


# ---------------------------------------------------------------------------
# Tests — defaults when no YAML file exists
# ---------------------------------------------------------------------------


class TestDefaults:
    """Behaviour when no ``display.yaml`` file exists."""

    def test_defaults_when_no_yaml(self, cache):
        """``get_defaults()`` returns hardcoded defaults when no YAML exists."""
        result = cache.get_defaults()
        assert result == _DEFAULTS

    def test_logs_no_yaml_on_startup(self, yaml_path, caplog):
        """INFO log emitted when no YAML is found on first load."""
        _clean_yaml(yaml_path)

        DisplayConfigCache._instance = None
        with caplog.at_level("INFO"):
            DisplayConfigCache()

        assert "Display config loaded from" in caplog.text
        assert str(yaml_path) in caplog.text


# ---------------------------------------------------------------------------
# Tests — valid custom YAML
# ---------------------------------------------------------------------------


class TestCustomYaml:
    """Behaviour when a valid ``display.yaml`` file exists."""

    def test_loads_custom_yaml(self, yaml_path):
        """Creating a valid ``display.yaml`` changes ``get_defaults()`` output."""
        _write_yaml(yaml_path, {"font_size": "18px", "theme": "dark"})

        cache = DisplayConfigCache()
        result = cache.get_defaults()

        assert result["font_size"] == "18px"
        assert result["theme"] == "dark"
        # Unset fields stay at defaults
        assert result["font_family"] == _DEFAULTS["font_family"]

    def test_logs_overrides_on_load(self, yaml_path, caplog):
        """INFO log emitted with config path on successful load."""
        _write_yaml(yaml_path, {"theme": "dark"})

        DisplayConfigCache._instance = None
        with caplog.at_level("INFO"):
            DisplayConfigCache()

        assert "Display config loaded from" in caplog.text
        assert str(yaml_path) in caplog.text

    def test_unknown_keys_ignored(self, yaml_path):
        """YAML keys outside the 8 known keys are silently stripped."""
        _write_yaml(yaml_path, {"theme": "light", "unknown_key": "should_be_ignored"})

        cache = DisplayConfigCache()
        result = cache.get_defaults()

        assert result["theme"] == "light"
        assert "unknown_key" not in result
        assert len(result) == len(_DEFAULTS)

    def test_singleton_identity(self):
        """Two calls to ``DisplayConfigCache()`` return the same object."""
        c1 = DisplayConfigCache()
        c2 = DisplayConfigCache()
        assert c1 is c2


# ---------------------------------------------------------------------------
# Tests — runtime file watching
# ---------------------------------------------------------------------------


class TestFileWatching:
    """Behaviour when the YAML file changes at runtime."""

    def test_detects_file_change(self, yaml_path):
        """Modifying ``display.yaml`` mid-process updates ``get_defaults()``."""
        _write_yaml(yaml_path, {"font_size": "16px"})
        cache = DisplayConfigCache()

        # Initial value
        assert cache.get_defaults()["font_size"] == "16px"

        # Modify file
        _write_yaml(yaml_path, {"font_size": "22px"})

        # Should detect change and return new value
        result = cache.get_defaults()
        assert result["font_size"] == "22px"

    def test_keeps_old_on_invalid_yaml(self, yaml_path):
        """Writing broken YAML doesn't corrupt cached config."""
        _write_yaml(yaml_path, {"font_size": "18px"})
        cache = DisplayConfigCache()
        assert cache.get_defaults()["font_size"] == "18px"

        # Overwrite with syntactically invalid YAML
        yaml_path.write_text("{invalid: yaml: unquoted: [}\n")
        time.sleep(0.02)

        # Old config is preserved
        result = cache.get_defaults()
        assert result["font_size"] == "18px"

    def test_keeps_old_on_invalid_values(self, yaml_path):
        """Writing a bad value like ``theme: neon`` keeps the last good config."""
        _write_yaml(yaml_path, {"font_size": "18px"})
        cache = DisplayConfigCache()
        assert cache.get_defaults()["font_size"] == "18px"

        # Overwrite with valid YAML but semantically invalid values
        _write_yaml(yaml_path, {"theme": "neon"})

        # Old config survives because ``neon`` fails ``DisplayConfig`` validation
        result = cache.get_defaults()
        assert result["font_size"] == "18px"
        assert result["theme"] == "auto"  # default (was never changed)

    def test_logs_error_on_invalid_yaml(self, yaml_path, caplog):
        """ERROR log emitted when invalid YAML is encountered."""
        _write_yaml(yaml_path, {"font_size": "18px"})
        cache = DisplayConfigCache()
        cache.get_defaults()  # prime the cache

        # Write invalid YAML (valid UTF-8, but unparseable by PyYAML)
        yaml_path.write_text("[[[: invalid\n")
        time.sleep(0.02)

        with caplog.at_level("ERROR"):
            cache.get_defaults()

        assert "Failed to parse" in caplog.text
        assert str(yaml_path) in caplog.text

    def test_reverts_on_file_removal(self, yaml_path):
        """Deleting ``display.yaml`` reverts to hardcoded defaults."""
        _write_yaml(yaml_path, {"font_size": "24px", "theme": "dark"})
        cache = DisplayConfigCache()

        # Verify custom config was loaded
        assert cache.get_defaults()["font_size"] == "24px"
        assert cache.get_defaults()["theme"] == "dark"

        # Delete YAML file
        yaml_path.unlink()
        time.sleep(0.02)

        # Should revert to hardcoded defaults
        result = cache.get_defaults()
        assert result["font_size"] == _DEFAULTS["font_size"]
        assert result["theme"] == _DEFAULTS["theme"]

    def test_logs_info_on_revert(self, yaml_path, caplog):
        """INFO log emitted when config reverts to defaults after file removal."""
        _write_yaml(yaml_path, {"font_size": "24px"})
        cache = DisplayConfigCache()
        cache.get_defaults()  # prime

        yaml_path.unlink()
        time.sleep(0.02)

        with caplog.at_level("INFO"):
            cache.get_defaults()

        assert "reset to defaults" in caplog.text
        assert str(yaml_path) in caplog.text


# ---------------------------------------------------------------------------
# Tests — thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Concurrent access safety under Gunicorn's threaded worker model."""

    def test_thread_safety(self, yaml_path):
        """Concurrent ``get_defaults()`` calls never see partial state."""
        _write_yaml(yaml_path, {"theme": "dark"})
        cache = DisplayConfigCache()
        cache.get_defaults()  # prime

        errors: list[Exception] = []
        lock = threading.Lock()

        def reader() -> None:
            try:
                for _ in range(50):
                    result = cache.get_defaults()
                    # Must always be a complete dict with all expected keys
                    assert set(result.keys()) == set(_DEFAULTS.keys())
                    # All values must be of the expected types
                    assert isinstance(result["theme"], str)
                    assert isinstance(result["font_size"], str)
                    assert isinstance(result["code_line_numbers"], bool)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety failures: {errors}"
