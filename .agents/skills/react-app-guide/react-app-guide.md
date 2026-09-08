# React Web App Engineering Guide
> **Scope**: React SPA (Vite + TS) | **Target**: TanStack Router + Query v5 · Zustand · mitt · shadcn/ui · Framer Motion · Tailwind CSS

---

## 1. Stack Overview

| Layer | Technology | Purpose |
|---|---|---|
| Build & UI | Vite + TypeScript · shadcn/ui | Fast HMR, type-safety, accessible primitives |
| Styling & Router | Tailwind CSS v4 · TanStack Router | Design tokens, type-safe file/code routing |
| Client State | Zustand (Persisted & Non-Persisted) | Global/Tab/Transient client states |
| Server State | TanStack Query v5 | Data caching, pagination, background sync, mutations |
| Signals & Motion | mitt Event Bus · Framer Motion | Decoupled component messaging, smooth micro-interactions |
| Series charts | Recharts 3 · shadcn `ChartContainer` + `ChartConfig` | Config-driven bar/line/area/donut/radar; theme via `--chart-1..5` |

---

## 2. Architecture Principles

* **Separation of Concerns**: *Models* (types) → *Views* (UI) → *Controllers* (Zustand stores/Query hooks) → *Services* (API layers).
* **State Ownership**:
  * **Server Data**: TanStack Query (Remote models, lists, pagination).
  * **Auth / session / JWT**: HttpOnly cookies + `/api/admin/public/me`. Zustand holds live auth in memory; `sessionStorage` only persists `mcpState`. Session-gated queries (`useAuthedQueryEnabled`) wait for `authProbeDone` (set after `/me`) **and** `status === "connected"` — never treat a stored `connected` flag as authenticated.
  * **Preferences**: Zustand `localStorage` (device-persistent).
  * **Transient UI State**: Zustand non-persisted (resets on unmount).
  * **One-Way Signals**: `mitt` bus (fire-and-forget UI updates; no state read-back needed).
* **Anti-Patterns**:
  * ❌ `queryClient.setQueryData` inside `useEffect` (use mutations or optimistic updates instead).
  * ❌ Raw `fetch()` inside UI components (always use `src/api/` client).
  * ❌ Zustand for server data (leads to stale state or duplicate syncing logic).

---

## 3. Project Structure

Routing is **file-based** (`src/routes/` + `@tanstack/router-plugin`). Do not add a parallel code-based `router.tsx`. Session-gated queries live in `src/hooks/` and wait on `useAuthedQueryEnabled`. API modules go through `src/api/catalogClient.ts`, never `callCatalogOp` or raw `fetch()` in UI.

```text
src/
├── main.tsx                        ← QueryClient + ThemeProvider + RouterProvider
├── routes/                         ← TanStack file routes (index, analytics, playground, …)
├── api/
│   ├── client.ts                   ← Cookie session, 401 refresh & retry queue
│   ├── catalogClient.ts            ← Typed catalog namespaces (the only execute path)
│   └── analytics.ts · plugins.ts · …  ← Thin wrappers over catalogClient
├── store/
│   ├── index.ts                    ← Composes slices; exports useAuthState, useMcpState
│   ├── authSlice.ts                ← In-memory FSM after /me (not persisted)
│   ├── sessionSlice.ts             ← mcpState in sessionStorage + authProbeDone
│   └── uiSlice.ts                  ← Non-persisted transient UI layout state
├── hooks/                          ← useQuery / useMutation query-key wrappers
├── events/
│   └── bus.ts                      ← Mitt instance + typed event map
├── components/
│   ├── analytics/                  ← Config + Recharts charts + section views
│   ├── ui/                         ← shadcn primitives (incl. chart.tsx)
│   └── shell/ · playground/ · goap/ · …
├── themes/                         ← *.theme.json globbed by registry.ts
├── types/
│   └── plugin.ts · oauth.ts · proxy.ts
└── index.css · ct-theme.css · globals.css
```

---

## 4. Routing & OAuth Callback

Composes code-based TanStack routing with PKCE handshake redirect capture:

