"""Display configuration — global defaults from YAML, per-share overrides.

Follows the same frozen-dataclass + module-level-singleton pattern as
:mod:`backend.config`.
"""

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict

import yaml

from backend.config import config

_logger = logging.getLogger(__name__)

# ── Valid values ──────────────────────────────────────────────────────────────

_VALID_THEMES = frozenset({"light", "dark", "auto"})

_KNOWN_KEYS = frozenset(
    {
        "font_family",
        "font_size",
        "line_height",
        "max_width",
        "theme",
        "code_font_size",
        "code_line_numbers",
        "custom_css",
    }
)

_STRING_KEYS = frozenset(
    {
        "font_family",
        "font_size",
        "line_height",
        "max_width",
        "theme",
        "code_font_size",
        "custom_css",
    }
)

_DEFAULTS: Dict[str, Any] = {
    "font_family": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "font_size": "16px",
    "line_height": "1.6",
    "max_width": "900px",
    "theme": "auto",
    "code_font_size": "14px",
    "code_line_numbers": False,
    "custom_css": "",
}


# ── DisplayConfig — immutable frozen dataclass ────────────────────────────────


@dataclass(frozen=True)
class DisplayConfig:
    """Immutable display configuration loaded from YAML at import time.

    Singleton instance ``display_config`` is created at module level so that
    ``from backend.display_config import display_config`` works everywhere.
    """

    font_family: str = _DEFAULTS["font_family"]  # type: ignore[assignment]
    font_size: str = _DEFAULTS["font_size"]  # type: ignore[assignment]
    line_height: str = _DEFAULTS["line_height"]  # type: ignore[assignment]
    max_width: str = _DEFAULTS["max_width"]  # type: ignore[assignment]
    theme: str = _DEFAULTS["theme"]  # type: ignore[assignment]
    code_font_size: str = _DEFAULTS["code_font_size"]  # type: ignore[assignment]
    code_line_numbers: bool = _DEFAULTS["code_line_numbers"]  # type: ignore[assignment]
    custom_css: str = _DEFAULTS["custom_css"]  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.theme not in _VALID_THEMES:
            raise ValueError(
                f"theme must be one of {sorted(_VALID_THEMES)!r}, got {self.theme!r}"
            )
        if not isinstance(self.code_line_numbers, bool):
            raise ValueError(
                f"code_line_numbers must be bool, got "
                f"{type(self.code_line_numbers).__name__}"
            )
        for key in _STRING_KEYS:
            value = getattr(self, key)
            if not isinstance(value, str):
                raise ValueError(
                    f"{key} must be a string, got {type(value).__name__}"
                )

    def get_defaults(self) -> Dict[str, Any]:
        """Return all eight display-config values as a dictionary."""
        return {key: getattr(self, key) for key in _DEFAULTS}


# ── Standalone helpers ────────────────────────────────────────────────────────


def _resolve_config_path() -> str:
    """Return the path to the display YAML config file."""
    env_path = os.environ.get("MDSHARE_DISPLAY_CONFIG")
    if env_path:
        return env_path
    return os.path.join(config.data_dir, "display.yaml")


def _get_mtime(path: str) -> float | None:
    """Return file modification time, or ``None`` if the file doesn't exist."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _load_yaml(path: str) -> Dict[str, Any]:
    """Load YAML file and merge with defaults. Returns defaults on error."""
    raw = _read_yaml_file(path)
    if raw is None:
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    for key, value in raw.items():
        if key in _KNOWN_KEYS:
            merged[key] = value
    return merged


def _read_yaml_file(path: str) -> Dict[str, Any] | None:
    """Read and parse a YAML file, returning ``None`` on any error."""
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except (yaml.YAMLError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


# ── DisplayConfigCache — thread-safe, reload-on-read singleton ────────────────


class DisplayConfigCache:
    """Cached singleton that holds the current :class:`DisplayConfig` and
    transparently reloads it when the YAML file changes on disk.

    Thread-safe for Gunicorn's ``--threads 4`` gthread worker model.
    """

    _instance: "DisplayConfigCache | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "DisplayConfigCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialize()
                    cls._instance = obj
        return cls._instance

    # ── instance attributes (set in _initialize) ────────────────────────────

    _config: DisplayConfig
    _path: str
    _mtime: float | None  # None = file didn't exist at last check

    def _initialize(self) -> None:
        """First-load: read YAML, build DisplayConfig, record mtime."""
        self._path = _resolve_config_path()
        data = _load_yaml(self._path)
        self._config = DisplayConfig(**data)
        self._mtime = _get_mtime(self._path)
        _logger.info("Display config loaded from %s", self._path)

    def _load(self, raw: Dict[str, Any]) -> DisplayConfig:
        """Validate raw values and return a new :class:`DisplayConfig`."""
        merged = dict(_DEFAULTS)
        for key, value in raw.items():
            if key in _KNOWN_KEYS:
                merged[key] = value
        return DisplayConfig(**merged)

    def _check_and_reload(self) -> None:
        """Compare current mtime with stored mtime; reload if changed."""
        current_mtime = _get_mtime(self._path)
        if current_mtime == self._mtime:
            return

        if not os.path.exists(self._path):
            # File was removed — revert to hardcoded defaults.
            self._config = DisplayConfig(**_DEFAULTS)
            self._mtime = None
            _logger.info("Display config reset to defaults (%s removed)", self._path)
            return

        raw = _read_yaml_file(self._path)
        if raw is None:
            # File is unparseable — keep old config, but avoid re-trying on every access.
            _logger.error(
                "Failed to parse display config %s; keeping current values",
                self._path,
            )
            self._mtime = current_mtime
            return

        try:
            new_config = self._load(raw)
        except Exception:
            _logger.exception(
                "Invalid display config in %s; keeping current values",
                self._path,
            )
            self._mtime = current_mtime  # avoid re-trying on every access
            return

        self._config = new_config
        self._mtime = current_mtime
        _logger.info("Reloaded display config from %s", self._path)

    def get_defaults(self) -> Dict[str, Any]:
        """Return current eight display-config values (auto-reloads if needed)."""
        with self._lock:
            self._check_and_reload()
            return self._config.get_defaults()


# Singleton — created immediately at import time.
display_config = DisplayConfigCache()
