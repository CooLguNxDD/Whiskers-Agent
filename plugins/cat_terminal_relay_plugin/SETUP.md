# Cat Terminal Relay — Setup Guide

Two capabilities live in this plugin:

1. **Relay** — reverse-WebSocket bridge to an interactive PTY hosted in an IDE
   extension. The MCP server is a dumb byte pipe; the shell runs on the user's host.
2. **Sandbox** — the native headless `exec_command` MCP tool. Runs a shell command
   **inside the MCP server process/container** (stateless, single-shot), like Claude
   Code's Bash tool. No PTY, no IDE host.

Both share one security spine: command allowlist + step-up elevation (TOTP/password) +
the gateway permission gate.

> Tier: `pro`. Already listed in `config/plugin_config.json` — no registration step needed.

---

## 1. Environment variables

| Variable | Applies to | Default | Purpose |
| --- | --- | --- | --- |
| `CAT_SANDBOX_ALLOWED_BINARIES` | Sandbox | *(empty = deny-all)* | Comma-separated leading binaries `exec_command` may run, e.g. `ls,echo,git,python`. |
| `CAT_SANDBOX_WORKDIR` | Sandbox | `<repo-root>/sandbox` | Root dir commands run in (created if missing). Caller `workdir` must stay under it. |
| `CAT_TERMINAL_ALLOWED_BINARIES` | Relay | *(empty = deny-all)* | Allowlist mirrored by `guard_line` for the PTY relay. The VS Code extension's `commandGuard.ts` is the authoritative filter. |
| `MCP_SERVER_URL` | Relay | `http://localhost:10000` | Base URL used to build the console WS URL. |
| `CAT_TERMINAL_DEV_AUTH` | Both | unset | `=1` bypasses bearer/cookie auth for local dev. |
| `CAT_TERMINAL_DEV_SUBJECT` | Both | unset | Subject used when dev-auth is on (grants `terminal:use`,`terminal:host`). |

**Allowlists ship empty (deny-all).** Nothing runs until you opt binaries in.
The two allowlists are independent — native exec is more dangerous (runs on the
server), so scope it tighter than the relay.

```bash
# docker-compose.yml -> whiskers-agent environment:
CAT_SANDBOX_ALLOWED_BINARIES=ls,echo,cat,git,python,grep,find
CAT_SANDBOX_WORKDIR=/app/sandbox
```

---

## 2. Tunables (`manifest.json` `settings`)

| Key | Default | Meaning |
| --- | --- | --- |
| `sandbox_timeout_s` | `60` | Per-command wall-clock limit; process is killed on timeout. |
| `sandbox_max_output_bytes` | `65536` | stdout/stderr cap each; `truncated:true` flagged when exceeded. |
| `sandbox_workdir` | `"sandbox"` | Informational default name (env `CAT_SANDBOX_WORKDIR` wins). |
| `elevation_ttl_seconds` | `300` | Life of a minted elevation token. |
| `max_attempts` / `lockout_seconds` | `3` / `300` | Step-up brute-force guard. |
| `step_up_method` | `"totp"` | Elevation factor. |

---

## Config defaults (`config.json`)

