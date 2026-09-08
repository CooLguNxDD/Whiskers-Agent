# Logger Convention — Setup Guide & Reference

## Why "whiskers"?

The FastMCP app singleton is created in `core/context.py` as:

```python
mcp = FastMCP("whiskers", ...)
```

The name `"whiskers"` is the internal brand name for this MCP server project. Using the same name as the logger root namespace gives two benefits:

1. **Consistency** — log output can be traced directly to the MCP app.
2. **Hierarchy** — `logging.getLogger("whiskers.plugins")` inherits handlers from `"whiskers"` automatically via Python's logger tree, so a single `logging.basicConfig` or Winston-style transport configuration at the root covers everything.

The old name `"legacy-mcp"` predates this naming decision and is fully retired.

---

## Affected Files — Session Record (Logger Rename, 2025)

The following files were updated from `"legacy-mcp"` → `"whiskers"` or `"whiskers.plugins"`:

| File | New logger name |
|---|---|
| `plugins/core_mcp_plugin/MCPTools/graph_tool.py` | `whiskers` |
| `plugins/core_mcp_plugin/MCPTools/SemanticTools/semantic_record.py` | `whiskers` |
| `plugins/core_mcp_plugin/MCPTools/SemanticTools/semantic_message.py` | `whiskers` |
| `plugins/core_mcp_plugin/MCPTools/generated_*.py` (11 files) | `whiskers` |
| `plugins/pro_plugin/src/pro_mcp_tools/dynamic_tools_loader.py` | `whiskers` |
| `plugins/pro_plugin/src/pro_mcp_tools/dynamic_api.py` | `whiskers` |
| `plugins/pro_plugin/src/pro_graph/dynamic_graph.py` | `whiskers` |

### Intentional exception

`oauth/oauth_provider.py` was **not** renamed. This file:
- Is marked deprecated in CLAUDE.md
- Emits a `DeprecationWarning` on import
- Is scheduled for deletion in a follow-up PR

There is no value in updating it before deletion.

---

## How to Verify Compliance

After any code generation or merge, run:

```powershell
# PowerShell — find all violations (excludes the deprecated file)
Select-String -Path "**\*.py" -Pattern '"legacy-mcp"' -Recurse |
  Where-Object { $_.Path -notlike '*oauth_provider.py*' }
```

A clean result (zero output) means the repo is compliant.

---

## Long-Term Maintenance

### When generating new tools

Both generators (`tools_generator.py`, `semantic_tools_generator.py`) may produce files that default to `"legacy-mcp"`. Always run the audit command above after generation.

### When writing new modules

Pick the logger at the top of the file:

```python
import logging
logger = logging.getLogger("whiskers")          # non-plugin
logger = logging.getLogger("whiskers.plugins")  # inside plugins/
```

Never use a module-specific logger like `logging.getLogger(__name__)` unless there is a specific operational reason (e.g. you need to configure a separate log level for that module). The flat two-name hierarchy (`whiskers` / `whiskers.plugins`) is intentional.

### Pre-commit check (optional addition)

A pre-commit hook can enforce compliance:

```bash
#!/bin/sh
# .git/hooks/pre-commit
VIOLATIONS=$(grep -rn '"legacy-mcp"' --include="*.py" . \
  --exclude="*/oauth_provider.py")
if [ -n "$VIOLATIONS" ]; then
  echo "Logger convention violation — use 'whiskers' or 'whiskers.plugins':"
  echo "$VIOLATIONS"
  exit 1
fi
```

---

## Related Documentation

- [SKILL.md](../SKILL.md) — Quick-reference card for auditing and fixing violations
- `CLAUDE.md` Rule #14 — Authoritative rule definition
- `core/context.py` — Where `FastMCP("whiskers", ...)` is declared
