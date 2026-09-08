/**
 * useConfig Hooks
 *
 * Hooks for reading and persisting non-sensitive server configuration overrides.
 * LLM provider, model, and RAG settings are fetched from /api/config/llm and
 * saved back via POST.  API keys are never exposed.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  getLlmConfig,
  saveLlmConfig,
  type LlmConfig,
  type LlmConfigUpdate,
  getLlmPool,
  addLlmPoolEntry,
  deleteLlmPoolEntry,
  setLlmActive,
  toggleLlmPoolEntryActive,
  type LlmPoolConfig,
  getGatewayConfig,
  saveGatewayConfig,
  type GatewayConfig,
  getStepModelPolicy,
  saveStepModelPolicy,
  type StepModelConfig,
  type StepModelPolicy,
  getModelRoles,
  saveModelRole,
  saveEffortMap,
  deleteModelRole,
  type ModelRoleSpecInput,
  getCliAgents,
  getPluginGates,
  savePluginGate,
  deletePluginGate,
  type PluginGateSpec,
  type PluginGatesConfig,
} from "@/api/config"

const LLM_CONFIG_KEY = ["config", "llm"] as const

/**
 * Fetches current LLM/RAG settings merged from env vars and DB overrides.
 * Stale after 30 s — config rarely changes.
 */
export function useLlmConfigQuery() {
  return useQuery({
    queryKey: LLM_CONFIG_KEY,
    queryFn: getLlmConfig,
    staleTime: 30_000,
  })
}

/**
 * Mutation to save non-sensitive LLM/RAG settings.
 * Optimistically merges the update into the cache; reverts on error.
 */
export function useSaveLlmConfigMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (update: Partial<LlmConfigUpdate>) => saveLlmConfig(update),

    onMutate: async (update) => {
      await qc.cancelQueries({ queryKey: LLM_CONFIG_KEY })
      const previous = qc.getQueryData<LlmConfig>(LLM_CONFIG_KEY)
      qc.setQueryData<LlmConfig>(LLM_CONFIG_KEY, (old) =>
        old ? { ...old, ...update } : old
      )
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(LLM_CONFIG_KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LLM_CONFIG_KEY })
    },
  })
}

const LLM_POOL_KEY = ["config", "llm-pool"] as const

/** Hook to fetch the LLM pool configuration. */
export function useLlmPoolQuery() {
  return useQuery({
    queryKey: LLM_POOL_KEY,
    queryFn: getLlmPool,
    staleTime: 5_000,
  })
}

/** Hook to add a new entry to the LLM pool. */
export function useAddLlmPoolEntryMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: addLlmPoolEntry,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LLM_POOL_KEY })
    },
  })
}

/** Hook to delete an entry from the LLM pool. */
export function useDeleteLlmPoolEntryMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteLlmPoolEntry,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LLM_POOL_KEY })
    },
  })
}

/** Hook to set the active LLM for a specific usage kind. */
export function useSetLlmActiveMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, id }: { kind: "chat" | "embedding" | "route" | "core"; id: string }) => setLlmActive(kind, id),
    onMutate: async ({ kind, id }) => {
      await qc.cancelQueries({ queryKey: LLM_POOL_KEY })
      const previous = qc.getQueryData<LlmPoolConfig>(LLM_POOL_KEY)
      qc.setQueryData<LlmPoolConfig>(LLM_POOL_KEY, (old) =>
        old ? { ...old, active: { ...old.active, [kind]: id } } : old
      )
      return { previous }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(LLM_POOL_KEY, ctx.previous)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LLM_POOL_KEY })
    },
  })
}

/** Hook to toggle the active state of an LLM pool entry. */
export function useToggleLlmPoolEntryActiveMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => toggleLlmPoolEntryActive(id, enabled),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: LLM_POOL_KEY })
      const previous = qc.getQueryData<LlmPoolConfig>(LLM_POOL_KEY)
      qc.setQueryData<LlmPoolConfig>(LLM_POOL_KEY, (old) =>
        old
          ? {
              ...old,
              entries: old.entries.map((e) =>
                e.id === id ? { ...e, is_active: enabled } : e
              ),
            }
          : old
      )
      return { previous }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(LLM_POOL_KEY, ctx.previous)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LLM_POOL_KEY })
    },
  })
}

const GATEWAY_CONFIG_KEY = ["config", "gateway"] as const

/**
 * Fetches current gateway (run_graph_unified) flag.
 */
