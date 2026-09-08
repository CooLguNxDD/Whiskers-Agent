"""
Command guard — the security spine of the terminal relay.

For an interactive PTY the guard runs on every submitted line (on Enter), not
once at spawn. A line is allowed only when its first token is an allowlisted
binary AND the raw line trips no blocked pattern. Parsing uses ``shlex`` so
quoting cannot smuggle a chain operator past the regex check.

``ALLOWED_BINARIES`` ships EMPTY (OSS posture): a default deployment permits
nothing. Operators opt in per binary via the ``CAT_TERMINAL_ALLOWED_BINARIES``
env var (comma-separated) or by editing the frozenset below in their own build.

The TypeScript ``commandGuard.ts`` in the VS Code extension mirrors this module
verbatim and is the authoritative filter (closest to execution); the relay may
mirror-check for defense-in-depth.

This module never raises — it returns a structured verdict (per the
error-handling-wrapper skill).
"""

import os
import re
import shlex

# Binaries that require step-up elevation. Env-overridable (comma-separated) via
# CAT_PRIVILEGED_BINARIES, defaulting to claude,codex when unset. The plugin's
# config_loader injects the config.json default before this module is imported.
_PRIV_ENV = os.environ.get("CAT_PRIVILEGED_BINARIES", "")
PRIVILEGED_BINARIES: frozenset[str] = frozenset(
    b.strip() for b in _PRIV_ENV.split(",") if b.strip()
) or frozenset({"claude", "codex"})

# Empty by default. Cat Studios' deployment populates via env or a patched build.
# Reference set (NOT enabled by default; mirrors whiskers-vscode/src/config.ts):
#   claude git npm npx node python python3 ls cat echo pwd which cd
#   clear cls dir type mkdir rmdir cp mv grep find code
# Reference rawMode-trigger binaries (mirrors whiskers-vscode/src/config.ts):
#   claude codex agy gemini
_ENV_BINARIES = os.environ.get("CAT_TERMINAL_ALLOWED_BINARIES", "")
ALLOWED_BINARIES: frozenset[str] = frozenset(
    b.strip() for b in _ENV_BINARIES.split(",") if b.strip()
)

# Patterns that are rejected regardless of the binary. Mirror in commandGuard.ts.
BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"[;&|`]"),     # chain / pipe / backtick command substitution
    re.compile(r"\$\("),       # $(...) command substitution
    re.compile(r"\.\./"),      # path traversal
    re.compile(r">\s*/"),      # redirect to an absolute path
    re.compile(r"rm\s+.*-[a-z]*[rf]"),  # rm -rf / -fr / -r / -f variants
]

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")



def classify_privileged(line: str) -> bool:
    """True if the line launches an autonomous/interactive agent needing step-up
    (bare or any-arg ``claude``/``codex``, or ``agy -p``). Mirror of the TS classifier."""
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False
    if not tokens:
        return False
    binary = tokens[0]
    if binary in PRIVILEGED_BINARIES:
        return True
    if binary == "agy" and "-p" in tokens:
        return True
    return False


def guard_line(line: str) -> dict:
    """Classify a single submitted command line.

    Returns ``{"allowed": bool, "reason": str | None, "binary": str | None, "privileged": bool}``.
    """
    if line is None:
        return {"allowed": False, "reason": "empty_line", "binary": None, "privileged": False}

    stripped = line.strip()
    if not stripped:
        # Bare Enter / whitespace — harmless, let it through (no binary).
        return {"allowed": True, "reason": None, "binary": None, "privileged": False}

    if "\n" in line or "\r" in line:
        return {"allowed": False, "reason": "blocked_pattern:newline", "binary": None, "privileged": False}

    # Blocked patterns are checked against the raw line first (shell metachars).
    for pat in BLOCKED_PATTERNS:
        if pat.search(stripped):
            return {
                "allowed": False,
                "reason": f"blocked_pattern:{pat.pattern}",
                "binary": None,
                "privileged": False,
            }

    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        return {"allowed": False, "reason": f"parse_error:{exc}", "binary": None, "privileged": False}

    if not tokens:
        return {"allowed": True, "reason": None, "binary": None, "privileged": False}

    binary = tokens[0]
    is_privileged = classify_privileged(stripped)
    if binary not in ALLOWED_BINARIES:
        return {
            "allowed": False,
            "reason": "binary_not_allowed",
            "binary": binary,
            "privileged": is_privileged,
        }

    return {"allowed": True, "reason": None, "binary": binary, "privileged": is_privileged}


