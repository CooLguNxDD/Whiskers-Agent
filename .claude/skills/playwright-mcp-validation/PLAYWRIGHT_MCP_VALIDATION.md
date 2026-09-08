# Playwright MCP Validation — Step Guide

How to prove a change still serves real traffic, using **Playwright MCP tools only**. No curl, no
shell probes — those bypass the client stack, which is exactly the part unit tests already skip.

The guide is written to be **overloaded**: §2 is a fixed step template, §3 supplies the per-target
overrides that fill it in, and §5 covers repeating a step to soak a surface. Adding a new target
means adding a row to §3, not rewriting the procedure.

---

## 1. Load the tools first

Playwright MCP tools are deferred. Batch every tool you expect into **one** `ToolSearch` call —
one call per tool wastes a round-trip each:

```
ToolSearch("select:mcp__playwright__browser_navigate,mcp__playwright__browser_snapshot,
mcp__playwright__browser_console_messages,mcp__playwright__browser_network_requests,
mcp__playwright__browser_take_screenshot,mcp__playwright__browser_close")
```

Add per-task tools to the same call when the target obviously needs them:
`browser_fill_form` / `browser_type` / `browser_click` for logins and forms,
`browser_wait_for` for async paints, `browser_evaluate` for reading page state.

**Preflight:** every target must be rebuilt from current source, or you are validating a stale
image and will report a false pass.

| Container | Port | Rebuild |
|---|---|---|
| `whiskers-mcp-server` | 10000 | `docker restart whiskers-mcp-server` — source is bind-mounted, no build needed |
| `whiskers-mcp-frontend` | 3000 | `docker compose -f docker-compose.yml -f docker-compose.dev.yml build cat-admin-frontend` then `... up -d cat-admin-frontend` |
| `catportfolio` | 11000 | `cd ../CatPortfolio && docker compose build catportfolio && docker compose up -d catportfolio` |

Confirm with `docker ps` before step 1. A container still in `health: starting` hands you a
connection error that looks exactly like a regression.

---

## 2. The step template

Every validation step is these five moves. Do not skip 3 and 4 — a page that *looks* right while
throwing 500s in the background is the failure mode this whole exercise exists to catch.

1. **`browser_navigate`** → the target URL.
2. **`browser_snapshot`** (`depth: 5` or `6`) — the accessibility tree, not a screenshot. Assert on
   named landmarks: a heading, a nav link, a specific button label. The snapshot is what you assert
   on; a screenshot is only evidence for a human.
3. **`browser_console_messages`** with `level: "error"` — the count *and the kind* of error is the
   pass condition, never merely "some errors appeared".
4. **`browser_network_requests`** with `static: false` (add `filter: "/api/"` to cut noise) — check
   status codes on the calls the page actually made.
5. **`browser_take_screenshot`** with a meaningful `filename` — evidence to attach to the report.

Write each result as: **what was expected, what was observed, and PASS / FAIL / BLOCKED.** A step
you could not run is `BLOCKED` with its reason — never quietly dropped, never reported as a pass.

### Reading the codes

- **401 is a pass** on a gated route before login. It proves routing, middleware and imports ran.
- **500 is the regression signal.** A broken import from a rename or a moved helper lands here.
- **200 on a public probe** (e.g. `/api/admin/public/exists`) is the end-to-end proof that the
  route registry survived.
- **A blank page with zero network calls** means the SPA never reached the server — check its
  `config.json` before blaming the backend.

---

## 3. Target overrides

Fill the §2 template with one of these. Add a row rather than inventing a new procedure.

### Admin console — `http://localhost:3000`
- **Covers:** route registry, auth middleware, catalog, and any core/plugin module that must import
  cleanly at boot.
- **Expect:** redirect to `/login?state=&next=%2F`; snapshot shows Username / Password /
  `enter the tunnel →`. Exactly three console errors, all 401
  (`/api/admin/public/me`, `/api/catalog/session_gated` ×2). `/api/admin/public/exists` → 200.
- **Login (when credentials exist):** `browser_type` into the two textboxes → `browser_click` the
  submit button → `browser_navigate` to the analytics route → `browser_snapshot` the KPI strip.
  This is the only way to see the `active_sessions` telemetry gauge in a browser.

### CatPortfolio — `http://localhost:11000`
- **Covers:** `portfolio_plugin`'s public surface, the schema mirror, the WebGL fish tank.
- **Expect:** header `🐱 Cat Portfolio` with Home / Ask; view toggles `3D` / `Flat` / `Text`; an
  `Interactive portfolio fish tank` canvas; depth legend Surface / Mid water / Deep bed.
  **Zero** console errors *and* zero warnings.
- **Known gap:** `/CatPortfolio/ask` redirects home and fetches only `config.json` — the deployed
  image carries no MCP server URL or ask key (moved to secrets), so `route_portfolio_ask` is never
  called. Ask mode is **not** browser-verifiable until that config is supplied; say so in the
  report rather than implying coverage.

### Text-mode portfolio — `http://localhost:11000/CatPortfolio/?v=text`
- **Covers:** the WebGL-free render path. Use when validating layout/schema changes without the
  tank in the way.

---

## 4. Writing the report

One file per validation pass, in `.claude/docs/`, screenshots beside it. Structure:

- **Preconditions** — the rebuild table, so the pass is reproducible.
- **One section per step** — numbered, each with a *"Why this catches a regression"* line naming the
  specific change under test. A step that cannot fail is not worth running.
- **Result line per step** — PASS / FAIL / BLOCKED, dated, screenshot filename.
- **Coverage summary table** — each change vs. whether the browser actually verified it. Mark
  indirect coverage as indirect. Overclaiming here is worse than an empty table.

Reference example: `.claude/docs/wave2-playwright-validation.md`.

---

## 5. Overloading a step — soak passes

The §2 template is a single pass. To hammer a surface, repeat the *navigate → console → network*
core and diff the results; the first divergence is the finding.

**Soak (same page, N times).** Loop `browser_navigate` → `browser_console_messages` →
`browser_network_requests`. Watch for: error count climbing across iterations (a leak), response
codes degrading from 200 to 5xx (pool or connection exhaustion), or growing latency in the request
list. A refactor that leaks a task or a DB session shows up here and nowhere else.

**Route sweep.** Walk a list of routes through the template back-to-back in one session, then read
`browser_console_messages` with `all: true` at the end to catch errors that fired on a page you
have already navigated away from.

**Parallel clients.** `browser_tabs` opens additional tabs against the same server; drive each
through the template. This is the only browser-side way to make `active_sessions` and other
concurrency gauges actually move.

**Keep it honest:** Playwright MCP drives a real browser, so this is functional soak testing, not a
load benchmark — do not report throughput numbers from it. If the goal is genuine load, say so and
reach for a load tool instead, and point it only at your own local stack.

---

## 6. Pitfalls

- **Never trigger `alert` / `confirm` / `prompt`.** A modal dialog blocks every subsequent MCP
  command and the session is dead until a human dismisses it. Avoid delete-style buttons that
  confirm.
- **Do not reuse tab IDs across sessions.** They are per-session; a stale id errors. Re-navigate.
- **Stop after 2–3 failed attempts** on the same action and report what you tried. Retrying a
  failing selector in a loop burns the session and finds nothing.
- **`browser_snapshot` over `browser_take_screenshot`** for assertions — the tree is stable text,
  the image is not.
- **Screenshots land in the CWD**, not next to the report. Move them deliberately.
- **Zero console errors is meaningful only if the page actually loaded.** Always pair the console
  check with a snapshot assertion on real content.
