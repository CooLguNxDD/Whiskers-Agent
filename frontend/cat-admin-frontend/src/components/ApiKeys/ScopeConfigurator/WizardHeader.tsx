/**
 * WizardHeader — 3-pane navigation header for the scope configurator:
 * pane tabs, key prefix badge, and Back/Next/Save controls.
 */

import { ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { ApiKey } from "@/api/apiKeys"

/** The 3 wizard steps (label + level badge), in step order, driving both the tab strip and the "Next: <label>" button text. */
export const PANE_TABS = [
  { step: 1 as const, label: "Core Platform", desc: "Level 1" },
  { step: 2 as const, label: "Plugins", desc: "Level 2" },
  { step: 3 as const, label: "Review & Presets", desc: "Level 3" },
]

interface WizardHeaderProps {
  editingKey: ApiKey
  step: 1 | 2 | 3
  onSelectStep?: (step: 1 | 2 | 3) => void
  onStepBack: () => void
  onStepNext: () => void
  onBack: () => void
  onSave: () => void
  isSaving: boolean
}

/**
 * Header component for the 3-pane ScopeConfigurator with tabbed navigation.
 */
export function WizardHeader({
  editingKey,
  step,
  onSelectStep,
  onStepBack,
  onStepNext,
  onBack,
  onSave,
  isSaving,
}: WizardHeaderProps) {
  return (
    <div className="sticky top-0 z-10 bg-card border-b border-border px-6 py-4 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={step === 1 ? onBack : onStepBack}
            className="p-1.5 hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)] rounded-lg transition-colors cursor-pointer bg-transparent border-0 text-muted-foreground hover:text-foreground focus:outline-none flex items-center justify-center"
            title={step === 1 ? "Back to Dashboard" : "Previous Step"}
          >
            <ArrowLeft className="size-4" />
          </button>
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-foreground">
                Configure Scopes — {editingKey.name}
              </span>
              <span className="ct-tag-amber text-[10px] font-mono px-1.5 py-0.5 rounded">
                {editingKey.prefix}…
              </span>
            </div>
            <span className="text-xs text-muted-foreground font-mono">
              Key ID: {editingKey.key_id}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            disabled={isSaving}
            className="text-xs font-bold font-sans h-8"
          >
            Cancel
          </Button>

          {step < 3 ? (
            <Button
              variant="default"
              size="sm"
              onClick={onStepNext}
              className="ct-btn-primary text-xs font-bold font-sans h-8"
            >
              Next: {PANE_TABS[step].label}
            </Button>
          ) : (
            <Button
              variant="default"
              size="sm"
              onClick={onSave}
              disabled={isSaving}
              className="ct-btn-primary text-xs font-bold font-sans h-8"
            >
              {isSaving ? "Saving..." : "Save & Apply Changes"}
            </Button>
          )}
        </div>
      </div>

      {/* Pane tabs bar */}
      <div className="flex items-center gap-2 border-t border-hairline/60 pt-3">
        {PANE_TABS.map((tab) => {
          const isActive = step === tab.step
          return (
            <button
              key={tab.step}
              type="button"
              onClick={() => onSelectStep && onSelectStep(tab.step)}
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-mono transition-colors border cursor-pointer ${
                isActive
                  ? "bg-[color-mix(in_oklch,var(--amber)_12%,transparent)] border-[color-mix(in_oklch,var(--amber)_30%,var(--hairline))] text-[var(--amber)] font-bold"
                  : "bg-transparent border-transparent text-muted-foreground hover:text-foreground hover:bg-[color-mix(in_oklch,var(--fg)_5%,transparent)]"
              }`}
            >
              <span className="text-[10px] opacity-75 font-sans font-medium">{tab.desc}:</span>
              <span>{tab.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default WizardHeader
