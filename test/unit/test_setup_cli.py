"""
Acceptance tests for the initial-setup CLI (``terminal/script/setup.py``).

These define the binary pass/fail contract for the Agy/Grok TDD pipeline
(see ``goals/setup-cli/``). Every test loads ``terminal/script/setup.py`` by file path
(the ``scripts`` directory is not an importable package) and only exercises
*pure* helpers — no Docker, no DB, no network. Heavy imports (``core.*`` /
``db_layer.*``) MUST stay lazy inside functions so importing the module is cheap.
"""

import asyncio
import base64
import importlib.util
import inspect
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module(rel_path: str, name: str):
    """Load a repo script by file path, bypassing package import."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup():
    """Load the setup CLI module fresh."""
    return _load_module("terminal/script/setup.py", "whiskers_setup_under_test")


# --------------------------------------------------------------------------- #
# MTU-1: setup defaults                                                        #
# --------------------------------------------------------------------------- #
def test_defaults_load():
    """load_setup_defaults() exposes recommended plugins."""
    setup = _setup()
    defaults = setup.load_setup_defaults()
    assert isinstance(defaults, dict)

    recs = defaults["recommended_plugins"]
    assert isinstance(recs, list) and recs, "recommended_plugins must be a non-empty list"


# --------------------------------------------------------------------------- #
# MTU-2: doctor / preflight                                                    #
# --------------------------------------------------------------------------- #
def test_doctor_checks():
    """check_prerequisites() reports a python check that passes on the runner."""
    setup = _setup()
    checks = setup.check_prerequisites()
    assert isinstance(checks, dict)
    assert "python" in checks and "docker" in checks
    for name, result in checks.items():
        assert "ok" in result, f"check {name} missing 'ok'"
        assert isinstance(result["ok"], bool)
    assert checks["python"]["ok"] is True  # runner is >= 3.11


# --------------------------------------------------------------------------- #
# MTU-3: env scaffolding                                                       #
# --------------------------------------------------------------------------- #
def test_master_key():
    """generate_master_key() returns a 44-char base64 string decoding to 32 bytes."""
    setup = _setup()
    key = setup.generate_master_key()
    assert isinstance(key, str) and len(key) == 44
    assert len(base64.b64decode(key)) == 32
    assert setup.generate_master_key() != key  # randomised per call


def test_env_templating():
    """render_env() replaces existing keys, appends new ones, preserves comments."""
    setup = _setup()
    template = (
        "# comment line\n"
        "DATABASE_URL=postgresql://old\n"
        "MASTER_KEY=your-key\n"
        "\n"
        "LLM_PROVIDER=gemini  # inline note\n"
    )
    out = setup.render_env(template, {"MASTER_KEY": "REALKEY", "OPENAI_API_KEY": "sk-x"})
    lines = out.splitlines()
    assert "# comment line" in lines
    assert "MASTER_KEY=REALKEY" in lines
    assert "DATABASE_URL=postgresql://old" in lines  # untouched
    assert any(l.startswith("OPENAI_API_KEY=sk-x") for l in lines)  # appended
    assert any(l.startswith("LLM_PROVIDER=") for l in lines)


def test_config_scaffold(tmp_path):
    """scaffold_config_files() copies *_example.json -> active json, idempotently."""
    setup = _setup()
    (tmp_path / "server_config_example.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "tools_api_config_example.json").write_text('{"b": 2}', encoding="utf-8")

    created = setup.scaffold_config_files(tmp_path)
    assert (tmp_path / "server_config.json").exists()
    assert (tmp_path / "tools_api_config.json").exists()
    assert json.loads((tmp_path / "server_config.json").read_text()) == {"a": 1}
    assert set(created) >= {"server_config.json", "tools_api_config.json"}

    # idempotent: a second run does not re-create existing files
    again = setup.scaffold_config_files(tmp_path)
    assert "server_config.json" not in again


# --------------------------------------------------------------------------- #
# MTU-4: LLM pool plan                                                         #
# --------------------------------------------------------------------------- #
# LLM preset build tests removed as LLM is configured exclusively within the TUI.


# --------------------------------------------------------------------------- #
# MTU-5: plugin discovery + boot list                                         #
# --------------------------------------------------------------------------- #
def test_plugin_list_parse():
    """list_available_plugins() finds real plugins under plugins/ with name + tier."""
    setup = _setup()
    plugins = setup.list_available_plugins()
    assert isinstance(plugins, list) and plugins
    names = {p["name"] for p in plugins}
    assert "portfolio_plugin" in names
    for p in plugins:
        assert "name" in p and "tier" in p


def test_boot_list_write(tmp_path):
    """write_plugin_boot_list() writes {tier, plugins:[...]} with normalised packages."""
    setup = _setup()
    cfg = tmp_path / "plugin_config.json"
    setup.write_plugin_boot_list(["portfolio_plugin", "plugins.job_search_plugin"], 100, cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["tier"] == 100
    assert "plugins.portfolio_plugin" in data["plugins"]
    assert "plugins.job_search_plugin" in data["plugins"]


# --------------------------------------------------------------------------- #
# MTU-7: admin password importable + validating                               #
# --------------------------------------------------------------------------- #
def test_set_admin_password_importable():
    """set_admin_password is an importable coroutine that rejects empty inputs."""
    mod = _load_module("terminal/script/set_admin_password.py", "whiskers_set_admin_under_test")
    fn = mod.set_admin_password
    assert inspect.iscoroutinefunction(fn)
    assert inspect.iscoroutinefunction(mod.admin_account_exists)
    with pytest.raises(ValueError):
        asyncio.run(fn(""))
    with pytest.raises(ValueError):
        asyncio.run(fn("some_pass", ""))


# --------------------------------------------------------------------------- #
# MTU-9: argparse + orchestration plan                                         #
# --------------------------------------------------------------------------- #
def test_argparse_subcommands():
    """build_parser() exposes the subcommands and shared flags."""
    setup = _setup()
    parser = setup.build_parser()

    ns = parser.parse_args(["doctor"])
    assert ns.command == "doctor"

    ns = parser.parse_args(["all", "-y", "--provider", "gemini"])
    assert ns.command == "all"
    assert ns.yes is True
    assert ns.provider == "gemini"
    ns = parser.parse_args(["all", "--from", "db", "--force", "--reset-plugins"])
    assert ns.from_step == "db"
    assert ns.force is True
    assert ns.reset_plugins is True
    assert not hasattr(ns, "config")


def test_all_dryrun():
    """plan_steps() returns the full ordered pipeline for the 'all' command."""
    setup = _setup()
    steps = setup.plan_steps("all")
    assert steps == [
        "doctor",
        "env",
        "llm",
        "db",
        "migrate",
        "plugin_migrate",
        "admin",
        "plugins",
        "up",
        "health",
        "tui",
    ]


# --------------------------------------------------------------------------- #
# Code-review fixes (PR #84): regression guards                                #
# --------------------------------------------------------------------------- #
def test_env_reloads_into_environ(tmp_path, monkeypatch):
    """run_env() loads freshly written .env values into os.environ for later steps."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    (tmp_path / ".env_sample").write_text(
        "MASTER_KEY=your-key\nSETUP_CLI_RELOAD_PROBE=loaded\n", encoding="utf-8"
    )
    monkeypatch.delenv("SETUP_CLI_RELOAD_PROBE", raising=False)

    rc = setup.run_env()
    assert rc == 0
    # The value written into the new .env must be visible to subsequent steps.
    assert os.environ.get("SETUP_CLI_RELOAD_PROBE") == "loaded"


