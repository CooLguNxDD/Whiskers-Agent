/** Platform modifier glyph for painted + bound accelerators (⌘ vs Ctrl+). */
export function kbdModifier(): string {
  if (typeof navigator === "undefined") return "Ctrl+"
  const ua = `${navigator.platform} ${navigator.userAgent}`
  return /Mac|iPhone|iPod|iPad/i.test(ua) ? "⌘" : "Ctrl+"
}
