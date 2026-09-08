/**
 * Provider Metadata
 *
 * Frontend-only display metadata (name, handle, accent color, scopes, description)
 * for known Layer-2 external OAuth providers, used by the Connect screen. The
 * `/api/plugins` payload does not expose this presentation data, so it is kept
 * as a small typed map with a generic fallback for unknown providers.
 */

export interface ProviderMeta {
  name: string
  handle: string
  color: string
  scopes: string[]
  desc: string
}

const CONNECT_PROVIDERS: Record<string, ProviderMeta> = {
  whiskers_core: {
    name: "Whiskers Agent",
    handle: "whiskers.local",
    color: "var(--amber)",
    scopes: ["catalog:read", "tools:execute"],
    desc: "Lets the plugin call the core Whiskers Agent MCP server and use gated catalog tools on your behalf.",
  },
  google_sheets: {
    name: "Google Sheets",
    handle: "sheets.google.com",
    color: "var(--neon)",
    scopes: ["sheets.readonly"],
    desc: "Read-only access to spreadsheets the plugin lists in its manifest.",
  },
  slack: {
    name: "Slack",
    handle: "slack.com",
    color: "var(--pink)",
    scopes: ["chat:write", "channels:read"],
    desc: "Post replies into the channels you select after authorizing.",
  },
}

/**
 * Title-cases a snake_case provider id for a generic display name (e.g. "some_provider" -> "Some Provider").
 */
function titleCaseId(id: string): string {
  return id
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ")
}

/**
 * Resolves display metadata for a provider id, falling back to a generic
 * derived entry (amber accent, no scope list, manifest-delegation copy) when
 * the provider isn't in the known map.
 */
export function getProviderMeta(provider: string): ProviderMeta {
  return (
    CONNECT_PROVIDERS[provider] ?? {
      name: titleCaseId(provider) || provider,
      handle: provider,
      color: "var(--amber)",
      scopes: [],
      desc: "Delegated access as declared in the plugin manifest.",
    }
  )
}
