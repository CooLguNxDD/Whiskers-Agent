/**
 * Auth — obtain a Layer-1 JWT carrying the `terminal:host` scope.
 * STUB — Phase 1 returns a stored token; PKCE flow is a follow-up.
 */
import * as vscode from "vscode";

const TOKEN_KEY = "whiskersAgent.jwt";

/** Return a stored bearer, or undefined if the host must authenticate. */
export async function getStoredToken(ctx: vscode.ExtensionContext): Promise<string | undefined> {
  return ctx.secrets.get(TOKEN_KEY);
}

/** Persist a bearer obtained out-of-band (paste flow for the stub). */
export async function storeToken(ctx: vscode.ExtensionContext, token: string): Promise<void> {
  await ctx.secrets.store(TOKEN_KEY, token);
}

/** Clear the persisted bearer token. */
export async function clearStoredToken(ctx: vscode.ExtensionContext): Promise<void> {
  await ctx.secrets.delete(TOKEN_KEY);
}

/**
 * Ensure a token with terminal:host scope is available.
 * TODO Phase 1 wiring: drive the Operator Console PKCE flow and verify scope.
 */
export async function ensureHostToken(ctx: vscode.ExtensionContext, forcePrompt: boolean = false): Promise<string> {
  if (!forcePrompt) {
    const existing = await getStoredToken(ctx);
    if (existing) {
      return existing;
    }
    const cfg = vscode.workspace.getConfiguration("whiskersAgent");
    const configToken = cfg.get<string>("token", "");
    if (configToken) {
      return configToken;
    }
  }
  const pasted = await vscode.window.showInputBox({
    prompt: "Paste a Whiskers Agent JWT with the terminal:host scope",
    password: true,
    ignoreFocusOut: true,
  });
  if (!pasted) {
    throw new Error("No terminal:host token provided.");
  }
  await storeToken(ctx, pasted);
  return pasted;
}
