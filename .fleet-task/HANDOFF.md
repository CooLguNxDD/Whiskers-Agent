# S01-profile-tenant Handoff

## Built
- Profile creation, upsert, and preference embedding writes now require a positive `current_tenant_id`; supplied `tenant_id` values cannot override the principal.
- Profile upsert conflict updates are tenant-guarded and fetch the result through `get_profile_scoped`. Invalid/missing tenant context causes scoped reads to return `None`.
- Added idempotent `0006_profile_tenant_backfill.py` to assign legacy NULL tenant IDs to tenant 1 in both profile and preference embedding tables.
- Added mocked-session contract tests and updated existing migration ordering/profile tests for the new behavior.

## Files changed
- `plugins/job_search_plugin/store.py` — fail-closed tenant resolution, stamped writes, guarded upsert, scoped follow-up read.
- `plugins/job_search_plugin/migrations/0006_profile_tenant_backfill.py` — NULL-to-1 backfill.
- `plugins/job_search_plugin/tests/test_profile_tenant.py` — tenant contract, SQL predicates, and migration tests.
- `plugins/job_search_plugin/tests/test_job_search_store.py` — tenant context fixture and migration ordering update.

## Verification
- Targeted verify script on `test_profile_tenant.py` and `test_job_search_store.py`: **23 passed**.
- `.fleet-task/CHECK.sh`: **CHECK S01 ok** (same 23 targeted tests pass). Expected warnings note delete-tool tests are not built yet.
- `git diff --check`: clean.
- No dependency changes. No unfinished work.

## Learned
- Existing profile-store tests relied on the context variable's former implicit tenant-1 fallback; tests that exercise valid profile writes need to establish an explicit principal tenant.
