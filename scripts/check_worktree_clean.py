#!/usr/bin/env python3
"""
Step-completion worktree check: flag unrelated or generated changes before commit.

Lists every file changed vs ``--base`` (committed + staged + unstaged, via
``git diff --name-only``) plus untracked files (``git status --porcelain``) and
exits 1 when any of them is:

  * a package lockfile (``package-lock.json``, ``yarn.lock``, ``poetry.lock``,
    ``uv.lock``, ``Packages/packages-lock.json``) not named by ``--allow-lock``;
  * stray/generated: under ``.claude/``, ``.fleet-task/``, ``__pycache__/``,
    ``.pytest_cache/``, a ``*.pyc`` / ``*.log`` file, or a ``.env`` file;
  * untracked and not matched by any ``--allow <glob>``.

Usage (from inside the worktree):
    python scripts/check_worktree_clean.py --base origin/<run_branch>
    python scripts/check_worktree_clean.py --allow-lock package-lock.json --allow "docs/*"

Exit codes: 0 clean (prints ``OK``), 1 offenders found, 2 git/usage error.
Stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys

LOCKFILE_NAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock"}
LOCKFILE_SUFFIXES = ("Packages/packages-lock.json",)
STRAY_DIRS = {".claude", ".fleet-task", "__pycache__", ".pytest_cache"}
STRAY_SUFFIXES = (".pyc", ".log")
STRAY_NAMES = {".env"}


def _git(*args: str) -> str:
    """Run git in the current directory and return stdout; raise RuntimeError on failure."""
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def _norm(path: str) -> str:
    """Normalize a repo-relative path to forward slashes without a leading ./."""
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def changed_files(base: str) -> list[str]:
    """Tracked files differing from the merge-base; lockfile changes use the requested base."""
    _git("rev-parse", "--verify", "--quiet", base + "^{commit}")
    try:
        ref = _git("merge-base", base, "HEAD").strip() or base
    except RuntimeError:
        ref = base  # unrelated histories: diff against base itself
    # Disable rename detection so the deleted source of a renamed lockfile remains visible.
    ordinary = _git("diff", "--no-renames", "--name-only", "-z", ref)
    locks = _git("diff", "--no-renames", "--name-only", "-z", base)
    paths = {_norm(p) for p in ordinary.split("\0") if p}
    paths.update(_norm(p) for p in locks.split("\0") if p and is_lockfile(_norm(p)))
    return sorted(paths)


def untracked_files() -> list[str]:
    """Untracked, non-ignored files (directories expanded to files)."""
    out = _git("status", "--porcelain", "-z", "--untracked-files=all")
    entries = out.split("\0")
    found: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":
            i += 1  # rename/copy carries the origin path as an extra entry
        if status == "??":
            found.append(_norm(path))
    return found


def is_lockfile(path: str) -> bool:
    """True for package lockfiles at any depth."""
    if path.rsplit("/", 1)[-1] in LOCKFILE_NAMES:
        return True
    return any(path == s or path.endswith("/" + s) for s in LOCKFILE_SUFFIXES)


def stray_reason(path: str) -> str | None:
    """Return why ``path`` is stray/generated, or None."""
    parts = path.split("/")
    for part in parts[:-1]:
        if part in STRAY_DIRS:
            return f"stray/generated path ({part}/)"
    name = parts[-1]
    if name in STRAY_NAMES:
        return "stray/generated file (.env)"
    if name.endswith(STRAY_SUFFIXES):
        return f"stray/generated file (*{name[name.rfind('.'):]})"
    return None


def _matches(path: str, patterns: list[str]) -> bool:
    """Glob match (``*`` crosses ``/``); a trailing ``/`` pattern matches a directory prefix."""
    for pat in patterns:
        pat = _norm(pat)
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


def find_offenders(
    base: str, allow: list[str], allow_lock: list[str]
) -> list[tuple[str, str]]:
    """Return sorted ``(path, reason)`` pairs for every offending change."""
    tracked = changed_files(base)
    untracked = untracked_files()
    offenders: dict[str, str] = {}
    for path in tracked + untracked:
        reason = stray_reason(path)
        if reason:
            offenders[path] = reason
            continue
        if is_lockfile(path) and not _matches(path, allow_lock):
            offenders[path] = "lockfile changed (pass --allow-lock to permit)"
    for path in untracked:
        if path in offenders:
            continue
        if not _matches(path, allow):
            offenders[path] = "untracked file not covered by --allow"
    return sorted(offenders.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main", help="revision to diff against (default origin/main)")
    ap.add_argument("--allow", action="append", default=[], metavar="GLOB",
                    help="glob of untracked paths that are expected (repeatable)")
    ap.add_argument("--allow-lock", action="append", default=[], metavar="PATH",
                    help="lockfile path (or glob) that may change (repeatable)")
    args = ap.parse_args(argv)
    try:
        offenders = find_offenders(args.base, args.allow, args.allow_lock)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not offenders:
        print("OK")
        return 0
    for path, reason in offenders:
        print(f"{path}: {reason}")
    print(f"FAIL: {len(offenders)} offending change(s) vs {args.base}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
