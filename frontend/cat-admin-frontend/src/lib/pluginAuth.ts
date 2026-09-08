import type { Plugin, PluginHealth } from "@/types/plugin"

/**
 * Determines whether a plugin currently has an active connection: a connected
 * external OAuth provider (Layer 2) or, for direct-login plugins, stored credentials.
 */
export function isPluginConnected(plugin: Plugin, healthData?: PluginHealth | null): boolean {
  if (plugin.layer2_oauth_enabled) {
    return (plugin.external_oauth_providers ?? []).some(
      (provider) => plugin.oauth_status?.[provider] === "connected",
    )
  }
  return healthData?.credentials_present === true || plugin.auth_status === "ok"
}
