"""Generic headless agent driver driven by CLI_AGENT_CMD template."""

from __future__ import annotations

import os
import shlex

from core_graph.goap_agent.cli.base import CliResult, CliRunOptions, which_binary
import logging
logger = logging.getLogger("whiskers")


class GenericCliDriver:
    """Runs any headless agent via ``CLI_AGENT_CMD`` with template placeholders.

    Placeholders: ``{prompt}``, ``{workdir}``, ``{mcp_config}``,
    ``{instructions_file}``. Auto-MCP also sets ``GOAP_AGENT_MCP_CONFIG`` /
    ``CLI_AGENT_MCP_CONFIG`` in the child env and may write workdir
    ``.mcp.json``.
    """

    name = "generic"

    def binary(self) -> str:
        cmd = (os.environ.get("CLI_AGENT_CMD") or "").strip()
        if not cmd:
            return ""
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return ""
        return parts[0] if parts else ""

    def available(self) -> bool:
        b = self.binary()
        return bool(b) and which_binary(b) is not None

    def build_cmd(self, prompt: str, *, options: CliRunOptions) -> list[str]:
        template = (os.environ.get("CLI_AGENT_CMD") or "").strip()
        if not template:
            raise ValueError("CLI_AGENT_CMD is not set for generic driver")
        workdir = options.workdir or os.getcwd()
        mcp_config = options.mcp_config_path or ""
        instructions_file = options.append_system_prompt_file or ""
        # Support both format-style and simple replace
        filled = (
            template.replace("{prompt}", prompt)
            .replace("{workdir}", workdir)
            .replace("{mcp_config}", mcp_config)
            .replace("{instructions_file}", instructions_file)
        )
        # Best-effort format fill; templates may contain braces from shell
        # placeholders ({0}, unbalanced braces) — never crash the driver.
        try:
            filled = filled.format(
                prompt=prompt,
                workdir=workdir,
                mcp_config=mcp_config,
                instructions_file=instructions_file,
            )
        except Exception:
            logger.debug("generic.py: swallowed exception", exc_info=True)
        parts = shlex.split(filled)
        if options.extra_args:
            parts.extend(options.extra_args)
        return parts

    def parse_result(self, stdout: str, stderr: str, returncode: int) -> CliResult:
        status = "ok" if returncode == 0 else "error"
        return CliResult(
            status=status,
            text=(stdout or "").strip(),
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
            agent=self.name,
            error=None if returncode == 0 else (stderr.strip() or f"exit {returncode}"),
        )
