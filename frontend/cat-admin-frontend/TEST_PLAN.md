# Frontend Test Plan — Scope & Playground

## Unit (Vitest)

- `hooks/__tests__/useScopeEditor.test.ts` — scope toggle, presets, `scopesForPersist`
  - `applyPreset("all")` → `["all"]` sentinel (not enumerated list)
  - null selection → `scopesForPersist()` returns `["all"]`
  - specific scopes persist as exact list
- API keys route save path uses `scopesForPersist()` for update + preset save

## Playground scope enforcement (manual / E2E targets)

| Scenario | Role | Action | Expected |
|----------|------|--------|----------|
| Viewer write invoke | viewer | `/tools/invoke` write tool | 403 `scope_denied` |
| Master read invoke | master | `/tools/invoke` read tool | 200 ok |
| MCP-mode run_graph | viewer | graph step needing write plugin | scope denied on step |
| MCP-mode run_graph | master/admin | any tool | allow (admin bypass) |
| API key "All" preset | n/a | save scopes then call tool | succeeds (`["all"]`) |

## Related backend tests

- `test/unit/test_playground_scope_enforcement.py`
- `test/unit/test_scope_contracts.py` (C10, C11)
