# Jules dispatch — branding reference audit (2026-09-07)

Repo: `sources/github/CooLguNxDD/Open-Cat-Tunnel-MCP`
Starting branch: `cleanup/strip-weltel-opencat-tunnel-refs` @ 85e5b926
Mode: AUDIT-ONLY (no code changes), automationMode=AUTO_CREATE_PR
Terms hunted: weltel, healthcare, opencat, open-cat, open cat, OpenCatTunnel, cat-tunnel, tunnel
Scope decision: load-bearing identifiers IN scope (report + rename plan, no edits)

| # | module | slice | session id | url | report file |
|---|--------|-------|-----------|-----|-------------|
| 01 | frontend-app | frontend/ non-test src + configs | 15414401059525487560 | https://jules.google.com/session/15414401059525487560 | docs/cleanup-audit/01-frontend-app.md |
| 02 | frontend-tests | frontend/ __tests__, *.test.*, mocks | 14681998954326684235 | https://jules.google.com/session/14681998954326684235 | docs/cleanup-audit/02-frontend-tests.md |
| 03 | portfolio-plugin | plugins/portfolio_plugin/** | 5729413087891149131 | https://jules.google.com/session/5729413087891149131 | docs/cleanup-audit/03-portfolio-plugin.md |
| 04 | other-plugins | plugins/** except portfolio_plugin | 13222264940212913862 | https://jules.google.com/session/13222264940212913862 | docs/cleanup-audit/04-other-plugins.md |
| 05 | tests | test/** | 8437003386038151246 | https://jules.google.com/session/8437003386038151246 | docs/cleanup-audit/05-tests.md |
| 06 | core | core/**, core_graph/** | 14552811457441702417 | https://jules.google.com/session/14552811457441702417 | docs/cleanup-audit/06-core.md |
| 07 | api-oauth-vscode | api/, oauth/, cat-tunnel-vscode/, terminal/ | 13844441531030978417 | https://jules.google.com/session/13844441531030978417 | docs/cleanup-audit/07-api-oauth-vscode.md |
| 08 | scripts-tooling-db | scripts/, Tools/, utils/, migrations/, db_layer/, config/ | 4477697266906759545 | https://jules.google.com/session/4477697266906759545 | docs/cleanup-audit/08-scripts-tooling-db.md |
| 09 | claude-tooling-ci | .claude/, .claude-plugin/, claude-plugins/, .github/ | 5418617474532266971 | https://jules.google.com/session/5418617474532266971 | docs/cleanup-audit/09-claude-tooling-ci.md |
| 10 | agents-root-docs | .agents/, root files, docs/ (excl. cleanup-audit) | 10622680109459310554 | https://jules.google.com/session/10622680109459310554 | docs/cleanup-audit/10-agents-root-docs.md |

## Acceptance per session
`git status` on the PR branch shows exactly one added file: the module's report.
No grep hit in the slice absent from the report.

## Baseline counts (local, HEAD 85e5b926)
weltel 4 files / 4 hits; healthcare 0; opencat 229 files / 717 hits; open-cat 36 / 80; "open cat" 3 / 12; tunnel 103 / 323. Union: 248 files.