def test_admin_non_interactive_generates_password(monkeypatch, capsys):
    """run_admin() mints a one-time credential in -y mode when ADMIN_PASSWORD is unset."""
    setup = _setup()
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(
        "getpass.getpass",
        lambda *a, **k: pytest.fail("must not prompt in non-interactive mode"),
    )

    created = []

    async def _no_admin():
        return False

    async def _remember(*args, **kwargs):
        created.append((args, kwargs))

    fake_mod = types.SimpleNamespace(admin_account_exists=_no_admin)
    setattr(fake_mod, "set_admin_password", _remember)
    monkeypatch.setattr(setup, "_load_set_admin_password_module", lambda: fake_mod)

    args = setup.build_parser().parse_args(["admin", "-y"])
    assert setup.run_admin(args) == 0
    assert len(created) == 1
    call_args, call_kwargs = created[0]
    minted = call_args[0] if call_args else call_kwargs.get("password")
    user = call_args[1] if len(call_args) > 1 else call_kwargs.get("username", "admin")
    assert user == "admin"
    assert isinstance(minted, str) and len(minted) >= 16
    out = capsys.readouterr().out
    assert "SAVE THIS NOW" in out
    assert minted in out


def test_admin_skips_when_account_exists(monkeypatch, capsys):
    """run_admin() does not create credentials when vault already has an admin."""
    setup = _setup()
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setattr(
        "getpass.getpass",
        lambda *a, **k: pytest.fail("must not prompt when admin already exists"),
    )
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("must not prompt"))

    created = []

    async def _exists():
        return True

    async def _set(*a, **k):
        created.append((a, k))

    fake_mod = type("M", (), {})()
    fake_mod.admin_account_exists = _exists
    fake_mod.set_admin_password = _set
    monkeypatch.setattr(setup, "_load_set_admin_password_module", lambda: fake_mod)

    args = setup.build_parser().parse_args(["admin"])
    assert setup.run_admin(args) == 0
    assert created == []
    out = capsys.readouterr().out
    assert "already exists" in out


