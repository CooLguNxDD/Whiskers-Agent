---
name: jules-review-fleet
description: Fire optional parallel Jules code-review sessions via Whiskers Agent server MCP tools (roles opt-in, not a fixed fleet). Use for jules review, fire sessions, code health, doc pass, diff/full scan.
---

## Server owns the driver (not the client)

The fleet builder and fire path live on the **Whiskers Agent server**:

| Server surface | Purpose |
|----------------|---------|
| `julesbuild_review_fleet` | Build configs only (no Jules API) |
| `julesfire_review_fleet` | Build + CreateSession on server |
| `plugins/jules_plugin/review_fleet/` | Templates + optional host CLI |
| `plugins/jules_plugin/skills/jules-sessions/SKILL.md` | GOAP skill |

**Roles are optional.** Empty / omitted → **no sessions** (no automatic 2+2+1).

Prefer MCP tools over local `driver.py`. The skill-local launcher is only a
fallback for offline config inspection.

## Preferred agent path (MCP)

1. Resolve tool prefix (`mcp__whiskers-tunnel-local__jules*` or native).
2. Call **once**:

   ```
   julesfire_review_fleet(
     roles="backend-b,docs",   # or "" / omit = fire nothing; "all" = classic five
     mode="diff",              # or "full"
     base="main",              # required for diff
     repo="owner/name",
     branch="feat-branch",
   )
   ```

3. Record `sessions[].sessionId` / `url` per role.
4. Later: `julesget_session`, `juleslist_activities`, `julesapprove_plan` if needed.

Dry-run (configs only, no create):

```
julesbuild_review_fleet(roles="all", mode="full", repo="owner/name", branch="main")
```

## Roles

| value | sessions |
|-------|----------|
| omitted / `""` | **none** |
| `frontend-a` | 1 |
| `frontend-a,backend-b,docs` | 3 |
| `all` | frontend-a/b, backend-a/b, docs |
| `transport,modding` | 2 custom domain sessions |

Valid keys: `frontend-a`, `frontend-b`, `backend-a`, `backend-b`, `docs`, or any domain slug. Distinct roles that sanitize to the same `CODE_HEALTH_*.md` filename (`frontend-a` + `Frontend-A`, `a/b` + `a?b`) raise.

Examples:

- PR security pass → `roles=backend-b`, `mode=diff`, `base=main`
- Classic fleet → `roles=all`
- “Just show me configs” → `julesbuild_review_fleet`, no fire

## Modes

- **full** — deep scan of scoped paths
- **diff** — only `{base}...HEAD` three-dot (pass `base` / `target-branch`)

Paths: `path`, `frontend_path`, `backend_path`, `docs_path` (default `.`).

### Diff merge-base resilience

A Jules session that cannot compute `git merge-base {base} HEAD` must **not**
fall back to two-dot `{base}..HEAD`. Two-dot inverts `{base}`-only work into
phantom deletions on a shallow clone.

1. `git merge-base {base} HEAD`
2. On failure: `git fetch --unshallow origin` (or `git fetch --deepen=200 origin`)
3. Retry merge-base. Still missing → record a shallow-clone notice at the top of
   the report and review the scoped files on the branch directly. Do not abort
   with an empty "Merge-base Unavailable" report, and do not invent a two-dot
   file list.

Prompts are expanded in `plugins/jules_plugin/review_fleet/templates.py`.

## Do not

- Seed `prompt: ["frontend-b", "docs"]` into `julescreate_session`
- Assume a fixed five-agent fleet when the user did not ask for roles
- Run client `driver.py` when Whiskers Agent is available
- Use two-dot `{base}..HEAD` when three-dot `{base}...HEAD` cannot be computed

## Optional host CLI (server checkout only)

```bash
# On the machine that has Whiskers-Agent-Full (server/dev host)
python plugins/jules_plugin/review_fleet/driver.py --roles ""          # → []
python plugins/jules_plugin/review_fleet/driver.py --roles all --mode diff --base main
```

## Report files (when roles fire)

| role | report |
|------|--------|
| frontend-a | `CODE_HEALTH_FRONTEND_A.md` |
| frontend-b | `CODE_HEALTH_FRONTEND_B.md` |
| backend-a | `CODE_HEALTH_BACKEND_A.md` |
| backend-b | `CODE_HEALTH_BACKEND_B.md` |
| docs | `DOC_GAPS_REPORT.md` |
