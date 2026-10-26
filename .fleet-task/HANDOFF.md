# S26 Handoff

## Built
- Corrected the fresh-install example's `goal_models` to match `DEFAULT_TASK_MODEL_MAP` and terminal-host token TTL to the 900-second code fallback.
- Removed the legacy `graph.envelope_budget` duplicate; the drift guard explicitly allows that legacy-only read while requiring `envelope_budget_bytes`.
- Corrected the helper docstring to describe its actual behavior.

## Review findings
- MAJOR `goal_models` empty map: **fixed**; example now matches `core/model_conductor.py:12-18` defaults.
- MAJOR host-token TTL 300 vs 900: **fixed**; example is 900, matching `utils/server_config.py` fallback to OAuth access TTL.
- MINOR legacy `envelope_budget`: **fixed**; removed from example and listed as a documented exception in the guard.
- MINOR inaccurate `missing_config_paths` docstring: **fixed**.
- MINOR hard-coded config aliases: **not changed**; current approach seeds aliases used across consumers and required scope was to address still-applicable blockers/majors, not expand scanner architecture.
- MINOR long conditions: **not changed**; non-blocking readability finding, not needed for the applicable blocker/major fixes.

## Files changed
- `config/server_config_example.json` — align fresh-install values with runtime defaults and remove dead legacy duplicate.
- `test/unit/test_server_config_example_drift.py` — document legacy exception and correct helper docstring.

## Verification
- `python -m pytest test/unit/test_server_config_example_drift.py -q` — passed (4 tests).
- `python -m py_compile test/unit/test_server_config_example_drift.py` — passed.
- `git diff --check` — passed.
- Full fleet verify was not run per the contract's instruction not to run it locally.
- No new dependencies. Nothing unfinished.

## Learned
- Fresh-install config examples are copied into active configuration, so their values must preserve the runtime fallback behavior.
