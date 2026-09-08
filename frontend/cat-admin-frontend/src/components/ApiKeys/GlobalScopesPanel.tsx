import { Globe } from "lucide-react"

const SCOPE_DESCRIPTIONS: Record<string, string> = {
  "whiskers": "Full access to Whiskers Agent endpoints.",
  "core:terminal:write": "Allow terminal command execution.",
  "core:terminal:read": "Allow viewing and hosting terminal sessions.",
  "agy": "Orchestrate agentic workflows.",
  "antigravity": "Run Antigravity capabilities.",
}

interface GlobalScopesPanelProps {
  selectedScopes: string[] | null
  onToggleScope: (scope: string) => void
  className?: string
  scopes?: Array<{ id: string; description?: string }> | string[]
}

/**
 * Renders checkboxes for assigning global system-level scopes.
 */
export function GlobalScopesPanel({
  selectedScopes,
  onToggleScope,
  className = "",
  scopes,
}: GlobalScopesPanelProps) {
  const isAllAccess =
    selectedScopes === null ||
    (selectedScopes.length === 1 &&
      (selectedScopes[0] === "all" || selectedScopes[0] === "*"))

  const scopesList = scopes
    ? scopes.map((s) => {
        if (typeof s === "string") {
          return { id: s, description: SCOPE_DESCRIPTIONS[s] || "" }
        }
        return { id: s.id, description: s.description ?? SCOPE_DESCRIPTIONS[s.id] ?? "" }
      })
    : [
        { id: "whiskers", description: SCOPE_DESCRIPTIONS["whiskers"] },
        { id: "core:terminal:write", description: SCOPE_DESCRIPTIONS["core:terminal:write"] },
        { id: "core:terminal:read", description: SCOPE_DESCRIPTIONS["core:terminal:read"] },
      ]

  return (
    <div className={`ct-panel bg-card border border-border rounded-xl shadow-sm p-5 ${className}`}>
      <div className="flex items-center gap-2 mb-4 pb-2 border-b border-hairline">
        <Globe className="size-4 text-[var(--amber)]" />
        <h3 className="text-sm font-bold text-foreground font-mono">Global Scopes</h3>
      </div>
      <p className="text-xs text-muted-foreground mb-4 font-sans">
        Core scopes applied globally across the server environment.
      </p>

      <div className="space-y-4">
        {scopesList.map((scope) => {
          const isSelected = isAllAccess || (selectedScopes ? selectedScopes.includes(scope.id) : false)
          return (
            <label
              key={scope.id}
              className="flex items-start gap-3 cursor-pointer group p-2 hover:bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] rounded transition-colors"
            >
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggleScope(scope.id)}
                className="accent-[var(--amber)] bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] border border-[var(--border)] rounded h-4 w-4 mt-0.5 cursor-pointer"
              />
              <div>
                <span
                  className={`text-xs font-mono font-bold block transition-colors ${
                    isSelected ? "text-[var(--amber)]" : "text-foreground"
                  }`}
                >
                  {scope.id}
                </span>
                <span className="text-[10px] text-muted-foreground block mt-0.5 font-sans">
                  {scope.description}
                </span>
              </div>
            </label>
          )
        })}
      </div>
    </div>
  )
}

export default GlobalScopesPanel
