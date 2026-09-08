---
version: "beta"
name: "CatAdmin Cozy Terminal"
colors:
  primary: "#FFAE19"
  secondary: "#FF2E93"
  neutral: "#FAF6EE"
  background: "#1E1A17"
  surface: "#2C2622"
  on-primary: "#1E1A17"
typography:
  heading:
    fontFamily: "Geist"
    fontSize: "32px"
    fontWeight: "600"
    lineHeight: "1.2"
  body:
    fontFamily: "Geist"
    fontSize: "15px"
    fontWeight: "regular"
    lineHeight: "1.5"
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
spacing:
  base: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.sm}"
    padding: "10px 20px"
---

# Design System Specification

## Codebase Blueprint

### Purpose & Context
**CatAdmin Cozy Terminal** (located in [frontend/cat-admin-frontend/](../../frontend/cat-admin-frontend/)) serves as the administrative control console for the **Whiskers Agent MCP Server**.

Whiskers Agent exposes plugin orchestration, proxy integrations, terminal relay, and multi-step `run_graph` workflows over the Model Context Protocol (MCP) using standard stdio and HTTP transports. Domain plugins (for example `search_plugin`, `portfolio_plugin`, `cat_terminal_relay_plugin`) register their own tools. The React single-page application (SPA) allows operators to monitor services, dynamic plugin configurations, Layer 2 OAuth integrations, and test agent runs interactively.

### Goals
1. **Zero-Downtime Configuration**: Swapping and activating runtime configurations, LLM providers, and data embedding models without server restarts.
2. **Multi-Agent & Tool Telemetry**: Auditing LangGraph SSE events, inspecting GOAP (Goal-Oriented Action Planning) planning execution states, and firing individual MCP tools with automated payloads.
3. **Step-Up Elevation & VS Code Extension Support**: Providing administrative capabilities to mint Layer 1 `terminal:host` authentication JWTs and handling password/TOTP elevation gates for PTY write executions.
4. **Data Integrity**: Honoring platform safety guards such as confirmation-first steps for bulk workflows and preventing fabricated entity data.

### Critical Files
- **App Entry & Server Setup**: 
  - [whiskers_agent_mcp.py](../../whiskers_agent_mcp.py): FastMCP startup/teardown & tool registry endpoint (`whiskers_mcp.py` is a compat shim).
  - [agent.py](../../agent.py): Interactive CLI client for LangGraph executions.
- **Config & Model Pools**: 
  - [server_config_example.json](../../config/server_config_example.json): Configuration template containing parameters like `context.platform_rules`, `oauth.valid_scopes`, `graph.long_chain_threshold`, and scheduled jobs.
  - [embedding_config.json](../../embedding_config.json): Models for Gemma (`embeddinggemma:300m`), Gemini (`gemini-embedding-001`), and OpenAI (`text-embedding-3-small`).
- **Core Graph Orchestrator**: 
  - [core_graph/](../../core_graph/): Contains prompts and planners.
  - [core_graph/goap/planner.py](../../core_graph/goap/planner.py): A* forward search planner for LangGraph.
- **Frontend Dashboard (cat-admin-frontend)**:
  - [src/index.css](../../frontend/cat-admin-frontend/src/index.css): Base Tailwind directives, typography, and font imports.
  - [src/ct-theme.css](../../frontend/cat-admin-frontend/src/ct-theme.css): Authoritative theme configuration containing custom OKLCH custom properties, UI layout utility classes, and glows.
  - [src/store/index.ts](../../frontend/cat-admin-frontend/src/store/index.ts): Zustand slices for session states and active UI properties.

---

## Visual Identity & Design Tokens

The styling combines high-fidelity administrative terminal UI controls with physical warmth. Color choices, layout density, and font pairings emphasize instrumentation telemetry.

### Colors & Themes (`data-theme`)
The application supports seven selectable themes driven by OKLCH parameters in `src/themes/*.theme.json` (Mocha is the FOUC fallback on `:root`):

