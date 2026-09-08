"""
Inject this plugin's env-var defaults from ``config.json`` without touching the
host ``.env``.

Values are applied with set-if-absent semantics: the real environment (.env / host
/ docker-compose) ALWAYS wins; ``config.json`` only fills keys that are unset. Empty
values are skipped so they never clobber code-level defaults. Must run once at plugin
registration, BEFORE ``command_guard`` is imported (it reads the allowlists at import
time). Never raises — a missing or malformed file is a no-op.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("whiskers.plugins")

_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_env_defaults(path: Path | None = None) -> dict[str, str]:
    """Apply config.json env defaults (host env wins, empty skipped); return applied keys."""
    cfg_path = path or _CONFIG_PATH
    applied: dict[str, str] = {}
    if not cfg_path.exists():
        return applied
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("cat_terminal_relay_plugin: ignoring bad config.json: %s", exc)
        return applied

    for section_name, section in data.items():
        # Skip metadata keys (e.g. _comment) and non-dict sections.
        if section_name.startswith("_") or not isinstance(section, dict):
            continue
        for key, value in section.items():
            if value is None:
                continue
            val = str(value).strip()
            if not val:
                continue
            if key in os.environ:  # host / .env wins
                continue
            os.environ[key] = val
            applied[key] = val

    if applied:
        logger.info("cat_terminal_relay_plugin: injected env defaults: %s", list(applied))
    return applied