def test_admin_non_interactive_skips_without_password_when_exists(monkeypatch):
    """-y mode does not require ADMIN_PASSWORD when an admin already exists."""
    setup = _setup()
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    created = []

    async def _exists():
        return True

    async def _set(*a, **k):
        created.append(True)

    fake_mod = type("M", (), {})()
    fake_mod.admin_account_exists = _exists
    fake_mod.set_admin_password = _set
    monkeypatch.setattr(setup, "_load_set_admin_password_module", lambda: fake_mod)

    args = setup.build_parser().parse_args(["admin", "-y"])
    assert setup.run_admin(args) == 0
    assert created == []


def test_run_tui_skipped_non_interactive(capsys):
    """run_tui() is a no-op under -y (TUI is interactive-only)."""
    setup = _setup()
    args = setup.build_parser().parse_args(["tui", "-y"])
    assert setup.run_tui(args) == 0
    out = capsys.readouterr().out
    assert "skipped tui" in out
    assert "setup_tui" in out or "python -m terminal" in out


def test_run_tui_launches_main(monkeypatch):
    """run_tui() calls the setup TUI main() when interactive."""
    setup = _setup()
    called = []

    fake_pkg = types.ModuleType("terminal.tui.setup_tui")
    fake_pkg.main = lambda: called.append(True)
    if "terminal" not in sys.modules:
        monkeypatch.setitem(sys.modules, "terminal", types.ModuleType("terminal"))
    if "terminal.tui" not in sys.modules:
        monkeypatch.setitem(sys.modules, "terminal.tui", types.ModuleType("terminal.tui"))
    monkeypatch.setitem(sys.modules, "terminal.tui.setup_tui", fake_pkg)

    args = setup.build_parser().parse_args(["tui"])
    assert setup.run_tui(args) == 0
    assert called == [True]


def test_doctor_requires_docker_compose(monkeypatch):
    """run_doctor() fails when docker compose is unavailable, even if docker is present."""
    setup = _setup()
    fake = {
        "python": {"ok": True, "detail": "3.11"},
        "docker": {"ok": True, "detail": "/usr/bin/docker"},
        "docker_compose": {"ok": False, "detail": "not found"},
    }
    monkeypatch.setattr(setup, "check_prerequisites", lambda: fake)
    assert setup.run_doctor() == 1


def test_health_uses_stdlib_http():
    """check_mcp_endpoint() relies on stdlib urllib, not third-party requests."""
    setup = _setup()
    src = inspect.getsource(setup.check_mcp_endpoint)
    assert "urllib" in src
    assert "import requests" not in src
    assert "requests." not in src


# --------------------------------------------------------------------------- #
# Code-review fixes (PR #85): regression guards                                #
# --------------------------------------------------------------------------- #
def test_mirror_plugin_active_upserts(monkeypatch):
    """mirror_plugin_active() ensures a row exists (register) before set_active.

    set_active is UPDATE-only; on a fresh DB the plugins table is empty, so
    without a preceding insert the disable would be a silent no-op.
    """
    setup = _setup()
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    calls = []

    class FakeRegistry:
        async def register(self, manifest):
            calls.append(("register", manifest["name"]))

        async def set_active(self, name, is_active):
            calls.append(("set_active", name, is_active))

    result = asyncio.run(
        setup.mirror_plugin_active(["a"], ["a", "b"], registry=FakeRegistry())
    )

    # Every plugin must get a row ensured before its active flag is written.
    registered = [c[1] for c in calls if c[0] == "register"]
    assert registered == ["a", "b"]
    assert ("set_active", "a", True) in calls
    assert ("set_active", "b", False) in calls
    assert result["enabled"] == ["a"]
    assert result["disabled"] == ["b"]


