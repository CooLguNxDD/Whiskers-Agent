/**
 * SummaryStep — Pane 3 of the scope configurator: review resolved token list,
 * implied-closure preview, and save configuration as preset.
 */

import { useMemo } from "react"
import { CheckCircle2, ShieldAlert, Sparkles } from "lucide-react"
import SavePresetForm from "./SavePresetForm"
import type { PluginScopeContribution, CoreScopeContribution } from "@/api/apiKeys"

interface SummaryStepProps {
  selectedScopes: string[] | null
  globalScopesList: string[]
  pluginScopesList?: PluginScopeContribution[]
  coreScopesList?: CoreScopeContribution[]
  plugins: Array<{ id: string; name?: string }>
  isScopeSelected: (scope: string) => boolean
  isPluginWideSelected: (pluginId: string) => boolean
  getPluginGroups?: (pluginId: string, idx: number) => string[]
  onSaveAsPreset: (name: string) => void
  isSavingPreset: boolean
  impliedFor?: (token: string) => string[]
}

/** SummaryStep component for Pane 3 of the scope configurator. */
export function SummaryStep({
  selectedScopes,
  globalScopesList,
  pluginScopesList = [],
  coreScopesList = [],
  plugins,
  isScopeSelected,
  isPluginWideSelected,
  onSaveAsPreset,
  isSavingPreset,
  impliedFor,
}: SummaryStepProps) {
  const isAllAccess =
    selectedScopes === null ||
    (selectedScopes.length === 1 &&
      (selectedScopes[0] === "all" || selectedScopes[0] === "*"))

  // Collect all explicitly held tokens and their implied tokens
  const explicitTokens = useMemo(() => {
    if (isAllAccess) return ["all"]
    return selectedScopes ?? []
  }, [selectedScopes, isAllAccess])

  const impliedMap = useMemo(() => {
    const map = new Map<string, Set<string>>()
    if (!impliedFor || isAllAccess) return map
    explicitTokens.forEach((token) => {
      const implied = impliedFor(token)
      if (implied.length > 0) {
        map.set(token, new Set(implied))
      }
    })
    return map
  }, [explicitTokens, impliedFor, isAllAccess])

  const allImpliedTokens = useMemo(() => {
    const set = new Set<string>()
    impliedMap.forEach((impliedSet) => {
      impliedSet.forEach((t) => set.add(t))
    })
    return set
  }, [impliedMap])

  return (
    <div className="ct-panel bg-card border border-border rounded-xl shadow-sm p-6 space-y-6">
      <div>
        <h3 className="text-sm font-bold text-foreground">
          Configuration Review
        </h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          Review resolved permissions and implied token closures before saving.
        </p>
      </div>

      {/* Explicit Grants */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground font-mono">
            Explicit Token Grants
          </h4>
          <span className="ct-scope-chip font-mono">
            {isAllAccess ? "Full Platform Access" : `${explicitTokens.length} token${explicitTokens.length === 1 ? "" : "s"}`}
          </span>
        </div>

        {isAllAccess ? (
          <div className="p-4 rounded-lg bg-[color-mix(in_oklch,var(--amber)_8%,transparent)] border border-[color-mix(in_oklch,var(--amber)_30%,transparent)] flex items-center gap-3">
            <Sparkles className="size-5 text-[var(--amber)] shrink-0" />
            <div>
              <span className="text-xs font-mono font-bold text-[var(--amber)]">all (*)</span>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Grants unrestricted access across all core platform APIs and installed plugins.
              </p>
            </div>
          </div>
        ) : explicitTokens.length === 0 ? (
          <div className="p-4 rounded-lg bg-[color-mix(in_oklch,var(--danger)_8%,transparent)] border border-[color-mix(in_oklch,var(--danger)_30%,transparent)] flex items-center gap-3">
            <ShieldAlert className="size-5 text-[var(--danger)] shrink-0" />
            <div>
              <span className="text-xs font-mono font-bold text-[var(--danger)]">No Scopes (none)</span>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                This key has no active permissions and will fail closed on any gated route.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            {explicitTokens.map((token) => {
              const isAlreadyImplied = allImpliedTokens.has(token)
              const hasImpliedChildren = impliedMap.has(token)
              const childTokens = Array.from(impliedMap.get(token) || [])

              return (
                <div
                  key={token}
                  className={`p-3 border rounded-lg flex flex-col gap-1 text-xs transition-colors ${
                    isAlreadyImplied
                      ? "border-border/60 bg-[color-mix(in_oklch,var(--fg)_3%,transparent)] opacity-75"
                      : "border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_6%,transparent)] text-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-[var(--amber)]">{token}</span>
                    {isAlreadyImplied && (
                      <span className="text-[9px] font-mono text-muted-foreground uppercase bg-[color-mix(in_oklch,var(--fg)_6%,transparent)] px-1.5 py-0.5 rounded">
                        implied by broader grant
                      </span>
                    )}
                  </div>
                  {hasImpliedChildren && (
                    <div className="text-[10px] text-muted-foreground font-mono flex items-center gap-1 mt-1">
                      <CheckCircle2 className="size-3 text-[var(--amber)]" />
                      <span>implies: {childTokens.join(", ")}</span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Save Preset */}
      <div className="pt-4 border-t border-hairline">
        <SavePresetForm onSaveAsPreset={onSaveAsPreset} isSavingPreset={isSavingPreset} />
      </div>
    </div>
  )
}

export default SummaryStep
