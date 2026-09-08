---
name: jules-sessions
description: Jules create/list/get and optional multi-role review fleet (server-side). USE FOR sessions and code-review fleets.
---

# Jules sessions — multi-job separation (server)

Fleet builder lives **on the Whiskers Agent server** (`plugins/jules_plugin/review_fleet/` +
MCP tools). Roles are **optional** — never auto-spawn 2+2+1.

## Tools

- `julesbuild_review_fleet` — build configs only (no network). **roles optional**.
- `julesfire_review_fleet` — build + CreateSession on server. **roles optional** (empty = no-op).
- `julescreate_session` — one session; `prompt` must be a full multi-line brief.
- `juleslist_sessions` / `julesget_session` / `juleslist_sources` / activities / `julesapprove_plan`.

## Roles optional (no fixed fleet)

| roles arg | effect |
|-----------|--------|
| omitted / `""` | **nothing** — `configs: []`, no sessions |
| `backend-b` | one session |
| `frontend-a,docs` | exactly those |
| `all` | classic five: frontend-a/b, backend-a/b, docs |

Valid: `frontend-a`, `frontend-b`, `backend-a`, `backend-b`, `docs`.

## Prefer server fleet tools

```
julesfire_review_fleet(roles="backend-b,docs", mode="diff", base="main",
                       repo="owner/name", branch="feat")
```

Empty roles:

```
julesfire_review_fleet(roles="")  → sessions: [], nothing created
```

Do **not** seed `prompt: ["frontend-a","docs"]`. Use fleet tools or expand
templates into full multi-line prompts.

## Hard anti-pattern

Bare role labels as `prompt` → sessions with no real work. Role names are job
keys only.

## Modes

| mode | when |
|------|------|
| **diff** | branch/PR vs `base` (`{base}...HEAD` three-dot only) |
| **full** | whole-tree deep scan |

**Diff merge-base is fail-closed.** A Jules session that cannot compute
`git merge-base {base} HEAD` must unshallow (`git fetch --unshallow origin` or
`--deepen`) and retry. If merge-base is still missing, it **aborts the review**
with that reason in the report. Never fall back to two-dot `{base}..HEAD`
(that inverts `{base}`-only work into phantom deletions on a shallow clone).

Pass `repo`, `branch` on the server (git autodetect may be unavailable in Docker).
`sourceContext` for raw create is a JSON **string**.

## Role → report (when roles requested)

| role | focus | report |
|------|-------|--------|
| frontend-a | UI health | `CODE_HEALTH_FRONTEND_A.md` |
| frontend-b | a11y + state | `CODE_HEALTH_FRONTEND_B.md` |
| backend-a | services | `CODE_HEALTH_BACKEND_A.md` |
| backend-b | security/tenant/SSRF/DAL | `CODE_HEALTH_BACKEND_B.md` |
| docs | missing docs | `DOC_GAPS_REPORT.md` |

Templates (BASE/DOC + mode fragments) live in
`plugins/jules_plugin/review_fleet/templates.py` — fleet tools expand them.

## Large fields / artifacts

- List/get/activities may return markers like `[offloaded: … short_id=art_…]` with an
  **inline `--- preview ---` block**.
- Use the **preview** (and small summary fields) for status/summary decisions.
- Call `fetch_artifact(short_id=…)` **only** when you must quote or apply the full
  patch/log — not after every Jules call.
- Do **not** call `get_artifact` then `fetch_artifact`; fetch alone returns meta + content.
- `juleslist_activities` is summary-first (full patches stripped); use `julesget_activity`
  when you need one activity body (still preview-first / offloaded if huge).

## GOAP recipe

1. Infer optional roles + mode + base/branch/repo from the user (no roles → do not invent a fleet).
2. Prefer **one** `julesfire_review_fleet` (or build then N× create) over N bare-label creates.
3. List→get: `juleslist_sessions` then `julesget_session` with a real id only.