def test_run_db_forwards_dev_flag(monkeypatch):
    """run_db() forwards the --dev flag to docker_compose_up for postgres overrides."""
    setup = _setup()
    captured = {}

    def fake_up(services, detached=True, build=False, dev=False):
        captured["services"] = services
        captured["dev"] = dev
        return 0

    monkeypatch.setattr(setup, "docker_compose_up", fake_up)
    monkeypatch.setattr(setup, "wait_for_postgres", lambda *a, **k: True)
    monkeypatch.setattr(setup, "run_migrations", lambda *a, **k: 0)

    args = setup.build_parser().parse_args(["db", "--dev"])
    assert setup.run_db(args) == 0
    assert captured["services"] == []
    assert captured["dev"] is True


def test_run_migrate_subcommand(monkeypatch):
    """run_migrate() subcommand calls run_migrations and returns its status."""
    setup = _setup()
    called = []
    monkeypatch.setattr(setup, "run_migrations", lambda: (called.append(True) or 0))

    args = setup.build_parser().parse_args(["migrate"])
    assert setup.run_migrate(args) == 0
    assert called == [True]


def test_env_templating_whitespace():
    """render_env() updates keys written with spaces around '=' in place (no duplicate)."""
    setup = _setup()
    template = "KEY = old\nOTHER=keep\n"
    out = setup.render_env(template, {"KEY": "new"})
    lines = out.splitlines()
    assert "KEY=new" in lines
    assert "KEY = old" not in lines  # updated in place, not appended
    assert sum(1 for l in lines if l.replace(" ", "").startswith("KEY=")) == 1


def test_health_check_survives_transient_errors(monkeypatch):
    """check_mcp_endpoint() does not crash on non-URLError transient socket errors."""
    setup = _setup()
    import urllib.request

    def boom(*a, **k):
        raise ConnectionResetError("peer reset during startup")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(setup.time, "sleep", lambda *_: None)

    # Must swallow the transient error and return False after the deadline.
    assert setup.check_mcp_endpoint(port=59999, timeout_s=0.05) is False


def test_run_restart_sdk_missing(capsys):
    """run_restart() returns 1 if docker SDK is missing."""
    setup = _setup()

    from unittest.mock import patch
    with patch.dict("sys.modules", {"docker": None}):
        rc = setup.run_restart()
        assert rc == 1
        captured = capsys.readouterr()
        assert "docker SDK not installed" in captured.out


def test_run_restart_success(capsys):
    """run_restart() restarts the container and returns 0 on success."""
    setup = _setup()

    from unittest.mock import MagicMock, patch

    class MockDockerException(Exception):
        pass
    class MockNotFound(MockDockerException):
        pass

    mock_errors = MagicMock()
    mock_errors.NotFound = MockNotFound
    mock_errors.DockerException = MockDockerException

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container

    mock_docker = MagicMock()
    mock_docker.from_env.return_value = mock_client

    mock_modules = {
        "docker": mock_docker,
        "docker.errors": mock_errors,
    }

    with patch.dict("sys.modules", mock_modules):
        rc = setup.run_restart()
        assert rc == 0
        mock_client.containers.get.assert_called_once_with("whiskers-agent-server")
        mock_container.restart.assert_called_once_with(timeout=10)
        captured = capsys.readouterr()
        assert "Restarting whiskers-agent-server ..." in captured.out
        assert "Restarted whiskers-agent-server." in captured.out


def test_run_restart_not_found(capsys):
    """run_restart() prints container not found and returns 1 when container isn't found."""
    setup = _setup()

    from unittest.mock import MagicMock, patch

    class MockDockerException(Exception):
        pass
    class MockNotFound(MockDockerException):
        pass

    mock_errors = MagicMock()
    mock_errors.NotFound = MockNotFound
    mock_errors.DockerException = MockDockerException

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = MockNotFound("container not found")

    mock_docker = MagicMock()
    mock_docker.from_env.return_value = mock_client

    mock_modules = {
        "docker": mock_docker,
        "docker.errors": mock_errors,
    }

    with patch.dict("sys.modules", mock_modules):
        rc = setup.run_restart()
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: container 'whiskers-agent-server' not found." in captured.out


def test_run_restart_docker_exception(capsys):
    """run_restart() returns 1 when docker daemon is unreachable."""
    setup = _setup()

    from unittest.mock import MagicMock, patch

    class MockDockerException(Exception):
        pass
    class MockNotFound(MockDockerException):
        pass

    mock_errors = MagicMock()
    mock_errors.NotFound = MockNotFound
    mock_errors.DockerException = MockDockerException

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = MockDockerException("daemon unreachable")

    mock_modules = {
        "docker": mock_docker,
        "docker.errors": mock_errors,
    }

    with patch.dict("sys.modules", mock_modules):
        rc = setup.run_restart()
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: could not reach Docker daemon: daemon unreachable" in captured.out


