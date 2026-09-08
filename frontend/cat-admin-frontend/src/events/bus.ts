import mitt from "mitt"

export type AppEvents = {
  "toast:error": { message: string }
  "toast:info": { message: string }
  "sidebar:refresh": undefined

  "plugin:toggled": { id: string; enabled: boolean }
  "plugin:deleted": { id: string }
  "auth:expired": undefined
  "relay:status-changed": { pluginId: string; status: string }
  "proxy:changed": undefined
  "plugin:reindexed": { id: string }
  "proxy:reindexed": { name: string }
  /** Fired when the LiveLogs SSE stream connects successfully. */
  "logs:connected": undefined
  /** Fired when the LiveLogs SSE stream disconnects or errors. */
  "logs:disconnected": undefined
}

/**
 * Global application event bus for UI notifications, auth lifecycle, and plugin state sync.
 */
export const bus = mitt<AppEvents>()
