/**
 * Server Config API
 *
 * Non-sensitive runtime configuration via OperationCatalog (owner `api.config`).
 */

import { catalogClient } from "./catalogClient"

export interface LlmConfig {
  llm_provider: "openai" | "gemini" | "anthropic" | string
  llm_model: string
  embed_provider: string
  embed_model: string
  embed_base_url: string
  embed_dimensions: number
  rag_enabled: boolean
  local_llm_enabled: boolean
  local_base_url: string
  cli_agent_provider: string
  /** True if the relevant API key env var is set on the server (key itself is never returned). */
  has_api_key: boolean
}

/** All fields except the read-only has_api_key sentinel. */
export type LlmConfigUpdate = Omit<LlmConfig, "has_api_key">

export interface SaveLlmConfigResponse {
  ok: boolean
  saved: Partial<LlmConfigUpdate>
}

/**
 * Returns current LLM/RAG settings — env var defaults merged with any DB overrides.
 */
export function getLlmConfig(): Promise<LlmConfig> {
  return catalogClient.config.getLlmConfig()
}

/**
 * Persists non-sensitive LLM/RAG settings to the server_settings table.
 */
export function saveLlmConfig(
  update: Partial<LlmConfigUpdate>,
): Promise<SaveLlmConfigResponse> {
  return catalogClient.config.saveLlmConfig(update)
}

export interface LlmPoolEntry {
  id: string
  name: string
  kind: "chat" | "embedding" | "core"
  provider: string
  model: string
  dimensions?: number
  base_url?: string
  strength?: number
  is_active: boolean
  has_api_key: boolean
}

export interface LlmPoolConfig {
  entries: LlmPoolEntry[]
  active: {
    chat?: string
    core?: string
    embedding?: string
    route?: string
  }
}

/**
 * Retrieves the full LLM pool configuration, including entries and active selections.
 */
export function getLlmPool(): Promise<LlmPoolConfig> {
  return catalogClient.config.getLlmPool()
}

/**
 * Adds a new model entry to the LLM pool.
 */
export function addLlmPoolEntry(entry: {
  name: string
  provider: string
  model: string
  kind: "chat" | "embedding" | "core"
  dimensions?: number
  base_url?: string
  api_key?: string
  strength?: number
}): Promise<{ ok: boolean; entry: LlmPoolEntry }> {
  return catalogClient.config.addLlmPoolEntry(entry)
}

/**
 * Deletes a model entry from the LLM pool by ID.
 */
export function deleteLlmPoolEntry(id: string): Promise<{ ok: boolean }> {
  return catalogClient.config.deleteLlmPoolEntry(id)
}

/**
 * Sets the active model for a specific usage kind (e.g. chat or core).
 */
export function setLlmActive(
  kind: "chat" | "embedding" | "route" | "core",
  id: string,
): Promise<{ ok: boolean; active: Record<string, string> }> {
  return catalogClient.config.setLlmActive(kind, id)
}

/**
 * Toggles a pool entry's active/disabled state.
 */
export function toggleLlmPoolEntryActive(
  id: string,
  enabled: boolean,
): Promise<{ ok: boolean }> {
  return catalogClient.config.toggleLlmPoolEntryActive(id, enabled)
}

export interface GatewayConfig {
  run_graph_unified: boolean
}

export interface GatewaySaveResponse {
  run_graph_unified: boolean
  applied?: number
}

/**
 * Returns the live gateway unified flag (DB or JSON default).
 */
export function getGatewayConfig(): Promise<GatewayConfig> {
  return catalogClient.config.getGatewayConfig()
}

/**
 * Save the gateway unified flag; server live-applies visibility and returns applied count.
 */
export function saveGatewayConfig(update: {
  run_graph_unified: boolean
}): Promise<GatewaySaveResponse> {
  return catalogClient.config.saveGatewayConfig(update)
}

export interface StepModelPolicy {
  strategy: "off" | "strength" | "task_type" | "explicit"
  task_type_map: Record<string, string>
  op_overrides: Record<string, string>
  parallel_enabled: boolean
  fanout_concurrency: number
}

export interface StepModelConfig {
  policy: StepModelPolicy
  pool_names: string[]
}

/**
 * Returns the current GOAP step-model policy and available pool entry names.
 */
export function getStepModelPolicy(): Promise<StepModelConfig> {
  return catalogClient.config.getStepModelPolicy()
}

/**
 * Persists a partial step-model policy patch; server returns the merged policy.
 */
export function saveStepModelPolicy(
  patch: Partial<StepModelPolicy>,
): Promise<{ ok: boolean; policy: StepModelPolicy }> {
  return catalogClient.config.saveStepModelPolicy(patch)
}

// ---------------------------------------------------------------------------
// Model-role ladders (core_graph/model_roles/) — spec-driven per-node model
// selection. Separate endpoint/key from step-model policy above (different
// lifecycle: read-time ladder resolution vs write-time step stamping).
// ---------------------------------------------------------------------------

