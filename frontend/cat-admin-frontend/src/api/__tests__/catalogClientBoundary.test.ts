/**
 * Enforcement test: no module under src/api/ except catalogClient.ts and
 * catalogRuntime.ts itself may import `callCatalogOp`.
 *
 * `callCatalogOp` is the raw, untyped transport (args: Record<string, unknown>).
 * Every hand-written api/*.ts module must go through the typed `catalogClient`
 * namespaces instead — see catalogClient.ts's module docstring. This is a
 * regression guard for the repo-polish phase 4 refactor: it's easy for a new
 * api/*.ts function to reach for `callCatalogOp` directly out of habit.
 *
 * Recurses into subdirectories (not just the top-level of src/api/) and
 * matches any import naming `callCatalogOp` from a `catalogRuntime` module —
 * covers `import * as`, multi-line braces, and relative paths that aren't
 * literally `./catalogRuntime` — rather than one exact import-statement shape.
 */
import { describe, it, expect } from "vitest"
import { readdirSync, readFileSync, statSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const API_DIR = join(dirname(fileURLToPath(import.meta.url)), "..")
const ALLOWED_FILES = new Set(["catalogClient.ts", "catalogRuntime.ts"])

function listApiModuleFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      out.push(...listApiModuleFiles(full))
      continue
    }
    if (name.endsWith(".ts") && !name.endsWith(".test.ts")) {
      out.push(full)
    }
  }
  return out
}

describe("catalogClient boundary", () => {
  it("no api/*.ts module besides catalogClient.ts/catalogRuntime.ts imports callCatalogOp", () => {
    const offenders: string[] = []
    for (const filePath of listApiModuleFiles(API_DIR)) {
      const relName = filePath.slice(API_DIR.length + 1).replace(/\\/g, "/")
      if (ALLOWED_FILES.has(relName)) continue
      const source = readFileSync(filePath, "utf-8")
      // Match any import (named or namespace, any brace formatting, any
      // relative path) that names callCatalogOp from a catalogRuntime module —
      // not a comment/docstring mention.
      if (
        /import\s+(?:\{[\s\S]*?\bcallCatalogOp\b[\s\S]*?\}|\*\s*as\s+\w+)\s*from\s*["'][^"']*catalogRuntime["']/.test(
          source,
        )
      ) {
        offenders.push(relName)
      }
    }
    expect(offenders).toEqual([])
  })
})