def test_run_plugins_preserves_existing(tmp_path, monkeypatch):
    """run_plugins() does not clobber an existing plugin_config.json."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "plugin_config.json"
    cfg.write_text(
        json.dumps({"tier": 50, "plugins": ["plugins.search_plugin"]}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "list_available_plugins", lambda: [{"name": "search_plugin", "tier": 1, "requires": []}])
    args = setup.build_parser().parse_args(["plugins", "-y"])
    assert setup.run_plugins(args) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["plugins"] == ["plugins.search_plugin"]
    assert data["tier"] == 50


def test_run_plugins_reset(tmp_path, monkeypatch):
    """--reset-plugins overwrites the boot list with recommended defaults."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = tmp_path / "config" / "plugin_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"tier": 1, "plugins": ["plugins.search_plugin"]}), encoding="utf-8")
    monkeypatch.setattr(setup, "list_available_plugins", lambda: [])
    args = setup.build_parser().parse_args(["plugins", "-y", "--reset-plugins"])
    assert setup.run_plugins(args) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "plugins.portfolio_plugin" in data["plugins"]


def test_run_llm_writes_provider(tmp_path, monkeypatch):
    """run_llm() writes LLM_PROVIDER and the matching key into .env."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    (tmp_path / ".env").write_text("LLM_PROVIDER=gemini\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    args = setup.build_parser().parse_args(["llm", "-y", "--provider", "openai"])
    assert setup.run_llm(args) == 0
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER=openai" in text
    assert "OPENAI_API_KEY=test-openai-key" in text


def test_run_all_dry_run_resume(tmp_path, monkeypatch, capsys):
    """--dry-run shows skipped-already-ok steps from the state file."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    state_dir = tmp_path / ".whiskers"
    state_dir.mkdir()
    (state_dir / "setup-state.json").write_text(
        json.dumps({"version": 1, "steps": {"env": {"status": "ok", "started": "", "duration": 1, "error": None}}}),
        encoding="utf-8",
    )
    args = setup.build_parser().parse_args(["all", "--dry-run"])
    assert setup.run_all(args) == 0
    out = capsys.readouterr().out
    assert "env  (skip: already ok)" in out
    assert "plugin_migrate" in out


def test_run_all_from_step(tmp_path, monkeypatch, capsys):
    """--from skips earlier steps even when they are not ok."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    args = setup.build_parser().parse_args(["all", "--dry-run", "--from", "db"])
    assert setup.run_all(args) == 0
    out = capsys.readouterr().out
    assert "doctor  (skip: --from db)" in out
    assert "    db\n" in out or "    db" in out


def test_run_all_skips_db_nonzero(tmp_path, monkeypatch, capsys):
    """Missing DATABASE_URL after env is a failed pipeline, not a silent success."""
    setup = _setup()
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text("MASTER_KEY=not-a-placeholder-key-value\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "plugin_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup, "run_doctor", lambda: 0)
    monkeypatch.setattr(setup, "run_env", lambda a=None: 0)
    monkeypatch.setattr(setup, "run_llm", lambda a=None: 0)
    monkeypatch.setattr(setup, "run_plugins", lambda a=None: 0)
    monkeypatch.setattr(setup, "run_up", lambda a=None: 0)
    monkeypatch.setattr(setup, "run_health", lambda a=None: 0)
    args = setup.build_parser().parse_args(["all", "-y"])
    rc = setup.run_all(args)
    assert rc != 0
    out = capsys.readouterr().out
    assert "DATABASE_URL not set" in out
    assert "setup summary" in out


def test_check_prerequisites_shape():
    """New doctor keys keep the {ok, detail} contract."""
    setup = _setup()
    checks = setup.check_prerequisites()
    for key in ("docker_daemon", "plugin_config", "master_key", "db_reachable", "port_mcp"):
        assert key in checks
        assert isinstance(checks[key]["ok"], bool)
        assert "detail" in checks[key]



def test_check_prerequisites_port_collision_reports_not_ok(monkeypatch):
    """A port that already answers is a collision doctor should flag, not silently pass."""
    import socket

    setup = _setup()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    busy_port = listener.getsockname()[1]
    try:
        monkeypatch.setenv("MCP_PORT", str(busy_port))
        checks = setup.check_prerequisites()
        assert checks["port_mcp"]["ok"] is False
        assert "in use" in checks["port_mcp"]["detail"]
    finally:
        listener.close()


def test_from_step_rejects_unknown_value():
    """--from with a typo/unknown step must fail argparse, not silently run the full pipeline."""
    setup = _setup()
    parser = setup.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["all", "--from", "dbb"])
