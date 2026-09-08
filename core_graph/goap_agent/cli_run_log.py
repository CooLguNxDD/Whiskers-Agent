"""Per-run logging for headless GoapAgent CLI subprocesses.

Writes a directory of artifacts under ``logs/goap_agent/<run_id>/`` and
mirrors lifecycle + stream lines into the ``whiskers_agent.goap_agent.cli`` logger
(so admin SSE log streams pick them up).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from core_graph.goap_agent.env_flags import env_truthy

logger = logging.getLogger("whiskers.goap_agent.cli")

# Avoid dumping secrets into log lines / artifacts.
_REDACT_PATTERNS = (
    re.compile(r"(Bearer\s+)(\S+)", re.I),
    re.compile(r"(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|Authorization)=([^\s]+)", re.I),
    re.compile(r'("Authorization"\s*:\s*"Bearer\s+)([^"]+)(")', re.I),
    re.compile(r'("apiKey"\s*:\s*")([^"]+)(")', re.I),
)



def logging_enabled() -> bool:
    """Whether to write run log directories (default on)."""
    return env_truthy("GOAP_AGENT_LOG", default=True)


def log_streams_to_logger() -> bool:
    """Mirror stdout/stderr lines into Python logger (default on)."""
    return env_truthy("GOAP_AGENT_LOG_STREAMS", default=True)


def log_full_prompt() -> bool:
    """Write full prompt to prompt.txt (default off — only a truncated preview)."""
    return env_truthy("GOAP_AGENT_LOG_PROMPT", default=False)


def default_log_dir() -> Path:
    """Root directory for CLI run logs."""
    override = (os.environ.get("GOAP_AGENT_LOG_DIR") or "").strip()
    if override:
        return Path(override)
    # Prefer CAT_SANDBOX_WORKDIR/logs, then repo logs/, then /tmp
    for key in ("CLI_AGENT_WORKDIR", "CAT_SANDBOX_WORKDIR"):
        base = (os.environ.get(key) or "").strip()
        if base:
            return Path(base) / "logs" / "goap_agent"
    try:
        repo = Path(__file__).resolve().parents[2]
        return repo / "logs" / "goap_agent"
    except Exception as exc:
        logger.debug("default_log_root: repo path resolve failed, using /tmp: %s", exc)
        return Path("/tmp/goap_agent_cli_logs")


def redact(text: str) -> str:
    """Best-effort redaction of bearer tokens / API keys in log text."""
    if not text:
        return text
    out = text
    for pat in _REDACT_PATTERNS:
        if pat.groups == 3:
            out = pat.sub(r"\1***\3", out)
        else:
            out = pat.sub(r"\1***", out)
    return out


def sanitize_cmd(cmd: list[str], *, prompt_max: int = 120) -> list[str]:
    """Return a log-safe copy of argv (truncate -p prompt, redact secrets)."""
    out: list[str] = []
    skip_next_value = False
    for i, arg in enumerate(cmd):
        if skip_next_value:
            skip_next_value = False
            # Values that may be huge or sensitive
            if len(arg) > prompt_max:
                out.append(arg[:prompt_max] + f"…(+{len(arg) - prompt_max} chars)")
            else:
                out.append(redact(arg))
            continue
        if arg in ("-p", "--print", "--append-system-prompt"):
            out.append(arg)
            skip_next_value = True
            continue
        out.append(redact(arg))
    return out


def new_run_id(agent: str = "cli") -> str:
    """Generate a filesystem-safe run id: UTC stamp + agent + short uuid."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_agent = re.sub(r"[^a-zA-Z0-9_-]+", "_", (agent or "cli"))[:24] or "cli"
    return f"{stamp}_{safe_agent}_{uuid.uuid4().hex[:8]}"


