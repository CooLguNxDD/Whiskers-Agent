/**
 * ScopeConfigurator — 3-pane configurator mirroring the 3-level scope model:
 * Pane 1: Level 1 Core Platform permissions
 * Pane 2: Level 2 Plugin scopes (full plugin access & fine-grained tokens)
 * Pane 3: Level 3 Review (resolved tokens, implied closures, preset saving)
 */

import { useState } from "react"
import type { ApiKey, ScopePreset, PluginScopeContribution, CoreScopeContribution } from "@/api/apiKeys"
import type { ScopeEditor } from "@/hooks/useScopeEditor"

import WizardHeader from "./WizardHeader"
import CoreScopesPane from "./panes/CoreScopesPane"
import PluginAccessStep from "./steps/PluginAccessStep"
import SummaryStep from "./steps/SummaryStep"

interface ScopeConfiguratorProps {
  editingKey: ApiKey
  plugins: Array<{ id: string; name?: string }>
  expandedPlugins: Record<string, boolean>
  onBack: () => void
  onSave: () => void
  isSaving: boolean
  formError?: string | null

  // Local scope-toggle state + actions (from useScopeEditor)
  scopeEditor: ScopeEditor

  onToggleExpand: (pluginId: string) => void

  // Presets
  presets: ScopePreset[]
  onSaveAsPreset: (name: string) => void
  isSavingPreset?: boolean

  globalScopesList: string[]
  pluginScopesList?: PluginScopeContribution[]
  coreScopesList?: CoreScopeContribution[]
}

/** ScopeConfigurator 3-pane component for configuring API key scopes across the 3-level model. */
export function ScopeConfigurator({
  editingKey,
  plugins,
  expandedPlugins,
  onBack,
  onSave,
  isSaving,
  formError,
  scopeEditor,
  onToggleExpand,
  presets,
  onSaveAsPreset,
  isSavingPreset = false,
  globalScopesList,
  pluginScopesList = [],
  coreScopesList = [],
}: ScopeConfiguratorProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1)

  const {
    selectedScopes,
    applyPreset,
    toggleScope,
    togglePluginWide,
    getPluginGroups,
    isPluginWideSelected,
    isScopeSelected,
    applyCustomScopes,
    getAllAvailableScopes,
    impliedFor,
  } = scopeEditor

  return (
    <div className="flex flex-col w-full h-full min-h-screen pb-12 ct-scope-configurator">
      <WizardHeader
        editingKey={editingKey}
        step={step}
        onSelectStep={(s) => setStep(s)}
        onStepBack={() => setStep((s) => (s - 1) as 1 | 2 | 3)}
        onStepNext={() => setStep((s) => (s + 1) as 1 | 2 | 3)}
        onBack={onBack}
        onSave={onSave}
        isSaving={isSaving}
      />

      {/* Main Content Area */}
      <div className="p-6 max-w-4xl w-full mx-auto">
        {/* Error display */}
        {formError && (
          <div className="mb-6 p-3 bg-[var(--danger-soft)] border border-[color-mix(in_oklch,var(--danger)_28%,var(--hairline))] text-[var(--danger)] rounded text-xs font-medium">
            {formError}
          </div>
        )}

        {/* Pane 1: Core Platform */}
        {step === 1 && (
          <CoreScopesPane
            coreScopesList={coreScopesList}
            selectedScopes={selectedScopes}
            onToggleScope={toggleScope}
            presets={presets}
            onApplyPreset={applyPreset}
            onApplyCustom={applyCustomScopes}
          />
        )}

        {/* Pane 2: Plugins */}
        {step === 2 && (
          <PluginAccessStep
            plugins={plugins}
            selectedScopes={selectedScopes}
            expandedPlugins={expandedPlugins}
            getPluginGroups={getPluginGroups}
            isPluginWideSelected={isPluginWideSelected}
            onToggleExpand={onToggleExpand}
            onTogglePluginWide={togglePluginWide}
            onToggleScope={toggleScope}
            pluginScopesList={pluginScopesList}
          />
        )}

        {/* Pane 3: Review */}
        {step === 3 && (
          <SummaryStep
            selectedScopes={selectedScopes}
            globalScopesList={globalScopesList}
            pluginScopesList={pluginScopesList}
            coreScopesList={coreScopesList}
            plugins={plugins}
            isScopeSelected={isScopeSelected}
            isPluginWideSelected={isPluginWideSelected}
            getPluginGroups={getPluginGroups}
            onSaveAsPreset={onSaveAsPreset}
            isSavingPreset={isSavingPreset}
            impliedFor={impliedFor}
          />
        )}
      </div>
    </div>
  )
}

export default ScopeConfigurator
