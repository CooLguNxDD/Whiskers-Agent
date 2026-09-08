/**
 * useClipboard — copy text to the clipboard with a Clipboard-API-first,
 * execCommand-fallback strategy, and a self-resetting "copied" flag.
 */

import { useEffect, useRef, useState } from "react"

const RESET_MS = 2000

/** Simple copy state: `copied` flips true for RESET_MS after a successful copy(). */
export function useClipboard(textareaRef?: React.RefObject<HTMLTextAreaElement | null>, onError?: (err: unknown) => void) {
  const [copied, setCopied] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isMounted = useRef(true)

  useEffect(() => {
    isMounted.current = true
    return () => {
      isMounted.current = false
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const copy = async (text: string) => {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text)
        if (!isMounted.current) return
        setCopied(true)
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopied(false), RESET_MS)
        return
      } catch (err) {
        console.error("Clipboard API copy failed, trying fallback select copy", err)
      }
    }

    if (textareaRef?.current) {
      textareaRef.current.select()
      try {
        document.execCommand("copy")
        setCopied(true)
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopied(false), RESET_MS)
      } catch (err) {
        console.error("Fallback copy execution failed", err)
        onError?.(err)
      }
    }
  }

  return { copied, copy }
}

/** Keyed variant: tracks which id was last copied (for per-row copy buttons). */
export function useKeyedClipboard() {
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isMounted = useRef(true)

  useEffect(() => {
    isMounted.current = true
    return () => {
      isMounted.current = false
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const copy = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text)
      if (!isMounted.current) return
      setCopiedId(id)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => setCopiedId(null), 1500)
    } catch (err) {
      console.error("Failed to copy", err)
    }
  }

  return { copiedId, copy }
}