@dataclass
class CliRunLog:
    """Filesystem + logger sink for one headless CLI run."""

    run_id: str
    agent: str
    dir: Path
    enabled: bool = True
    _process_fp: TextIO | None = field(default=None, repr=False)
    _events_fp: TextIO | None = field(default=None, repr=False)
    _started_at: float = field(default_factory=time.monotonic)
    _started_wall: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _closed: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def process_log_path(self) -> str:
        return str(self.dir / "process.log")

    @property
    def events_path(self) -> str:
        return str(self.dir / "events.jsonl")

    @property
    def summary_path(self) -> str:
        return str(self.dir / "run.json")

    def open(self) -> None:
        """Create log directory and open file handles."""
        if not self.enabled:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._process_fp = open(self.dir / "process.log", "w", encoding="utf-8")
            self._events_fp = open(self.dir / "events.jsonl", "w", encoding="utf-8")
            self.event(
                "run_open",
                agent=self.agent,
                run_id=self.run_id,
                dir=str(self.dir),
            )
            self.line(f"=== GoapAgent CLI run {self.run_id} agent={self.agent} ===")
            self.line(f"started_at={self._started_wall}")
        except Exception as exc:
            logger.warning("goap_agent.cli: failed to open run log %s: %s", self.dir, exc)
            self.enabled = False
            self._safe_close_handles()

    def line(self, message: str, *, level: int = logging.INFO, mirror: bool = True) -> None:
        """Append a human-readable process.log line (+ optional logger mirror)."""
        msg = redact(message.rstrip("\n"))
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        rendered = f"[{ts}] {msg}"
        if self._process_fp is not None:
            try:
                self._process_fp.write(rendered + "\n")
                self._process_fp.flush()
            except Exception:
                logger.debug("cli_run_log.py: swallowed exception", exc_info=True)
        if mirror and self.enabled:
            logger.log(level, "goap_agent.cli[%s] %s", self.run_id[-12:], msg)

    def event(self, kind: str, **fields: Any) -> None:
        """Append a structured JSONL event (redacted)."""
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "kind": kind,
            **{k: _json_safe(v) for k, v in fields.items()},
        }
        raw = redact(json.dumps(payload, ensure_ascii=False, default=str))
        if self._events_fp is not None:
            try:
                self._events_fp.write(raw + "\n")
                self._events_fp.flush()
            except Exception:
                logger.debug("cli_run_log.py: swallowed exception", exc_info=True)

    def write_text(self, name: str, content: str) -> str | None:
        """Write a sibling artifact file; return path or None."""
        if not self.enabled:
            return None
        try:
            path = self.dir / name
            path.write_text(content if content is not None else "", encoding="utf-8")
            return str(path)
        except Exception as exc:
            self.line(f"artifact write failed ({name}): {exc}", level=logging.WARNING)
            return None

    def log_stream(self, channel: str, text: str) -> None:
        """Record a stdout/stderr chunk (line-oriented preferred)."""
        if not text:
            return
        for part in text.splitlines() or [text]:
            if not part and not text.strip():
                continue
            prefix = "OUT" if channel == "stdout" else "ERR"
            level = logging.INFO if channel == "stdout" else logging.WARNING
            mirror = log_streams_to_logger()
            self.line(f"{prefix}| {part}", level=level, mirror=mirror)
            self.event("stream", channel=channel, line=part[:4000])

    def finish(
        self,
        *,
        status: str,
        returncode: int | None = None,
        error: str | None = None,
        result_meta: dict[str, Any] | None = None,
        text_preview: str | None = None,
    ) -> dict[str, Any]:
        """Close handles and write run.json summary; return paths dict."""
        duration_s = round(time.monotonic() - self._started_at, 3)
        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "agent": self.agent,
            "status": status,
            "returncode": returncode,
            "error": error,
            "duration_s": duration_s,
            "started_at": self._started_wall,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "dir": str(self.dir) if self.enabled else None,
            "process_log": self.process_log_path if self.enabled else None,
            "events_jsonl": self.events_path if self.enabled else None,
            "meta": result_meta or {},
            "text_preview": (text_preview or "")[:500],
            **self.meta,
        }
        self.event("run_finish", status=status, returncode=returncode, duration_s=duration_s, error=error)
        self.line(
            f"=== finished status={status} returncode={returncode} duration_s={duration_s} ===",
            level=logging.INFO if status == "ok" else logging.WARNING,
        )
        if error:
            self.line(f"error: {error}", level=logging.ERROR)
        if self.enabled:
            try:
                (self.dir / "run.json").write_text(
                    json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("goap_agent.cli: failed to write run.json: %s", exc)
        self._safe_close_handles()
        self._closed = True
        return {
            "run_id": self.run_id,
            "log_dir": str(self.dir) if self.enabled else None,
            "process_log": self.process_log_path if self.enabled else None,
            "events_jsonl": self.events_path if self.enabled else None,
            "summary": self.summary_path if self.enabled else None,
            "duration_s": duration_s,
        }

    def _safe_close_handles(self) -> None:
        for fp in (self._process_fp, self._events_fp):
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    logger.debug("cli_run_log.py: swallowed exception", exc_info=True)
        self._process_fp = None
        self._events_fp = None


def start_run_log(*, agent: str, enabled: bool | None = None) -> CliRunLog:
    """Create and open a CliRunLog for a new invocation."""
    on = logging_enabled() if enabled is None else enabled
    run_id = new_run_id(agent)
    root = default_log_dir()
    run_dir = root / run_id
    log = CliRunLog(run_id=run_id, agent=agent or "cli", dir=run_dir, enabled=on)
    log.open()
    return log


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 8000:
            return value[:8000] + f"…(+{len(value) - 8000})"
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value[:200]]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in list(value.items())[:100]}
    return redact(str(value))[:4000]
