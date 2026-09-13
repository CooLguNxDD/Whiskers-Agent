#!/usr/bin/env python3
"""Build Jules review-session configs (prompt + sourceContext) for
julescreate_session calls. Prints JSON to stdout; does not call any network API
itself — the calling agent feeds each config into the julescreate_session MCP tool.

Canonical location (Whiskers Agent repo)::

    python plugins/jules_plugin/review_fleet/driver.py --mode diff --roles all
    # or, with PYTHONPATH=repo root:
    python -m plugins.jules_plugin.review_fleet.driver --mode full --roles backend-b

This module is the single source of truth shared with:

- Plugin skill ``plugins/jules_plugin/skills/jules-sessions/SKILL.md`` (GOAP)
Modes
-----
  full  Deep scan of the scoped path(s).
  diff  Only review changes from --base to the starting branch.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Allow `python plugins/jules_plugin/review_fleet/driver.py` without install.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.jules_plugin.review_fleet.paths import plugin_skill_path  # noqa: E402
from plugins.jules_plugin.review_fleet.templates import (  # noqa: E402
    ALL_ROLES,
    FLEET_ALL,
    build_configs,
    parse_roles,
)


def current_branch() -> str:
    """Return current git branch name (worktree dir fallback for detached HEAD)."""
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if out and out != "HEAD":
        return out
    import os

    return os.path.basename(os.getcwd())


def current_repo() -> str | None:
    """Best-effort owner/repo autodetect from the local git remote."""
    out = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True
    ).stdout.strip()
    if not out:
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", out)
    return m.group(1) if m else None


def detect_default_branch() -> str | None:
    """Best-effort default/base branch from origin/HEAD or local main/master."""
    out = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if out:
        return out.split("/", 1)[-1] if "/" in out else out

    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if out and out != "origin/HEAD":
        return out.split("/", 1)[-1] if out.startswith("origin/") else out

    for candidate in ("main", "master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{candidate}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/remotes/origin/{candidate}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    return None


def main(argv: list[str] | None = None) -> None:
    """CLI entry: emit JSON array of julescreate_session configs."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--mode",
        choices=["full", "diff"],
        default="full",
        help="full = deep scan of scoped paths (default); "
        "diff = only changes from --base to the starting branch",
    )
    ap.add_argument(
        "--base",
        "--target-branch",
        dest="base",
        default=None,
        metavar="BRANCH",
        help="for --mode diff: compare against this branch (default/target). "
        "Autodetects origin/HEAD → main/master when omitted. "
        "Aliases: --target-branch",
    )
    ap.add_argument(
        "--roles",
        default=None,
        help="optional comma-separated runners to spawn: "
        f"{','.join(ALL_ROLES)}, custom domain slugs, or 'all' for the classic "
        "2 frontend + 2 backend + 1 docs fleet. "
        "Omit or pass empty to spawn nothing (no fixed fleet). "
        f"Example classic fleet: --roles {FLEET_ALL}",
    )
    ap.add_argument(
        "--repo",
        default=None,
        help="owner/repo as it appears under Jules' sources/github/<owner>/<repo> "
        "(default: autodetected from `git remote get-url origin`)",
    )
    ap.add_argument("--branch", default=None, help="startingBranch override (Jules checkout)")
    ap.add_argument(
        "--path",
        default=None,
        help="repo-relative directory every role should scope to (default: `.`). "
        "Overridden per-role by --frontend-path/--backend-path when given.",
    )
    ap.add_argument(
        "--frontend-path",
        default=None,
        help="repo-relative path scoped by the frontend-a/frontend-b roles",
    )
    ap.add_argument(
        "--backend-path",
        default=None,
        help="repo-relative path scoped by the backend-a/backend-b roles",
    )
    ap.add_argument(
        "--docs-path",
        default=None,
        help="repo-relative path scoped by the docs role",
    )
    ap.add_argument("--require-plan-approval", action="store_true")
    ap.add_argument(
        "--automation-mode",
        default="AUTOMATION_MODE_UNSPECIFIED",
        choices=["AUTOMATION_MODE_UNSPECIFIED", "AUTO_CREATE_PR"],
    )
    ap.add_argument(
        "--print-skill-path",
        action="store_true",
        help="print absolute path to plugins/jules_plugin/skills/jules-sessions/SKILL.md and exit",
    )
    args = ap.parse_args(argv)

    if args.print_skill_path:
        print(plugin_skill_path())
        return

    try:
        roles = parse_roles(args.roles, allow_custom=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    repo = args.repo or current_repo()
    if not repo:
        raise SystemExit(
            "could not autodetect repo from `git remote get-url origin`; pass --repo owner/name"
        )

    default_path = args.path or "."
    frontend_path = args.frontend_path or default_path
    backend_path = args.backend_path or default_path
    docs_path = args.docs_path or default_path

    branch = args.branch or current_branch()

    base_branch = args.base
    if args.mode == "diff":
        if not base_branch:
            base_branch = detect_default_branch()
        if not base_branch:
            raise SystemExit(
                "--mode diff requires a base branch; could not autodetect default "
                "(origin/HEAD / main / master). Pass --base <branch> or --target-branch <branch>."
            )
        if base_branch == branch:
            raise SystemExit(
                f"--mode diff: base branch `{base_branch}` equals starting branch `{branch}`; "
                "checkout a feature branch or pass --branch / --base explicitly."
            )

    configs = build_configs(
        roles,
        repo,
        branch,
        args.require_plan_approval,
        args.automation_mode,
        frontend_path,
        backend_path,
        docs_path,
        mode=args.mode,
        base_branch=base_branch if args.mode == "diff" else None,
    )
    print(json.dumps(configs, indent=2))


if __name__ == "__main__":
    main()
