export interface Plugin {
  id: string
  name: string
  version: string
  tier: "free" | "pro"
  enabled: boolean
  description: string
  required_credentials?: string[]
  external_oauth_providers?: string[]
  oauth_status?: Record<string, "connected" | "not_connected">
  capabilities?: string[]
  auth_status?: "ok" | "needs_reauth"
  layer2_oauth_enabled?: boolean
  /** True when the plugins DB row no longer matches disk / proxy_servers. */
  stale?: boolean
  /** Full-tree or proxy-identity content hash (`sha256:<hex>`). */
  content_hash?: string | null
}

export interface PluginHealth {
  is_active: boolean
  oauth_status: Record<string, string>
  credentials_present: boolean
  tools_count: number
  lifecycle_state: "loaded" | "unloaded"
}
