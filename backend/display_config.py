"""Display configuration — global defaults from YAML, per-share overrides.

Follows the same frozen-dataclass + module-level-singleton pattern as
:mod:`backend.config`.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict

import yaml

from backend.config import config

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
        "code_font_size",
        "custom_css",
    }
)

# ── Defaults (used when no YAML file is present) ─────────────────────────────

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


def _resolve_config_path() -> str:
    """Return the path to the display YAML config file."""
    env_path = os.environ.get("MDSHARE_DISPLAY_CONFIG")
    if env_path:
        return env_path
    return os.path.join(config.data_dir, "display.yaml")


def _load_yaml_config() -> Dict[str, Any]:
    """Load display config from YAML, merge with defaults, strip unknown keys."""
    result = dict(_DEFAULTS)
    path = _resolve_config_path()

    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        return result
    except (yaml.YAMLError, OSError):
        return result

    if not isinstance(raw, dict):
        return result

    for key, value in raw.items():
        if key in _KNOWN_KEYS:
            result[key] = value

    return result


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


# Singleton — created immediately at import time.
_config_data = _load_yaml_config()
display_config = DisplayConfig(**_config_data)
