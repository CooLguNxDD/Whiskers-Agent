/**
 * Extension configuration accessor. Reads VS Code settings under `whiskersAgent.*`.
 * The host id is persisted in global state so it stays stable across restarts
 * (the relay binds sessions to it).
 */
import * as vscode from "vscode";

export interface WhiskersAgentConfig {
  tunnelUrl: string;
  ideId: string;
  allowedBinaries: string[];
  rawModeBinaries: string[];
  idleTtlSeconds: number;
  stepUpMethod: string;
  token: string;
}

const IDE_ID_KEY = "whiskersAgent.ideId";

export function readConfig(ctx: vscode.ExtensionContext): WhiskersAgentConfig {
  const cfg = vscode.workspace.getConfiguration("whiskersAgent");
  return {
    tunnelUrl: cfg.get<string>("tunnelUrl", "ws://localhost:10000"),
    ideId: cfg.get<string>("ideId", "") || stableIdeId(ctx),
    allowedBinaries: cfg.get<string[]>("allowedBinaries", [
      "claude", "git", "npm", "npx", "node", "python", "python3", "ls", "cat", "echo", "pwd", "which", "cd",
      "clear", "cls", "dir", "type", "mkdir", "rmdir", "cp", "mv", "grep", "find", "code", "gemini", "agy"
    ]),
    rawModeBinaries: cfg.get<string[]>("rawModeBinaries", ["claude", "codex", "agy", "gemini"]),
    idleTtlSeconds: cfg.get<number>("idleTtlSeconds", 900),
    stepUpMethod: cfg.get<string>("stepUpMethod", "totp"),
    token: cfg.get<string>("token", "dev:admin:terminal:host"),
  };
}

/** Return a host id persisted in global state, generating one on first run. */
function stableIdeId(ctx: vscode.ExtensionContext): string {
  let id = ctx.globalState.get<string>(IDE_ID_KEY);
  if (!id) {
    id = `ide-${process.platform}-${Math.random().toString(36).slice(2, 10)}`;
    void ctx.globalState.update(IDE_ID_KEY, id);
  }
  return id;
}
