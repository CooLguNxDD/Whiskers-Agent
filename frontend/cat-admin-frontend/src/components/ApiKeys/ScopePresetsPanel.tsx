import { Sparkles, ChevronRight, Info } from "lucide-react"
import type { ScopePreset } from "@/api/apiKeys"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

interface ScopePresetsPanelProps {
  onApply: (preset: "all" | "none" | "terminal") => void
  presets: ScopePreset[]
  onApplyCustom: (scopes: string[] | null) => void
  selectedScopes: string[] | null
  allScopes: string[]
  className?: string
}

/**
 * Renders a panel to select predefined scope presets.
 */
export function ScopePresetsPanel({
  onApply,
  presets,
  onApplyCustom,
  selectedScopes,
  allScopes,
  className = "",
}: ScopePresetsPanelProps) {
  const isAllActive =
    selectedScopes === null ||
    (selectedScopes.length === 1 &&
      (selectedScopes[0] === "all" || selectedScopes[0] === "*")) ||
    (selectedScopes.length === allScopes.length &&
      allScopes.every((s) => selectedScopes.includes(s)))
  const isNoneActive = selectedScopes !== null && selectedScopes.length === 0
  const isTerminalActive =
    selectedScopes !== null &&
    selectedScopes.length === 2 &&
    selectedScopes.includes("terminal:use") &&
    selectedScopes.includes("terminal:host")

  const activeCustomPreset = presets.find((preset) => {
    if (selectedScopes === null && preset.scopes === null) return true
    if (selectedScopes === null || preset.scopes === null) return false
    if (selectedScopes.length !== preset.scopes.length) return false
    return preset.scopes.every((scope) => selectedScopes.includes(scope))
  })
  const activeCustomPresetId = activeCustomPreset ? activeCustomPreset.id : undefined

  const handleCustomPresetChange = (presetId: string) => {
    const preset = presets.find((p) => p.id === presetId)
    if (preset) {
      onApplyCustom(preset.scopes)
    }
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Scope Presets & Security Card */}
      <div className="bg-card border border-border rounded-xl p-4 space-y-4">
        <div className="flex items-center gap-2 text-sm font-bold text-foreground">
          <Sparkles className="size-4 text-[var(--amber)]" />
          <span>Scope Presets &amp; Security</span>
        </div>

        <div className="space-y-2">
          <button
            type="button"
            onClick={() => onApply("all")}
            className={`w-full text-left px-3.5 py-2.5 border rounded-lg text-xs font-semibold text-foreground flex items-center justify-between transition group cursor-pointer bg-transparent ${
              isAllActive
                ? "border-[color-mix(in_oklch,var(--amber)_45%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_15%,transparent)]"
                : "border-border hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] hover:bg-[color-mix(in_oklch,var(--amber)_5%,transparent)]"
            }`}
          >
            <div>
              <div className={`font-semibold transition-colors ${isAllActive ? "text-[var(--amber)]" : "text-foreground"}`}>
                Grant All Access
              </div>
              <div className="text-[10px] text-muted-foreground font-normal mt-0.5">Every current scope, explicitly listed</div>
            </div>
            <ChevronRight
              className={`size-4 transition-colors ${
                isAllActive ? "text-[var(--amber)]" : "text-muted-foreground group-hover:text-[var(--amber)]"
              }`}
            />
          </button>

          <button
            type="button"
            onClick={() => onApply("none")}
            className={`w-full text-left px-3.5 py-2.5 border rounded-lg text-xs font-semibold text-foreground flex items-center justify-between transition group cursor-pointer bg-transparent ${
              isNoneActive
                ? "border-[color-mix(in_oklch,var(--amber)_45%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_15%,transparent)]"
                : "border-border hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] hover:bg-[color-mix(in_oklch,var(--amber)_5%,transparent)]"
            }`}
          >
            <div>
              <div className={`font-semibold transition-colors ${isNoneActive ? "text-[var(--amber)]" : "text-foreground"}`}>
                Deny All Scopes (Lockdown)
              </div>
              <div className="text-[10px] text-muted-foreground font-normal mt-0.5">[] Empty set</div>
            </div>
            <ChevronRight
              className={`size-4 transition-colors ${
                isNoneActive ? "text-[var(--amber)]" : "text-muted-foreground group-hover:text-[var(--amber)]"
              }`}
            />
          </button>

          <button
            type="button"
            onClick={() => onApply("terminal")}
            className={`w-full text-left px-3.5 py-2.5 border rounded-lg text-xs font-semibold text-foreground flex items-center justify-between transition group cursor-pointer bg-transparent ${
              isTerminalActive
                ? "border-[color-mix(in_oklch,var(--amber)_45%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_15%,transparent)]"
                : "border-border hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] hover:bg-[color-mix(in_oklch,var(--amber)_5%,transparent)]"
            }`}
          >
            <div>
              <div className={`font-semibold transition-colors ${isTerminalActive ? "text-[var(--amber)]" : "text-foreground"}`}>
                Terminal Only Profile
              </div>
              <div className="text-[10px] text-muted-foreground font-normal mt-0.5 font-mono">terminal:use + terminal:host</div>
            </div>
            <ChevronRight
              className={`size-4 transition-colors ${
                isTerminalActive ? "text-[var(--amber)]" : "text-muted-foreground group-hover:text-[var(--amber)]"
              }`}
            />
          </button>

          {/* Saved Presets */}
          {presets.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-border mt-2 flex flex-col">
              <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider px-1 mb-1">
                Custom Presets
              </div>
              <Select value={activeCustomPresetId} onValueChange={handleCustomPresetChange}>
                <SelectTrigger
                  className={`w-full h-10 border text-xs flex items-center justify-between px-3.5 transition cursor-pointer ${
                    activeCustomPresetId
                      ? "border-[color-mix(in_oklch,var(--amber)_45%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_15%,transparent)] text-[var(--amber)] font-semibold"
                      : "border-border bg-transparent text-foreground hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] hover:bg-[color-mix(in_oklch,var(--amber)_5%,transparent)]"
                  }`}
                >
                  <SelectValue placeholder="Select a custom preset..." />
                </SelectTrigger>
                <SelectContent>
                  {presets.map((preset) => {
                    let subtitle = "No access"
                    if (preset.scopes === null) {
                      subtitle = "All access"
                    } else if (preset.scopes.length > 0) {
                      subtitle = `${preset.scopes.length} scope${preset.scopes.length === 1 ? "" : "s"}`
                    }
                    return (
                      <SelectItem key={preset.id} value={preset.id} className="text-xs cursor-pointer">
                        {preset.name} ({subtitle})
                      </SelectItem>
                    )
                  })}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
      </div>

      {/* Warning Card */}
      <div className="bg-[color-mix(in_oklch,var(--amber)_5%,transparent)] border border-[color-mix(in_oklch,var(--amber)_20%,var(--hairline))] rounded-xl p-4 space-y-2">
        <div className="flex items-center gap-2 text-xs font-bold text-[var(--amber)]">
          <Info className="size-4 text-[var(--amber)]" />
          <span>Scope Semantics</span>
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          A step is permitted if the caller holds <code className="font-mono text-[var(--amber)]">plugin:&lt;id&gt;</code> (whole plugin) OR any <code className="font-mono text-[var(--amber)]">group:&lt;id&gt;:&lt;tag&gt;</code> matching the route's declared tags.
        </p>
      </div>
    </div>
  )
}

export default ScopePresetsPanel
