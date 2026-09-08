# Whiskers Agent Terminal Host (VS Code extension) — STUB

Phase 1 scaffold for the host side of the **Whiskers Agent Terminal Relay**. The PTY
lives **here**, on your machine, inside this extension. The extension dials
**outbound** to Whiskers Agent and holds the WebSocket open, so Docker never needs to
reach the host. Once registered, the relay pumps terminal I/O between your
browser Operator Console and this PTY.

> **This is a stub.** The files compile and lay out the architecture with
> `// TODO Phase 1 wiring` markers. It is **not** wired end-to-end yet and is not
> published. The relay (Python) side in `plugins/cat_terminal_relay_plugin/` is
> the verified deliverable this pass.

## Security

`src/commandGuard.ts` is the **authoritative** filter (closest to execution) and
is a verbatim parity port of `plugins/cat_terminal_relay_plugin/command_guard.py`.
`ALLOWED_BINARIES` is **empty by default** — configure `whiskersAgent.allowedBinaries`
to opt in per binary. Run the host behind a constrained user account / sandbox.

## Layout

| File | Role |
|---|---|
| `src/extension.ts` | `activate()`: connect outbound, bridge relay ↔ PTY |
| `src/relayClient.ts` | outbound WS to `/terminal/host/{ideId}` + reconnect |
| `src/ptyHost.ts` | `node-pty` spawn + guarded stdin / stdout pump |
| `src/commandGuard.ts` | allowlist + blocked-pattern guard (Python parity) |
| `src/auth.ts` | terminal:host JWT (paste flow for the stub; PKCE later) |
| `src/config.ts` | `whiskersAgent.*` settings |

## Build

```bash
npm install        # pulls node-pty, ws, @types/vscode (not run in this repo pass)
npm run typecheck  # tsc --noEmit
npm run compile    # tsc → out/
```

Before `npm install`, `src/ambient.d.ts` stubs `vscode` / `ws` / `node-pty` so
the scaffold type-checks standalone.