The configuration file lives at `plugins/cat_terminal_relay_plugin/config.json` and has three sections: `common`, `sandbox`, and `terminal`. Values from this file are injected at plugin load time with set-if-absent semantics (host `.env` always wins, and empty values are skipped). Specifically, `common.CAT_PRIVILEGED_BINARIES` defines which binaries require elevation (defaulting to `"claude,codex"`). Any changes to `config.json` require a server restart. The configuration keys map directly to the environment variables documented in [Section 1](#1-environment-variables).

```json
{
  "common": {
    "CAT_PRIVILEGED_BINARIES": "claude,codex"
  },
  "sandbox": {
    "CAT_SANDBOX_ALLOWED_BINARIES": "ls,cat,git"
  },
  "terminal": {
    "CAT_TERMINAL_ALLOWED_BINARIES": "claude,git"
  }
}
```

---

## 3. Auth scopes

- `terminal:use` — required for every MCP tool here, incl. `exec_command`.
- `terminal:host` — host leg + `host_token` minting.

Scopes come from the validated bearer (`payload.scopes`). The same `sub` is read from
the bearer (MCP tools) and the session cookie (REST control plane), so elevation keyed
`sandbox:{subject}` lines up across both.

---

## 4. Provision step-up (one-time)

Privileged binaries (`claude`, `codex`, `agy -p`) require an elevation token before
`exec_command` will run them. Provision a factor first (admin session cookie required):

**TOTP**
```bash
curl -b session=<cookie> -X POST $BASE/api/terminal/totp/provision   # -> {otpauth_uri}
# scan otpauth_uri in an authenticator app, then:
curl -b session=<cookie> -X POST $BASE/api/terminal/totp/verify -d '{"code":"123456"}'
```

**Password (alternative)**
```bash
curl -b session=<cookie> -X POST $BASE/api/terminal/password/provision -d '{"password":"..."}'
```

Status check: `GET $BASE/api/terminal/totp/status`. Vault keys `TOTP_SEED` /
`PASSWORD_HASH` are shared with the relay's elevation.

---

## 5. Running commands

### Non-privileged (allowlisted binary, no elevation)
Call via the gateway — `exec_command` is hidden from direct `list_tools` but reachable
through `run_graph` / `discover_tools`. As a write op it hits the permission gate
(confirmation by default).

```
run_graph: "run `git status` in the sandbox"
-> {"status":"ok","exit_code":0,"stdout":"...","stderr":"","binaries":["git"],
    "workdir":"/app/sandbox","truncated":false}
```

`exec_command(command, workdir=None, timeout_s=None)`:
- Chaining allowed: `git fetch && git log | head`. Command substitution
  (`` ` `` / `$()`) is rejected.
- Every chained segment's leading binary must be in `CAT_SANDBOX_ALLOWED_BINARIES`.
- `workdir` (optional) is resolved under `CAT_SANDBOX_WORKDIR`; escapes → `invalid_workdir`.

### Privileged (needs elevation)
```
exec_command("claude --version")
-> {"status":"error","error":"elevation_required",
    "elevate_url":"/api/terminal/sandbox/elevate", ...}

curl -b session=<cookie> -X POST $BASE/api/terminal/sandbox/elevate -d '{"totp":"123456"}'
-> {"status":"ok","expires_at":...,"ttl":300}

# retry within TTL:
exec_command("claude --version")   -> {"status":"ok", ...}
```

Error codes: `200` ok · `401` bad factor / unauthenticated · `403` locked ·
`409` not provisioned.

---

## 6. Relay quickstart (interactive PTY)

1. `request_terminal_token()` → short-lived `terminal:use` token.
2. `list_ide_hosts()` → online IDE extension hosts for the subject.
3. `open_terminal_session(ide_id, terminal_token)` → `session_id` + console WS URL.
4. Connect the browser console to the WS URL; `kill_terminal_session(session_id)` to close.

Host JWT for the VS Code extension: `GET /api/terminal/host-token`.

---

## 7. Deploy checklist

- [ ] Set `CAT_SANDBOX_ALLOWED_BINARIES` (+ optional `CAT_SANDBOX_WORKDIR`) in the env.
- [ ] Rebuild the `whiskers-agent-server` image so `MCPTools/sandbox_tools.py` ships.
- [ ] Restart. No DB migration required.
- [ ] Provision TOTP/password if privileged binaries will be used.
- [ ] Smoke test (dev): `CAT_TERMINAL_DEV_AUTH=1 CAT_TERMINAL_DEV_SUBJECT=dev`,
      `CAT_SANDBOX_ALLOWED_BINARIES=echo`, then `run_graph` an `echo hi`.
- [ ] Tests: `python scripts/run_tests.py test/unit/test_sandbox_exec.py`.

---

## 8. Security notes

- Deny-all default: an unset allowlist runs nothing.
- Redirects are permitted (`> file`); a `> /abs/path` can write outside the sandbox
  cwd. Keep the allowlist tight if that matters.
- `exec_command` is single-shot — no shell state persists between calls.
- Every tool returns a structured dict and never raises (error-handling-wrapper
  convention).
