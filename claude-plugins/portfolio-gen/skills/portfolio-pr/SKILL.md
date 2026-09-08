---
name: portfolio-pr
description: Branch, commit, and PR conventions for shipping generated CatPortfolio changes. Use at the end of every portfolio generation run — local or CI.
---

# Portfolio PR conventions

## Hard rules

- NEVER push to `main`. The PR is the deploy gate — merge triggers Pages deploy.
- NEVER modify `.github/workflows/deploy.yml`.
- Never open a PR with a red gate (`compile:layout`, `lint`, `test`, `build`).

## Branch

```
portfolio-gen/<yyyy-mm-dd>-<slug>
```

`<slug>` = 2-4 kebab words from the brief (e.g. `portfolio-gen/2026-07-07-recruiter-stats`).
In CI, append the run id if provided (e.g. `portfolio-gen/2026-07-07-brief-slug-<run_id>`).

## Commit

- `design/layout.yaml` and `src/content/layout.json` always change together.
- New block type: include all checklist artifacts in ONE commit (schema,
  component, barrel, registry, tests, pending-mirror file, CLAUDE.md).
- Message: conventional, e.g. `feat(layout): recruiter-focused layout with timeline block`.

## PR

```
gh pr create --title "<concise change>" --body-file <tempfile>
```

Body template:

```markdown
## What changed
<bullets: layout changes, new block types>

## Brief
<the original brief, verbatim>

## Gate
- [x] compile:layout  - [x] lint  - [x] test  - [x] build

## Context sources
| id | kind | resolved via | notes |
|----|------|--------------|-------|
| source-id | github | fetch_external_context (OCT) | |
| other-id | notion | skipped | OCT offline, no fallback |

## Blocks
<table or bullets: block order before → after; new types flagged>

## Mirror drift            <!-- only when design/pending-mirror/ changed -->
New block type(s) pending Python mirror sync in OpenCat-Mcp-Full/utils/ui_layout_schema.py:
- design/pending-mirror/<file>.md
Cross-linked OCT PR: <url or "not yet opened — OCT repo not accessible from this run">
```

## Dual-repo runs (local, new block type)

When OpenCat-Mcp-Full is accessible: branch there too
(`portfolio-mirror/<yyyy-mm-dd>-<type>`), apply the Pydantic patch to
`utils/ui_layout_schema.py`, run its schema tests
(`pytest test/unit/test_ui_layout_format.py`), open the OCT PR first, then
reference its URL from the CatPortfolio PR's Mirror drift section, and in the
same CatPortfolio PR update `design/mirror-manifest.json` + remove the
pending-mirror file.

## Output

Always end by printing the PR URL(s).
