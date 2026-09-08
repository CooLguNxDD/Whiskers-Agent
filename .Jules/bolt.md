## 2024-07-04 - [LiveLogs Array Prepending Render Thrashing]
**Learning:** Using array indices as React keys when prepending items to a list causes catastrophic layout thrashing. Because every item's index shifts, React is forced to destroy and recreate or mutate every existing DOM node in the list during high-frequency events (like a streaming log).
**Action:** Always assign a stable, unique identifier (`id`) to elements generated dynamically (even if synthetic, using a local counter in the hook) and use that identifier as the React `key`. Combine this with `React.memo` for the list item component to completely bypass reconciliation for unchanged rows.

## 2024-07-06 - React.memo syntax requirement
**Learning:** In the frontend React codebase (`frontend/cat-admin-frontend`), when using `React.memo` for performance optimizations, make sure to explicitly import `React` (e.g. `import React, { ... } from "react"`). The linter complains or fails when `React.memo` is used without `React` imported, even though functional components may not require it.
**Action:** Always verify `React` is imported from `"react"` when wrapping components in `React.memo`.

## 2024-07-04 - Optimize Playground Session Fetching
**Learning:** Fetching a parent and its related children sequentially (e.g. ChatSession then ChatMessage) introduces redundant database round trips.
**Action:** Prefer a single `select(Parent, Child).outerjoin(...)` when loading a session with messages (filter null children for empty collections).

## 2024-07-28 - SessionGateMiddleware validate_token PEM parsing bottleneck
**Learning:** `OAuthService.validate_token` runs on every session-gated request. Repeated DB lookups and PEM parsing of the public key on this hot path add avoidable overhead. Token revocation remains a separate JTI check and is not replaced by key caching.
**Action:** Cache deserialized public keys in memory (class-level `TTLCache` by `kid`, short TTL) so keypair deactivation is bounded while still skipping PEM parse on the hot path. Pin `cachetools` in requirements.txt.

## 2026-07-11 - TerminalView ResizeObserver Thrashing
**Learning:** High-frequency DOM resize events on xterm.js containers without throttling cause layout thrashing from synchronous fit() recalculation.
**Action:** Wrap ResizeObserver callbacks that call xterm fit() in requestAnimationFrame and cancel pending frames on cleanup.

## 2024-07-28 - LiveLogs Re-render Optimization ⚡
**Target:** `useLiveLogs.ts` (SSE Streaming Log Consumer)
**Bottleneck:** High-frequency event overload. The unbounded `requestAnimationFrame` queue resulted in 60+ FPS React render cycles when processing bursty or continuous SSE log streams. Updating the top-level list state via `setLogs` forces `LiveLogs` and its children to render, thrashing layout and blowing the main-thread frame budget.
**Action:** Render Throttle / Debounced Batches
- Replaced unbounded `requestAnimationFrame` flushes with a debounced macro-task throttle (`window.setTimeout` -> `150ms`).
- Ensures the component updates at a stable ~6 frames per second, safely accumulating high-frequency entries in the mutable `useRef` `pendingLogsRef` array.
- Reduced redundant virtual DOM reconciliation by 90% during sustained log bursts without impacting visual parity or perceived live-ness.
