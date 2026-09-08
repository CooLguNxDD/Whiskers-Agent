/**
 * SavePresetForm — inline form (name input + save button) at the bottom of the
 * Summary step, letting the user persist the current scope selection as a preset.
 */

import { useState } from "react"

interface SavePresetFormProps {
  onSaveAsPreset: (name: string) => void
  isSavingPreset: boolean
}

/** SavePresetForm component for persisting the current scope selection as a custom preset. */
export function SavePresetForm({ onSaveAsPreset, isSavingPreset }: SavePresetFormProps) {
  const [customProfileName, setCustomProfileName] = useState("")

  return (
    <div className="pt-4 border-t border-border space-y-2">
      <label className="block text-[11px] font-semibold text-muted-foreground">
        Save current configuration as a custom preset/profile
      </label>
      <div className="flex gap-2 max-w-md">
        <input
          type="text"
          placeholder="Preset/Profile name..."
          value={customProfileName}
          onChange={(e) => setCustomProfileName(e.target.value)}
          className="flex-1 bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--amber)]"
          disabled={isSavingPreset}
        />
        <button
          type="button"
          onClick={() => {
            if (customProfileName.trim() && !isSavingPreset) {
              onSaveAsPreset(customProfileName.trim())
              setCustomProfileName("")
            }
          }}
          disabled={!customProfileName.trim() || isSavingPreset}
          className="px-3 py-1.5 bg-[color-mix(in_oklch,var(--amber)_15%,transparent)] border border-[color-mix(in_oklch,var(--amber)_30%,var(--hairline))] text-[var(--amber)] hover:bg-[color-mix(in_oklch,var(--amber)_25%,transparent)] rounded-lg text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition cursor-pointer"
        >
          {isSavingPreset ? "Saving..." : "Save Preset"}
        </button>
      </div>
    </div>
  )
}

export default SavePresetForm
