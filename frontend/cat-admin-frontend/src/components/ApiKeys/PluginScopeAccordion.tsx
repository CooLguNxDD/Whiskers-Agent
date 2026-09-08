import { Sliders, ChevronDown, ChevronUp } from "lucide-react"

interface PluginScopeAccordionProps {
  plugin: { id: string; name?: string }
  groups: string[] // route group tags e.g. ["read","write","admin"]
  selectedScopes: string[] | null
  isExpanded: boolean
  isPluginWideSelected: boolean
  onToggleExpand: () => void
  onToggleCoarse: () => void // toggles plugin:<id>
  onToggleGroup: (scope: string) => void // toggles group:<id>:<tag>
}

/**
 * Accordion component allowing users to set fine-grained plugin scopes.
 */
export function PluginScopeAccordion({
  plugin,
  groups,
  selectedScopes,
  isExpanded,
  isPluginWideSelected,
  onToggleExpand,
  onToggleCoarse,
  onToggleGroup,
}: PluginScopeAccordionProps) {
  const isAll =
    selectedScopes === null ||
    (selectedScopes.length === 1 &&
      (selectedScopes[0] === "all" || selectedScopes[0] === "*"))
  const scopes = selectedScopes ?? []
  const selectedCount = isAll
    ? 1 + groups.length
    : (scopes.includes(`plugin:${plugin.id}`) ? 1 : 0) +
      groups.filter((g) => scopes.includes(`group:${plugin.id}:${g}`)).length

  return (
    <div className="bg-transparent border-b border-hairline last:border-b-0">
      <button
        onClick={onToggleExpand}
        className="w-full text-left px-5 py-4 flex items-center justify-between hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)] transition-colors bg-transparent border-0 cursor-pointer focus:outline-none"
      >
        <div className="flex items-center gap-3">
          <Sliders className="size-4 text-muted-foreground" />
          <span className="text-xs font-mono font-medium text-foreground">{plugin.id}</span>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded border transition-colors ${
              selectedCount > 0 ? "ct-tag-amber" : "ct-scope-chip"
            }`}
          >
            {selectedCount} selected
          </span>
          {isExpanded ? (
            <ChevronUp className="size-3 text-muted-foreground" />
          ) : (
            <ChevronDown className="size-3 text-muted-foreground" />
          )}
        </div>
      </button>

      {/* Expanded details */}
      {isExpanded && (
        <div className="px-5 pb-5 pt-2 bg-[color-mix(in_oklch,var(--fg)_6%,transparent)] border-t border-hairline/30 space-y-4">
          {/* Plugin Wide toggle */}
          <div className="flex items-center justify-between pb-3 border-b border-hairline/30">
            <div>
              <span className="text-xs font-bold text-foreground block">Plugin-wide Access</span>
              <span className="text-[10px] text-muted-foreground block mt-0.5 font-sans">
                Grant full access to all routes within this plugin.
              </span>
            </div>
            <input
              type="checkbox"
              checked={isPluginWideSelected}
              onChange={onToggleCoarse}
              className="accent-[var(--amber)] bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] border-[var(--border)] rounded h-4 w-4 cursor-pointer"
            />
          </div>

          {/* Group list */}
          {groups.length === 0 ? (
            <div className="text-[10px] text-muted-foreground italic font-mono p-1">
              No specific route groups defined for this plugin.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {groups.map((group) => {
                const groupScope = `group:${plugin.id}:${group}`
                const isSelected =
                  isPluginWideSelected ||
                  (selectedScopes !== null && selectedScopes.includes(groupScope))

                return (
                  <label
                    key={group}
                    className={`flex items-start gap-3 cursor-pointer p-2 rounded border border-transparent hover:bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] transition-colors ${
                      isSelected ? "bg-[color-mix(in_oklch,var(--amber)_5%,transparent)] border-[color-mix(in_oklch,var(--amber)_15%,var(--hairline))]" : ""
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => onToggleGroup(groupScope)}
                      className="accent-[var(--amber)] bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] border-[var(--border)] rounded h-3.5 w-3.5 mt-0.5 cursor-pointer"
                    />
                    <div className="flex flex-col">
                      <span
                        className={`text-[10px] font-mono font-medium transition-colors ${
                          isSelected ? "text-[var(--amber)]" : "text-foreground"
                        }`}
                      >
                        group:{plugin.id}:{group}
                      </span>
                    </div>
                  </label>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default PluginScopeAccordion
