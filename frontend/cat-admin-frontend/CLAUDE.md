# Cat Admin Frontend — Claude Instructions

## Project Summary
React SPA (Vite + TypeScript) — the operator console for the Whiskers Agent server.
Manages plugin lifecycle, external OAuth connections (Layer 2), and MCP client auth (Layer 1).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Build | Vite + TypeScript |
| UI | shadcn/ui + Tailwind CSS v4 |
| Routing | TanStack Router (file-based, `src/routes/`) |
| Server state | TanStack Query v5 |
| Client state | Zustand v5 |
| Animation | Framer Motion |
| Series charts | Recharts 3 + shadcn `ChartContainer` / `ChartConfig` |

---

## Store Architecture

Two stores, slice-based pattern:

**`useSessionStore`** — persisted to `sessionStorage` under `"whiskers-session"` (v2: **only** `mcpState`; migrate drops leftover auth fields). Auth fields are in-memory after `/api/admin/public/me`. `authProbeDone` is in-memory only; catalog/health/TOTP queries use `useAuthedQueryEnabled` (`authProbeDone && status === "connected"`).
- `AuthSlice` (`authSlice.ts`) — FSM: `disconnected | authorizing | connected`; fields: `status`, `subject`, `scopes`, `expiresAt`
- `SessionSlice` (`sessionSlice.ts`) — MCP pending auth state token; fields: `mcpState`, `setMcpState`, `clearMcpState`, `authProbeDone`, `markAuthProbeDone`

**`useUIStore`** — transient, not persisted
- `UISlice` (`uiSlice.ts`) — `expandedPluginId`, `setExpandedPlugin`

**Selectors** (exported from `store/index.ts`):
- `useAuthState()` — shallow-compared auth fields
- `useMcpState()` — `mcpState` string or null

**Store selector tests**:
- `src/store/__tests__/selectors.test.ts` — uses `renderHook` + `act`/`rerender` to verify `useAuthState` initial shape and `useMcpState` reactivity to `setMcpState`/`clearMcpState` on the live persisted store.

**MCP state flow:**
1. `?state=<token>` arrives in URL at `/` → stored via `setMcpState`, URL param cleared
2. "Return to MCP client" banner shown on dashboard while `mcpState` is set
3. User clicks → navigates to `/oauth/complete-layer1/{state}`; `clearMcpState()` called
4. `/connect` back button passes `mcpState` from store as `?state=` so the banner persists on return

---

## Routes

| Route | File | Purpose |
|---|---|---|
| `/` | `src/routes/index.tsx` | Dashboard: plugin list, MCP return banner, OAuth success banner |
| `/api-keys` | `src/routes/api-keys.tsx` | API Keys management: list active/revoked keys, create keys with show-once token copy, revoke and delete keys |
| `/connect` | `src/routes/connect.tsx` | ct-themed full-page Layer 2 connect screen (generic, driven by `plugin_id` + `provider` params), outside `AppShell` like `/login`. Exports `ConnectPage` for testing. Renders `components/Connect/ConnectScreen` — OAuth vs Direct Login `MethodTabs`, plugin↔provider diagram, `SuccessBanner` once authorized, `ConnectRevokeModal` (typed-confirm disconnect). Provider display metadata (name/handle/color/scopes/desc) comes from a frontend-only map in `components/Connect/providers.ts` (`getProviderMeta`), since `/api/plugins` doesn't expose it. |
| `/login` | `src/routes/login.tsx` | Admin session login |
| `/analytics` | `src/routes/analytics.tsx` | Tunnel Analytics: `?tab=` (`overview` / `tools` / `ask_turns` / `errors`) + `?range=` (`1h` / `24h` / `7d` / `30d`). Controller in `components/analytics/AnalyticsPage.tsx`; Recharts via shadcn `ChartContainer` (MCP vs graph dual-axis, plugin stacked area). |
| `/playground` | `src/routes/playground.tsx` | MCP playground, 3 modes. **MCP**: unified agent experience (backend triage handles chat vs task automatically via SSE `POST /api/playground/stream_goap`; per-message state snapshot renders the GOAP DAG inline under the bubble with fullscreen maximize modal support, real-time user-facing token streaming, and database-backed chat history/sessions sidebar). **Tool Test** / **Group Test**: real calls via `POST /api/playground/tools/invoke`; registry with full JSON schemas from `GET /api/playground/tools` (`useToolRegistry`). The old GOAP Sim tab/route and `goapStore` are removed. |
| `/analytics` | `src/routes/analytics.tsx` | Tunnel analytics. Page is composition only (`AnalyticsPage`): config (`analyticsConfig.ts` ChartConfig palettes + ranges) → pure series adapters → Recharts views (`McpGraphChart`, `StackedTrafficChart`) → section views. Data from `useAnalyticsSummary` / `useTunnelMetrics` / `useAskTurns`. TinySpark stays CSS. Colors are `--chart-1..5`, never raw oklch. |

---

## OAuth Flows (frontend perspective)