export interface ModelRoleRung {
  selector: string
  max_attempts: number
  timeout_s: number | null
}

export interface ModelRoleValidation {
  require_json: boolean
  required_keys: string[]
  enum_field: string | null
  enum_values: string[]
  non_empty: boolean
}

export interface ModelRoleCondition {
  field: string
  op: "gt" | "gte" | "eq" | "truthy" | "present" | "in"
  value: unknown
  advance: number
}

export interface ModelRolePreviewRung {
  selector: string
  resolved_model: string | null
}

export interface ModelRoleEntry {
  role_id: string
  description: string
  ladder: ModelRoleRung[]
  validate: ModelRoleValidation | null
  entry_conditions: ModelRoleCondition[]
  escalate_on_exception: boolean
  escalate_on_invalid: boolean
  terminal_fallback: "ctx_llm" | "none" | "error"
  owner: string
  source: "db" | "core" | "plugin"
  preview: ModelRolePreviewRung[]
}

export interface ModelRolesConfig {
  roles: ModelRoleEntry[]
  effort_map: Record<string, string>
  pool_names: string[]
  aliases: string[]
  efforts: string[]
  node_roles: Record<string, string[]>
}

/** Spec payload accepted by saveModelRole — role_id/source/preview are server-derived. */
export type ModelRoleSpecInput = Omit<ModelRoleEntry, "source" | "preview" | "owner">

/**
 * Returns every model-role ladder (source-tagged) plus effort_map, active pool
 * names, the selector/effort vocabulary, and the generated node->role map.
 */
export function getModelRoles(): Promise<ModelRolesConfig> {
  return catalogClient.config.getModelRoles()
}

/**
 * Saves one role's ladder. Server validates strictly; an invalid spec rejects
 * with a 400 (surfaced by callCatalogOp as a thrown error).
 */
export function saveModelRole(
  roleId: string,
  spec: ModelRoleSpecInput,
): Promise<{ ok: boolean; role: ModelRoleEntry | null }> {
  return catalogClient.config.saveModelRole(roleId, spec)
}

/**
 * Saves the fleet-wide effort_map (effort level -> selector alias).
 */
export function saveEffortMap(
  effortMap: Record<string, string>,
): Promise<{ ok: boolean; effort_map: Record<string, string> }> {
  return catalogClient.config.saveEffortMap(effortMap)
}

/**
 * Reverts one role's DB override back to its core/plugin default.
 */
export function deleteModelRole(
  roleId: string,
): Promise<{ ok: boolean; role: ModelRoleEntry | null }> {
  return catalogClient.config.deleteModelRole(roleId)
}

// ---------------------------------------------------------------------------
// CLI Agent Driver Registry
// ---------------------------------------------------------------------------

/**
 * A single registered CLI agent driver as returned by the server registry.
 */
export interface CliAgentDriver {
  /** Registered driver name: "claude" | "agy" | "grok" | "generic" */
  name: string
  /** True when the binary is on PATH in the server environment. */
  available: boolean
  /** Resolved binary name or path (e.g. "agy", "/usr/local/bin/claude"). */
  binary: string
}

/** Response shape from GET /api/config/cli-agents. */
export interface CliAgentsResponse {
  drivers: CliAgentDriver[]
  /** Currently active CLI_AGENT_PROVIDER from DB or env (null = server default). */
  active: string | null
}

/**
 * Fetches all registered CLI agent drivers with live binary availability status.
 * Backed by the server-side registry.py — no hardcoded list on the frontend.
 */
export function getCliAgents(): Promise<CliAgentsResponse> {
  return catalogClient.config.getCliAgents()
}

// ---------------------------------------------------------------------------
// Plugin Gates (core/scope_management/gates.py) — level 3 ceilings.
// ---------------------------------------------------------------------------

export interface PluginGateOperations {
  allow?: string[]
  deny?: string[]
}

export interface PluginGateSpec {
  plugin_id: string
  core: string[]
  access: "read" | "write"
  operations?: PluginGateOperations
  endpoints?: string[]
  owner?: string
  source?: "manifest" | "db"
}

export interface PluginGatesConfig {
  gates: PluginGateSpec[]
}

/**
 * Returns every plugin's effective gate (manifest or DB override), source-tagged.
 */
export function getPluginGates(): Promise<PluginGatesConfig> {
  return catalogClient.config.getPluginGates()
}

/**
 * Saves one plugin's DB gate override ({plugin_id, spec}).
 */
export function savePluginGate(
  pluginId: string,
  spec: Partial<PluginGateSpec>,
): Promise<{ ok: boolean; gate: PluginGateSpec }> {
  return catalogClient.config.savePluginGate(pluginId, spec)
}

/**
 * Reverts one plugin's DB gate override to its manifest default.
 */
export function deletePluginGate(
  pluginId: string,
): Promise<{ ok: boolean; gate: PluginGateSpec | null }> {
  return catalogClient.config.deletePluginGate(pluginId)
}
