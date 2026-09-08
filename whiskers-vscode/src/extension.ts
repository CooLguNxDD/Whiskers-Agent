/**
 * Extension entrypoint. On activate, dial Whiskers Agent's control leg and register
 * this host; react to "open"/"kill" control frames by spawning/closing per-session
 * data legs (each with its own node-pty). One host serves N concurrent sessions.
 */
import * as vscode from "vscode";

import { ensureHostToken, clearStoredToken } from "./auth";
import { setAllowedBinaries, setRawModeBinaries } from "./commandGuard";
import { readConfig } from "./config";
import { RelayClient, type ControlMessage } from "./relayClient";
import { SessionConnection } from "./sessionConnection";

let client: RelayClient | null = null;
const sessions = new Map<string, SessionConnection>();
let ctxRef: { tunnelUrl: string; ideId: string; token: string; stepUpMethod: string } | null = null;

/**
 * Activate the Whiskers Agent extension, registering commands and connecting to relay.
 */
export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  ctx.subscriptions.push(
    vscode.commands.registerCommand("whiskersAgent.connect", () => connect(ctx, false)),
    vscode.commands.registerCommand("whiskersAgent.forceConnect", () => connect(ctx, true)),
    vscode.commands.registerCommand("whiskersAgent.disconnect", () => disconnect()),
  );
  await connect(ctx);
}

async function connect(ctx: vscode.ExtensionContext, forcePrompt = false): Promise<void> {
  const cfg = readConfig(ctx);
  setAllowedBinaries(cfg.allowedBinaries);
  setRawModeBinaries(cfg.rawModeBinaries);
  let token: string;
  try {
    token = await ensureHostToken(ctx, forcePrompt);
  } catch (err) {
    vscode.window.showErrorMessage(`Whiskers Agent connection failed: ${(err as Error).message}`);
    return;
  }
  ctxRef = { tunnelUrl: cfg.tunnelUrl, ideId: cfg.ideId, token, stepUpMethod: cfg.stepUpMethod };

  client = new RelayClient({
    tunnelUrl: cfg.tunnelUrl,
    ideId: cfg.ideId,
    token,
    onOpen: () => vscode.window.setStatusBarMessage("Whiskers Agent: host online", 3000),
    onClose: async (code?: number) => {
      vscode.window.setStatusBarMessage("Whiskers Agent: host offline", 3000);
      if (code === 4401) {
        vscode.window.showErrorMessage(
          "Whiskers Agent: Connection unauthorized (invalid/expired token). Clearing stored token and prompting for a new one. If you set whiskersAgent.token in settings, please update it."
        );
        await clearStoredToken(ctx);
        void connect(ctx, true);
      }
    },
    onControl: handleControl,
  });
  client.connect();
}

function handleControl(msg: ControlMessage): void {
  if (!ctxRef) return;
  if (msg.type === "open") {
    if (sessions.has(msg.session_id)) {
      // Tear down a stale/zombie session (e.g. crashed PTY not yet removed) before recreating.
      sessions.get(msg.session_id)?.close();
      sessions.delete(msg.session_id);
    }
    const conn = new SessionConnection({
      tunnelUrl: ctxRef.tunnelUrl,
      ideId: ctxRef.ideId,
      sessionId: msg.session_id,
      token: ctxRef.token,
      stepUpMethod: ctxRef.stepUpMethod,
      workdir: msg.workdir,
      onClose: (id) => sessions.delete(id),
    });
    sessions.set(msg.session_id, conn);
    conn.start();
  } else if (msg.type === "kill") {
    sessions.get(msg.session_id)?.close();
    sessions.delete(msg.session_id);
  } else if (msg.type === "elevation_granted") {
    sessions.get(msg.session_id)?.grantElevation(msg.expires_at ?? 0);
  }
}

function disconnect(): void {
  for (const conn of sessions.values()) conn.close();
  sessions.clear();
  client?.stop();
  client = null;
  ctxRef = null;
}

/**
 * Deactivate the Whiskers Agent extension, closing all active terminal sessions.
 */
export function deactivate(): void {
  disconnect();
}
