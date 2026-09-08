---
description: Generate portfolio layout/components from a brief and open a PR (CatPortfolio)
argument-hint: "<brief> [audience=<recruiter|hiring-manager|peer|default>]"
---

You are running the portfolio generation pipeline for the CatPortfolio repo.
The working directory must be the CatPortfolio repo root (contains `design/layout.yaml`).

Brief: $ARGUMENTS

Follow these steps exactly:

## 1. Load context

- Read `design/design.md` (design contract: tokens, voice, audience guidance, block rules).
- Read `design/layout.yaml` (current layout source of truth).
- Read `src/content/schema.ts` (the block whitelist — 7+ types, discriminated union).
- Read `CLAUDE.md` for repo rules.

## 2. Live context (optional, degrade gracefully)

If a Whiskers Agent MCP server is available in this session, call:
- `get_design_context` (audience template + design tokens + supported block types)
- `get_projects` / `get_star_stories` for fresh content

Also load `design/sources.yaml` and apply the **context-sourcing skill** to
resolve each declared source:
- `kind: github` → `fetch_external_context(kind="github", ...)` via OCT, or
  `gh api repos/{ref}` + README as CI fallback.
- `kind: url` / `kind: gdoc` → `fetch_external_context` via OCT, or `WebFetch`
  as CI fallback.
- `kind: notion` → `fetch_external_context` via OCT; skip + note in PR body
  when OCT is offline.
- For project-linked sources, call `get_project_context(slug="...")` to
  resolve all per-project `context_sources` in one call.
- For ad-hoc OCT tool discovery when connected, `run_graph` / `discover_tools`
  are available (optional).

If OCT is NOT reachable (typical in CI), do NOT fail — use the committed
`design/` files and existing `src/content/layout.json` content as context.
Record resolved / skipped sources for the PR body.

## 3. Generate

- Edit `design/layout.yaml` to satisfy the brief. Follow the layout-authoring
  skill (grammar, audience section ordering, block usage rules).
- Only if the brief genuinely requires a presentation that no existing block
  type can express: create a new block type following the block-authoring
  skill's 10-step checklist (schema member, component, barrel, registry,
  tests, pending-mirror file, CLAUDE.md).
- Never edit `src/content/layout.json` by hand — it is compiled output.

## 4. Verify (the full gate — all must pass)

```
npm run compile:layout
npm run lint
npm run test
npm run build
```

Fix failures and re-run until green. Do not proceed with a red gate.

## 5. Ship

Follow the portfolio-pr skill:
- Branch `portfolio-gen/<yyyy-mm-dd>-<slug>` (NEVER commit to main).
- Commit layout.yaml + compiled layout.json (+ any new component files) together.
- `git push -u origin <branch>` and `gh pr create` using the PR body template.
- If `design/pending-mirror/` gained files, add the `## Mirror drift` section
  to the PR body; if the OpenCat-Mcp-Full repo is accessible locally, also apply
  the Pydantic patch there on a branch and open a cross-linked second PR.
- Print the PR URL(s) as the final output.

Hard rules: never push to `main`; never modify `.github/workflows/deploy.yml`;
never bypass the compile/lint/test/build gate.
