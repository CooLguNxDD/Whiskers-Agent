# Specialist composer — multi-block GenUI

## Goal classes

| Class | When | Recipe |
|-------|------|--------|
| `scoped_ask` | "show SRE work", subset ask | SCOPED_ASK_PLAN / default scoped |
| `redesign` | full redesign / re-layout | BAKE_REDESIGN_PLAN (≥4 blocks, ≥3 types) |
| `bake_for_job` | job-tailored short_id | compose quality plan then `bake_portfolio_for_job` |
| `discover` | refresh GitHub/Notion index | discovery agent only |

## Quality bar (bake / redesign)

hero → kpiGrid → flowAnim + chart → starStory×2 → archDiagram → **card**×N (or projectGrid) → quickActions

Prefer domain-tinted **`card`** tiles (`domain: ai|devops|mobile|platform`) over a monolithic
projectGrid when the ask is multi-domain. Optional `meta.dag.levels` stamps the Open Design
level-row matrix (L0 Intro → L1 Impact → L2 Projects …) for Home/Ask band layout.

Min blocks: bake 5, redesign 4. Min distinct types: 3.

## Tools

- `compose_scoped_layout` (server) with `block_plan`
- `validate_layout` schema + quality gate
- `bake_portfolio_for_job` for public `?j=` short ids
- Prefer `PortfolioAgent_run(goal=...)` over hand-chaining classic GOAP

## Anti-patterns

- Empty `design_layout(sections=[])`
- Classic GOAP single `design_layout` with no search/build
- Silent success when Notion proxy missing (discovery must error/partial)
