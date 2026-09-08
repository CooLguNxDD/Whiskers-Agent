"""Async subprocess runner for headless CLI agents."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from pathlib import Path

from core_graph.goap_agent.cli.base import (
    CliResult,
    CliRunOptions,
    default_max_output_bytes,
    default_timeout_s,
    default_workdir,
    materialize_root_private_binary,
    max_concurrent,
    which_binary,
)
from core_graph.goap_agent.cli.registry import get_driver
from core_graph.goap_agent.cli_run_log import (
    CliRunLog,
    log_full_prompt,
    redact,
    sanitize_cmd,
    start_run_log,
)

logger = logging.getLogger("whiskers.goap_agent.cli")

_sem: asyncio.Semaphore | None = None
_sem_lock = asyncio.Lock()


async def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is not None:
        return _sem
    async with _sem_lock:
        if _sem is None:
            _sem = asyncio.Semaphore(max_concurrent())
        return _sem


def reset_semaphore_for_tests() -> None:
    """Reset concurrency semaphore (unit tests)."""
    global _sem
    _sem = None


def _attach_log(result: CliResult, log_info: dict[str, Any] | None) -> CliResult:
    if log_info:
        result.log = log_info
        result.meta = {**(result.meta or {}), "log": log_info}
    return result


# ---------------------------------------------------------------------------
# Privilege-drop setup
# ---------------------------------------------------------------------------

def _setup_privilege_drop(options: CliRunOptions, run_log: CliRunLog) -> Any:
    """Resolve drop-user identity, ensure home, and record run_as_user on the log.

    Returns the resolved ``run_user`` (or ``None`` when running as current process).
    """
    from core_graph.goap_agent.cli.user_drop import (
        ensure_user_home,
        resolve_cli_run_user,
    )

    run_user = resolve_cli_run_user(options.run_as_user)
    if run_user is not None:
        options.run_as_user = run_user.name
        ensure_user_home(run_user)
        run_log.line(f"run_as_user={run_user.name} uid={run_user.uid} home={run_user.home}")
        run_log.meta["run_as_user"] = run_user.name
        run_log.meta["run_as_uid"] = run_user.uid
    else:
        run_log.line("run_as_user=(current process identity)")
    return run_user


def _apply_privilege_env_and_paths(
    env: dict[str, str],
    options: CliRunOptions,
    run_user: Any,
    inject: Any | None,
) -> dict[str, str]:
    """Apply drop-user env overrides and ensure temp/MCP paths are readable."""
    from core_graph.goap_agent.cli.user_drop import (
        apply_user_env,
        ensure_readable_by_user,
    )

    env = apply_user_env(env, run_user)
    # Temp MCP config + system prompt (+ parent goap_agent_* dirs) must
    # be readable/traversable by the drop user (mkdtemp is 0o700 root).
    for path in (
        options.mcp_config_path,
        options.append_system_prompt_file,
    ):
        if path:
            ensure_readable_by_user(path, run_user)
    if inject is not None:
        for path in list(getattr(inject, "cleanup_paths", None) or []):
            ensure_readable_by_user(path, run_user)
        # Workdir discovery files (bearer-bearing) must be readable by drop user.
        placement = getattr(inject, "_mcp_placement", None)
        if placement is not None:
            for path, _orig in list(getattr(placement, "restore", None) or []):
                ensure_readable_by_user(path, run_user)
            for path in list(getattr(placement, "paths_created", None) or []):
                ensure_readable_by_user(path, run_user)
    return env


# ---------------------------------------------------------------------------
# MCP-config placement setup
# ---------------------------------------------------------------------------

async def _setup_mcp_placement(
    inject: Any | None,
    driver_name: str,
    workdir: str,
    options: CliRunOptions,
    run_log: CliRunLog,
) -> None:
    """Auto-MCP: place discovery files where non-Claude agents load servers.

    Claude keeps using --mcp-config against the temp JSON path; Grok/Agy/generic
    get workdir discovery files via ``inject.place_mcp_discovery``.
    """
    if inject is None or not getattr(inject, "mcp_config_path", None):
        return
    try:
        await inject.place_mcp_discovery(driver_name, workdir)
        place_meta = (getattr(inject, "meta", None) or {}).get("mcp_place") or {}
        if place_meta:
            run_log.line(
                f"mcp_place strategy={place_meta.get('strategy')} "
                f"path={place_meta.get('path') or options.mcp_config_path}"
            )
            run_log.meta["mcp_place"] = place_meta
    except Exception as exc:
        run_log.line(f"mcp_place failed: {exc}", level=logging.WARNING)


# ---------------------------------------------------------------------------
# Per-driver argv/config assembly
# ---------------------------------------------------------------------------

def _ensure_driver_workspace(
    driver: Any,
    workdir: str,
    run_user: Any,
    run_log: CliRunLog,
) -> None:
    """Per-driver workspace/config prep keyed by driver id.

    Claude Code: trust workdir so headless runs don't fail on trust dialog.
    Other drivers: no-op.
    """
    name = getattr(driver, "name", "") or ""
    if name != "claude":
        return
    try:
        from core_graph.goap_agent.cli.claude import ensure_workspace_trust

        ensure_workspace_trust(
            workdir,
            home=run_user.home if run_user else None,
            config_dir=(
                str(Path(run_user.home) / ".claude") if run_user else None
            ),
            owner_uid=run_user.uid if run_user else None,
            owner_gid=run_user.gid if run_user else None,
        )
        run_log.line(f"workspace_trust ensured for {workdir}")
    except Exception as exc:
        run_log.line(f"workspace_trust skipped: {exc}", level=logging.DEBUG)


def _build_driver_cmd(
    driver: Any,
    prompt: str,
    options: CliRunOptions,
    bin_name: str,
) -> list[str]:
    """Assemble argv via the driver, rewriting the binary path when materialised.

    Strategy: each driver implements ``build_cmd``; this helper owns the shared
    public-binary rewrite so PATH order cannot re-hit a root symlink.
    """
    cmd = list(driver.build_cmd(prompt, options=options))
    # Prefer absolute public path when we materialised / resolved it.
    if bin_name and cmd and cmd[0] != bin_name and os.path.sep in str(bin_name):
        cmd = [bin_name, *cmd[1:]]
    return cmd


def _build_child_env(
    options: CliRunOptions,
    run_user: Any,
    inject: Any | None,
) -> dict[str, str]:
    """Assemble sanitized parent env + option overrides + drop-user env/paths."""
    # Strip DB/API secrets from the parent env so LLM-driven children cannot
    # inherit MASTER_KEY / DATABASE_URL / vault keys. Apply denylist first so
    # options.env overrides (pool OAuth, GOAP_AGENT_MCP_TOKEN) still win.
    from core_graph.goap_agent.cli.env_filter import sanitized_parent_env

    env = sanitized_parent_env()
    if options.env:
        # Empty / None values pop the key so pool OAuth overrides can clear a
        # parent ANTHROPIC_API_KEY (and vice versa) for the child process.
        for ek, ev in options.env.items():
            if ev is None or (isinstance(ev, str) and not ev.strip()):
                env.pop(ek, None)
            else:
                env[ek] = ev
    # Surface MCP config path for generic drivers / child tooling.
    if options.mcp_config_path:
        env.setdefault("GOAP_AGENT_MCP_CONFIG", options.mcp_config_path)
        env.setdefault("CLI_AGENT_MCP_CONFIG", options.mcp_config_path)

    if run_user is not None:
        env = _apply_privilege_env_and_paths(env, options, run_user, inject)
    return env


def _log_run_header(
    *,
    prompt: str,
    cmd: list[str],
    workdir: str,
    timeout: float,
    max_bytes: int,
    options: CliRunOptions,
    run_user: Any,
    inject: Any | None,
    run_log: CliRunLog,
) -> None:
    """Record redacted spawn header, inject meta, and prompt artifact."""
    safe_cmd = sanitize_cmd(cmd)
    run_log.line(f"cwd={workdir}")
    run_log.line(f"timeout_s={timeout} max_output_bytes={max_bytes}")
    run_log.line(f"cmd={safe_cmd}")
    run_log.event(
        "spawn_prepare",
        cwd=workdir,
        timeout_s=timeout,
        cmd=safe_cmd,
        run_as_user=run_user.name if run_user else None,
        mcp_config=options.mcp_config_path or None,
        strict_mcp=bool(options.strict_mcp_config),
        verbose=bool(options.verbose) if options.verbose is not None else None,
    )
    if inject is not None:
        inj_fields = {
            "token_source": getattr(inject, "token_source", None),
            "mcp_url": getattr(inject, "mcp_url", None),
            "mcp_config_path": getattr(inject, "mcp_config_path", None),
        }
        # inject.meta may already include mcp_url — don't double-pass kwargs.
        for k, v in dict(getattr(inject, "meta", None) or {}).items():
            if k not in inj_fields:
                inj_fields[k] = v
        run_log.event("inject", **inj_fields)
        run_log.line(
            f"inject token_source={inj_fields.get('token_source')} "
            f"mcp_url={inj_fields.get('mcp_url')} "
            f"mcp_config={inj_fields.get('mcp_config_path')}"
        )
    # Prompt artifact (truncated by default)
    preview = prompt if log_full_prompt() else (prompt[:500] + ("…" if len(prompt) > 500 else ""))
    run_log.write_text("prompt.txt", redact(preview))
    if options.append_system_prompt_file:
        run_log.line(f"system_prompt_file={options.append_system_prompt_file}")
    if options.mcp_config_path:
        run_log.line(f"mcp_config_path={options.mcp_config_path}")
        # Redacted copy of MCP config for debugging (no live bearer).
        # Note: use module-level Path — a local ``from pathlib import Path``
        # here would make Path unbound earlier in this function (trust setup).
        try:
            raw = Path(options.mcp_config_path).read_text(encoding="utf-8")
            run_log.write_text("mcp_config.redacted.json", redact(raw))
        except Exception:
            logger.debug("runner.py: swallowed exception", exc_info=True)


# ---------------------------------------------------------------------------
# Public orchestration entry
# ---------------------------------------------------------------------------

async def run_cli_agent(
    prompt: str,
    *,
    agent: str | None = None,
    options: CliRunOptions | None = None,
    inject: Any | None = None,
) -> CliResult:
    """Spawn a headless CLI agent, capture output, parse into CliResult.

    When *inject* is a ``CliInjection``, cleanup of temp MCP/instruction files
    runs after the process exits (success or failure).

    Process lifecycle + stdout/stderr are written under
    ``logs/goap_agent/<run_id>/`` (see ``cli_run_log``) and mirrored to the
    ``whiskers_agent.goap_agent.cli`` logger.
    """
    if not prompt or not str(prompt).strip():
        return CliResult(status="error", error="missing_prompt", agent=agent or "")

    options = options or CliRunOptions()
    try:
        driver = get_driver(agent)
    except KeyError as exc:
        return CliResult(status="error", error=str(exc), agent=agent or "")

    run_log = start_run_log(agent=driver.name, enabled=options.log_enabled)
    run_log.meta["agent"] = driver.name

    bin_name = driver.binary()
    if not which_binary(bin_name):
        info = run_log.finish(
            status="binary_not_found",
            error=f"binary '{bin_name}' not found on PATH",
        )
        return _attach_log(
            CliResult(
                status="binary_not_found",
                error=(
                    f"CLI agent binary '{bin_name}' not found on PATH. "
                    "Install claude/agy/grok in the server environment or set "
                    "CLAUDE_CLI_BINARY / AGY_CLI_BINARY / GROK_CLI_BINARY / CLI_AGENT_CMD."
                ),
                agent=driver.name,
            ),
            info,
        )

    # xAI installer may leave /usr/local/bin/grok → /root/.grok/...; drop user
    # cannot exec through /root (700). Materialize a public binary when needed.
    public_bin = materialize_root_private_binary(bin_name)
    if public_bin and public_bin != bin_name and os.path.basename(public_bin) == os.path.basename(
        bin_name
    ):
        # Prefer absolute public path so PATH order cannot re-hit a root symlink.
        bin_name = public_bin
        run_log.line(f"materialized CLI binary at {public_bin}")

    workdir = options.workdir or default_workdir()
    options.workdir = workdir
    timeout = options.timeout_s if options.timeout_s is not None else default_timeout_s()
    max_bytes = options.max_output_bytes or default_max_output_bytes()

    # Drop privileges to whiskers-claude (non-root) so Claude Code accepts
    # --dangerously-skip-permissions in Docker.
    run_user = _setup_privilege_drop(options, run_log)

    # Auto-MCP: place discovery files where non-Claude agents load servers.
    await _setup_mcp_placement(inject, driver.name, workdir, options, run_log)

    # Per-driver workspace/config (Claude trust dialog, etc.).
    _ensure_driver_workspace(driver, workdir, run_user, run_log)

    try:
        cmd = _build_driver_cmd(driver, prompt, options, bin_name)
    except Exception as exc:
        if inject is not None:
            try:
                inject.cleanup()
            except Exception:
                logger.debug("runner.py: swallowed exception", exc_info=True)
        info = run_log.finish(status="error", error=str(exc))
        return _attach_log(
            CliResult(status="error", error=str(exc), agent=driver.name),
            info,
        )

    env = _build_child_env(options, run_user, inject)

    _log_run_header(
        prompt=prompt,
        cmd=cmd,
        workdir=workdir,
        timeout=timeout,
        max_bytes=max_bytes,
        options=options,
        run_user=run_user,
        inject=inject,
        run_log=run_log,
    )

    sem = await _get_sem()
    result: CliResult | None = None
    try:
        async with sem:
            logger.info(
                "goap_agent.cli: start run_id=%s agent=%s timeout=%ss cwd=%s mcp=%s",
                run_log.run_id,
                driver.name,
                timeout,
                workdir,
                options.mcp_config_path or "-",
            )
            run_log.line("spawning subprocess…")
            t0 = time.monotonic()
            from core_graph.goap_agent.cli.user_drop import spawn_kwargs_for_user

            spawn_extra = spawn_kwargs_for_user(run_user)
            try:
                # Claude --output-format json can emit one huge line; StreamReader
                # default limit is 64KiB and raises LimitOverrunError, wiping stdout.
                stream_limit = max(int(max_bytes) + 65536, 16 * 1024 * 1024)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                    env=env,
                    limit=stream_limit,
                    **spawn_extra,
                )
            except FileNotFoundError:
                info = run_log.finish(
                    status="binary_not_found",
                    error=f"Failed to spawn '{cmd[0]}'",
                )
                return _attach_log(
                    CliResult(
                        status="binary_not_found",
                        error=f"Failed to spawn '{cmd[0]}'",
                        agent=driver.name,
                        cmd=cmd,
                    ),
                    info,
                )
            except PermissionError as exc:
                hint = (
                    f"spawn failed: {exc}. If the CLI binary lives under /root "
                    "(common for xAI grok install symlinks), rebuild the image "
                    "or set GROK_CLI_BINARY to a world-executable path."
                )
                info = run_log.finish(status="error", error=hint)
                return _attach_log(
                    CliResult(
                        status="error",
                        error=hint,
                        agent=driver.name,
                        cmd=cmd,
                    ),
                    info,
                )
            except Exception as exc:
                err = f"spawn failed: {exc}"
                # errno 13 often surfaces as OSError, not PermissionError
                if getattr(exc, "errno", None) == 13 or "Permission denied" in str(exc):
                    err = (
                        f"{err}. If the CLI binary lives under /root "
                        "(common for xAI grok install symlinks), rebuild the image "
                        "or set GROK_CLI_BINARY to a world-executable path."
                    )
                info = run_log.finish(status="error", error=err)
                return _attach_log(
                    CliResult(
                        status="error",
                        error=err,
                        agent=driver.name,
                        cmd=cmd,
                    ),
                    info,
                )

            run_log.event("spawned", pid=getattr(proc, "pid", None))
            run_log.line(f"pid={getattr(proc, 'pid', None)}")

            try:
                stdout, stderr = await asyncio.wait_for(
                    _collect_process_output(proc, max_bytes=max_bytes, run_log=run_log),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                run_log.line(f"TIMEOUT after {timeout}s — killing process", level=logging.ERROR)
                run_log.event("timeout", timeout_s=timeout)
                with contextlib_suppress():
                    proc.kill()
                    await proc.wait()
                info = run_log.finish(
                    status="timeout",
                    returncode=getattr(proc, "returncode", None),
                    error=f"CLI agent timed out after {timeout}s",
                )
                return _attach_log(
                    CliResult(
                        status="timeout",
                        error=f"CLI agent timed out after {timeout}s",
                        agent=driver.name,
                        cmd=cmd,
                        returncode=None,
                        raw_stdout="",
                        raw_stderr="",
                    ),
                    info,
                )

            elapsed = round(time.monotonic() - t0, 3)
            run_log.line(
                f"process exited returncode={proc.returncode} elapsed_s={elapsed} "
                f"stdout_bytes≈{len(stdout)} stderr_bytes≈{len(stderr)}"
            )
            run_log.event(
                "exited",
                returncode=proc.returncode,
                elapsed_s=elapsed,
                stdout_len=len(stdout),
                stderr_len=len(stderr),
            )
            # Persist full captures for offline inspection
            run_log.write_text("stdout.txt", stdout)
            run_log.write_text("stderr.txt", stderr)

        result = driver.parse_result(stdout, stderr, proc.returncode or 0)
        result.cmd = cmd
        result.agent = driver.name
        if inject is not None and getattr(inject, "meta", None):
            result.meta = {
                **(result.meta or {}),
                "inject": dict(inject.meta),
                "token_source": inject.token_source,
            }
        # Surface cost/session from Claude JSON when present (before finish closes files).
        if result.meta:
            cost = result.meta.get("total_cost_usd")
            sid = result.meta.get("session_id")
            if cost is not None or sid:
                run_log.line(
                    f"cli_meta cost_usd={cost} session_id={sid} model={result.meta.get('model')}"
                )
                run_log.event(
                    "cli_meta",
                    total_cost_usd=cost,
                    session_id=sid,
                    model=result.meta.get("model"),
                    usage=result.meta.get("usage"),
                )
        if result.text:
            run_log.line(f"result_text_preview: {(result.text or '')[:400]}")
        info = run_log.finish(
            status=result.status,
            returncode=result.returncode,
            error=result.error,
            result_meta=result.meta,
            text_preview=result.text,
        )
        return _attach_log(result, info)
    finally:
        if inject is not None:
            try:
                inject.cleanup()
            except Exception:
                logger.debug("runner.py: swallowed exception", exc_info=True)


async def _collect_process_output(
    proc: asyncio.subprocess.Process,
    *,
    max_bytes: int,
    run_log: CliRunLog,
) -> tuple[str, str]:
    """Drain stdout/stderr, streaming lines into the run log.

    Falls back to ``communicate()`` when pipes are missing (unit-test mocks).
    """
    stdout_attr = getattr(proc, "stdout", None)
    stderr_attr = getattr(proc, "stderr", None)

    # Mocked processes often only implement communicate().
    if stdout_attr is None and stderr_attr is None and hasattr(proc, "communicate"):
        run_log.line("collect via communicate() (no live streams)")
        stdout_b, stderr_b = await proc.communicate()
        stdout = _clip(stdout_b or b"", max_bytes)
        stderr = _clip(stderr_b or b"", max_bytes)
        # Still dump into process log as bulk blocks
        if stdout:
            run_log.log_stream("stdout", stdout)
        if stderr:
            run_log.log_stream("stderr", stderr)
        return stdout, stderr

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    async def _drain(stream: Any, buf: bytearray, channel: str) -> None:
        if stream is None:
            return
        try:
            while True:
                try:
                    line = await stream.readline()
                except (asyncio.LimitOverrunError, ValueError) as overrun:
                    # Oversized line: fall back to raw chunked read so we still
                    # capture Claude's single-line JSON result.
                    run_log.line(
                        f"{channel} line overrun ({overrun}); switching to read()",
                        level=logging.WARNING,
                    )
                    while True:
                        chunk = await stream.read(65536)
                        if not chunk:
                            return
                        if len(buf) < max_bytes:
                            remain = max_bytes - len(buf)
                            buf.extend(chunk[:remain])
                        try:
                            run_log.log_stream(
                                channel,
                                chunk.decode("utf-8", errors="replace").rstrip("\n"),
                            )
                        except Exception:
                            logger.debug("runner.py: swallowed exception", exc_info=True)
                    return
                if not line:
                    break
                if len(buf) < max_bytes:
                    remain = max_bytes - len(buf)
                    buf.extend(line[:remain])
                try:
                    text = line.decode("utf-8", errors="replace")
                except Exception:
                    text = str(line)
                run_log.log_stream(channel, text.rstrip("\n"))
        except Exception as exc:
            run_log.line(f"{channel} drain error: {exc}", level=logging.WARNING)

    await asyncio.gather(
        _drain(stdout_attr, stdout_buf, "stdout"),
        _drain(stderr_attr, stderr_buf, "stderr"),
        proc.wait(),
    )
    # If streams closed before wait, ensure process reaped (wait already in gather)
    stdout = _clip(bytes(stdout_buf), max_bytes)
    stderr = _clip(bytes(stderr_buf), max_bytes)
    if len(stdout_buf) >= max_bytes or len(stderr_buf) >= max_bytes:
        run_log.line(f"output clipped at max_bytes={max_bytes}", level=logging.WARNING)
    return stdout, stderr


def _clip(data: bytes, max_bytes: int) -> str:
    if len(data) > max_bytes:
        data = data[:max_bytes] + b"\n...(truncated)"
    return data.decode("utf-8", errors="replace")


class contextlib_suppress:
    """Minimal suppress for kill failures."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


