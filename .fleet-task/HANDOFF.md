# S08-prevent-test-runs-from-review-fix — Handoff

## Built
- Fixed all three blocker findings in `scripts/check_worktree_clean.py`; added regression coverage and updated the behavior documentation.
- No dependencies added.

## Findings
- **BLOCKER: `--base` vs merge-base lockfile comparison — fixed.** Ordinary tracked-path changes remain compared against the merge-base, while lockfiles are separately compared against the exact requested base. `test_lockfile_compared_to_requested_base_not_merge_base` covers a branch-specific lockfile change.
- **BLOCKER: allowed lockfiles bypass stray/untracked guards — fixed.** Stray checks run before lockfile allowances, and untracked files (including lockfiles) still require a matching `--allow`. Tests cover a `.claude/package-lock.json` with `--allow-lock`, plus requiring both flags for an ordinary untracked lockfile.
- **BLOCKER: rename hides protected lockfile source — fixed.** Both diffs use `--no-renames`; `git mv package-lock.json ordinary.json` without `--allow-lock` is covered.

## Files changed
- `scripts/check_worktree_clean.py` — independent exact-base lockfile comparison, rename-safe path reporting, and independent stray/untracked enforcement.
- `test/unit/test_check_worktree_clean.py` — regressions for all three review blockers.
- `docs/worktree-clean-check.md` — explains exact-base/merge-base distinction, rename behavior, and dual allowance for untracked lockfiles.
- `.fleet-task/HANDOFF.md` — required step report.

## Verification and triage
- `python -m py_compile scripts/check_worktree_clean.py test/unit/test_check_worktree_clean.py` — passed.
- `bash "$(git rev-parse --path-format=absolute --git-common-dir)/../../../goal/opencat/2026-10-24-1535-embed-gemma2/docs/verify-opencat.sh" test/unit/test_check_worktree_clean.py` — **14 passed**.
- An initial `python scripts/run_tests.py test/unit/test_check_worktree_clean.py` attempted to run in the existing container, which cannot see this worktree and returned file-not-found/0 collected. Switched to the prescribed worktree-aware verify script; the narrow test run passed.
- S06 last verify output supplied in the task was empty; no failing line was available to triage. No unrelated/perf failure identified.
- Fleet acceptance `CHECK.sh` and full verify gate were not run per contract; CHECK itself invokes the full verify gate. No known test or compile failures remain.

## Unfinished
- None.

## Learned
- A worktree-aware narrow test run must use `docs/verify-opencat.sh <pytest paths>`; `scripts/run_tests.py` can delegate into a separate existing Docker container that does not contain the worktree.
