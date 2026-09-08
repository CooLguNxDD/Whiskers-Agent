import React from "react"

/**
 * CSS bar sparkline for table cells — Recharts-per-row is too heavy.
 */
export const TinySpark = React.memo(function TinySpark({
  values,
  color = "var(--amber)",
}: {
  values: number[]
  color?: string
}) {
  if (!values || values.length === 0) return null
  const max = Math.max(...values, 1)
  return (
    <span className="ct-spark">
      {values.map((v, i) => (
        <span
          key={i}
          style={{
            height: `${(v / max) * 18}px`,
            background: color,
            opacity: 0.6 + (v / max) * 0.4,
          }}
        />
      ))}
    </span>
  )
})
