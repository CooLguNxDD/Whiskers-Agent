import { useState, useMemo } from "react"

import AppShell from "@/components/shell/AppShell"
import { getErrorMessage } from "@/utils/errors"
import type { ApiKey } from "@/api/apiKeys"
import { Plus, Refresh } from "@/components/shell/Icons"
import { useKeyedClipboard } from "@/hooks/useClipboard"
import { useScopeEditor } from "@/hooks/useScopeEditor"

import {
  useApiKeysQuery,
  useCreateApiKeyMutation,
  useRevokeApiKeyMutation,
  useDeleteApiKeyMutation,
  useUpdateApiKeyScopesMutation,
  usePluginsQuery,
  usePluginToolsQueries,
  useScopePresetsQuery,
  useSaveScopePresetMutation,
  useScopeVocabularyQuery,
} from "@/hooks/useApiKeys"

import {
  TokenRevealBanner,
  ConfirmActionModal,
  CreateKeyModal,
  ScopeConfigurator,
  EmptyState,
  ApiKeysDashboard,
  buildApiKeyColumns,
} from "@/components/ApiKeys"
import type { ApiKeyStatusFilter } from "@/components/ApiKeys/ApiKeysDashboard"

/** ApiKeysPage component for managing API keys, presenting a dashboard to view, create, and revoke keys. */
export function ApiKeysPage() {
  // --- State (view mode + modals) ---
  const [editingKey, setEditingKey] = useState<ApiKey | null>(null)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [confirmAction, setConfirmAction] = useState<{ type: "revoke" | "delete"; key: ApiKey } | null>(null)
  const [generatedToken, setGeneratedToken] = useState<string | null>(null)
  const [expandedPlugins, setExpandedPlugins] = useState<Record<string, boolean>>({})
  const [scopeFormError, setScopeFormError] = useState<string | null>(null)

  // --- Pagination and filtering ---
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<ApiKeyStatusFilter>("all")

  // --- Clipboard ---
  const { copiedId: copiedPrefixId, copy: copyPrefix } = useKeyedClipboard()

  // --- Server state hooks ---
  const { data: apiKeys = [], isPending, error, refetch } = useApiKeysQuery()

  const createMutation = useCreateApiKeyMutation((resp) => {
    setGeneratedToken(resp.token)
    setCreateModalOpen(false)
  })

  const revokeMutation = useRevokeApiKeyMutation((resp) => {
    setGeneratedToken(resp.token)
    setConfirmAction(null)
  })

  const deleteMutation = useDeleteApiKeyMutation()

  const updateScopesMutation = useUpdateApiKeyScopesMutation(() => {
    setEditingKey(null)
  })

  const { data: presetsData = [] } = useScopePresetsQuery(!!editingKey)
  const savePresetMutation = useSaveScopePresetMutation()

  const { data: pluginsData } = usePluginsQuery(!!editingKey)
  const plugins = pluginsData?.plugins ?? []
  const toolsQueries = usePluginToolsQueries(plugins, !!editingKey)
  const { data: scopeVocabularyData } = useScopeVocabularyQuery(!!editingKey)
  const globalScopesList = useMemo(() => {
    return scopeVocabularyData?.global_scopes ?? ["whiskers", "core:terminal:write", "core:terminal:read"]
  }, [scopeVocabularyData])
  const pluginScopesList = useMemo(() => {
    return scopeVocabularyData?.plugin_scopes ?? []
  }, [scopeVocabularyData])
  const coreScopesList = useMemo(() => {
    return scopeVocabularyData?.core_scopes ?? []
  }, [scopeVocabularyData])
  const scopeEditor = useScopeEditor(
    editingKey ? (editingKey.scopes ?? null) : null,
    plugins,
    toolsQueries,
    globalScopesList,
    pluginScopesList,
    coreScopesList
  )

  const handleSaveAsPreset = (name: string) => {
    savePresetMutation.mutate({
      name,
      scopes: scopeEditor.scopesForPersist(),
    })
  }

  // --- Filtering logic ---
  const filtered = useMemo(() => {
    return apiKeys.filter((key) => {
      const q = query.trim().toLowerCase()
      if (q && !(
        key.name.toLowerCase().includes(q) ||
        key.key_id.toLowerCase().includes(q) ||
        key.prefix.toLowerCase().includes(q)
      )) return false
      if (filter === "active" && key.status !== "active") return false
      if (filter === "revoked" && key.status !== "revoked") return false
      return true
    })
  }, [apiKeys, query, filter])

  // --- Column definitions ---
  const columns = useMemo(() => buildApiKeyColumns({
    copiedPrefixId,
    onCopyPrefix: (prefix, keyId) => copyPrefix(prefix, keyId),
    onConfigureScopes: (k) => setEditingKey(k),
    onRotate: (k) => setConfirmAction({ type: "revoke", key: k }),
    onDelete: (k) => setConfirmAction({ type: "delete", key: k }),
  }), [copiedPrefixId, copyPrefix])

  // --- Callbacks ---
  const handleConfirm = () => {
    if (!confirmAction) return
    if (confirmAction.type === "revoke") {
      revokeMutation.mutate(confirmAction.key.key_id)
    } else {
      deleteMutation.mutate(confirmAction.key.key_id, {
        onSuccess: () => {
          setConfirmAction(null)
        }
      })
    }
  }

  const handleSaveScopes = () => {
    if (!editingKey) return
    setScopeFormError(null)
    updateScopesMutation.mutate(
      { keyId: editingKey.key_id, scopes: scopeEditor.scopesForPersist() },
      {
        onError: (err) => {
          setScopeFormError(getErrorMessage(err, "Failed to update scopes."))
        },
      }
    )
  }

  // --- View routing ---
  // View 1: Scope configurator (editingKey is set)
  if (editingKey) {
    return (
      <AppShell active="api-keys">
        <ScopeConfigurator
          key={editingKey.key_id}
          editingKey={editingKey}
          plugins={plugins}
          expandedPlugins={expandedPlugins}
          onBack={() => setEditingKey(null)}
          onSave={handleSaveScopes}
          isSaving={updateScopesMutation.isPending}
          formError={scopeFormError}
          scopeEditor={scopeEditor}
          onToggleExpand={(id) => setExpandedPlugins((p) => ({ ...p, [id]: !p[id] }))}
          presets={presetsData}
          onSaveAsPreset={handleSaveAsPreset}
          isSavingPreset={savePresetMutation.isPending}
          globalScopesList={globalScopesList}
          pluginScopesList={pluginScopesList}
          coreScopesList={coreScopesList}
        />
      </AppShell>
    )
  }
  return (
    <AppShell active="api-keys">
      <div className="ct-page-head">
        <div>
          <div className="ct-page-title flex items-center gap-2">
            API Keys
            <span className="ct-pill is-amber font-mono text-[10px]">BETA</span>
          </div>
          <div className="ct-page-sub">
            Manage credentials for external applications to connect to the Whiskers Agent server.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="ct-btn-ghost" onClick={() => void refetch()}>
            <Refresh width="13" height="13" style={{ marginRight: 6 }} /> Refresh
          </button>
          {apiKeys.length > 0 && (
            <button className="ct-btn-primary" onClick={() => setCreateModalOpen(true)}>
              <Plus width="13" height="13" /> Create API Key
          </button>
          )}
        </div>
      </div>

      {/* Token reveal banner (when a key was just created or rotated) */}
      {generatedToken && (
        <TokenRevealBanner
          token={generatedToken}
          onDismiss={() => setGeneratedToken(null)}
        />
      )}

      {/* Loading / Error */}
      {isPending && <div className="py-8 text-muted-foreground text-xs font-mono">Loading…</div>}
      {error && <div className="py-8 text-[var(--danger)] text-xs">{getErrorMessage(error)}</div>}

      {/* Empty state */}
      {!isPending && !error && apiKeys.length === 0 && (
        <EmptyState onCreateClick={() => setCreateModalOpen(true)} />
      )}

      {/* Dashboard: filters + grid list + pagination */}
      {!isPending && !error && apiKeys.length > 0 && (
        <ApiKeysDashboard
          columns={columns}
          filtered={filtered}
          totalCount={apiKeys.length}
          query={query}
          onQueryChange={(q) => { setQuery(q); setPage(1) }}
          filter={filter}
          onFilterChange={(f) => { setFilter(f); setPage(1) }}
          page={page}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={(n) => { setPerPage(n); setPage(1) }}
          onRowClick={(k) => setEditingKey(k)}
        />
      )}

      {/* Modals */}
      <CreateKeyModal
        open={createModalOpen}
        onOpenChange={setCreateModalOpen}
        onSubmit={(name, secs) => createMutation.mutate({ keyName: name, secs })}
        isPending={createMutation.isPending}
        error={createMutation.isError ? getErrorMessage(createMutation.error) : null}
      />

      <ConfirmActionModal
        action={confirmAction}
        onConfirm={handleConfirm}
        onClose={() => setConfirmAction(null)}
        isPending={revokeMutation.isPending || deleteMutation.isPending}
      />
    </AppShell>
  )
}