```typescript
// src/router.tsx
import { createRouter, createRoute, createRootRoute } from '@tanstack/react-router'
import { RootLayout, ConsolePage, CallbackPage } from './pages'

const rootRoute = createRootRoute({ component: RootLayout })
const consoleRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: ConsolePage })
const callbackRoute = createRoute({ getParentRoute: () => rootRoute, path: '/callback', component: CallbackPage })

export const router = createRouter({ routeTree: rootRoute.addChildren([consoleRoute, callbackRoute]) })

// src/pages/CallbackPage.tsx - Isolates URL interception and navigates home
export function CallbackPage() {
  const { handleCallback } = useOAuth()
  const navigate = useNavigate()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    if (code && state) {
      handleCallback(code, state).then(() => navigate({ to: '/' }))
    }
  }, [])

  return <div className="animate-pulse flex items-center justify-center h-screen">Authorizing…</div>
}
```

---

## 5. Server State (TanStack Query v5)

Exposes typed query keys, queries, and mutations wrapped with standard optimistic updates (cancel → snapshot → set → rollback):

```typescript
// src/hooks/useMods.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getMods, toggleMod } from '../api/mods'

export const modsQueryKey = ['mods'] as const

export function useModsQuery() {
  return useQuery({ queryKey: modsQueryKey, queryFn: getMods, staleTime: 5 * 60 * 1000 })
}

export function useToggleModMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => toggleMod(id, enabled),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: modsQueryKey })
      const prev = qc.getQueryData(modsQueryKey)
      qc.setQueryData(modsQueryKey, (old: Mod[]) => old.map(m => m.id === id ? { ...m, enabled } : m))
      return { prev }
    },
    onError: (_err, _vars, ctx) => qc.setQueryData(modsQueryKey, ctx?.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: modsQueryKey }),
  })
}
```

---

## 6. Client State (Zustand Store Slicing)

```typescript
// src/store/authSlice.ts
export type ConnectionStatus = 'disconnected' | 'authorizing' | 'connected'
export interface AuthSlice {
  status: ConnectionStatus
  clientName: string | null
  setConnected: (clientName: string) => void
  setDisconnected: () => void
}
export const createAuthSlice: StateCreator<AuthSlice> = (set) => ({
  status: 'disconnected',
  clientName: null,
  setConnected: (clientName) => set({ status: 'connected', clientName }),
  setDisconnected: () => set({ status: 'disconnected', clientName: null }),
})

// src/store/index.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { useShallow } from 'zustand/react/shallow'

export const useSessionStore = create<AuthSlice>()(
  persist(createAuthSlice, { name: 'whiskers-session', storage: createJSONStorage(() => sessionStorage) })
)
export const useAuthState = () => useSessionStore(useShallow(s => ({ status: s.status, clientName: s.clientName })))
```

---

## 7. UI Signals (mitt Event Bus)

Ideal for decoupled side-effects (toasts, micro-animations, or session expiration handlers):

```typescript
// src/events/bus.ts
import mitt from 'mitt'
export type AppEvents = { 'mod:toggled': { id: string; enabled: boolean }; 'auth:expired': undefined }
export const bus = mitt<AppEvents>()

// Emitter
bus.emit('mod:toggled', { id, enabled })

// Subscriber
useEffect(() => {
  const handler = ({ id }: AppEvents['mod:toggled']) => id === targetId && triggerPulse()
  bus.on('mod:toggled', handler)
  return () => bus.off('mod:toggled', handler)
}, [targetId])
```

---

## 8. Styling & shadcn/ui Bridges

Design tokens define styling constraints using standard CSS variables inside Tailwind config:

```css
/* src/styles/tokens.css */
:root {
  --bg: #0a0a0c;
  --surface: #141418;
  --border: #2d2d35;
  --amber: #fbbf24;
}
```
```typescript
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: { bg: 'var(--bg)', surface: 'var(--surface)', border: 'var(--border)', amber: 'var(--amber)' },
      fontFamily: { mono: ['JetBrains Mono', 'monospace'] }
    }
  }
}
```

---

## 9. Animation (Framer Motion)

Enforces performant, reduced-motion-aware transitions. Always wraps conditional components inside `<AnimatePresence>`:

```typescript
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'

export function AnimatedCard({ isExpanded, children }: { isExpanded: boolean, children: React.ReactNode }) {
  const reduced = useReducedMotion()
  return (
    <AnimatePresence>
      {isExpanded && (
        <motion.div
          initial={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
          animate={reduced ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
          exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          className="overflow-hidden"
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
```