**Layer 1 (MCP client auth):**
- MCP client → `/oauth/{state}` (backend) → `/?state=<token>` (frontend)
- Frontend stores `state` in Zustand, shows "Return to MCP client" banner
- User clicks → `/oauth/complete-layer1/{state}` → MCP client receives auth code

**Layer 2 (plugin external OAuth):**
- `Layer2Stub`/`PluginCard`/`PluginDetail` Connect controls → `Link to="/connect"` with `plugin_id`/`provider` search params (PluginCard/PluginDetail no longer open an in-page modal; `RevokeConfirmModal` still handles the "revoke everything for this plugin" action from those list/detail views)
- `/connect` page → OAuth tab CTA links to `/oauth/plugin/{provider}/authorize` (backend PKCE relay); Direct Login tab submits via `useSetDirectCredentialsMutation`/`useClearDirectCredentialsMutation`
- Callback → `/connect?oauth_success={plugin_id}` → `SuccessBanner` shown, "Finish →" enabled
- Disconnect on `/connect` opens `ConnectRevokeModal` (typed-confirm on the provider name) → `useRevokeOAuthMutation` (OAuth) or `clearDirectCredentials` (Direct), then clears `oauth_success` from the URL

---

## Key Components

| Component | Location | Purpose |
|---|---|---|
| `AuthPill` | `components/AuthPill` | Session status + logout |
| `PluginCard` | `components/PluginCard` | Expandable plugin row with enable toggle |
| `Layer2Stub` | `components/Layer2Stub` | Per-provider OAuth connect/revoke controls |
| `DirectLoginForm` | `components/DirectLoginForm` | Username/password credentials form for direct auth |
| `StatsStrip` | `components/StatsStrip` | Plugin count + auth layer summary |
| `ToolsTab` | `components/PluginDetail/ToolsTab` | Unified per-tool list with two toggles: **MCP** exposure (`useToggleToolMutation` / batch `useBatchToolStateMutation` → `api/tools.ts`) + **Route** semantic routing (`useToggleRouteMutation` → `api/routes.ts`, shown only when RAG on). Replaces the old separate Routes tab. |
| `ElevationPrompt` | `components/Terminal/ElevationPrompt` | Card overlay for verification factors (TOTP/Password) to elevate terminal sessions |
| `LogsTab` | `components/PluginDetail/LogsTab` | Paginated, status-filterable (`all`/`ok`/`error`) tool-call history for a plugin — `usePluginLogsQuery` → `GET /api/plugins/{id}/logs` (`tool_call_events`), rendered via the shared `Table`/`Pagination` primitives. |
| `ConfigTab` | `components/PluginDetail/ConfigTab` | Raw-JSON editor for a plugin's `config.json`. Shows on-disk `base` (read-only reference) vs console `override`; saves the full object via `usePluginConfigQuery`/`useSavePluginConfigMutation` → `PUT /api/plugins/{id}/config` (persisted as a DB override, applied on next plugin reload — never writes the file). `useResetPluginConfigMutation` clears the override. |
| `ConnectScreen` | `components/Connect/ConnectScreen` | ct-themed `/connect` page body: method tabs, plugin↔provider diagram, OAuth scope panel / Direct Login form, connected state, primary Connect/Disconnect CTA, Finish-return-to-MCP-client link. Composes `MethodTabs`, `SuccessBanner`, `ConnectRevokeModal`, and `providers.ts` (`getProviderMeta`). |
| `RevokeConfirmModal` | `components/ConnectModal/RevokeConfirmModal` | "Revoke everything" dialog opened from `PluginCard`/`PluginDetail` — clears all connected OAuth providers + direct credentials for a plugin in one action (distinct from `ConnectRevokeModal`, which only revokes the single active method on the `/connect` screen itself). |


---

## Dev

```bash
npm run dev       # Vite HMR on :3000
npm run build     # production build
npm run lint      # ESLint
```

---

## Skills

- `react-generator` — `.claude/skills/react_generator/react-generator-skill.md` — component scaffolding
- `react-app-guide` — `.claude/skills/react-app-guide/react-app-guide.md` — architecture patterns

## Type-Safety & Error Utilities

- `src/utils/errors.ts` — `getErrorMessage(err: unknown)` centralises safe extraction (replaces repeated `err instanceof Error ? err.message : String(err)` and `catch (err: any)`).
- DELETE bodies are now supported: `api.delete(path, body?)` (used by `deletePluginSkill`).
- All targeted `any` casts, `catch (err: any)`, and non-null `!` in type-safety scope were removed (see verify-and-plan-the-composed-pie plan).
- `src/api/catalogClient.ts` — hand-authored typed operation namespaces (`catalogClient.plugins.enable(id)`, `catalogClient.routes.forPlugin(id)`, …) over `callCatalogOp`, one per catalog owner. Every `src/api/*.ts` module calls through it instead of `callCatalogOp` directly, typing both the request args (previously untyped `Record<string, unknown>` in several spots) and the response. `src/api/__tests__/catalogClientBoundary.test.ts` fails CI if a module imports `callCatalogOp` directly. See skill `inference-guide`.
