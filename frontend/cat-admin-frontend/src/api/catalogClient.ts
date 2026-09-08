/**
 * catalogClient — typed operation namespaces over callCatalogOp.
 *
 * Hand-authored (no codegen source exists for catalog operation types — see
 * `.claude/skills/inference-guide/`). One namespace per route owner
 * (`plugin_id` in the catalog), each method typing both the request args and
 * the response, closing the untyped `Record<string, unknown>` args gap the
 * individual `api/*.ts` modules previously cast through.
 *
 * `api/*.ts` modules keep their exported functions/JSDoc unchanged — only
 * their transport line calls through here instead of `callCatalogOp`
 * directly. `test/api/__tests__` enforces that `callCatalogOp` is imported
 * only from `catalogClient.ts` and `catalogRuntime.ts` itself.
 *
 * `api.route`'s `api_list_routes` returns two different shapes depending on
 * whether `plugin_id` is passed — modeled as two named methods
 * (`routes.aggregate()` / `routes.forPlugin(id)`) rather than one op-keyed
 * entry, which is exactly the case a generated/op-keyed client can't express.
 */

import { callCatalogOp } from "./catalogRuntime"
import type { Plugin, PluginHealth } from "@/types/plugin"
import type { ProxyServer, ProxyAuthMode } from "@/types/proxy"
import type {
  PluginsResponse,
  PluginToolsResponse,
  PluginSkillsResponse,
  PluginSkillsReloadResponse,
  PluginLogsResponse,
  PluginLogStatusFilter,
  PluginConfigResponse,
} from "./plugins"
import type {
  LlmConfig,
  LlmConfigUpdate,
  SaveLlmConfigResponse,
  LlmPoolConfig,
  LlmPoolEntry,
  GatewayConfig,
  GatewaySaveResponse,
  StepModelConfig,
  StepModelPolicy,
  ModelRolesConfig,
  ModelRoleEntry,
  ModelRoleSpecInput,
  CliAgentsResponse,
  PluginGatesConfig,
  PluginGateSpec,
} from "./config"
import type { ApiKey, CreateApiKeyResponse, ScopePreset, ScopeVocabulary } from "./apiKeys"
import type { AnalyticsSummaryResponse, AnalyticsWsTicketResponse, AskTurnsResponse } from "./analytics"
import type {
  ToolToggleResponse,
  ToolEmbeddingModelResponse,
  ToolHideResponse,
  ToolPermission,
  ToolPermissionResponse,
  ToolStateResponse,
  BatchToolStateResponse,
} from "./tools"
import type {
  TerminalHostsResponse,
  HostsWsTicketResponse,
  OpenSessionResponse,
  ElevateResponse,
  HostTokenResponse,
  TotpStatusResponse,
  ProvisionTotpResponse,
} from "./terminal"
import type {
  GraphResponse,
  InvokeResult,
  DBChatSession,
  BackendGraph,
  PlaygroundTool,
} from "./playground"
import type { RoutesAggregateResponse, RoutesDetailResponse, RouteToggleResponse } from "./routes"
import type { SystemHealth } from "./health"
import type { DirectCredentialsBody, DirectCredentialsResponse } from "./directCreds"

/**
 * Namespaced catalog client: `catalogClient.<owner>.<method>(...)` — one
 * namespace per route owner (`plugins`, `config`, `tools`, `terminal`,
 * `playground`, `routes`, `health`, `directCreds`, …), each method a typed
 * wrapper over `callCatalogOp`. See the file header for why this exists.
 */