1.  **🐱 Mocha (Default Dark)** — Catppuccin Mocha. Background `oklch(0.243 0.030 284)`, peach primary.
2.  **🐱 Macchiato / 🐱 Frappé** — Catppuccin dark deltas over Mocha.
3.  **🐱 Latte** — Catppuccin light. Same light-mode structural CSS as Paper (`.ct-root[data-light="true"]`).
4.  **Cozy (Warm Dark)**:
    - Background: `oklch(0.18 0.018 45)` (Warm control-room dark)
    - Card: `oklch(0.23 0.022 48)`
    - Amber Accent (Primary): `oklch(0.82 0.165 70)`
    - Hot Pink (Secondary): `oklch(0.76 0.180 350)`
    - Terminal Green (Success): `oklch(0.86 0.200 145)`
5.  **Paper (Light Mode)**:
    - Background: `oklch(0.97 0.012 80)` (Warm off-white paper texture)
    - Card: `oklch(0.99 0.008 80)`
    - Ink Text (Foreground): `oklch(0.22 0.030 40)`
    - Amber Accent: `oklch(0.62 0.155 55)`
6.  **Neon Alley (Cyberpunk Dark)**:
    - Background: `oklch(0.16 0.040 285)` (Indigo-tinted cool dark)
    - Card: `oklch(0.21 0.055 290)`
    - Magenta Primary: `oklch(0.78 0.200 320)`
    - Cyan Accent: `oklch(0.82 0.130 200)`

### Accent Overrides (`data-accent`)
The primary highlight color (`--amber`) is runtime-configurable using the `data-accent` attribute:
- **`amber`**: Standard primary focus actions.
- **`pink`**: Expressive primary mappings.
- **`neon`**: Terminal green representation.
- **`cyan`**: Cyber-cyan telemetry mappings.
- **`violet`**: Custom deep violet.

### Density Scales (`data-density`)
Grid spacings and paddings automatically adjust via the density configuration:
- **`comfortable`**: 16px/18px card paddings (`--pad-card`), 14px row paddings (`--pad-row`).
- **`compact`**: 11px/13px card paddings (`--pad-card`), 9px row paddings (`--pad-row`), stat cards compressed to 100px min-height (`--row-min`).

### Typography Pairing
- **Geist (Sans)**: Utilized for standard UI controls, dashboard labels, menus, and forms.
- **JetBrains Mono (Monospace)**: Leveraged for system badges, planning headers, stats counters, logs, and token streams.
  - *Mono Eyebrow Treatment*: Uppercase monospace with wide letter-spacing (`0.14em` to `0.18em`) for instrumentation labels.
  - *Tabular Numerals*: Enforced on stat cards to prevent layout shifts as numbers update.

---

## Component Catalog & Specifications

