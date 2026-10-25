# Handoff — S16-add-drift-guard-test-every-s

## Built
- Added an AST-based unit guard that scans tracked Python modules for confidently mapped literal reads through `SERVER_CONFIG` aliases / `load_server_config()` and compares resulting dotted paths against the example JSON. Stale allow-list entries also fail.
- Added parametrized synthetic missing-key checks.
- Documented missing `parameters` and `terminal_relay` defaults in the tracked example; live config was not touched.

## Files changed
- `test/unit/test_server_config_example_drift.py` — AST collector and drift/synthetic tests.
- `config/server_config_example.json` — documents missing server config sections/defaults.

## Verification
- Narrow test: `bash "$(git rev-parse --path-format=absolute --git-common-dir)/../../../goal/opencat/2026-10-24-1535-embed-gemma2/docs/verify-opencat.sh" test/unit/test_server_config_example_drift.py` — 3 passed.
- Step acceptance `CHECK.sh` — CHECK OK.
- Full required verify command not run per contract.

## Unfinished / dependencies
- None. No dependencies added.
- AST mapping intentionally avoids uncertain dynamic reads; source with a BOM is normalized before parsing.

## Learned
- Python source scanning in this repository must strip a UTF-8 BOM before `ast.parse` for robustness.
