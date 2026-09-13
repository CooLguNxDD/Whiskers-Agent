"""Role templates and config builder for Jules multi-session code reviews.

Single source of truth shared by the CLI driver and the jules-sessions skill
docs. Do not fork these strings into agent skill drivers — import from here.
"""
from __future__ import annotations

import json
import re
from typing import Any

ALL_ROLES: tuple[str, ...] = (
    "frontend-a",
    "frontend-b",
    "backend-a",
    "backend-b",
    "docs",
)

# Classic full fleet (opt-in via --roles all). Not the default.
FLEET_ALL = "frontend-a,frontend-b,backend-a,backend-b,docs"

BASE_TEMPLATE = """Act as a Principal Code Architect and Lead Quality Assurance Specialist. {mode_preamble}

### EVALUATION CRITERIA
- Robustness & Edge Cases: race conditions, unhandled async ops, missing error boundaries, bad behavior on empty/null inputs.
- Performance & Resource Efficiency: redundant work in loops, inefficient data access, unnecessary allocations.
- Defensive Programming: input validation, graceful degradation, secure non-leaky error logging.
- Maintainability & Design: coupling, SOLID alignment, dead code, idiomatic-pattern departures.
{extra_criteria}
### EXECUTION WORKFLOW
1. {mode_scan_step}
2. Compile findings into `{report_name}` at the project root.
3. For every finding include: Priority (High/Medium/Low), file + line range, explanation, concrete refactor.
4. Do NOT modify any existing production files this run — output only the report file."""

DOC_TEMPLATE = """Act as a documentation specialist. {mode_preamble}

Task: identify every exported/public function, class, hook, or store slice missing required documentation per this project's standard:
- Top-level docstring/JSDoc for all components, hooks, store slices, functions, classes (no arg/return sections needed).
- 1-3 line description for every exported function.
- Inline comments (1 line max) only where logic is genuinely non-obvious (hidden constraint, workaround, subtle invariant) — never restating what the code already says.

{mode_scan_step}

Compile findings into `{report_name}` at the project root: file name, function/class name, and a suggested 1-3 line docstring or inline comment. Do NOT modify any existing production files — output only the report file."""


def mode_text(mode: str, base_branch: str | None, scope_desc: str) -> dict[str, str]:
    """Return mode_preamble + mode_scan_step fragments for the templates."""
    if mode == "diff":
        base = base_branch or "main"
        return {
            "mode_preamble": (
                f"Perform a **diff-scoped** code review of {scope_desc}. "
                f"Review **only** the changes introduced on the current branch relative to "
                f"`{base}` (the merge-base / three-dot range `{base}...HEAD`). "
                "Ignore unchanged files and untouched regions. Prefer `git diff`, "
                f"`git log {base}..HEAD`, and PR-style review over full-tree browsing. "
                f"NEVER fall back to two-dot `{base}..HEAD` as a substitute for "
                f"`{base}...HEAD` — two-dot inverts `{base}`-only work into phantom deletions "
                "on a shallow clone."
            ),
            "mode_scan_step": (
                f"Compute `git merge-base {base} HEAD` first. If that fails "
                "(shallow clone / no merge-base): run `git fetch --unshallow origin` "
                "(or `git fetch --deepen=200 origin` if unshallow is refused), then retry. "
                "If merge-base still cannot be computed (e.g. shallow clone on a private repo "
                "without interactive credentials): STOP: do not fall back to two-dot "
                f"`{base}..HEAD` (which inverts `{base}`-only work into phantom deletions). "
                "Instead, record a shallow-clone notice at the top of the report file, inspect branch "
                "commits via `git log --oneline` (or `git show --stat`), and perform the code health "
                f"review directly on the files in {scope_desc}. "
                f"When merge-base succeeds, list files with `git diff --name-only {base}...HEAD` "
                "(three-dot) and the corresponding patch hunks. Restrict analysis and findings "
                "to those deltas and their immediate call-site context. Do not deep-scan unrelated modules."
            ),
        }
    return {
        "mode_preamble": (
            f"Perform an **exhaustive deep scan** (full historical and functional context) "
            f"of {scope_desc}. This is a full-tree health assessment, not a PR delta review."
        ),
        "mode_scan_step": (
            "Deep-scan the full historical and functional context of targeted files — "
            "read related modules, call chains, and config that the scoped tree depends on."
        ),
    }


