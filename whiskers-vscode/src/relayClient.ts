/**
 * RelayClient — holds the OUTBOUND CONTROL WebSocket to /api/terminal/none/host/{ideId}.
 * Docker can't reach the host, so the host dials Whiskers Agent and keeps this leg
 * open for presence + signalling. The relay pushes JSON control frames
 * ({"type":"open"|"kill", session_id, workdir}); raw PTY bytes travel on separate
 * per-session DATA legs (see SessionConnection). Auto-reconnects with backoff.
 */
import WebSocket from "ws";

export interface ControlMessage {
  type: "open" | "kill" | "elevation_granted";
  session_id: string;
  workdir?: string | null;
  expires_at?: number;
}

export interface RelayClientOptions {
  tunnelUrl: string;
  ideId: string;
  token: string;
  onControl: (msg: ControlMessage) => void;
  onOpen: () => void;
  onClose: (code?: number, reason?: string) => void;
}

/**
 * WebSocket client that maintains the control channel between VSCode and the relay server.
 */
export class RelayClient {
  private ws: WebSocket | null = null;
  private reconnectMs = 1000;
  private stopped = false;

  constructor(private readonly opts: RelayClientOptions) {}

  /** Dial the control leg; auto-reconnect with backoff until stop(). */
  connect(): void {
    const { tunnelUrl, ideId, token } = this.opts;
    const url = `${tunnelUrl.replace(/\/$/, "")}/api/terminal/none/host/${encodeURIComponent(ideId)}?token=${encodeURIComponent(token)}`;
    this.ws = new WebSocket(url);

    this.ws.on("open", () => {
      this.reconnectMs = 1000;
      this.opts.onOpen();
    });
    this.ws.on("message", (data: WebSocket.RawData) => {
      try {
        const msg = JSON.parse(data.toString()) as ControlMessage;
        if (msg && msg.type && msg.session_id) this.opts.onControl(msg);
      } catch {
        /* ignore non-JSON control frames */
      }
    });
    this.ws.on("close", (code: number, reason: Buffer) => {
      this.opts.onClose(code, reason.toString());
      if (!this.stopped) {
        if (code === 4401) {
          this.stop();
          return;
        }
        setTimeout(() => this.connect(), this.reconnectMs);
        this.reconnectMs = Math.min(this.reconnectMs * 2, 30000);
      }
    });
    // Treat 401/403 as terminal since extension_ws.py rejects invalid tokens pre-handshake.
    this.ws.on("unexpected-response", (_req, res) => {
      const status = res.statusCode;
      if (status === 401 || status === 403) {
        this.stop();
        this.opts.onClose(status, "handshake rejected — token invalid or expired");
        return;
      }
      // Unknown non-101 response: fall back to normal backoff via "close"/"error".
    });
    this.ws.on("error", () => {
      /* surfaced via close */
    });
  }

  stop(): void {
    this.stopped = true;
    this.ws?.close();
    this.ws = null;
  }
}
