# Design system usage (agent read order)

1. Read DESIGN.md for voice, anti-patterns, and band intent.
2. Apply tokens.json as `meta.themeOverrides` (CSS variables only).
3. Read settings.json for hero identity, layout_presets, quick_actions, design_tokens prose.
4. Choose a page recipe, then plan LayoutPlan steps.
5. Search portfolio context before authored blocks.
6. Never invent metrics, employers, or commit counts.

## Package layout

```
design_systems/default/
  USAGE.md
  DESIGN.md
  tokens.json      # CSS-var themeOverrides
  settings.json    # hero, layout_presets, quick_actions, audiences, design_tokens
```

Operational discovery/allowlist config stays in plugin `manifest.json` — not here.