### 1. App Shell & Layout
- **`AppShell`**: Coordinates layout grids (`ct-app`) containing the sidebar, top navigation, main viewport, and bottom log docks. Responsive toggles handle the log terminal collapsible states (`is-logs-off`).
- **`BrandMark`**: Displays the glowing logo frame incorporating `ct-brand-mark` shadows, styling the brand icon over a sunken background.
- **`Sidebar`**: Sidebar navigations render status indications (`ct-status-dot`) denoting whether the agent's background services are `online`, `warning`, or `offline`. Shows connected user scopes (e.g. `whiskers`, `terminal:use`) at the bottom.
- **`TopBar`**: Renders search prompts, active workspace indicator pills (`ct-node-pill`), and the [AuthPill](../../frontend/cat-admin-frontend/src/components/AuthPill/) component displaying session status.
- **`LiveLogs`**: The collapsible real-time stream for backend actions. Shows a scanline texture, color-coded headers matching log levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`), scroll locks, and custom paw-toggle switches.

### 2. Terminal & Step-up Auth
- **`TerminalPanel` / `TerminalView`**: Houses the interactive shell. Handles input and maps keypress events to PTY execution requests.
- **`ElevationPrompt`**: Intercepts privileged command attempts (e.g., `claude`, `codex`, or `agy -p`). Renders interactive authentication dialogs to elevate sessions using TOTP code verification or passwords.
- **`HostTokenButton`**: Renders a copy-to-clipboard widget that requests RS256 tokens (`terminal:host` scope) to enable VS Code extension connectivity.

### 3. Playground & Multi-Agent Chat
- **`ServerBar`**: Renders configured server instances, allowing operators to route playground queries.
- **`ChatMode`**: Compiles dynamic conversation bubbles with SSE event listeners. Renders token-streaming animations and formats dynamic execution plans.
- **`ToolTestMode`**: Shows registered tools with auto-derived schemas (`useToolRegistry`). Automatically compiles test payloads and outputs formatted execution results.
- **`GroupTestMode`**: The batch runner panel. Evaluates tool groupings sequentially, showing visual progress bars, pass/fail counts, and execution latency.

### 4. GOAP Visualizer
- **`GoapInline`**: Visualizes plans inline inside chat dialog bubbles.
- **`GraphCanvas`**: An SVG-driven node graph detailing action sequences, facts, bounds, and current plan state. Highlighted edges denote the path compiled by the A* planner.
- **`InspectorPanels` / `NodeOutputs`**: Interactive sideboards allowing developers to click node bubbles to inspect inputs, resolved bindings, and world state mutations.

### 5. Plugin Lifecycle & Tabs
- **`PluginCard` / `PluginRow`**: Renders plugin names (`plugins.search_plugin`, `plugins.portfolio_plugin`, `plugins.cat_terminal_relay_plugin`), state toggles (`ct-switch`), tier labels (`pro` vs `free`), and usage bars that transition to warning colors (`var(--warn)`) or danger colors (`var(--danger)`) based on load thresholds.
- **`PluginDetail`**: Sub-views for selected plugins:
  - **Overview**: Core description, files, and versions.
  - **Config**: Raw JSON config editor.
  - **Logs**: Isolated plugin event stream.
  - **ToolsTab**: Lists tools with toggles to expose them to the MCP client (`useToggleToolMutation`) or hook them into semantic routing (`useToggleRouteMutation`).

### 6. Authentication & Setup Blocks
- **`DirectLoginForm`** (login route): Handles administrator username/password sessions and API key bearer authentication.
- **`Layer2Stub`**: Standardized buttons to initiate PKCE outbound handshakes with external providers (such as upstream API clients), presenting callback banners upon redirection.

### 7. Configuration Managers
- **`ProxyManager`**: Manages HTTP and TCP tunnels, reporting current port mappings, statuses, and throughput statistics.
- **`GraphModelSection`**: Exposes controls to dynamically route LangGraph tasks. Swaps active model configs (Chat, Data embedding, and Route discovery embedding) at runtime.
- **`LlmRagSection`**: Toggles local endpoint options (e.g., LM Studio/Ollama base URLs) and configures embedding dimensions and prefix templates.

---

### 8. Charts (analytics)

- Series charts use Recharts via `components/ui/chart.tsx` (`ChartContainer` + `ChartConfig`).
- Palette is `--chart-1..5` (aliases of `--amber` / `--pink` / `--neon` / `--cyan` / `--accent-violet`). Never hardcode `oklch(...)` in a chart component.
- `ChartConfig` is the legend/color source of truth. Kind dispatch is a registry, not a growing switch.

## Explicit Guardrails

1. **Warm Neutrals Only**: Never use standard cool-toned slate/gray/zinc colors in the Cozy theme. All borders, shadows, and secondary texts must retain warm tones (e.g., `oklch(0.32 0.025 55)`). Catppuccin flavors (Mocha / Macchiato / Frappé / Latte) are exempt — they use the official cool mauve/blue surfaces.
2. **Strict Spacing Boundaries**: Maintain consistent roundness rules. Never use border-radius parameters that exceed `14px` (`--radius-lg`) or fall below `6px` (`--radius-sm`).
3. **No Unsanitized Inputs**: In accordance with `platform_rules`, always prompt with confirmation models on any bulk operation.
4. **No Undocumented Terminology**: Keep dashboard wording synced with back-end configuration names and plugin manifests.
5. **No Visual Placeholders**: All icons, graphics, and charts must render real metrics or active system parameters. If data is absent, show empty states with descriptive instructions.
