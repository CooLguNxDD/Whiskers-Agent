---
name: block-authoring
description: The 10-step checklist for adding a NEW block type to CatPortfolio — Zod schema, React component, registry, tests, Python mirror sync. Use only when no existing block type can express what the layout needs.
---

# Block authoring — new block type checklist

Adding a block type touches the runtime whitelist. Every step is mandatory;
CI enforces most of them (compile error via `satisfies`, mirror-drift test).
Example type used below: `timeline`.

## 1. Zod schema member — `src/content/schema.ts`

```ts
const Timeline = z.object({ type: z.literal("timeline"), id: z.string(), props: z.object({
  events: z.array(z.object({ date: z.string(), title: z.string(), detail: z.string().optional() })) }) });
```

Add it to the `z.discriminatedUnion("type", [...])` array. Match the file's
existing compact style. URLs use `z.string().url()`; lists that may be omitted
use `.default([])`.

## 2. Component — `src/blocks/Timeline.tsx`

- Props type comes from the schema: component receives `Layout["blocks"][n]["props"]` for its type (see existing blocks for the pattern — they type props structurally, e.g. `PropsOf<"timeline">` via `src/render/registry.ts` helpers).
- Tailwind + theme CSS custom properties only — never hardcode colors (OKLCH tokens are injected by ThemeProvider).
- Respect reduced motion (`useReducedMotion` from `motion/react`) if animating.
- Heavy dependencies (like mermaid) go in a lazy sibling component loaded via `React.lazy`, NOT exported from the barrel.

## 3. Barrel export — `src/blocks/index.ts`

Add `export { Timeline } from "./Timeline";` (match existing style).

## 4. Registry entry — `src/render/registry.ts`

Add `timeline: Timeline` to `REGISTRY`. The `satisfies` clause makes a missing
entry a compile error — if `npm run build` fails here, you skipped this step.

## 5. Tests

- `src/blocks/__tests__/Timeline.test.tsx` — renders with valid props.
- Extend the fixture coverage in `src/content/__tests__/content.test.ts`
  expectations if they enumerate block types/counts.
- The registry↔schema drift test in `src/render/__tests__` passes automatically
  once steps 1+4 are consistent.

## 6. Use it — `design/layout.yaml`

The new type may now appear in the layout. Run `npm run compile:layout`.

## 7. Python mirror sync — `design/pending-mirror/<yyyy-mm-dd>-timeline.md`

The schema is mirrored in `OpenCat-Mcp-Full/utils/ui_layout_schema.py`. Write a
pending-mirror file containing the ready-to-paste Pydantic patch:

```markdown
# Pending mirror sync: timeline

Add to `OpenCat-Mcp-Full/utils/ui_layout_schema.py` (and to its BLOCK_TYPES /
discriminated union):

    class TimelineEvent(BaseModel):
        date: str
        title: str
        detail: str | None = None

    class TimelineProps(BaseModel):
        events: list[TimelineEvent]

    class TimelineBlock(BaseModel):
        type: Literal["timeline"]
        id: str
        props: TimelineProps
```

Do NOT edit `design/mirror-manifest.json` yet — it only lists types the mirror
actually has. The mirror-drift test accepts either manifest membership or a
`*-timeline.md` pending file.

**Local mode with OpenCat-Mcp-Full accessible:** apply the patch to
`utils/ui_layout_schema.py` directly on a branch there, update
`mirror-manifest.json` (add type + bump `lastSynced`), delete the pending file,
and open a second, cross-linked PR in OpenCat-Mcp-Full.

## 8. CLAUDE.md

Update block count and Project Structure in CatPortfolio's `CLAUDE.md`
(it names the blocks and says "The 7 whitelisted block components").

## 9. Full gate

```
npm run compile:layout && npm run lint && npm run test && npm run build
```

## 10. PR

Ship via the portfolio-pr skill. New block types ALWAYS need human review —
call them out at the top of the PR body with a `## Mirror drift` section.
