# Display Config File Watcher & Cached Singleton

## Overview

Refactor [`backend/display_config.py`](../backend/display_config.py) so that the global
`display.yaml` config file is:

1. **Loaded and logged at startup** — when a custom `display.yaml` is found, the loaded
   settings are logged at INFO level.
2. **Cached in a thread-safe singleton** — all consumers access the config exclusively
   through `DisplayConfigCache.get_defaults()`.
3. **Hot-reloaded on file change** — when `display.yaml` is modified on disk, the new
   values are read, validated, and swapped into the cache. Invalid changes are rejected
   (old values stay) and logged at ERROR level.

---

## Architecture

```
                    ┌──────────────────────────────┐
                    │     DisplayConfigCache        │  (singleton, thread-safe)
                    │                              │
   get_defaults() ──│  _check_and_reload()         │
                    │    ├─ os.path.getmtime()      │  ← checks on every call
                    │    └─ _load() if changed      │
                    │         ├─ _read_yaml_file()  │
                    │         ├─ DisplayConfig()    │  ← frozen dataclass (unchanged)
                    │         └─ logging            │
                    │                              │
                    │  _current: DisplayConfig      │  ← cached, swapped atomically
                    │  _lock: threading.Lock        │
                    │  _last_mtime: float           │
                    └──────────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Change detection | `os.path.getmtime` on every `get_defaults()` call | No background thread needed; `stat()` is ~1µs; avoids Gunicorn preload/fork issues |
| Singleton | Class with `__new__` returning cached instance | Standard Python singleton; thread-safe |
| Thread safety | `threading.Lock` around cache swap | Gunicorn `--threads 4` (gthread worker) |
| Backward compat | `display_config.get_defaults()` API unchanged | [`share_service.py`](../backend/services/share_service.py:244) needs zero changes |
| Logging | `logging.getLogger(__name__)` | stdlib, no new dependency |
| `DisplayConfig` | Frozen dataclass kept exactly as-is | Validation logic reused; only the _caching layer_ changes |

---

## Implementation Steps

### Step 1 — Add logging

```python
import logging
import threading

_logger = logging.getLogger(__name__)
```

### Step 2 — Extract `_read_yaml_file()` helper

```python
def _read_yaml_file(path: str) -> dict:
    """Read and parse a YAML file.  Raises FileNotFoundError, yaml.YAMLError, OSError."""
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}
```

### Step 3 — Create `DisplayConfigCache` class

```python
class DisplayConfigCache:
    """Thread-safe singleton cache for DisplayConfig with file-change detection."""

    _instance: "DisplayConfigCache | None" = None

    def __new__(cls) -> "DisplayConfigCache":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        self._lock = threading.Lock()
        self._yaml_path = os.path.join(config.data_dir, "display.yaml")
        self._last_mtime: float = 0.0
        self._current: DisplayConfig = DisplayConfig(**_DEFAULTS)
        self._load(initial=True)

    def _load(self, initial: bool = False) -> None:
        """Read YAML, validate, and update _current.  Logs outcome."""
        try:
            yaml_data = _read_yaml_file(self._yaml_path)
        except FileNotFoundError:
            if initial:
                _logger.info(
                    "No custom display.yaml found at %s — using defaults",
                    self._yaml_path,
                )
            else:
                _logger.info("display.yaml removed — reverting to defaults")
            with self._lock:
                self._current = DisplayConfig(**_DEFAULTS)
                self._last_mtime = 0.0
            return
        except (yaml.YAMLError, OSError) as exc:
            _logger.error(
                "Failed to parse display.yaml: %s — using %s",
                exc,
                "defaults" if initial else "previous config",
            )
            if initial:
                with self._lock:
                    self._current = DisplayConfig(**_DEFAULTS)
            return

        # Merge YAML values over hardcoded defaults (known keys only).
        merged = dict(_DEFAULTS)
        for key, value in (yaml_data or {}).items():
            if key in _KNOWN_KEYS:
                merged[key] = value

        # Validate via the frozen dataclass constructor.
        try:
            new_config = DisplayConfig(**merged)
        except ValueError as exc:
            _logger.error(
                "Invalid display.yaml values: %s — keeping %s",
                exc,
                "defaults" if initial else "previous config",
            )
            if initial:
                with self._lock:
                    self._current = DisplayConfig(**_DEFAULTS)
            return

        # Atomically swap cached config.
        with self._lock:
            self._current = new_config
            try:
                self._last_mtime = os.path.getmtime(self._yaml_path)
            except OSError:
                self._last_mtime = 0.0

        # Log which settings differ from hardcoded defaults.
        overridden = {k: v for k, v in merged.items() if v != _DEFAULTS[k]}
        if overridden:
            _logger.info(
                "Loaded display config from %s — overrides: %s",
                self._yaml_path,
                overridden,
            )
        else:
            _logger.info(
                "Loaded display config from %s — all values are defaults",
                self._yaml_path,
            )

    def _check_and_reload(self) -> None:
        """If display.yaml mtime changed since last load, re-read it."""
        try:
            current_mtime = os.path.getmtime(self._yaml_path)
        except OSError:
            current_mtime = 0.0
        if current_mtime != self._last_mtime:
            self._load(initial=False)

    def get_defaults(self) -> Dict[str, Any]:
        """Return all eight display-config values, re-reading YAML if changed."""
        self._check_and_reload()
        with self._lock:
            return self._current.get_defaults()