---

## 10. API Client (Fetch with Auto-Refresh)

A robust central client that handles automatic JWT authorization bearer injections and refreshes on 401:

```typescript
// src/api/client.ts
import { tokenStore } from '../auth/tokenStore'
import { bus } from '../events/bus'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = tokenStore.get()
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  })

  if (res.status === 401) {
    const refreshed = await performTokenRefresh() // exchanges refresh_token on backend
    if (!refreshed) {
      bus.emit('auth:expired', undefined)
      throw new Error('Session expired')
    }
    return request<T>(path, init) // retry once
  }

  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, b: unknown) => request<T>(p, { method: 'POST', body: JSON.stringify(b) }),
  put: <T>(p: string, b: unknown) => request<T>(p, { method: 'PUT', body: JSON.stringify(b) }),
  delete: <T>(p: string, b?: unknown) => request<T>(p, { method: 'DELETE', body: b !== undefined ? JSON.stringify(b) : undefined }),
}
```

---

## 10.1. Error Handling (type-safe)

Use the shared helper to avoid `catch (err: any)` and `err.message` casts:

```ts
import { getErrorMessage } from "@/utils/errors"

try { ... } catch (err) {
  setError(getErrorMessage(err, "Operation failed"))
}
```

- `src/utils/errors.ts` — `getErrorMessage(err: unknown, fallback?)` handles Error | string | unknown.
- The `api/client.ts` always throws plain `Error` on failure, so `instanceof Error` branch is reliable.
- Also used for react-query `onError` callbacks (err is `unknown`).

---

## 11. OAuth PKCE Rules & MCP State Persistence

* **Token Storage**: `access_token` in memory only. `refresh_token` in `sessionStorage` (survives refreshes in the active tab; cleared on tab close).
* **MCP State Capture**: When an MCP client initiates a Layer 1 handshake, a `?state=<mcp_token>` param is passed. This token is stored in Zustand (`sessionSlice`) to survive navigation/callbacks, then cleared upon completion when returning back to the MCP client.

```typescript
// src/store/sessionSlice.ts
export interface SessionSlice {
  mcpState: string | null
  setMcpState: (state: string | null) => void
  clearMcpState: () => void
}
export const createSessionSlice: StateCreator<SessionSlice> = (set) => ({
  mcpState: null,
  setMcpState: (mcpState) => set({ mcpState }),
  clearMcpState: () => set({ mcpState: null }),
})
```

---

## 12. Vite Config Proxy

Configures API proxies pointing to the local MCP FastMCP backend, exporting build targets to the serving FastAPI static asset directories:

```typescript
// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  server: {
    proxy: {
      '/api/': 'http://localhost:8000',
      '/oauth/': 'http://localhost:8000',
      '/admin/': 'http://localhost:8000',
      '/mcp/': 'http://localhost:8000',
      '/terminal/': 'http://localhost:8000',
      '/analytics/ws': 'http://localhost:8000',
    }
  },
  build: { outDir: '../console/dist', emptyOutDir: true },
})
```

---

## 13. Playground (live-wired)

`/playground` (`src/routes/playground.tsx`) has 3 modes, all wired to real endpoints:

- **ChatMode** (`components/playground/ChatMode.tsx`): engine toggle —
  - `agent`: SSE `POST /api/playground/stream_goap`. Each assistant message accumulates a merged `values` state snapshot (`msg.goapState`) + token log; the GOAP DAG renders **inline** under the bubble via `components/goap/GoapInline.tsx`, which calls the pure `buildSimState()` in `components/goap/simDerive.ts` (extracted from the deleted `goapStore`). The DAG renders the simplified 7-node conceptual execution lifecycle (`input` -> `embedder` -> `planner` -> `gate` -> `bev` -> `dispatch_sum` -> `goal`). The inspector includes a dedicated **Session Memory** tab rendering `SessionMemoryView` (showing overarching goal, working memory facts, iterations vs max iterations progress bar, and the latest natural language summary). Confirm flow (`status === "confirmation_needed"`) re-streams `force_execute=true` into the SAME message. Cancelling sets `goapState.__cancelled` (downstream nodes render skipped).
  - `llm`: SSE `POST /api/playground/chat_llm` — stateless plain chat; the full visible thread is sent as `messages[]` each turn.
