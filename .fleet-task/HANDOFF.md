# S04 Handoff

Implemented delete-scope authorization contracts, GOAP hiding, and operator documentation.

## Files changed
- `plugins/portfolio_plugin/manifest.json` — declared `group:portfolio_plugin:delete`.
- `plugins/job_search_plugin/manifest.json` — declared `group:job_search_plugin:delete`.
- `db_layer/embeddings/embeddings_routes.py` — denylisted all six destructive operations.
- `plugins/portfolio_plugin/plugin_config.py` — hid all three portfolio deletes from GOAP.
- `plugins/portfolio_plugin/tests/test_delete_scopes.py` — verifies restricted grants deny, delete scope allows, manifest scope exists, tags are not write, and denylist/hidden list entries remain present.
- `plugins/job_search_plugin/tests/test_delete_scopes.py` — corresponding job-search contract tests.
- `CLAUDE.md` — documented scope, tenant, dry-run, force, cleanup, reconcile, and GOAP details.
- `.claude/skills/career-ops-pipeline/SKILL.md` — clarified delete is separate from pipeline credentials and stages.
- `plugins/job_search_plugin/skills/job-search-pipeline/JOB_SEARCH_SKILL.md` — documented destructive tools.
- `plugins/portfolio_plugin/skills/context-discovery.md` — added portfolio delete guidance.
- Fast-forwarded the worktree to `origin/run/delete-tools` after receiving fleet-advisor confirmation that the dispatched S03 base predated the S02 merge. The S02 files are now sourced from the run branch, not hand-restored.

The cross-plugin `job_search_plugin__list_applications_for_portfolio_job` op is a read-tagged adapter used by portfolio layout reference checks, not an operator/internal delete op, so it is not denylisted.

## Verification
- Narrow verify script invocation for both new scope tests and the existing portfolio GOAP denylist test: **8 passed**.
- Did not run the full fleet verify command or PlayMode tests, per the worker contract.
- Full gate remains to be run by the orchestrator.

## Unfinished
- No known unfinished code changes.

## Dependencies
- No new dependencies.

## Learned
- `evaluate_access` accepts plugin/tags or a typed `AccessRequest`; the latter carries operation identity when testing an individual op.
- FastMCP-decorated tool functions do not expose their decorator tag metadata as `.tags`; static contract tests can inspect the source decorators.
