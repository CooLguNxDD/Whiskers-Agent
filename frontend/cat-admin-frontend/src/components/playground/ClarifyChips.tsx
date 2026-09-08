/**
 * ClarifyChips Component
 *
 * Renders structured clarify questions as interactive option chips or free-text inputs,
 * allowing the user to select answers or fill in parameters to refine their query.
 */

import { useState } from "react"
import type { ClarifyQuestion } from "@/api/playground"

interface ClarifyChipsProps {
  questions: ClarifyQuestion[]
  disabled?: boolean
  onSubmit: (answers: string) => void
}

/** Renders a list of clarifying questions with option chips and free-text inputs. */
export function ClarifyChips({ questions, disabled, onSubmit }: ClarifyChipsProps) {
  // Store selected option values per question header: Record<question_header, string[]>
  const [selected, setSelected] = useState<Record<string, string[]>>({})
  // Store free-text inputs per question header: Record<question_header, string>
  const [textInputs, setTextInputs] = useState<Record<string, string>>({})

  const handleOptionToggle = (header: string, value: string, multiSelect: boolean) => {
    setSelected((prev) => {
      const current = prev[header] || []
      if (multiSelect) {
        if (current.includes(value)) {
          return { ...prev, [header]: current.filter((v) => v !== value) }
        } else {
          return { ...prev, [header]: [...current, value] }
        }
      } else {
        // Single select toggle: if clicked again, deselect; otherwise select only this one
        if (current.includes(value)) {
          return { ...prev, [header]: [] }
        } else {
          return { ...prev, [header]: [value] }
        }
      }
    })
  }

  const handleTextChange = (header: string, val: string) => {
    setTextInputs((prev) => ({ ...prev, [header]: val }))
  }

  const handleSubmit = () => {
    const answers = questions
      .map((q) => {
        const hasOptions = q.options && q.options.length > 0
        if (hasOptions) {
          const vals = selected[q.header] || []
          if (vals.length === 0) return null
          const valsStr = vals.join(", ")
          return q.value ? `${q.value}: ${valsStr}` : valsStr
        } else {
          const textVal = textInputs[q.header] || ""
          if (!textVal.trim()) return null
          return q.value ? `${q.value}: ${textVal.trim()}` : textVal.trim()
        }
      })
      .filter(Boolean) as string[]

    if (answers.length > 0) {
      onSubmit(answers.join("\n"))
    }
  }

  // Check if at least one question has been answered to enable the submit button
  const hasAnswers = questions.some((q) => {
    const hasOptions = q.options && q.options.length > 0
    if (hasOptions) {
      return (selected[q.header] || []).length > 0
    } else {
      return !!(textInputs[q.header] || "").trim()
    }
  })

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: 12,
        background: "var(--bg-sunken)",
        borderRadius: 8,
        border: "1px solid var(--hairline)",
        marginTop: 8,
        maxWidth: 500,
      }}
    >
      {questions.map((q, qIdx) => {
        const hasOptions = q.options && q.options.length > 0
        const selectedList = selected[q.header] || []

        return (
          <div key={qIdx} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                className="ct-eyebrow"
                style={{
                  background: "var(--bg-surface)",
                  padding: "2px 6px",
                  borderRadius: 4,
                  fontSize: 10,
                  color: "var(--fg-subtle)",
                  border: "1px solid var(--hairline)",
                }}
              >
                {q.header}
              </span>
              <span style={{ fontSize: 12, fontWeight: 500, color: "var(--fg)" }}>
                {q.question}
              </span>
            </div>

            {hasOptions ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 2 }}>
                {q.options.map((opt, oIdx) => {
                  const isActive = selectedList.includes(opt.value)
                  return (
                    <button
                      key={oIdx}
                      type="button"
                      disabled={disabled}
                      onClick={() => handleOptionToggle(q.header, opt.value, q.multiSelect)}
                      className={isActive ? "ct-btn-primary" : "ct-btn-ghost"}
                      style={{
                        padding: "4px 10px",
                        fontSize: 12,
                        height: "auto",
                        borderRadius: 20,
                        textAlign: "left",
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        border: "1px solid var(--hairline)",
                      }}
                      title={opt.description}
                    >
                      <span style={{ fontWeight: 600 }}>{opt.label}</span>
                      {opt.description && (
                        <span
                          style={{
                            fontSize: 10,
                            opacity: 0.8,
                            fontWeight: "normal",
                            display: "block",
                          }}
                        >
                          {opt.description}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div style={{ marginTop: 2 }}>
                <input
                  type="text"
                  disabled={disabled}
                  placeholder={`Enter ${q.header}...`}
                  value={textInputs[q.header] || ""}
                  onChange={(e) => handleTextChange(q.header, e.target.value)}
                  style={{
                    width: "100%",
                    padding: "6px 10px",
                    fontSize: 12,
                    borderRadius: 4,
                    border: "1px solid var(--hairline)",
                    background: "var(--bg-surface)",
                    color: "var(--fg)",
                    outline: "none",
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && hasAnswers) {
                      e.preventDefault()
                      handleSubmit()
                    }
                  }}
                />
              </div>
            )}
          </div>
        )
      })}

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
        <button
          type="button"
          disabled={disabled || !hasAnswers}
          onClick={handleSubmit}
          className="ct-btn-primary"
          style={{ padding: "6px 14px", height: "auto", fontSize: 12 }}
        >
          Submit Answers
        </button>
      </div>
    </div>
  )
}
