# Handoff — S27-report-checkpoint-tabl-review-fix

## Built
- Fixed the major PostgreSQL estimate issue: checkpoint row estimates now use `GREATEST(reltuples, 0)` so PostgreSQL's `-1` unanalyzed-table sentinel cannot appear as a negative gauge.
- A `to_regclass` lookup for a missing table returns no row; this now maps to zero. Added `LIMIT 1` to the catalog query.

## Review findings
- **MAJOR — `bootstrap.py:124`, negative reltuples — Fixed.** Query clamps negative estimates with `GREATEST(reltuples, 0)`; regression test verifies the expression is used.
- **MINOR — cache initialized at zero / count failure looks empty — No longer applies to this patch / left unchanged.** This remains a pre-existing behavior and is outside the major finding; expanding cache validity semantics would be a separate change, not a trivial extension of clamping PostgreSQL's sentinel.
- **MINOR — worker gauge register/unregister lifecycle untested — Left unchanged.** Existing tests cover direct registration/removal, but adding lifecycle-specific worker start/stop tests is orthogonal to the requested row-estimate fix and not required for the major finding.
- **MINOR — missing-table and `-1` estimate cases lack tests — Fixed.** Added coverage for the SQL clamp and `fetchone() is None` missing-table handling.
- **CHECKED — bounded catalog count, parameter binding and dict/tuple rows — Preserved.** Table names remain bound parameters and result row handling remains intact; `LIMIT 1` explicitly bounds the catalog lookup.

## Files changed
- `core_graph/runtime/bootstrap.py` — clamp negative PostgreSQL estimates and handle absent catalog rows.
- `test/unit/test_checkpoint_telemetry.py` — regression coverage for the clamp and missing table.

## Verification
- `python scripts/run_tests.py test/unit/test_checkpoint_telemetry.py -q` — **6 passed** in Docker.
- `git diff --check` — passed.
- The step contract's full verification gate was not run (contract reserves it for the fleet gate). S24's supplied last-verify failure output was empty, so there was no failing test or build line to triage; no perf/timing issue was reported.

## Unfinished
- None for the major review finding. The two unrelated minor behaviors above remain as noted.

## Dependencies
- None added.

## Learned
- `to_regclass` can produce no `pg_class` row for an absent table; map that missing result to zero and keep system-catalog lookups bounded with `LIMIT 1`.