export const catalogClient = {
  plugins: {
    list: (): Promise<PluginsResponse> => callCatalogOp("api.plugins", "api_list_plugins"),
    enable: (pluginId: string): Promise<Plugin> =>
      callCatalogOp("api.plugins", "api_enable_plugin", { plugin_id: pluginId }),
    disable: (pluginId: string): Promise<Plugin> =>
      callCatalogOp("api.plugins", "api_disable_plugin", { plugin_id: pluginId }),
    delete: (pluginId: string): Promise<{ id: string; deleted: boolean }> =>
      callCatalogOp("api.plugins", "api_delete_plugin", { plugin_id: pluginId }),
    health: (pluginId: string): Promise<PluginHealth> =>
      callCatalogOp("api.plugins", "api_get_plugin_health", { plugin_id: pluginId }),
    tools: (pluginId: string): Promise<PluginToolsResponse> =>
      callCatalogOp("api.plugins", "api_get_plugin_tools", { plugin_id: pluginId }),
    reindex: (pluginId: string): Promise<{ status: string; message: string }> =>
      callCatalogOp("api.plugins", "api_reindex_plugin", { plugin_id: pluginId }),
    hideTools: (
      pluginId: string,
    ): Promise<{ plugin_id: string; tools_hidden: boolean; applied: number }> =>
      callCatalogOp("api.plugins", "api_hide_plugin_tools", { plugin_id: pluginId }),
    showTools: (
      pluginId: string,
    ): Promise<{ plugin_id: string; tools_hidden: boolean; applied: number }> =>
      callCatalogOp("api.plugins", "api_show_plugin_tools", { plugin_id: pluginId }),
    skills: (pluginId: string): Promise<PluginSkillsResponse> =>
      callCatalogOp("api.plugins", "api_get_plugin_skills", { plugin_id: pluginId }),
    saveSkill: (
      pluginId: string,
      key: string,
      content: string,
    ): Promise<{
      plugin_id: string
      key: string
      saved: boolean
      content_hash?: string | null
      fs_content_hash?: string | null
      in_sync?: boolean
    }> =>
      callCatalogOp("api.plugins", "api_put_plugin_skill", { plugin_id: pluginId, key, content }),
    deleteSkill: (
      pluginId: string,
      key: string,
    ): Promise<{ plugin_id: string; key: string; deleted: boolean }> =>
      callCatalogOp("api.plugins", "api_delete_plugin_skill", { plugin_id: pluginId, key }),
    reloadSkills: (pluginId: string, key?: string): Promise<PluginSkillsReloadResponse> =>
      callCatalogOp("api.plugins", "api_reload_plugin_skills", {
        plugin_id: pluginId,
        ...(key != null ? { key } : {}),
      }),
    logs: (
      pluginId: string,
      page: number,
      perPage: number,
      status: PluginLogStatusFilter = "all",
    ): Promise<PluginLogsResponse> => {
      const args: Record<string, unknown> = { plugin_id: pluginId, page, per_page: perPage }
      if (status !== "all") args.status = status
      return callCatalogOp("api.plugins", "api_get_plugin_logs", args)
    },
    config: (pluginId: string): Promise<PluginConfigResponse> =>
      callCatalogOp("api.plugins", "api_get_plugin_config", { plugin_id: pluginId }),
    saveConfig: (
      pluginId: string,
      config: Record<string, unknown>,
    ): Promise<{ plugin_id: string; saved: boolean }> =>
      callCatalogOp("api.plugins", "api_put_plugin_config", { plugin_id: pluginId, config }),
    resetConfig: (pluginId: string): Promise<{ plugin_id: string; deleted: boolean }> =>
      callCatalogOp("api.plugins", "api_delete_plugin_config", { plugin_id: pluginId }),
    setDirectCredentials: (
      pluginId: string,
      body: DirectCredentialsBody,
    ): Promise<DirectCredentialsResponse> =>
      callCatalogOp("api.plugins", "api_set_plugin_direct_credentials", {
        plugin_id: pluginId,
        ...body,
      }),
    clearDirectCredentials: (pluginId: string): Promise<{ status: string }> =>
      callCatalogOp("api.plugins", "api_delete_plugin_direct_credentials", {
        plugin_id: pluginId,
      }),
    revokeOAuth: (pluginId: string, provider: string): Promise<void> =>
      callCatalogOp("api.plugins", "api_revoke_plugin_oauth", { plugin_id: pluginId, provider }),
  },

  config: {
    getLlmConfig: (): Promise<LlmConfig> => callCatalogOp("api.config", "api_get_llm_config"),
    saveLlmConfig: (update: Partial<LlmConfigUpdate>): Promise<SaveLlmConfigResponse> =>
      callCatalogOp("api.config", "api_save_llm_config", update as Record<string, unknown>),
    getLlmPool: (): Promise<LlmPoolConfig> => callCatalogOp("api.config", "api_list_llm_pool"),
    addLlmPoolEntry: (entry: {
      name: string
      provider: string
      model: string
      kind: "chat" | "embedding" | "core"
      dimensions?: number
      base_url?: string
      api_key?: string
      strength?: number
    }): Promise<{ ok: boolean; entry: LlmPoolEntry }> =>
      callCatalogOp("api.config", "api_add_llm_pool", entry as Record<string, unknown>),
    deleteLlmPoolEntry: (id: string): Promise<{ ok: boolean }> =>
      callCatalogOp("api.config", "api_delete_llm_pool", { entry_id: id }),
    setLlmActive: (
      kind: "chat" | "embedding" | "route" | "core",
      id: string,
    ): Promise<{ ok: boolean; active: Record<string, string> }> =>
      callCatalogOp("api.config", "api_set_llm_active", { kind, id }),
    toggleLlmPoolEntryActive: (id: string, enabled: boolean): Promise<{ ok: boolean }> =>
      callCatalogOp("api.config", "api_toggle_llm_active", { entry_id: id, enabled }),
    getGatewayConfig: (): Promise<GatewayConfig> =>
      callCatalogOp("api.config", "api_get_gateway_config"),
    saveGatewayConfig: (update: { run_graph_unified: boolean }): Promise<GatewaySaveResponse> =>
      callCatalogOp("api.config", "api_save_gateway_config", update),
    getStepModelPolicy: (): Promise<StepModelConfig> =>
      callCatalogOp("api.config", "api_get_step_models"),
    saveStepModelPolicy: (
      patch: Partial<StepModelPolicy>,
    ): Promise<{ ok: boolean; policy: StepModelPolicy }> =>
      callCatalogOp("api.config", "api_save_step_models", patch as Record<string, unknown>),
    getModelRoles: (): Promise<ModelRolesConfig> =>
      callCatalogOp("api.config", "api_get_model_roles"),
    saveModelRole: (
      roleId: string,
      spec: ModelRoleSpecInput,
    ): Promise<{ ok: boolean; role: ModelRoleEntry | null }> =>
      callCatalogOp("api.config", "api_save_model_role", {
        role_id: roleId,
        spec: spec as unknown as Record<string, unknown>,
      }),
    saveEffortMap: (
      effortMap: Record<string, string>,
    ): Promise<{ ok: boolean; effort_map: Record<string, string> }> =>
      callCatalogOp("api.config", "api_save_model_role", { effort_map: effortMap }),
    deleteModelRole: (roleId: string): Promise<{ ok: boolean; role: ModelRoleEntry | null }> =>
      callCatalogOp("api.config", "api_delete_model_role", { role_id: roleId }),
    getCliAgents: (): Promise<CliAgentsResponse> =>
      callCatalogOp("api.config", "api_get_cli_agents"),
    getPluginGates: (): Promise<PluginGatesConfig> =>
      callCatalogOp("api.config", "api_get_plugin_gates"),
    savePluginGate: (
      pluginId: string,
      spec: Partial<PluginGateSpec>,
    ): Promise<{ ok: boolean; gate: PluginGateSpec }> =>
      callCatalogOp("api.config", "api_save_plugin_gate", { plugin_id: pluginId, spec }),
    deletePluginGate: (pluginId: string): Promise<{ ok: boolean; gate: PluginGateSpec | null }> =>
      callCatalogOp("api.config", "api_delete_plugin_gate", { plugin_id: pluginId }),
  },

  apiKeys: {
    list: (): Promise<{ keys: ApiKey[] }> => callCatalogOp("api.apikeys", "api_list_api_keys"),
    create: (name: string, expiresIn?: number): Promise<CreateApiKeyResponse> =>
      callCatalogOp("api.apikeys", "api_create_api_key", {
        name,
        expires_in: expiresIn ?? null,
      }),
    revoke: (keyId: string): Promise<CreateApiKeyResponse> =>
      callCatalogOp("api.apikeys", "api_revoke_api_key", { key_id: keyId }),
    delete: (keyId: string): Promise<{ ok: boolean }> =>
      callCatalogOp("api.apikeys", "api_delete_api_key", { key_id: keyId }),
    updateScopes: (keyId: string, scopes: string[] | null): Promise<{ ok: boolean }> =>
      callCatalogOp("api.apikeys", "api_update_api_key_scopes", { key_id: keyId, scopes }),
    listScopePresets: (): Promise<{ presets: ScopePreset[] }> =>
      callCatalogOp("api.apikeys", "api_list_scope_presets"),
    createScopePreset: (name: string, scopes: string[] | null): Promise<ScopePreset> =>
      callCatalogOp("api.apikeys", "api_create_scope_preset", { name, scopes }),
    deleteScopePreset: (presetId: string): Promise<{ ok: boolean }> =>
      callCatalogOp("api.apikeys", "api_delete_scope_preset", { preset_id: presetId }),
    getScopeVocabulary: (): Promise<ScopeVocabulary> =>
      callCatalogOp("api.apikeys", "api_get_scope_vocabulary"),
  },

  analytics: {
    summary: (range: string): Promise<AnalyticsSummaryResponse> =>
      callCatalogOp("api.analytics", "api_analytics_summary", { range }),
    wsTicket: (): Promise<AnalyticsWsTicketResponse> =>
      callCatalogOp("api.analytics", "api_analytics_ws_ticket"),
    // Plugin-owned route (owner="portfolio_plugin" in
    // plugins/portfolio_plugin/routes.py) — plugin_id/operation_id resolve
    // by (owner, name) from the same live OperationCatalog, not by page.
    askTurns: (limit: number, intent?: string): Promise<AskTurnsResponse> =>
      callCatalogOp("portfolio_plugin", "portfolio_ask_turns", {
        limit,
        ...(intent ? { intent } : {}),
      }),
  },

  tools: {
    enable: (pluginId: string, toolName: string): Promise<ToolToggleResponse> =>
      callCatalogOp("api.tool", "api_enable_tool", { plugin_id: pluginId, tool_name: toolName }),
    disable: (pluginId: string, toolName: string): Promise<ToolToggleResponse> =>
      callCatalogOp("api.tool", "api_disable_tool", { plugin_id: pluginId, tool_name: toolName }),
    setEmbeddingModel: (
      pluginId: string,
      toolName: string,
      modelId: string | null,
    ): Promise<ToolEmbeddingModelResponse> =>
      callCatalogOp("api.tool", "api_set_tool_embedding_model", {
        plugin_id: pluginId,
        tool_name: toolName,
        model: modelId,
      }),
    hide: (pluginId: string, toolName: string): Promise<ToolHideResponse> =>
      callCatalogOp("api.tool", "api_hide_tool", { plugin_id: pluginId, tool_name: toolName }),
    show: (pluginId: string, toolName: string): Promise<ToolHideResponse> =>
      callCatalogOp("api.tool", "api_unhide_tool", { plugin_id: pluginId, tool_name: toolName }),
    getPermission: (pluginId: string, toolName: string): Promise<ToolPermissionResponse> =>
      callCatalogOp("api.tool", "api_get_tool_permission", {
        plugin_id: pluginId,
        tool_name: toolName,
      }),
    setPermission: (
      pluginId: string,
      toolName: string,
      permission: Partial<ToolPermission>,
    ): Promise<ToolPermissionResponse> =>
      callCatalogOp("api.tool", "api_set_tool_permission", {
        plugin_id: pluginId,
        tool_name: toolName,
        ...permission,
      }),
    setState: (
      pluginId: string,
      toolName: string,
      state: "enabled" | "hidden" | "disabled",
    ): Promise<ToolStateResponse> =>
      callCatalogOp("api.tool", "api_set_tool_state", {
        plugin_id: pluginId,
        tool_name: toolName,
        state,
      }),
    setBatchState: (
      pluginId: string,
      toolNames: string[],
      patch: {
        state?: "enabled" | "hidden" | "disabled"
        permission?: Partial<ToolPermission>
      },
    ): Promise<BatchToolStateResponse> =>
      callCatalogOp("api.tool", "api_set_tools_batch_state", {
        plugin_id: pluginId,
        tool_names: toolNames,
        ...patch,
      }),
  },

  terminal: {
    listHosts: (): Promise<TerminalHostsResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_list_hosts"),
    hostsWsTicket: (): Promise<HostsWsTicketResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_hosts_ws_ticket"),
    openSession: (ideId: string, workdir?: string): Promise<OpenSessionResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_open_session", {
        ide_id: ideId,
        workdir: workdir || null,
      }),
    killSession: (sessionId: string): Promise<{ status: string; killed: boolean }> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_kill_session", {
        session_id: sessionId,
      }),
    elevateSession: (
      sessionId: string,
      factors: { totp?: string; password?: string },
    ): Promise<ElevateResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_elevate_session", {
        session_id: sessionId,
        ...factors,
      }),
    hostToken: (): Promise<HostTokenResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_host_token"),
    totpStatus: (): Promise<TotpStatusResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_totp_status"),
    provisionTotp: (): Promise<ProvisionTotpResponse> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_provision_totp"),
    verifyTotp: (code: string): Promise<{ status: string }> =>
      callCatalogOp("cat_terminal_relay_plugin", "terminal_verify_totp", { code }),
  },

  playground: {
    chat: (message: string, forceExecute = false): Promise<GraphResponse> =>
      callCatalogOp("api.playground", "api_playground_chat", {
        message,
        force_execute: forceExecute,
      }),
    tools: (): Promise<{ tools: PlaygroundTool[] }> =>
      callCatalogOp("api.playground", "api_playground_tools"),
    invokeTool: (tool: string, args: Record<string, unknown>): Promise<InvokeResult> =>
      callCatalogOp("api.playground", "api_playground_invoke_tool", { tool, arguments: args }),
    createChatSession: (title: string): Promise<DBChatSession> =>
      callCatalogOp("api.playground", "api_playground_create_chat_session", { title }),
    listChatSessions: (): Promise<{ sessions: DBChatSession[] }> =>
      callCatalogOp("api.playground", "api_playground_list_chat_sessions"),
    getChatSession: (sessionId: string): Promise<DBChatSession> =>
      callCatalogOp("api.playground", "api_playground_get_chat_session", {
        session_id: sessionId,
      }),
    appendChatMessage: (
      sessionId: string,
      role: "user" | "assistant",
      content: string,
      engine?: string | null,
      goapState?: Record<string, unknown> | null,
      raw?: GraphResponse | null,
    ): Promise<{ id: string }> =>
      callCatalogOp("api.playground", "api_playground_append_chat_message", {
        session_id: sessionId,
        role,
        content,
        engine,
        goap_state: goapState,
        raw,
      }),
    graph: (): Promise<BackendGraph> => callCatalogOp("api.playground", "api_playground_graph"),
  },

  proxies: {
    list: (): Promise<ProxyServer[]> => callCatalogOp("api.proxy", "list_proxies"),
    add: (payload: {
      name: string
      transport: "http" | "sse"
      url: string
      authMode: ProxyAuthMode
      bearerToken?: string
      oauthConfig?: {
        authorize_url?: string
        token_url?: string
        client_id?: string
        scopes?: string[]
        pkce?: string
        auth_header?: string
      }
      clientSecret?: string
      customDescription?: string
      workspaceLabel?: string
    }): Promise<ProxyServer> =>
      callCatalogOp("api.proxy", "add_proxy", payload as unknown as Record<string, unknown>),
    remove: (name: string): Promise<{ ok: boolean; restartRequired: boolean }> =>
      callCatalogOp("api.proxy", "remove_proxy", { name }),
    test: (name: string): Promise<ProxyServer> => callCatalogOp("api.proxy", "test_proxy", { name }),
    startOAuth: (name: string): Promise<{ authorizeUrl: string }> =>
      callCatalogOp("api.proxy", "start_proxy_oauth", { name }),
    reindex: (name: string): Promise<{ status: string; message: string }> =>
      callCatalogOp("api.proxy", "reindex_proxy", { name }),
    updateDescription: (
      name: string,
      customDescription?: string | null,
      workspaceLabel?: string | null,
    ): Promise<{
      name: string
      customDescription?: string | null
      workspaceLabel?: string | null
      status: string
    }> => {
      const payload: Record<string, unknown> = { name }
      if (customDescription !== undefined) payload.customDescription = customDescription
      if (workspaceLabel !== undefined) payload.workspaceLabel = workspaceLabel
      return callCatalogOp("api.proxy", "update_proxy", payload)
    },
  },

  routes: {
    /** One aggregate row per plugin with route counts and enabled status. */
    aggregate: (): Promise<RoutesAggregateResponse> =>
      callCatalogOp("api.route", "api_list_routes"),
    /** Individual route rows for a specific plugin — same op, `plugin_id`-scoped response. */
    forPlugin: (pluginId: string): Promise<RoutesDetailResponse> =>
      callCatalogOp("api.route", "api_list_routes", { plugin_id: pluginId }),
    enablePluginRoutes: (pluginId: string): Promise<RouteToggleResponse> =>
      callCatalogOp("api.route", "api_enable_plugin_routes", { plugin_id: pluginId }),
    disablePluginRoutes: (pluginId: string): Promise<RouteToggleResponse> =>
      callCatalogOp("api.route", "api_disable_plugin_routes", { plugin_id: pluginId }),
    enableRoute: (
      pluginId: string,
      routeId: number,
    ): Promise<{ id: number; enabled: boolean; plugin_id: string }> =>
      callCatalogOp("api.route", "api_enable_route", { plugin_id: pluginId, route_id: routeId }),
    disableRoute: (
      pluginId: string,
      routeId: number,
    ): Promise<{ id: number; enabled: boolean; plugin_id: string }> =>
      callCatalogOp("api.route", "api_disable_route", { plugin_id: pluginId, route_id: routeId }),
  },

  health: {
    system: (): Promise<SystemHealth> => callCatalogOp("api.health", "api_system_health"),
  },

  relay: {
    status: (): Promise<{ configured: boolean }> =>
      callCatalogOp("api.relay", "api_relay_status"),
  },
}
