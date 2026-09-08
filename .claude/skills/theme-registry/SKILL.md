---
name: theme-registry
description: Shared theme vocabulary — JSON files are the only id list. Python utils.theme_registry, frontend registry.ts isLight, fish-tank water tokens. USE FOR: adding a theme, SVG/TUI colors, light/dark CSS, CatPortfolio gen:themes.
---

# Theme registry

A new theme is a `*.theme.json` file under `frontend/cat-admin-frontend/src/themes/`. Consuming code reads the registry; it never hardcodes an id list or a parallel hex table.

## Canonical source

`frontend/cat-admin-frontend/src/themes/*.theme.json` (oklch tokens, optional `extends`). CatPortfolio keeps a copy; refresh with `npm run gen:themes` (needs live OCT). `check:themes` is manual/pre-release, not CI.

## Python (`utils/theme_registry.py`)

Importable from plugins and `terminal/tui/` without crossing plugin↔core internals.

- `load_raw_themes()` / `public_raw_defs()` — unresolved JSON (for `theme_defs` on design-context).
- `resolve_theme_vars` — same ancestors-first merge as `registry.ts` (cycle/missing/max-depth → own vars).
- `oklch_to_hex` — stdlib OKLCH→sRGB; `var(--name)` one-level; `#hex` passthrough; `None` on garbage.
- `THEME_REGISTRY` / `SUPPORTED_THEMES` — built at import. Missing themes dir → cozy/neon/paper hex fallback (cosmetic fail-safe).

Call sites: `plugins/portfolio_plugin/themes.py` re-exports `SUPPORTED_THEMES`; SVG and TUI map **token names**, not colors.

## Light/dark

`ThemeDef.isLight` = OKLCH L of `vars.bg` > 0.6. Admin CSS uses `.ct-root[data-light="true"]`. Keep per-flavor `[data-accent][data-theme="paper"|"latte"]` color overrides; do not gate structural rules on ids.

## Fish tank water

Optional CSS vars `water` and `water-deep` (hex or oklch). Absent → existing dark/light formula. Do not add a per-id lookup table.
