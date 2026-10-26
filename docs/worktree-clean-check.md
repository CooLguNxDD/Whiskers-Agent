# Worktree clean check (step-completion hook)

`scripts/check_worktree_clean.py` (stdlib only) flags unrelated or generated changes
before a step is committed. It diffs ordinary tracked paths against the merge-base of
`--base` (committed, staged and unstaged), and checks protected lockfiles against the
requested `--base` revision itself. Rename detection is disabled so renamed-away lockfiles
remain visible. It adds untracked files from
`git status --porcelain`. It exits `1` and prints `<path>: <reason>` per offender when a
change is:

- a package lockfile (`package-lock.json`, `yarn.lock`, `poetry.lock`, `uv.lock`,
  `Packages/packages-lock.json`) and `--allow-lock <path-or-glob>` was not passed; an
  untracked lockfile additionally needs a matching `--allow <glob>`;
- stray/generated: under `.claude/`, `.fleet-task/`, `__pycache__/`, `.pytest_cache/`,
  a `*.pyc` / `*.log` file, or `.env` (`--allow` never excuses these);
- untracked and not matched by an `--allow <glob>` (repeatable; `*` crosses `/`,
  a trailing `/` matches a directory prefix).

It exits `0` with `OK` otherwise, and `2` on a git error (e.g. unknown `--base`).

## Fleet owner: line to add to the step-completion hook

```
python scripts/check_worktree_clean.py --base origin/<run_branch>
```

Add `--allow-lock <path>` for a step that legitimately updates a lockfile and
`--allow <glob>` for new files the step spec expects. This repo does not run the check
itself; wiring it into the fleet is a follow-up for the fleet repo's owner.