- **ToolTestMode / GroupTestMode**: real invocations via `POST /api/playground/tools/invoke` (`{tool, arguments}` → `{ok, ms, structured_content, content, error, error_type}`); registry with full JSON schemas from `GET /api/playground/tools` through `useToolRegistry()` (TanStack Query, maps to legacy `PGTool` shape). Group test runs a bounded promise pool (4) in parallel mode — calls have real side effects.
- **SSE plumbing**: `src/api/playground.ts` `streamSSE()` — fetch + ReadableStream parse of `data: {...}\n\n`, with one 401 → `tryRefreshSession()` (exported from `api/client.ts`) retry.
- The old GOAP Sim tab (`routes/playground-goap.tsx`), `store/goapStore.ts`, and `GoapQueryBar.tsx` are **deleted**.

**MCP-mode (MCP Client-Host via `McpMode` + `src/api/mcpClient.ts`)**: Uses the `@modelcontextprotocol/sdk` `Client` over `StreamableHTTPClientTransport` calling the `run_graph` tool directly. Elicitations (confirm/clarify) are answered in-band via `setElicitationResolver` + `ElicitRequestSchema` handler. For long human waits, `runGraphMcp` passes `{ resetTimeoutOnProgress: true, onprogress }` to `callTool` (3rd arg) so server `report_progress` keepalives (every 15s from `_elicit_keepalive`) reset the SDK's 60s timer; see dedicated test `src/api/__tests__/mcpClient.test.ts`. Falls back to two-call `*_needed` path when no progressToken.

---

## 14. Tri-state Tool Exposure & Permission Controls

Provides unified, category-grouped management for MCP tools:
- **Tri-State Exposure**: Replaces legacy dual-toggles with one 3-stage FSM switch (`enabled` / `hidden` / `disabled`) backed by `/api/plugins/{plugin_id}/tools/{tool_name}/state`.
- **Permission Modes**: Exposes Native `<select>` modes: `auto` (auto-allow read/write, confirmation=false), `approval` (allow read/write, confirmation=true), or `custom` (reveals R/W/C individual toggles).
- **Two-Level Grouping**: Tools are grouped dynamically by declared tag categories (`group`) and access methods (`access` = `read` | `write`). Category/subgroup headers display bulk-toggles mapping to member tools.
- **State Helpers**: `src/lib/toolState.ts` maps `is_enabled` and `is_hidden` values into the unified FSM states, stable groups tools, and aggregates state/permission values over sets.

---

## 15. Charts & Analytics (config-driven Recharts)

`/analytics` (`src/routes/analytics.tsx`) owns `?tab=` + `?range=` (same `validateSearch` pattern as `/config?section=`). `AnalyticsPage` is the controller: TanStack Query + WS overlay, no Zustand for server data.

Series charts (bar / line / area / donut / radar) go through `src/components/ui/chart.tsx` — a shadcn `ChartContainer` that injects `--color-<key>` from a `ChartConfig`. Never hand-roll SVG for those kinds. Sparklines (tiny CSS bars) may stay non-Recharts.

```ts
import type { ChartConfig } from "@/components/ui/chart"

const MCP_GRAPH_CONFIG = {
  mcp: { label: "MCP calls", color: "var(--chart-1)" },
  graph: { label: "Graph runs", color: "var(--chart-4)" },
} satisfies ChartConfig
```

Rules:
* Colors are `--chart-1..5` aliases of theme tokens (`index.css`). No raw `oklch(...)` in chart components.
* `ChartConfig` is the palette/legend source of truth.
* **MCP vs graph** (`McpGraphChart`): `AreaChart` `type="natural"`, dual `YAxis` (`yAxisId="mcp"` left, `yAxisId="graph"` right). Series toggles live in card-local `useState`; `nextSeriesVisibility` refuses both-off. Plugin stacked area is Tools-tab only (`stackId="plugin"`, shared Y-axis).
* Do **not** set `role="img"` on `ChartContainer` when the child chart uses `accessibilityLayer` — that collapses the keyboard-navigable data layer to a single image node. Keep `aria-label`.
* Resolve chart `kind` with `CHART_KINDS.includes` (own-property), never `kind in REGISTRY` (prototype walk). Fold the series index into `seriesKey` so duplicate names stay distinct; missing points are `null` (gap), not `0`.