export function useGatewayConfigQuery() {
  return useQuery({
    queryKey: GATEWAY_CONFIG_KEY,
    queryFn: getGatewayConfig,
    staleTime: 10_000,
  })
}

/**
 * Mutation to save gateway flag (live apply server-side).
 */
export function useSaveGatewayConfigMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (update: { run_graph_unified: boolean }) => saveGatewayConfig(update),

    onMutate: async (update) => {
      await qc.cancelQueries({ queryKey: GATEWAY_CONFIG_KEY })
      const previous = qc.getQueryData<GatewayConfig>(GATEWAY_CONFIG_KEY)
      qc.setQueryData<GatewayConfig>(GATEWAY_CONFIG_KEY, (old) =>
        old ? { ...old, ...update } : update
      )
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(GATEWAY_CONFIG_KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: GATEWAY_CONFIG_KEY })
    },
  })
}

const STEP_MODELS_KEY = ["config", "step-models"] as const

/**
 * Fetches current GOAP step-model policy and pool entry names.
 */
export function useStepModelPolicyQuery() {
  return useQuery({
    queryKey: STEP_MODELS_KEY,
    queryFn: getStepModelPolicy,
    staleTime: 10_000,
  })
}

/**
 * Mutation to save step-model policy (optimistic merge into cached policy).
 */
export function useSaveStepModelPolicyMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (patch: Partial<StepModelPolicy>) => saveStepModelPolicy(patch),

    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: STEP_MODELS_KEY })
      const previous = qc.getQueryData<StepModelConfig>(STEP_MODELS_KEY)
      qc.setQueryData<StepModelConfig>(STEP_MODELS_KEY, (old) =>
        old ? { ...old, policy: { ...old.policy, ...patch } } : old
      )
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(STEP_MODELS_KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: STEP_MODELS_KEY })
    },
  })
}

const MODEL_ROLES_KEY = ["config", "model-roles"] as const

/**
 * Fetches every model-role ladder (source-tagged), the effort_map, active
 * pool names, the selector/effort vocabulary, and the generated node->role
 * map for the config UI.
 */
export function useModelRolesQuery() {
  return useQuery({
    queryKey: MODEL_ROLES_KEY,
    queryFn: getModelRoles,
    staleTime: 10_000,
  })
}

/**
 * Mutation to save one role's ladder. Invalidates on settle rather than
 * optimistic-merging — a rejected (400) spec must not appear to have saved.
 */
export function useSaveModelRoleMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: ({ roleId, spec }: { roleId: string; spec: ModelRoleSpecInput }) =>
      saveModelRole(roleId, spec),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MODEL_ROLES_KEY })
    },
  })
}

/**
 * Mutation to save the fleet-wide effort_map.
 */
export function useSaveEffortMapMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (effortMap: Record<string, string>) => saveEffortMap(effortMap),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MODEL_ROLES_KEY })
    },
  })
}

/**
 * Mutation to revert one role's DB override to its core/plugin default.
 */
export function useDeleteModelRoleMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (roleId: string) => deleteModelRole(roleId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MODEL_ROLES_KEY })
    },
  })
}

const CLI_AGENTS_KEY = ["config", "cli-agents"] as const

/**
 * Fetches registered CLI agent drivers with live binary availability from the server.
 * Replaces the hardcoded CLI_PROVIDERS constant — source of truth is now registry.py.
 */
export function useCliAgentsQuery() {
  return useQuery({
    queryKey: CLI_AGENTS_KEY,
    queryFn: getCliAgents,
    staleTime: 30_000,
  })
}

const PLUGIN_GATES_KEY = ["config", "plugin-gates"] as const

/**
 * Fetches all plugin gates (manifest or DB override) for the config UI.
 */
export function usePluginGatesQuery() {
  return useQuery({
    queryKey: PLUGIN_GATES_KEY,
    queryFn: getPluginGates,
    staleTime: 10_000,
  })
}

/**
 * Mutation to save one plugin's gate override.
 */
export function useSavePluginGateMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: ({ pluginId, spec }: { pluginId: string; spec: Partial<PluginGateSpec> }) =>
      savePluginGate(pluginId, spec),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_GATES_KEY })
    },
  })
}

/**
 * Mutation to delete/reset one plugin's DB gate override back to manifest default.
 */
export function useDeletePluginGateMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (pluginId: string) => deletePluginGate(pluginId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_GATES_KEY })
    },
  })
}
