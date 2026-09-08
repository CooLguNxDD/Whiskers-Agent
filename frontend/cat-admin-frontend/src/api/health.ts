/**
 * System Health API
 *
 * Fetches aggregated instance status for the admin shell Live strip:
 * plugins, proxies, success-rate metrics, and instance name.
 *
 * Paths resolved via live OperationCatalog (owner `api.health`).
 */

import { catalogClient } from "./catalogClient"

export interface HealthPlugins {
  total: number
  enabled: number
}

export interface HealthTunnels {
  total: number
  active: number
}

export interface HealthMetrics {
  success_rate: number
  p50_latency_ms: number
  p99_latency_ms: number
}

export interface HealthChecks {
  registry: boolean
  db: boolean
  telemetry: boolean
  proxies: boolean
}

export interface SystemHealth {
  status: "healthy" | "degraded" | "unhealthy" | string
  instance_name: string
  uptime_seconds: number
  plugins: HealthPlugins
  tunnels: HealthTunnels
  metrics: HealthMetrics
  system_tier: number
  system_tier_name: string
  checks: HealthChecks
}

/**
 * Retrieve aggregated system health for the admin shell.
 */
export function getHealth(): Promise<SystemHealth> {
  return catalogClient.health.system()
}
