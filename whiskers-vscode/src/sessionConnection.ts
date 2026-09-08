/**
 * SessionConnection — one relay DATA leg + one node-pty, for a single session.
 *
 * After the control leg receives an "open" frame, the extension creates a
 * SessionConnection: it dials /api/terminal/none/host/{ideId}/session/{sessionId}, spawns a
 * PtyHost (cwd = requested workdir), pumps PTY output → data WS and console
 * keystrokes → PtyHost.write (guarded). On data-WS close or PTY exit the whole
 * session tears down. No reconnect: a dropped data leg kills the PTY (clean state).
 */
import * as vscode from "vscode";
import WebSocket from "ws";

import { PtyHost } from "./ptyHost";

export interface SessionConnectionOptions {
  tunnelUrl: string;
  ideId: string;
  sessionId: string;
  token: string;
  stepUpMethod: string;
  workdir?: string | null;
  onClose: (sessionId: string) => void;
}

/**
 * Manages the bidirectional WebSocket bridge and PTY process for a terminal session.
 */
export class SessionConnection {
  private ws: WebSocket | null = null;
  private host: PtyHost | null = null;
  private closed = false;

  constructor(private readonly opts: SessionConnectionOptions) {}

  start(): void {
    const { tunnelUrl, ideId, sessionId, token } = this.opts;
    const url =
      `${tunnelUrl.replace(/\/$/, "")}/api/terminal/none/host/${encodeURIComponent(ideId)}` +
      `/session/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
    this.ws = new WebSocket(url);

    this.ws.on("open", () => {
      this.host = new PtyHost({
        shell: process.platform === "win32" ? "powershell.exe" : "bash",
        cwd: this.opts.workdir || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd(),
        stepUpMethod: this.opts.stepUpMethod,
        onData: (chunk) => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(Buffer.from(chunk, "utf8"));
        },
        onExit: () => this.close(),
        onElevationRequired: (method) => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ t: "elevation_required", session_id: this.opts.sessionId, method }));
          }
        },
      });
      this.host.start();
    });
    this.ws.on("message", (data: WebSocket.RawData) => {
      this.host?.write(data.toString());
    });
    this.ws.on("close", () => this.close());
    this.ws.on("error", () => this.close());
  }

  /** Grant elevation privilege to the underlying PtyHost. */
  grantElevation(expiresAt: number): void {
    this.host?.setElevation(expiresAt);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.host?.kill();
    this.host = null;
    try { this.ws?.close(); } catch { /* already closed */ }
    this.ws = null;
    this.opts.onClose(this.opts.sessionId);
  }
}
