/**
 * Command guard — authoritative filter (closest to execution).
 *
 * Verbatim parity with plugins/cat_terminal_relay_plugin/command_guard.py.
 * Invoked PER SUBMITTED LINE (on Enter) before any PTY write. ALLOWED_BINARIES
 * ships EMPTY (OSS posture); operators opt in via the whiskersAgent.allowedBinaries
 * setting. Returns a verdict, never throws.
 */

export interface GuardVerdict {
  allowed: boolean;
  reason: string | null;
  binary: string | null;
  privileged: boolean;
}

/** Set of binaries that require step-up authorization. */
export const PRIVILEGED_BINARIES: Set<string> = new Set(["claude", "codex"]);

/**
 * Classifies if a command line represents a privileged execution.
 */
export function classifyPrivileged(line: string | null | undefined): boolean {
  if (!line) return false;
  const trimmed = line.trim();
  if (!trimmed) return false;
  const tokens = trimmed.split(/\s+/);
  const binary = firstToken(trimmed) ?? tokens[0];
  if (!binary) return false;
  const baseBinary = binary.replace(/^.*[/\\]/, "");
  if (PRIVILEGED_BINARIES.has(baseBinary)) return true;
  if (baseBinary === "agy" && tokens.includes("-p")) return true;
  return false;
}

/**
 * Checks if a command line represents a binary that triggers rawMode on launch.
 */
export function isRawModeBinary(line: string | null | undefined): boolean {
  if (!line) return false;
  const trimmed = line.trim();
  if (!trimmed) return false;
  const tokens = trimmed.split(/\s+/);
  const binary = firstToken(trimmed) ?? tokens[0];
  if (!binary) return false;
  const baseBinary = binary.replace(/^.*[/\\]/, "");
  return RAW_MODE_BINARIES.has(baseBinary);
}

// Empty by default. Populated from extension config at runtime.
export let ALLOWED_BINARIES: Set<string> = new Set<string>();

/**
 * Set the allowlist of executable binaries permitted by the command guard.
 */
export function setAllowedBinaries(binaries: string[]): void {
  ALLOWED_BINARIES = new Set(binaries.filter((b) => b && b.trim()).map((b) => b.trim()));
}

// Empty by default. Populated from extension config at runtime.
export let RAW_MODE_BINARIES: Set<string> = new Set<string>();

/**
 * Set the list of binaries that require raw interactive terminal mode.
 */
export function setRawModeBinaries(binaries: string[]): void {
  RAW_MODE_BINARIES = new Set(binaries.filter((b) => b && b.trim()).map((b) => b.trim()));
}

// Mirror of BLOCKED_PATTERNS in command_guard.py.
const BLOCKED_PATTERNS: RegExp[] = [
  /[;&|`]/, // chain / pipe / backtick command substitution
  /\$\(/, // $(...) command substitution
  /\.\.\//, // path traversal
  />\s*\//, // redirect to an absolute path
  /rm\s+.*-[a-z]*[rf]/, // rm -rf / -fr / -r / -f variants
];

/** Minimal shell tokenizer (first token only is needed for the allowlist). */
function firstToken(line: string): string | null {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }
  // Respect a leading quoted segment; otherwise split on whitespace.
  const m = trimmed.match(/^("([^"]*)"|'([^']*)'|(\S+))/);
  if (!m) {
    return null;
  }
  return m[2] ?? m[3] ?? m[4] ?? null;
}

/**
 * Evaluate a command line against security patterns and binary allowlists.
 */
export function guardLine(line: string | null | undefined): GuardVerdict {
  if (line === null || line === undefined) {
    return { allowed: false, reason: "empty_line", binary: null, privileged: false };
  }
  const stripped = line.trim();
  if (!stripped) {
    return { allowed: true, reason: null, binary: null, privileged: false };
  }
  for (const pat of BLOCKED_PATTERNS) {
    if (pat.test(stripped)) {
      return { allowed: false, reason: `blocked_pattern:${pat.source}`, binary: null, privileged: false };
    }
  }
  const binary = firstToken(stripped);
  if (!binary) {
    return { allowed: true, reason: null, binary: null, privileged: false };
  }
  if (!ALLOWED_BINARIES.has(binary)) {
    return { allowed: false, reason: "binary_not_allowed", binary, privileged: classifyPrivileged(stripped) };
  }
  return { allowed: true, reason: null, binary, privileged: classifyPrivileged(stripped) };
}
