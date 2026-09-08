---
name: react-generator-skill
description: >
  Use this skill when the user wants to scaffold new React (web) components,
  or when working with the project's existing React Native generator scripts
  (navigator, screen, component, colors). Covers when to run each script,
  what flags to pass, what files get created, and the manual wiring steps
  that always follow generation.
---

# React Generator Skill

This project ships a set of `scripts/generate-*.ts` scaffolding tools.
Each script produces boilerplate that matches the project's coding conventions
so you don't write repetitive setup by hand.

---

## Scripts at a glance

| Script | Target stack | What it creates |
|---|---|---|
| `generate-component-web.ts` | React (web) | `<Name>.tsx` + `index.ts` barrel, inside `src/<dir>/<Name>/` |
---

## React web — `generate-component-web.ts`

### Stack context

The web generator targets the **Cat Tunnel Operator Console** stack:
Vite + React 18 + TypeScript · Tailwind CSS · shadcn/ui (`cn()`) · Framer Motion · Zustand · mitt · TanStack Query v5 · TanStack Router.

### Usage

```bash
npx ts-node scripts/generate-component-web.ts <Name> [options]
```

### Options

| Flag | Effect |
|---|---|
| `--dir <path>` | Subdirectory under `src/` (default: `components`) |
| `--motion` | Add Framer Motion `motion.div` with enter/exit animation + `AnimatePresence` |
| `--store` | Add Zustand `useUIStore` import stub |
| `--events` | Add mitt event bus import + typed `useEffect` listener stub |
| `--query` | Add TanStack Query `useQuery` stub |
| `--page` | Alias for `--motion --store` (typical page-level component) |

### Examples

```bash
# src/components/ModCard/ — with Framer Motion + Zustand
npx ts-node scripts/generate-component-web.ts ModCard --motion --store

# src/components/AuthPill/ — with Framer Motion + mitt event listener
npx ts-node scripts/generate-component-web.ts AuthPill --motion --events

# src/components/StatsStrip/ — with TanStack Query stub
npx ts-node scripts/generate-component-web.ts StatsStrip --query

# src/pages/ConsolePage/ — --page alias = --motion + --store
npx ts-node scripts/generate-component-web.ts ConsolePage --page --dir pages

# src/components/Badge/ — plain, no extras
npx ts-node scripts/generate-component-web.ts Badge
```

### Output structure

```
src/<dir>/<Name>/
  <Name>.tsx    ← functional component; cn() for className merging; stubs per flags
  index.ts      ← barrel: re-exports default + Props type
```

> Note: CSS module files are no longer generated. Styling is handled via Tailwind
> utilities passed to `cn()` in the component root — consistent with shadcn/ui conventions.

### Generated component shape (all flags)

```tsx
import type { FC, ReactNode } from "react"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"
import { useUIStore } from "@/store"
import { useEffect } from "react"
import { bus } from "@/events/bus"
import { useQuery } from "@tanstack/react-query"

export interface ModCardProps {
  /** Content rendered inside the component. */
  children?: ReactNode
  /** Additional Tailwind class names merged onto the root element. */
  className?: string
}

const ModCard: FC<ModCardProps> = ({ children, className }) => {
  // TODO: replace with real query key + fetcher
  const { data, isPending } = useQuery({
    queryKey: ["modcard"],
    queryFn: () => Promise.resolve(null),
  })

  // TODO: select only the slices you need
  const expandedModId = useUIStore(s => s.expandedModId)

  useEffect(() => {
    // TODO: replace event name + handler
    const handler = () => {}
    bus.on("mod:toggled", handler)
    return () => bus.off("mod:toggled", handler)
  }, [])

  return (
    <AnimatePresence>
      <motion.div
        className={cn("flex flex-col", className)}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  )
}

export default ModCard
```

### After generation

1. Import via barrel: `import ModCard from "@/components/ModCard"`
2. Extend `<Name>Props` with real props as you build out the component.
3. Replace `// TODO` stubs with real query keys, store selectors, or event names.
4. Style with Tailwind utilities — add classes to the `cn()` call or child elements.

---

## React Native — `generate-component.ts`

### Usage

```bash
python scripts/generate_component_web.py <Name> [--dir <subdirectory>]
```

### Examples

```bash
python scripts/generate_component_web.py ModCard --motion --store
python scripts/generate_component_web.py AuthPill --motion --events
python scripts/generate_component_web.py ConsolePage --page --dir pages
python scripts/generate_component_web.py Badge
```

### Generated shape

Functional component with `useAppTheme`, typed `ThemedStyle<ViewStyle>` and
`ThemedStyle<TextStyle>` constants at the bottom of the file.


## Conventions enforced by all generators

- **PascalCase** component and file names — pass the name in any casing; scripts normalise it.
- **No overwriting** — every script exits with an error if the target file already exists.
- **Barrel exports** (web) — `index.ts` always re-exports the default and the Props type.
- **Relative imports** — generated files use `@/` path aliases; make sure `tsconfig.json`
  has `"paths": { "@/*": ["src/*"] }` (web) or the equivalent RN alias config.
- **Web: no CSS modules** — Tailwind utilities via `cn()` replace scoped `.module.css` files.
