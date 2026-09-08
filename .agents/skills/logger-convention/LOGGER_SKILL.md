---
name: logger-convention
description: '**CODE PATTERN SKILL** — Logger naming convention for the Whiskers Agent Server. USE FOR: understanding the "whiskers" logger namespace, renaming legacy "legacy-mcp" loggers, auditing logger usage across plugins, verifying compliance, setting up logging in new modules. DO NOT USE FOR: logging configuration (transports, handlers, levels) or Python logging architecture in general.'
argument-hint: 'Optional: specify a file or plugin folder to audit'
---

# Logger Convention — `whiskers` Namespace

## Overview

All Python modules in the Whiskers Agent Server must use the `"whiskers"` logger namespace.
The legacy `"legacy-mcp"` name is **retired** (CLAUDE.md Rule #14).

> **Rule #14** (CLAUDE.md): *Use `"whiskers"` and `"whiskers.plugins"` everywhere. `"legacy-mcp"` is retired.*

---

## Correct Patterns

| Context | Logger name | Import line |
|---|---|---|
| Top-level modules (`core/`, `utils/`, `oauth/`, `db_layer/`) | `"whiskers"` | `logger = logging.getLogger("whiskers")` |
| Plugin modules (`plugins/<name>/`) | `"whiskers.plugins"` | `logger = logging.getLogger("whiskers.plugins")` |
| Any new module | use the closest of the two above | — |

```python
# ✅ Correct — top-level module
import logging
logger = logging.getLogger("whiskers")

# ✅ Correct — plugin module
import logging
logger = logging.getLogger("whiskers.plugins")

# ❌ Retired — do NOT use
logger = logging.getLogger("legacy-mcp")
```

---

## When to Apply This Skill

- Creating a **new tool module** or plugin
- Reviewing a **diff / PR** that adds logging calls
- Running an **architecture audit** (the audit skill flags logger violations)
- After **generating code** via `tools_generator.py` or `semantic_tools_generator.py` (generators may emit `"legacy-mcp"` in templates)

---

## Audit Procedure

### 1. Find all violations in the repo

```powershell
# From repo root (Whiskers Agent repo)
Select-String -Path "**\*.py" -Pattern '"legacy-mcp"' -Recurse
```

Or with `grep` on Linux/macOS:

```bash
grep -r '"legacy-mcp"' --include="*.py" .
```

### 2. Identify the correct replacement

- File lives under `plugins/` → replace with `"whiskers.plugins"`
- File lives anywhere else → replace with `"whiskers"`

### 3. Fix each violation

```python
# Before
logger = logging.getLogger("legacy-mcp")

# After (top-level)
logger = logging.getLogger("whiskers")

# After (plugin)
logger = logging.getLogger("whiskers.plugins")
```

### 4. Batch-fix generated files (PowerShell)

If the generators produced multiple files with the old name:

```powershell
Get-ChildItem -Path "plugins\core_mcp_plugin\MCPTools" -Filter "generated_*.py" -Recurse |
  ForEach-Object {
    (Get-Content $_.FullName) -replace '"legacy-mcp"', '"whiskers"' |
    Set-Content $_.FullName
  }
```

---

## Known Exception

**`oauth/oauth_provider.py`** — This file is **deprecated** and emits a `DeprecationWarning` on import. Its `"legacy-mcp"` logger is intentionally left as-is. Do **not** rename it; the file is scheduled for deletion.

---

## Adding Logging to a New Module

```python
import logging

# Choose one based on location:
logger = logging.getLogger("whiskers")          # top-level
logger = logging.getLogger("whiskers.plugins")  # plugin

# Use standard log levels
logger.info("Server started")
logger.warning("Config missing — using default")
logger.error("API call failed: %s", exc)
logger.debug("Response payload: %s", payload)
```

---

## See Also

- [Setup Guide](./references/setup-guide.md) — Background, affected files, and long-term maintenance
- `CLAUDE.md` Rule #14 — The authoritative source of truth
- `architecture-audit` skill — Full codebase audit including logger checks