def build_roles(frontend_path: str, backend_path: str, docs_path: str) -> dict[str, dict[str, Any]]:
    """Role definitions keyed by fleet role id (frontend-a, backend-b, docs, …)."""
    return {
        "frontend-a": dict(
            title="[Review] Frontend code health — core components/routes",
            scope_desc=f"`{frontend_path}` (components, routes, hooks, or equivalent UI-layer code)",
            extra_criteria="",
            report_name="CODE_HEALTH_FRONTEND_A.md",
            template=BASE_TEMPLATE,
        ),
        "frontend-b": dict(
            title="[Review] Frontend a11y & state management",
            scope_desc=(
                f"`{frontend_path}` — focus on accessibility (keyboard nav, ARIA, focus states) "
                "and state management (data-fetching hooks, store slices, prop drilling)"
            ),
            extra_criteria=(
                "- Accessibility: keyboard-only + screen-reader usability of custom interactive elements.\n"
                "- State Management: stale closures, redundant re-renders, unstable query keys.\n"
            ),
            report_name="CODE_HEALTH_FRONTEND_B.md",
            template=BASE_TEMPLATE,
        ),
        "backend-a": dict(
            title="[Review] Backend code health — core services",
            scope_desc=f"`{backend_path}` (services, orchestration, or equivalent business logic)",
            extra_criteria="",
            report_name="CODE_HEALTH_BACKEND_A.md",
            template=BASE_TEMPLATE,
        ),
        "backend-b": dict(
            title="[Review] Backend security & data layer",
            scope_desc=f"`{backend_path}` — focus on security and data-access boundaries",
            extra_criteria=(
                "- Authorization/Tenant Isolation: any access-scope filter (tenant/org/user id) "
                "that can silently default to unrestricted.\n"
                "- SSRF/Unsafe Requests: outbound HTTP calls to user-influenced URLs not going "
                "through a vetted client/allowlist.\n"
                "- Data Access: raw queries or DB calls bypassing the project's data-access layer, "
                "especially inside HTTP route handlers.\n"
            ),
            report_name="CODE_HEALTH_BACKEND_B.md",
            template=BASE_TEMPLATE,
        ),
        "docs": dict(
            title="[Review] Doc-writer — missing docstrings & inline comments",
            scope_desc=f"`{docs_path}`",
            extra_criteria="",
            report_name="DOC_GAPS_REPORT.md",
            template=DOC_TEMPLATE,
        ),
    }


def parse_roles(raw: str | None, allow_custom: bool = False) -> list[str]:
    """Parse --roles. Empty/omitted → no runners. 'all' → classic 2+2+1 fleet."""
    if raw is None:
        return []
    raw = raw.strip()
    if not raw:
        return []
    if raw.lower() == "all":
        return list(ALL_ROLES)
    roles = [r.strip() for r in raw.split(",") if r.strip()]
    if not allow_custom:
        unknown = [r for r in roles if r not in ALL_ROLES]
        if unknown:
            raise ValueError(f"unknown role(s): {unknown}; valid: {list(ALL_ROLES)} or 'all'")
    seen: set[str] = set()
    out: list[str] = []
    for r in roles:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _custom_report_name(slug: str) -> str:
    """Sanitize a custom domain slug into a CODE_HEALTH_*.md report filename."""
    # Hyphens and other punctuation become underscores in one pass.
    safe_slug = re.sub(r"[^a-zA-Z0-9_]", "_", slug).upper()
    return f"CODE_HEALTH_{safe_slug}.md"


def build_configs(
    roles: list[str],
    repo: str,
    branch: str,
    require_plan_approval: bool,
    automation_mode: str,
    frontend_path: str,
    backend_path: str,
    docs_path: str,
    mode: str,
    base_branch: str | None,
) -> list[dict[str, Any]]:
    """Build julescreate_session-ready configs (rich prompt + sourceContext string)."""
    role_defs = build_roles(frontend_path, backend_path, docs_path)
    configs: list[dict[str, Any]] = []
    mode_suffix = "diff" if mode == "diff" else "full"
    base_for_title = base_branch or "main"
    used_reports: dict[str, str] = {}

    for key in roles:
        if key in role_defs:
            r = role_defs[key]
        else:
            slug = key.strip()
            if not slug:
                continue
            report_name = _custom_report_name(slug)
            title = f"[Review] {slug.replace('-', ' ').replace('_', ' ').title()} code health"
            scope_path = backend_path if backend_path and backend_path != "." else (docs_path if docs_path and docs_path != "." else frontend_path)
            scope_desc = f"`{scope_path}` ({slug} domain)"
            r = dict(
                title=title,
                scope_desc=scope_desc,
                extra_criteria=f"- Domain Focus: deep analysis of {slug} architecture, reliability, interfaces, and defensive error handling.\n",
                report_name=report_name,
                template=BASE_TEMPLATE,
            )
        report_name = r["report_name"]
        prior = used_reports.get(report_name)
        if prior is not None:
            raise ValueError(
                f"role {key!r} collides with {prior!r} on report {report_name}"
            )
        used_reports[report_name] = key
        fragments = mode_text(mode, base_branch, r["scope_desc"])
        prompt = r["template"].format(
            scope_desc=r["scope_desc"],
            extra_criteria=r["extra_criteria"],
            report_name=r["report_name"],
            **fragments,
        )
        title = r["title"]
        if mode == "diff":
            title = f"{title} (diff vs {base_for_title})"
        else:
            title = f"{title} (full deep scan)"

        configs.append(
            {
                "role": key,
                "mode": mode_suffix,
                "baseBranch": base_branch if mode == "diff" else None,
                "title": title,
                "prompt": prompt,
                "sourceContext": json.dumps(
                    {
                        "source": f"sources/github/{repo}",
                        "githubRepoContext": {"startingBranch": branch},
                    }
                ),
                "requirePlanApproval": require_plan_approval,
                "automationMode": automation_mode,
            }
        )
    return configs
