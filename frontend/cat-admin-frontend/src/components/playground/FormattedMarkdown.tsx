import React from "react"

/**
 * Parse inline markdown tokens (`code`, **bold**, *italic*) into React nodes.
 */
export function parseInline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    const token = match[0]
    if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(
        <code
          key={match.index}
          style={{
            background: "var(--bg-sunken)",
            padding: "1px 5px",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            fontSize: "0.9em",
            border: "1px solid var(--hairline)",
          }}
        >
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(
        <strong key={match.index} style={{ fontWeight: 600, color: "var(--fg)" }}>
          {token.slice(2, -2)}
        </strong>
      )
    } else if (token.startsWith("*") && token.endsWith("*")) {
      parts.push(
        <em key={match.index} style={{ fontStyle: "italic" }}>
          {token.slice(1, -1)}
        </em>
      )
    }
    lastIndex = regex.lastIndex
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts
}

interface FormattedMarkdownProps {
  content: string
  className?: string
  style?: React.CSSProperties
}

/**
 * FormattedMarkdown — Renders structured markdown text (headings, lists, bold, italics, code)
 * with proper line breaks and vertical margins.
 */
export function FormattedMarkdown({ content, className, style }: FormattedMarkdownProps) {
  if (!content || !content.trim()) return null

  // Normalize newlines, inline section headers, and list start transitions
  const normalized = content
    .replace(/\r\n/g, "\n")
    .replace(/([^#\n])(#{1,6}\s+)/g, "$1\n\n$2")
    .replace(/([^\n])\n([-*]|\d+\.)\s+/g, "$1\n\n$2 ")

  const rawBlocks = normalized.split(/\n\s*\n/)
  const elements: React.ReactNode[] = []

  rawBlocks.forEach((block, blockIdx) => {
    const trimmed = block.trim()
    if (!trimmed) return

    // Heading block check (e.g. ## Heading or ## Heading Body Text)
    const headingMatch = trimmed.match(/^(#{1,6})\s+(.*)$/s)
    if (headingMatch) {
      const level = headingMatch[1].length
      const rest = headingMatch[2].trim()

      // Check if heading rest contains a newline or body text attached
      const firstNL = rest.indexOf("\n")
      let title = rest
      let bodyText = ""

      if (firstNL !== -1) {
        title = rest.slice(0, firstNL).trim()
        bodyText = rest.slice(firstNL + 1).trim()
      } else {
        // If title is followed by period or long sentence, split first line/sentence if header title is short
        const matchInlineBody = rest.match(/^([^.:!\n]{2,40}[:.!\n])\s+(.*)$/)
        if (matchInlineBody && !rest.startsWith("- ") && !rest.startsWith("* ")) {
          title = matchInlineBody[1].replace(/[:.!\n]$/, "").trim()
          bodyText = matchInlineBody[2].trim()
        }
      }

      const fontSize = level === 1 ? 16 : level === 2 ? 14.5 : 13.5
      elements.push(
        <div
          key={`h-${blockIdx}`}
          role="heading"
          aria-level={level}
          style={{
            fontSize,
            fontWeight: 600,
            color: "var(--amber)",
            marginTop: blockIdx === 0 ? 0 : 12,
            marginBottom: 6,
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.02em",
          }}
        >
          {parseInline(title)}
        </div>
      )

      if (bodyText) {
        elements.push(
          <div key={`hb-${blockIdx}`} style={{ marginBottom: 8, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
            {parseInline(bodyText)}
          </div>
        )
      }
      return
    }

    // List block check (lines starting with - or * or numbers)
    const lines = trimmed.split("\n")
    const isListBlock = lines.every(
      (line) => line.trim().startsWith("- ") || line.trim().startsWith("* ") || /^\d+\.\s+/.test(line.trim())
    )

    if (isListBlock) {
      elements.push(
        <ul
          key={`ul-${blockIdx}`}
          style={{
            margin: "6px 0 10px 0",
            paddingLeft: 20,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {lines.map((line, lineIdx) => {
            const itemText = line.trim().replace(/^([-*]|\d+\.)\s+/, "")
            return (
              <li key={lineIdx} style={{ lineHeight: 1.55, listStyleType: "disc" }}>
                {parseInline(itemText)}
              </li>
            )
          })}
        </ul>
      )
      return
    }

    // Code block check (```code```)
    if (trimmed.startsWith("```") && trimmed.endsWith("```")) {
      const codeLines = trimmed.slice(3, -3).trim().split("\n")
      const firstLine = codeLines[0].trim()
      const isLang = firstLine && !firstLine.includes(" ")
      const codeContent = isLang ? codeLines.slice(1).join("\n") : codeLines.join("\n")
      elements.push(
        <pre
          key={`code-${blockIdx}`}
          style={{
            background: "var(--bg-sunken)",
            padding: 10,
            borderRadius: 6,
            border: "1px solid var(--hairline)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            overflowX: "auto",
            margin: "8px 0",
            whiteSpace: "pre",
          }}
        >
          {codeContent}
        </pre>
      )
      return
    }

    // Standard paragraph block
    elements.push(
      <div
        key={`p-${blockIdx}`}
        style={{
          marginBottom: blockIdx === rawBlocks.length - 1 ? 0 : 10,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
        }}
      >
        {lines.map((line, lIdx) => (
          <React.Fragment key={lIdx}>
            {lIdx > 0 && <br />}
            {parseInline(line)}
          </React.Fragment>
        ))}
      </div>
    )
  })

  return (
    <div className={className} style={{ width: "100%", ...style }}>
      {elements}
    </div>
  )
}
