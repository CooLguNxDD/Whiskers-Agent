"""xAI Grok CLI headless driver (``grok -p``)."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from core_graph.goap_agent.cli.base import (
    CliResult,
    CliRunOptions,
    env_csv,
    which_binary,
)
from core_graph.goap_agent.cli.user_drop import resolve_cli_run_user

logger = logging.getLogger("whiskers.goap_agent.cli")


class GrokCliDriver:
    """Runs xAI Grok CLI in non-interactive print mode.

    Auth (in process env, never on argv):
      - ``GROK_AUTH_JSON`` — valid base64 / JSON representation of ~/.grok/auth.json.
        Decoded and written to ~/.grok/auth.json before run.
    """

    name = "grok"

    def binary(self) -> str:
        return (os.environ.get("GROK_CLI_BINARY") or "grok").strip()

    def available(self) -> bool:
        return which_binary(self.binary()) is not None

    def build_cmd(self, prompt: str, *, options: CliRunOptions) -> list[str]:
        # Handle writing out auth.json from GROK_AUTH_JSON
        # Find auth value from options.env override or process env
        auth_val = ""
        if options.env and "GROK_AUTH_JSON" in options.env:
            auth_val = (options.env.get("GROK_AUTH_JSON") or "").strip()
        else:
            auth_val = (os.environ.get("GROK_AUTH_JSON") or "").strip()

        if auth_val:
            run_user = resolve_cli_run_user(options.run_as_user)
            self.ensure_grok_auth(
                auth_val,
                home=run_user.home if run_user else None,
                owner_uid=run_user.uid if run_user else None,
                owner_gid=run_user.gid if run_user else None,
            )

        bin_name = self.binary()
        # MCP is discovered from workdir ``.grok/config.toml`` (auto-placed).
        # --always-approve so Mode B can call GoapAgent MCP tools headlessly.
        cmd: list[str] = [
            bin_name,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--always-approve",
        ]

        extra = list(options.extra_args) or env_csv("GROK_CLI_EXTRA_ARGS")
        skip = {
            "-p",
            "--single",
            "--output-format",
            "json",
            "--always-approve",
            "--yolo",
        }
        filtered = [a for a in extra if a not in skip]
        cmd.extend(filtered)

        if options.model:
            cmd.extend(["--model", options.model])

        # Optional system-rules file path via extra; long GoapAgent instructions
        # are prepended into the prompt by run_cli_agent_dict for non-claude.

        return cmd

    def ensure_grok_auth(
        self,
        auth_env_val: str,
        *,
        home: str | None = None,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
    ) -> None:
        if not auth_env_val:
            return

        # Check if auth_env_val is base64 encoded
        auth_json = auth_env_val.strip()
        try:
            # Let's try parsing as JSON first
            json.loads(auth_json)
        except json.JSONDecodeError:
            # If it fails, try base64 decoding
            try:
                decoded = base64.b64decode(auth_json).decode("utf-8")
                # Verify decoded is valid JSON
                json.loads(decoded)
                auth_json = decoded
            except Exception:
                # If both fail, keep the original to write
                logger.debug("grok.py: auth JSON base64 decode failed, keeping raw", exc_info=True)

        from pathlib import Path
        home_path = Path(home) if home else Path.home()
        grok_dir = home_path / ".grok"
        auth_file = grok_dir / "auth.json"

        try:
            grok_dir.mkdir(parents=True, exist_ok=True)
            auth_file.write_text(auth_json, encoding="utf-8")
            try:
                os.chmod(auth_file, 0o600)
            except Exception:
                logger.debug("grok.py: swallowed exception", exc_info=True)

            if owner_uid is not None and owner_gid is not None and hasattr(os, "chown"):
                try:
                    if hasattr(os, "geteuid") and os.geteuid() == 0:
                        os.chown(auth_file, owner_uid, owner_gid)
                        os.chown(grok_dir, owner_uid, owner_gid)
                except Exception:
                    logger.debug("grok.py: swallowed exception", exc_info=True)
            logger.info(f"Wrote Grok auth to {auth_file}")
        except Exception as e:
            logger.warning(f"Failed to write Grok auth file to {auth_file}: {e}")

    def parse_result(self, stdout: str, stderr: str, returncode: int) -> CliResult:
        text, meta = _extract_text_json(stdout)
        is_error = returncode != 0 or bool(meta.get("is_error"))
        status = "error" if is_error else "ok"
        if not is_error:
            err = None
        else:
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
    """Parse Grok --output-format json / verbose stream payload; fall back to raw text.

    Handles:
      - single result object
      - NDJSON lines (one event per line)
      - a JSON array of events
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
