import type { KeyboardEvent } from "react"
import type { UnifiedStateOrMixed, UnifiedState } from "@/lib/toolState"

interface StateToggleProps {
  value: UnifiedStateOrMixed
  onChange: (state: UnifiedState) => void
  disabled?: boolean
  indeterminate?: boolean
  isGroup?: boolean
}

/**
 * Tri-state radiogroup toggle for tool exposure (Live/Hidden/Off).
 */
export function StateToggle({
  value,
  onChange,
  disabled = false,
  indeterminate = false,
  isGroup = false,
}: StateToggleProps) {
  const states: { key: UnifiedStateOrMixed; label: string }[] = [
    { key: "enabled", label: "Live" },
    { key: "hidden", label: "Hidden" },
    { key: "disabled", label: "Off" },
  ]

  if (isGroup) {
    states.push({ key: "mixed", label: "Custom" })
  }

  const isMixed = value === "mixed" || indeterminate

  const handleKeyDown = (
    e: KeyboardEvent<HTMLButtonElement>,
    index: number
  ) => {
    if (disabled) return
    let targetIdx: number
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      targetIdx = (index + 1) % states.length
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      targetIdx = (index - 1 + states.length) % states.length
    } else {
      return
    }
    e.preventDefault()
    const targetKey = states[targetIdx].key
    if (targetKey !== "mixed") {
      onChange(targetKey as UnifiedState)
    }
    
    // Focus the new button
    const container = e.currentTarget.parentElement
    if (container) {
      const buttons = container.querySelectorAll("button")
      buttons[targetIdx]?.focus()
    }
  }

  return (
    <div
      className="ct-tristate"
      role="radiogroup"
      aria-label="Tool exposure state"
      style={disabled ? { opacity: 0.5, pointerEvents: "none" } : undefined}
    >
      {states.map((s, idx) => {
        const isActive = isGroup ? value === s.key : !isMixed && value === s.key
        return (
          <button
            key={s.key}
            type="button"
            role="radio"
            aria-checked={isActive}
            tabIndex={isActive ? 0 : isMixed && idx === 0 ? 0 : -1}
            disabled={disabled}
            onClick={() => {
              if (s.key !== "mixed") {
                onChange(s.key as UnifiedState)
              }
            }}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className={`ct-tristate-segment${isActive ? " is-active" : ""} state-${s.key}`}
          >
            {s.label}
          </button>
        )
      })}
    </div>
  )
}
