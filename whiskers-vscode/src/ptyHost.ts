/**
 * PtyHost — spawns a node-pty on the HOST and pumps stdin/stdout.
 *
 * Keystrokes are forwarded raw to the PTY (the shell does its own echo + line
 * editing), and the authoritative command guard runs on each completed line: the
 * submitting carriage-return is only forwarded when the line is allowed, otherwise
 * the line is cleared (NAK) and a notice is echoed to the console.
 *
 * Once a privileged binary (claude/codex) or a configured rawMode-trigger binary
 * (e.g. agy, gemini) is submitted, the guard hands off to `rawMode`: all further
 * input is forwarded byte-for-byte, unguarded, for the rest of the session —
 * the step-up auth (for privileged binaries) or rawMode allowlist is the trust
 * boundary, and a line-oriented guard can't parse keystrokes typed into a
 * raw-mode TUI (its prompts/slash-commands aren't shell commands).
 */
import * as pty from "node-pty";

import { guardLine, GuardVerdict, isRawModeBinary } from "./commandGuard";

export interface PtyHostOptions {
  shell: string;
  cwd: string;
  stepUpMethod: string;
  onData: (chunk: string) => void; // forward PTY output (+ guard notices) → relay
  onExit: () => void;
  onElevationRequired: (method: string) => void;
}

export class PtyHost {
  private proc: pty.IPty | null = null;
  private lineBuffer = "";
  private elevationExpiry: number | null = null;
  private rawMode = false;

  constructor(private readonly opts: PtyHostOptions) {}

  /** Set the elevation expiry timestamp in wall-clock seconds. */
  setElevation(expiresAt: number): void {
    this.elevationExpiry = expiresAt;
  }

  /** Check if the current time is within the elevation window. */
  private isElevated(): boolean {
    return this.elevationExpiry !== null && Date.now() / 1000 < this.elevationExpiry;
  }

  /**
   * Erase the currently-echoed, unsubmitted line. Uses backspace-space-backspace
   * per character rather than \x15 (VKILL), which is POSIX-only and prints a
   * literal "^U" under Windows ConPTY/PSReadLine.
   */
  private eraseLine(): void {
    if (!this.proc) return;
    this.proc.write("\b \b".repeat(this.lineBuffer.length));
  }

  /** Spawn the PTY with the host's real env and cwd. */
  start(): void {
    this.proc = pty.spawn(this.opts.shell, [], {
      name: "xterm-color",
      cwd: this.opts.cwd,
      env: process.env as { [key: string]: string },
    });
    this.proc.onData((d: string) => this.opts.onData(d));
    this.proc.onExit(() => this.opts.onExit());
  }

  /**
   * Forward incoming keystrokes to the PTY, gating the submit (Enter) on the
   * command guard. Returns the verdict for the last submitted line, if any.
   */
  write(data: string): GuardVerdict | null {
    if (!this.proc) {
      return null;
    }
    if (this.rawMode) {
      this.proc.write(data);
      return null;
    }
    let verdict: GuardVerdict | null = null;
    for (const ch of data) {
      if (ch === "\r" || ch === "\n") {
        const submittedLine = this.lineBuffer;
        verdict = guardLine(submittedLine);
        if (verdict.allowed && verdict.privileged && !this.isElevated()) {
          this.eraseLine();
          this.opts.onData("\r\n\x1b[33m[elevation required: " + this.opts.stepUpMethod + "]\x1b[0m\r\n");
          this.opts.onElevationRequired(this.opts.stepUpMethod);
          this.lineBuffer = "";
          continue;
        }
        if (verdict.allowed) {
          this.proc.write("\r"); // submit the already-echoed line
          if (verdict.privileged || isRawModeBinary(submittedLine)) {
            // Elevated launch of claude/codex or launch of configured rawMode binary:
            // hand off to raw passthrough — the TUI's own input isn't shell commands the guard can parse.
            this.rawMode = true;
          }
        } else {
          this.eraseLine(); // clear the readline buffer (unsubmitted)
          this.opts.onData(`\r\n\x1b[31m[blocked: ${verdict.reason}]\x1b[0m\r\n`);
        }
        this.lineBuffer = "";
      } else if (ch === "\x7f" || ch === "\b") {
        this.lineBuffer = this.lineBuffer.slice(0, -1);
        this.proc.write(ch);
      } else if (ch.charCodeAt(0) < 0x20) {
        // Control byte (arrows, Ctrl-C, …): forward raw, keep it out of the buffer.
        this.proc.write(ch);
      } else {
        this.lineBuffer += ch;
        this.proc.write(ch);
      }
    }
    return verdict;
  }

  kill(): void {
    this.proc?.kill();
    this.proc = null;
  }
}