_SANDBOX_ENV = os.environ.get("CAT_SANDBOX_ALLOWED_BINARIES", "")
SANDBOX_ALLOWED_BINARIES: frozenset[str] = frozenset(
    b.strip() for b in _SANDBOX_ENV.split(",") if b.strip()
)


def filter_redirects(seg: list[str]) -> list[str]:
    """Helper to remove redirects and their target tokens from command segment."""
    filtered = []
    i = 0
    n = len(seg)
    while i < n:
        # Filter out stderr redirect patterns like '2>' or '2>>'
        if seg[i] == "2" and i + 1 < n and seg[i+1] in {">", ">>"}:
            i += 3
        # Filter out standard redirects like '<', '>', '>>'
        elif seg[i] in {"<", ">", ">>"}:
            i += 2
        else:
            filtered.append(seg[i])
            i += 1
    return filtered


def guard_pipeline(line: str, allowed: frozenset[str] | None = None) -> dict:
    """Validate a possibly-chained command line for native sandbox execution.

    Chaining (| || && ; & and redirects) is permitted, but every command
    segment's leading binary must be in ``allowed`` (defaults to
    SANDBOX_ALLOWED_BINARIES) and command substitution (backtick / $()) is
    always rejected. Never raises.

    Returns {"allowed": bool, "reason": str|None,
             "binaries": list[str], "privileged": bool}.
    """
    if allowed is None:
        allowed = SANDBOX_ALLOWED_BINARIES

    if line is None:
        return {"allowed": True, "reason": None, "binaries": [], "privileged": False}

    if len(line) > 4096:
        return {"allowed": False, "reason": "command_too_long", "binaries": [], "privileged": False}

    stripped = line.strip()
    if not stripped:
        return {"allowed": True, "reason": None, "binaries": [], "privileged": False}

    if "\n" in line or "\r" in line:
        return {"allowed": False, "reason": "blocked_pattern:newline", "binaries": [], "privileged": False}

    # Substitution patterns are blocked up front
    if "`" in line or "$(" in line:
        return {"allowed": False, "reason": "blocked_pattern:substitution", "binaries": [], "privileged": False}

    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError as exc:
        return {"allowed": False, "reason": f"parse_error:{exc}", "binaries": [], "privileged": False}

    # Split tokens into segments on operators like |, ||, &&, ;, &
    segments = []
    current_segment = []
    for token in tokens:
        if token in {"|", "||", "&&", ";", "&"}:
            if current_segment:
                segments.append(current_segment)
                current_segment = []
        else:
            current_segment.append(token)
    if current_segment:
        segments.append(current_segment)

    binaries = []
    privileged = False
    for seg in segments:
        filtered = filter_redirects(seg)
        if not filtered:
            return {"allowed": False, "reason": "redirect_without_binary", "binaries": binaries, "privileged": privileged}
        idx = 0
        while idx < len(filtered) and _ENV_ASSIGN_RE.match(filtered[idx]):
            idx += 1
        if idx >= len(filtered):
            return {"allowed": False, "reason": "redirect_without_binary", "binaries": binaries, "privileged": privileged}
        binary = filtered[idx]
        binaries.append(binary)
        # Check if the segment invokes a privileged binary
        if binary in PRIVILEGED_BINARIES:
            privileged = True
        if binary == "agy" and "-p" in seg:
            privileged = True

    # Validate that all identified binaries are allowed
    for binary in binaries:
        if binary not in allowed:
            return {"allowed": False, "reason": "binary_not_allowed", "binaries": binaries, "privileged": privileged}

    return {"allowed": True, "reason": None, "binaries": binaries, "privileged": privileged}

