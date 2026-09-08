"""Claude Code CLI headless driver (``claude -p``)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from core_graph.goap_agent.env_flags import env_truthy
from core_graph.goap_agent.cli.base import (
    CliResult,
    CliRunOptions,
    env_csv,
    which_binary,
)

logger = logging.getLogger("whiskers.goap_agent.cli")


class ClaudeCliDriver:
    """Runs Anthropic Claude Code in non-interactive print mode.

    Auth (in process env, never on argv):
      - ``CLAUDE_CODE_OAUTH_TOKEN`` — preferred for Docker / headless (from
        ``claude setup-token``); takes precedence over keychain.
      - ``ANTHROPIC_API_KEY`` — API-key fallback.
    """

    name = "claude"

    def binary(self) -> str:
        return (os.environ.get("CLAUDE_CLI_BINARY") or "claude").strip()

    def available(self) -> bool:
        return which_binary(self.binary()) is not None

    def build_cmd(self, prompt: str, *, options: CliRunOptions) -> list[str]:
        bin_name = self.binary()
        # IMPORTANT: ``--bare`` skips OAuth/keychain reads. With only
        # CLAUDE_CODE_OAUTH_TOKEN set (Docker headless), bare mode returns
        # "Not logged in". Prefer bare only for API-key / explicit opt-in.
        use_bare = _should_use_bare(options=options)
        cmd: list[str] = [bin_name, "-p", prompt, "--output-format", "json"]
        if use_bare:
            cmd.append("--bare")
        # Headless auto-approve. Claude Code refuses this flag when euid==0.
        # When the runner drops to whiskers-claude (non-root), include the flag.
        if _should_skip_permissions(run_as_user=options.run_as_user):
            cmd.append("--dangerously-skip-permissions")
        extra = list(options.extra_args) or env_csv("CLAUDE_CLI_EXTRA_ARGS")
        skip = {
            "--bare", "--output-format", "json", "-p", "--print",
            "--dangerously-skip-permissions",
        }
        filtered = [a for a in extra if a not in skip]
        cmd.extend(filtered)
        if options.model:
            cmd.extend(["--model", options.model])
        if options.mcp_config_path:
            cmd.extend(["--mcp-config", options.mcp_config_path])
        if options.strict_mcp_config and options.mcp_config_path:
            cmd.append("--strict-mcp-config")
        # Prefer file form for long GoapAgent instruction docs; fall back to inline.
        if options.append_system_prompt_file:
            cmd.extend(["--append-system-prompt-file", options.append_system_prompt_file])
        elif options.append_system_prompt:
            cmd.extend(["--append-system-prompt", options.append_system_prompt])
        if options.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(options.allowed_tools)])
        # Surface Claude's internal process chatter on stderr for run logs.
        if _should_verbose(options):
            if "--verbose" not in cmd and "--verbose" not in filtered:
                cmd.append("--verbose")
        return cmd

    def parse_result(self, stdout: str, stderr: str, returncode: int) -> CliResult:
        text, meta = _extract_text_json(stdout)
        # Claude may exit non-zero with a structured result body (model_not_found, etc.)
        is_error = returncode != 0 or bool(meta.get("is_error"))
        status = "error" if is_error else "ok"
        if not is_error:
            err = None
        else:
            # Prefer structured result text over bare "exit 1" when stderr is empty.
            err = (
                (stderr or "").strip()
                or (text or "").strip()
                or f"exit {returncode if returncode is not None else 1}"
            )
        return CliResult(
            status=status,
            text=text or stdout.strip(),
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
            agent=self.name,
            error=err,
            meta=meta,
        )



def _auth_env_value(name: str, options: CliRunOptions | None = None) -> str:
    """Resolve an auth env var: per-call ``options.env`` override, else process env.

    An explicit empty string in ``options.env`` means "unset" (pool OAuth
    override clearing a parent API key) — do not fall back to process env.
    """
    if options is not None and options.env is not None and name in options.env:
        return str(options.env.get(name) or "").strip()
    return (os.environ.get(name) or "").strip()

def _should_use_bare(*, options: CliRunOptions | None = None) -> bool:
    """Whether to pass ``--bare`` (skips OAuth — incompatible with setup-token alone).

    Pool / per-call ``options.env`` overrides are consulted so a frontend
    OAuth-token override is not defeated by a parent-process API key (or by
    missing process-level ``CLAUDE_CODE_OAUTH_TOKEN``).
    """
    # Explicit override wins (process env only — operators set CLAUDE_CLI_BARE).
    if os.environ.get("CLAUDE_CLI_BARE", "").strip() != "":
        return env_truthy("CLAUDE_CLI_BARE")
    api_key = _auth_env_value("ANTHROPIC_API_KEY", options)
    oauth = _auth_env_value("CLAUDE_CODE_OAUTH_TOKEN", options)
    # OAuth token requires non-bare (keychain/token path). Prefer oauth when both set.
    if oauth:
        return False
    if api_key:
        return True
    # No auth configured — bare still useful for CI dry-runs.
    return True

def _should_skip_permissions(*, run_as_user: str | None = None) -> bool:
    """Whether to pass ``--dangerously-skip-permissions``.

    Default on for non-root. Claude Code hard-rejects the flag when the
    process euid is 0. If the runner will drop privileges to a non-root
    *run_as_user* (e.g. ``whiskers-claude``), enable the flag even when the
    parent MCP server is still root.
    """
    if not env_truthy("CLAUDE_CLI_SKIP_PERMISSIONS", default=True):
        return False
    # Explicit empty string on options means "no drop" — fall through to euid.
    try:
        from core_graph.goap_agent.cli.user_drop import resolve_cli_run_user

        user = resolve_cli_run_user(run_as_user)
        if user is not None and user.uid != 0:
            return True
    except Exception:
        logger.debug("claude.py: swallowed exception", exc_info=True)
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return False
    except Exception:
        logger.debug("claude.py: swallowed exception", exc_info=True)
    return True


def _should_verbose(options: CliRunOptions) -> bool:
    """Whether to pass ``--verbose`` so internal agent steps hit stderr.

    Per-call ``options.verbose`` wins; else ``GOAP_AGENT_CLI_VERBOSE`` /
    ``CLAUDE_CLI_VERBOSE`` (default on for headless observability).
    """
    if options.verbose is not None:
        return bool(options.verbose)
    if os.environ.get("CLAUDE_CLI_VERBOSE", "").strip() != "":
        return env_truthy("CLAUDE_CLI_VERBOSE")
    return env_truthy("GOAP_AGENT_CLI_VERBOSE", default=True)


def ensure_workspace_trust(
    workdir: str,
    *,
    home: str | None = None,
    config_dir: str | None = None,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> None:
    """Mark *workdir* as trusted in Claude's config so headless runs don't hang.

    When the CLI runs as ``whiskers-claude``, pass that user's *home* /
    *config_dir* so trust is written where the child will read it.

    Writes both ``~/.claude.json`` and ``~/.claude/.claude.json`` (path varies
    by Claude Code version) and chowns them to *owner_uid*/*owner_gid*.
    """
    from pathlib import Path

    home_path = Path(home) if home else Path.home()
    cfg_dir = Path(config_dir) if config_dir else Path(
        os.environ.get("CLAUDE_CONFIG_DIR") or (home_path / ".claude")
    )
    # Claude also reads ~/.claude.json for project trust in some versions
    cfg_paths = [
        home_path / ".claude.json",
        cfg_dir / ".claude.json" if cfg_dir.name == ".claude" else cfg_dir.parent / ".claude.json",
        cfg_dir / "settings.json",  # some builds gate permissions via settings
    ]
    # de-dupe
    seen: set[str] = set()
    workdir_abs = str(Path(workdir).resolve())
    for cfg_path in cfg_paths:
        if cfg_path is None:
            continue
        key = str(cfg_path)
        if key in seen:
            continue
        seen.add(key)
        try:
            data: dict[str, Any] = {}
            if cfg_path.is_file():
                try:
                    data = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
                except Exception as exc:
                    logger.debug("ensure_workspace_trust: bad config JSON %s: %s", cfg_path, exc)
                    data = {}
            if not isinstance(data, dict):
                data = {}
            # settings.json uses a different shape — only stamp .claude.json projects.
            if cfg_path.name == "settings.json":
                continue
            projects = data.get("projects")
            if not isinstance(projects, dict):
                projects = {}
            entry = projects.get(workdir_abs)
            if not isinstance(entry, dict):
                entry = {}
            if entry.get("hasTrustDialogAccepted") is True:
                # Still ensure ownership so the drop user can read it.
                if owner_uid is not None and owner_gid is not None and hasattr(os, "chown"):
                    try:
                        if hasattr(os, "geteuid") and os.geteuid() == 0:
                            os.chown(cfg_path, owner_uid, owner_gid)
                    except Exception:
                        logger.debug("claude.py: swallowed exception", exc_info=True)
                continue
            entry["hasTrustDialogAccepted"] = True
            projects[workdir_abs] = entry
            data["projects"] = projects
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if owner_uid is not None and owner_gid is not None and hasattr(os, "chown"):
                try:
                    if hasattr(os, "geteuid") and os.geteuid() == 0:
                        os.chown(cfg_path, owner_uid, owner_gid)
                        os.chown(cfg_path.parent, owner_uid, owner_gid)
                except Exception:
                    logger.debug("claude.py: swallowed exception", exc_info=True)
        except Exception:
            # Best-effort per path; continue other candidates.
            logger.debug("claude.py: settings path candidate failed", exc_info=True)
            continue


def _meta_from_result_obj(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: data[k]
        for k in ("session_id", "total_cost_usd", "usage", "model", "is_error", "subtype")
        if k in data
    }


def _text_from_result_obj(data: dict[str, Any]) -> str:
    text = (
        data.get("result")
        or data.get("structured_output")
        or data.get("text")
        or ""
    )
    if isinstance(text, dict):
        text = json.dumps(text)
    # assistant message content blocks (stream-json style)
    if not text and isinstance(data.get("message"), dict):
        content = data["message"].get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
    return str(text) if text is not None else ""


def _extract_from_event_list(events: list[Any]) -> tuple[str, dict[str, Any]] | None:
    """Pull final result (or last assistant text) from a list of stream events."""
    result_obj: dict[str, Any] | None = None
    last_assistant: dict[str, Any] | None = None
    for item in events:
        if not isinstance(item, dict):
            continue
        et = item.get("type")
        if et == "result" or item.get("subtype") in ("success", "error"):
            result_obj = item
        elif et == "assistant":
            last_assistant = item
    if result_obj is not None:
        text = _text_from_result_obj(result_obj)
        meta = _meta_from_result_obj(result_obj)
        return text, meta
    if last_assistant is not None:
        return _text_from_result_obj(last_assistant), _meta_from_result_obj(last_assistant)
    return None


def _extract_text_json(stdout: str) -> tuple[str, dict[str, Any]]:
    """Parse Claude --output-format json / verbose stream payload; fall back to raw text.

    Handles:
      - single result object
      - NDJSON lines (one event per line)
      - a JSON array of events (verbose / newer Claude Code builds)
    """
    raw = (stdout or "").strip()
    if not raw:
        return "", {}

    # Whole-buffer JSON (object or array of stream events)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return _text_from_result_obj(data), _meta_from_result_obj(data)
    if isinstance(data, list):
        extracted = _extract_from_event_list(data)
        if extracted is not None:
            return extracted

    # NDJSON / multi-line: prefer last type=result object, else last JSON object line
    if "\n" in raw:
        events: list[Any] = []
        last_obj: dict[str, Any] | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                events.append(obj)
                last_obj = obj
        if events:
            extracted = _extract_from_event_list(events)
            if extracted is not None:
                return extracted
        if last_obj is not None:
            return _text_from_result_obj(last_obj), _meta_from_result_obj(last_obj)

    return raw, {}
