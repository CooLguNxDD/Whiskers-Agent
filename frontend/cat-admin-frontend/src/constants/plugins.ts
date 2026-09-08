/**
 * Central plugin id constants — avoids scattering plugin package names
 * as magic strings across routes/components/tests.
 */

/** Canonical `plugin_id` string for every first-party plugin — see file header. */
export const PLUGIN_IDS = {
  SEARCH: "search_plugin",
  JOB_SEARCH: "job_search_plugin",
  JULES: "jules_plugin",
  TERMINAL_RELAY: "cat_terminal_relay_plugin",
  PORTFOLIO: "portfolio_plugin",
  MEMORY: "memory_plugin",
  WORLD_SEMANTIC: "world_semantic_plugin",
} as const

/** Default plugin id for connect-flow search params when none is supplied. */
export const DEFAULT_PLUGIN_ID = PLUGIN_IDS.TERMINAL_RELAY

/** Display name used for the core plugin's traffic-legend entry. */
export const CORE_PLUGIN_DISPLAY_NAME = "Whiskers Core"

/** Approximate per-plugin latency shown on the dashboard plugin card. */
export const PLUGIN_LATENCY_MAP: Record<string, string> = {
  [PLUGIN_IDS.TERMINAL_RELAY]: "18ms",
  [PLUGIN_IDS.PORTFOLIO]: "32ms",
}