```

### Step 4 — Replace module-level singleton

**Remove:**
```python
_config_data = _load_yaml_config()
display_config = DisplayConfig(**_config_data)
```

**Replace with:**
```python
display_config = DisplayConfigCache()
```

### Step 5 — Keep unchanged

- `DisplayConfig` frozen dataclass (including `__post_init__` and `get_defaults()`)
- `_DEFAULTS`, `_KNOWN_KEYS`, `_VALID_THEMES`, `_STRING_KEYS` constants
- The old `_load_yaml_config()` function is **removed** (its logic moves into `DisplayConfigCache._load`)

### Step 6 — New tests: `backend/__tests__/test_display_config_cache.py`

| Test | What it verifies |
|------|-----------------|
| `test_defaults_when_no_yaml` | `get_defaults()` returns hardcoded defaults when no `display.yaml` exists |
| `test_loads_custom_yaml` | Creating a valid `display.yaml` changes `get_defaults()` output |
| `test_logs_overrides_on_load` | INFO log emitted with override details (use `caplog`) |
| `test_logs_no_yaml_on_startup` | INFO log emitted when no YAML is found on first load |
| `test_detects_file_change` | Modifying `display.yaml` mid-process updates `get_defaults()` |
| `test_keeps_old_on_invalid_yaml` | Writing broken YAML doesn't corrupt cached config |
| `test_keeps_old_on_invalid_values` | Writing `theme: neon` doesn't corrupt cached config |
| `test_logs_error_on_invalid_yaml` | ERROR log emitted when invalid YAML is written |
| `test_reverts_on_file_removal` | Deleting `display.yaml` reverts to hardcoded defaults |
| `test_thread_safety` | Concurrent `get_defaults()` calls never see partial state |
| `test_singleton_identity` | Two imports return the same object |
| `test_unknown_keys_ignored` | YAML keys outside the 8 known keys are silently stripped |

### Step 7 — Verify no regressions

```bash
make ci-unit-test
```

This builds the Docker test image and runs the full pytest suite (including existing
`test_display_config.py` HTTP-level tests).

### Step 8 — Update `docs/TASK_LOG.md`

Document the change with date and summary.

---

## Files Changed

| File | Change |
|------|--------|
| [`backend/display_config.py`](../backend/display_config.py) | Major refactor — add `DisplayConfigCache` class, logging, mtime checking; remove `_load_yaml_config()` |
| [`backend/__tests__/test_display_config_cache.py`](../backend/__tests__/test_display_config_cache.py) | **New file** — 12 unit tests for cache/watcher behavior |
| [`backend/__tests__/conftest.py`](../backend/__tests__/conftest.py) | Minor — optional `DisplayConfigCache._instance = None` in a fixture for test isolation |
| [`docs/TASK_LOG.md`](../docs/TASK_LOG.md) | Append change entry |

## Files NOT Changed

| File | Reason |
|------|--------|
| [`backend/services/share_service.py`](../backend/services/share_service.py) | `display_config.get_defaults()` API is unchanged |
| [`backend/app.py`](../backend/app.py) | No direct `display_config` usage |
| [`backend/__tests__/test_display_config.py`](../backend/__tests__/test_display_config.py) | HTTP-level tests pass as-is |
| [`backend/requirements.txt`](../backend/requirements.txt) | No new dependencies (stdlib only) |
