---
name: layout-authoring
description: Grammar and rules for editing CatPortfolio's design/layout.yaml — the layout source of truth compiled to src/content/layout.json. Use when changing portfolio layout content, block order, or audience targeting.
---

# Layout authoring (design/layout.yaml)

`design/layout.yaml` is the only file you edit for layout changes. It compiles
to `src/content/layout.json` via `npm run compile:layout`; CI rejects PRs where
the two are out of sync (`npm run check:layout`).

## File grammar

```yaml
version: 1                 # literal 1
theme: cozy                # compile-time only — must match an id in src/themes/*.theme.json
meta:
  audience: default        # recruiter | hiring-manager | peer | default
  generatedAt: auto        # "auto" → compiler stamps ISO time (or explicit ISO string)
blocks:                    # ordered list; each block: { type, id, props }
  - type: hero
    id: h1                 # ids unique within the file, short kebab/alnum
    props: { ... }         # shape defined per-type in src/content/schema.ts
```

The `theme` key is stripped at compile time; everything else must pass the Zod
`LayoutSchema` in `src/content/schema.ts` — read it before writing props. All
`href` values must be absolute URLs (relative paths fail validation).

## Block types (current whitelist)

| type | props | notes |
| --- | --- | --- |
| hero | name, tagline, pitch?, links?[{label,href}] | one per layout, always first |
| statStrip | stats[{label,value}] | short values ("144 → 2–3"), lowercase labels |
| projectGrid | projects[{id,name,summary,tags[],metrics[],links[]}] | summaries ≤ 2 sentences, ≥ 1 tag each |
| starStory | situation, task, action, result, tags[] | single sentences per field |
| archDiagram | title, kind(mermaid\|svg), source | prefer mermaid; use `source: \|-` block scalar |
| codeSnippet | lang, code, caption? | code must be real code from the repos, never invented |
| prose | markdown | GFM (tables OK); use `\|-` block scalar |

If the schema has grown beyond these, `src/content/schema.ts` is authoritative.

## Audience section ordering

- recruiter: hero → statStrip → projectGrid → rest (breadth, impact metrics)
- hiring-manager: hero → starStory first among content (STAR outcomes, ownership)
- peer: hero → archDiagram/codeSnippet early (technical depth)
- default: balanced, one of everything

Voice: first-person, confident, playful-but-precise ("Andrew the cat");
metrics beat superlatives; no filler adjectives. Full contract: `design/design.md`.

## YAML style

- Multi-line text: `|-` block scalars (markdown, code, mermaid).
- Long single sentences: `>-` folded scalars.
- Quote values that YAML would mistype (`"144"`, values starting with `~` are fine unquoted only if intended as strings — when unsure, quote).

## After editing

Always run `npm run compile:layout` and commit BOTH `design/layout.yaml` and
`src/content/layout.json`. Never hand-edit the JSON.