async def run_cli_agent_dict(
    prompt: str,
    *,
    agent: str | None = None,
    workdir: str | None = None,
    timeout_s: float | None = None,
    mcp_config_path: str | None = None,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    session_id: str | None = None,
    inject_mcp: bool | None = None,
    bearer_token: str | None = None,
    prepend_instructions: bool | None = None,
    verbose: bool | None = None,
    log_enabled: bool | None = None,
    run_as_user: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper with optional auto MCP/instruction injection.

    When *inject_mcp* is True (or None and ``GOAP_AGENT_AUTO_MCP`` is on),
    writes a temp MCP config pointing at this Whiskers Agent server for **all** CLI
    drivers (Claude ``--mcp-config``, Grok/Agy workdir discovery files,
    generic env/template), and loads ``instructions/CLI_AGENT.md``.

    Run process logs (lifecycle + internal stdout/stderr) land under
    ``GOAP_AGENT_LOG_DIR`` / ``logs/goap_agent/<run_id>/``.

    Subprocess identity defaults to ``whiskers-claude`` when the parent is
    root (``CLI_AGENT_USER`` / ``CLI_AGENT_DROP_PRIVS``).

    *env*: optional child-process env overrides (e.g. pool OAuth/API-key auth
    from ``cli_auth_env_overrides``). Empty/None values pop an inherited key
    (see ``run_cli_agent``'s env merge).
    """
    from core_graph.goap_agent.cli_inject import compose_prompt, prepare_cli_injection

    injection = await prepare_cli_injection(
        session_id=session_id,
        mcp_config_path=mcp_config_path,
        inject_mcp=inject_mcp,
        bearer_token=bearer_token,
        agent=agent,
    )

    agent_l = (agent or "claude").strip().lower()
    claude_like = agent_l in ("claude", "claude-cli", "")

    # Prefer system-prompt-file so the user prompt stays the goal only (Claude).
    # Other agents get instructions prepended into the prompt body (or via
    # driver-specific flags / {instructions_file} for generic).
    use_file = bool(injection.instructions_path) and claude_like
    do_prepend = prepend_instructions
    if do_prepend is None:
        do_prepend = not use_file

    full_prompt = compose_prompt(prompt, injection=injection, prepend_instructions=do_prepend)

    opts = CliRunOptions(
        workdir=workdir,
        timeout_s=timeout_s,
        mcp_config_path=injection.mcp_config_path or mcp_config_path,
        strict_mcp_config=bool(injection.strict_mcp and claude_like),
        append_system_prompt_file=injection.instructions_path if use_file else None,
        append_system_prompt=(
            injection.instructions
            if (not use_file and injection.instructions and not do_prepend)
            else None
        ),
        allowed_tools=allowed_tools,
        model=model,
        verbose=verbose,
        log_enabled=log_enabled,
        run_as_user=run_as_user,
        env=env,
    )
    # Non-claude drivers: put instructions in the prompt body; keep MCP path
    # so place_mcp_discovery + env wiring still run.
    if not claude_like:
        full_prompt = compose_prompt(prompt, injection=injection, prepend_instructions=True)
        opts.append_system_prompt = None
        opts.append_system_prompt_file = None

    result = await run_cli_agent(full_prompt, agent=agent, options=opts, inject=injection)
    out = result.to_dict()
    out["inject"] = {
        "token_source": injection.token_source,
        "mcp_url": injection.mcp_url,
        "mcp_config": bool(injection.mcp_config_path),
        "instructions": bool(injection.instructions),
        **(injection.meta or {}),
    }
    return out
