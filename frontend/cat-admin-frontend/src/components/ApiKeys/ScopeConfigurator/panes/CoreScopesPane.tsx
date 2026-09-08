/**
 * CoreScopesPane — Level 1 scope configurator: platform "core" domains.
 *
 * Groups the flat `core:<domain>:<read|write>` vocabulary by domain and
 * renders one read/write toggle pair per domain. The Quick Presets bar
 * applies a whole-domain shortcut ("all" -> ["all"] sentinel, "terminal" ->
 * the two terminal core tokens, "none" -> deny-all `[]`) plus any saved
 * custom presets from the caller.
 */
import { useMemo } from "react"
import { Shield, Sparkles } from "lucide-react"
import type { CoreScopeContribution, ScopePreset } from "@/api/apiKeys"

interface CoreScopesPaneProps {
  coreScopesList: CoreScopeContribution[]
  selectedScopes: string[] | null
  onToggleScope: (scope: string) => void
  presets?: ScopePreset[]
  onApplyPreset?: (preset: "all" | "none" | "terminal") => void
  onApplyCustom?: (scopes: string[] | null) => void
}

interface DomainGroup {
  domain: string
  readToken?: string
  readDesc?: string
  writeToken?: string
  writeDesc?: string
}

/** Renders the level-1 core-domain read/write toggle grid plus the quick-preset bar. See file header for the grouping/preset semantics. */
export function CoreScopesPane({
  coreScopesList,
  selectedScopes,
  onToggleScope,
  presets = [],
  onApplyPreset,
  onApplyCustom,
}: CoreScopesPaneProps) {
  const isAllAccess =
    selectedScopes === null ||
    (selectedScopes.length === 1 && (selectedScopes[0] === "all" || selectedScopes[0] === "*"))

  const isSelected = (token: string): boolean => {
    if (isAllAccess) return true
    return selectedScopes ? selectedScopes.includes(token) : false
  }

  // Group core scopes by domain
  const domainGroups = useMemo(() => {
    const map = new Map<string, DomainGroup>()
    coreScopesList.forEach((item) => {
      let domain = item.domain || item.id_or_domain
      let access = item.access
      if (!domain && item.token.startsWith("core:")) {
        const parts = item.token.split(":")
        domain = parts[1]
        access = parts[2]
      }
      if (!domain) domain = "platform"

      if (!map.has(domain)) {
        map.set(domain, { domain })
      }
      const g = map.get(domain)!
      if (access === "write") {
        g.writeToken = item.token
        g.writeDesc = item.description
      } else {
        g.readToken = item.token
        g.readDesc = item.description
      }
    })
    return Array.from(map.values()).sort((a, b) => a.domain.localeCompare(b.domain))
  }, [coreScopesList])

  return (
    <div className="space-y-6">
      {/* Quick Presets Bar */}
      {onApplyPreset && (
        <div className="ct-panel bg-card border border-border rounded-xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3 pb-2 border-b border-hairline">
            <Sparkles className="size-4 text-[var(--amber)]" />
            <h4 className="text-xs font-bold uppercase tracking-wider text-foreground font-mono">
              Quick Presets
            </h4>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(() => {
              const isTerminalOnly =
                !isAllAccess &&
                selectedScopes?.length === 2 &&
                selectedScopes.includes("core:terminal:write") &&
                selectedScopes.includes("core:terminal:read")
              const isNone = !isAllAccess && selectedScopes?.length === 0
              return (
                <>
                  <button
                    type="button"
                    onClick={() => onApplyPreset("all")}
                    aria-pressed={isAllAccess}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-colors border cursor-pointer ${
                      isAllAccess
                        ? "bg-[color-mix(in_oklch,var(--amber)_15%,transparent)] text-[var(--amber)] border-[color-mix(in_oklch,var(--amber)_35%,transparent)]"
                        : "bg-transparent text-muted-foreground border-border hover:text-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                    }`}
                  >
                    Full Access (All)
                  </button>
                  <button
                    type="button"
                    onClick={() => onApplyPreset("terminal")}
                    aria-pressed={isTerminalOnly}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-colors border cursor-pointer ${
                      isTerminalOnly
                        ? "bg-[color-mix(in_oklch,var(--amber)_15%,transparent)] text-[var(--amber)] border-[color-mix(in_oklch,var(--amber)_35%,transparent)]"
                        : "bg-transparent text-muted-foreground border-border hover:text-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                    }`}
                  >
                    Terminal Only
                  </button>
                  <button
                    type="button"
                    onClick={() => onApplyPreset("none")}
                    aria-pressed={isNone}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-colors border cursor-pointer ${
                      isNone
                        ? "bg-[color-mix(in_oklch,var(--danger)_15%,transparent)] text-[var(--danger)] border-[color-mix(in_oklch,var(--danger)_35%,transparent)]"
                        : "bg-transparent text-muted-foreground border-border hover:text-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                    }`}
                  >
                    None (Revoke All)
                  </button>
                </>
              )
            })()}

            {presets.length > 0 && onApplyCustom && (
              <>
                <div className="h-4 w-px bg-hairline mx-1" />
                {presets.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => onApplyCustom(p.scopes)}
                    className="px-2.5 py-1.5 rounded-lg text-xs font-sans text-muted-foreground border border-border hover:text-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)] transition-colors cursor-pointer"
                  >
                    {p.name}
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {/* Level 1 Core Scopes */}
      <div className="ct-panel bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="p-5 border-b border-hairline ct-surface-sunken flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
              <Shield className="size-4 text-[var(--amber)]" />
              Level 1 — Core Platform Permissions
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Core platform scopes structured by domain with read and write access controls.
            </p>
          </div>
          <span className="ct-scope-chip font-mono">
            {domainGroups.length} Domains
          </span>
        </div>

        <div className="divide-y divide-hairline">
          {domainGroups.map((group) => {
            const hasRead = !!group.readToken
            const hasWrite = !!group.writeToken
            const readSelected = group.readToken ? isSelected(group.readToken) : false
            const writeSelected = group.writeToken ? isSelected(group.writeToken) : false

            return (
              <div
                key={group.domain}
                className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-[color-mix(in_oklch,var(--fg)_3%,transparent)] transition-colors"
              >
                <div className="flex flex-col gap-1 min-w-[200px]">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono font-bold text-foreground">
                      core:{group.domain}:*
                    </span>
                  </div>
                  <span className="text-[11px] text-muted-foreground">
                    {group.writeDesc || group.readDesc || `Access to ${group.domain} domain`}
                  </span>
                </div>

                <div className="flex items-center gap-4">
                  {hasRead && (
                    <label
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-mono cursor-pointer transition-colors ${
                        readSelected || (writeSelected && !isAllAccess)
                          ? "border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_8%,transparent)] text-[var(--amber)]"
                          : "border-border text-muted-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={readSelected}
                        onChange={() => onToggleScope(group.readToken!)}
                        className="accent-[var(--amber)] rounded cursor-pointer"
                      />
                      <span>read</span>
                    </label>
                  )}

                  {hasWrite && (
                    <label
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-mono cursor-pointer transition-colors ${
                        writeSelected
                          ? "border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_8%,transparent)] text-[var(--amber)]"
                          : "border-border text-muted-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={writeSelected}
                        onChange={() => onToggleScope(group.writeToken!)}
                        className="accent-[var(--amber)] rounded cursor-pointer"
                      />
                      <span>write</span>
                    </label>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default CoreScopesPane
