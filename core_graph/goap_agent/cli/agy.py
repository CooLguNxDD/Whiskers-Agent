"""Google Antigravity CLI headless driver (``agy``)."""

from __future__ import annotations

import json
import os
from typing import Any

from core_graph.goap_agent.cli.base import (
    CliResult,
    CliRunOptions,
    env_csv,
    which_binary,
)


class AgyCliDriver:
    """Runs Antigravity CLI non-interactively with print + auto-approve.

    MCP servers are loaded from workspace ``.agents/mcp_config.json``
    (auto-placed by :mod:`core_graph.goap_agent.cli.mcp_place` when Mode B
    inject is on).
    """

    name = "agy"

    def binary(self) -> str:
        return (os.environ.get("AGY_CLI_BINARY") or "agy").strip()

    def available(self) -> bool:
        return which_binary(self.binary()) is not None

    def build_cmd(self, prompt: str, *, options: CliRunOptions) -> list[str]:
        bin_name = self.binary()
        extra = list(options.extra_args) or env_csv("AGY_CLI_EXTRA_ARGS")
        # -p / --print is non-interactive. Prefer --dangerously-skip-permissions
        # (current CLI); pass --headless (and --approve <value>) via
        # AGY_CLI_EXTRA_ARGS to run against older Antigravity builds instead.
        legacy = "--headless" in extra
        cmd: list[str] = [bin_name, "-p", prompt]
        if not legacy:
            cmd.append("--dangerously-skip-permissions")
        skip = {"-p", "--print", "--prompt", "--dangerously-skip-permissions"}
        cmd.extend(a for a in extra if a not in skip)
        if options.model:
            cmd.extend(["--model", options.model])
        return cmd

    def parse_result(self, stdout: str, stderr: str, returncode: int) -> CliResult:
        text, meta = _extract_text(stdout)
        status = "ok" if returncode == 0 else "error"
        return CliResult(
            status=status,
            text=text or stdout.strip(),
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
            agent=self.name,
            error=None if returncode == 0 else (stderr.strip() or f"exit {returncode}"),
            meta=meta,
        )


def _extract_text(stdout: str) -> tuple[str, dict[str, Any]]:
    raw = (stdout or "").strip()
    if not raw:
        return "", {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            text = data.get("result") or data.get("text") or data.get("output") or ""
            return str(text), {k: data[k] for k in data if k not in ("result", "text", "output")}
    except json.JSONDecodeError:
        pass
    return raw, {}
