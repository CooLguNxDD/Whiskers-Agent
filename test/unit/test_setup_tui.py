from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from pathlib import Path
import json
import importlib.util

ROOT = Path(__file__).resolve().parents[2]

def _load_module(rel_path: str, name: str):
    """Load a repo script by file path, bypassing package import."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_edit_json_file_handles_missing_file():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()
    path = Path("nonexistent_file_xyz.json")
    
    # It should print a red error and return None without raising
    setup_tui._edit_json_file(console, "TestLabel", path)
    
    # Verify console.print was called with red error message
    assert console.print.called
    args = console.print.call_args[0]
    assert "[red]Failed to load config" in args[0]

def test_edit_json_file_handles_malformed_json(tmp_path):
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()
    path = tmp_path / "malformed.json"
    path.write_text("{invalid json", encoding="utf-8")
    
    # It should print a red error and return None without raising
    setup_tui._edit_json_file(console, "TestLabel", path)
    
    # Verify console.print was called with red error message
    assert console.print.called
    args = console.print.call_args[0]
    assert "[red]Failed to load config" in args[0]


def test_screen_tenants_db_unavailable(monkeypatch):
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    monkeypatch.setattr(setup_tui, "_db_available", lambda: False)

    setup_tui.screen_tenants(console)

    assert console.print.called
    args = console.print.call_args[0]
    assert "DATABASE_URL must be set for tenant management." in args[0]


def test_prompt_password_confirm_success():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_ask:
        mock_ask.side_effect = ["mysecretpass", "mysecretpass"]
        pwd = setup_tui._prompt_password_confirm(console)
        assert pwd == "mysecretpass"
        assert mock_ask.call_count == 2


def test_prompt_password_confirm_mismatch():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_ask:
        # 3 attempts * 2 prompts/attempt = 6 calls
        mock_ask.side_effect = [
            "pass1", "pass2",
            "pass3", "pass4",
            "pass5", "pass6"
        ]
        pwd = setup_tui._prompt_password_confirm(console)
        assert pwd is None
        assert mock_ask.call_count == 6


def test_discover_plugins_local_normalizes_object_credentials(tmp_path, monkeypatch):
    """Object-form optional_credentials must become plain key strings (portfolio_plugin)."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    plugins_dir = tmp_path / "plugins" / "portfolio_plugin"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "manifest.json").write_text(
        json.dumps({
            "name": "portfolio_plugin",
            "optional_credentials": [
                {"key": "GITHUB_TOKEN", "description": "GitHub PAT", "required": False},
                {"key": "NOTION_API_KEY", "description": "Notion secret"},
            ],
            "required_credentials": ["LEGACY_KEY"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_tui, "_REPO", tmp_path)
    found = setup_tui._discover_plugins_local()
    assert len(found) == 1
    assert found[0]["id"] == "portfolio_plugin"
    assert found[0]["required"] == ["LEGACY_KEY"]
    assert found[0]["optional"] == ["GITHUB_TOKEN", "NOTION_API_KEY"]
    # Must be hashable strings so vault status `k in stored_set` never TypeErrors.
    assert all(isinstance(k, str) for k in found[0]["required"] + found[0]["optional"])


@pytest.mark.asyncio
async def test_create_tenant_flow_happy_path():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_prompt_ask, \
         patch.object(setup_tui.Confirm, "ask") as mock_confirm_ask, \
         patch.object(setup_tui, "_prompt_password_confirm") as mock_pwd_confirm, \
         patch("core.user_management.create_tenant", new_callable=AsyncMock) as mock_create_tenant, \
         patch("core.user_management.create_user", new_callable=AsyncMock) as mock_create_user:

        mock_prompt_ask.side_effect = ["My Tenant", "my-tenant", "admin_user"]
        mock_pwd_confirm.return_value = "adminpass"
        mock_confirm_ask.return_value = True
        mock_create_tenant.return_value = {"id": 42, "name": "My Tenant", "slug": "my-tenant"}
        mock_create_user.return_value = {"id": 100, "username": "admin_user", "role": "master", "tenant_id": 42}

        await setup_tui._create_tenant_flow(console)

        mock_create_tenant.assert_called_once_with("My Tenant", "my-tenant")
        mock_create_user.assert_called_once_with("admin_user", "adminpass", role="master", tenant_id=42)
        assert any("created with master user" in call_args[0][0] for call_args in console.print.call_args_list)


@pytest.mark.asyncio
async def test_create_tenant_flow_create_tenant_error():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_prompt_ask, \
         patch.object(setup_tui.Confirm, "ask") as mock_confirm_ask, \
         patch.object(setup_tui, "_prompt_password_confirm") as mock_pwd_confirm, \
         patch("core.user_management.create_tenant", new_callable=AsyncMock) as mock_create_tenant, \
         patch("core.user_management.create_user", new_callable=AsyncMock) as mock_create_user:

        mock_prompt_ask.side_effect = ["My Tenant", "my-tenant", "admin_user"]
        mock_pwd_confirm.return_value = "adminpass"
        mock_confirm_ask.return_value = True
        mock_create_tenant.side_effect = ValueError("Tenant with name 'My Tenant' already exists.")

        await setup_tui._create_tenant_flow(console)

        mock_create_tenant.assert_called_once_with("My Tenant", "my-tenant")
        mock_create_user.assert_not_called()
        assert any("Tenant with name 'My Tenant' already exists." in call_args[0][0] for call_args in console.print.call_args_list)


def test_screen_restart_confirmed():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Confirm, "ask", return_value=True) as mock_ask, \
         patch.object(setup_tui._setup, "run_restart", return_value=0) as mock_run_restart:
        
        setup_tui.screen_restart(console)

        mock_ask.assert_called_once()
        mock_run_restart.assert_called_once()
        printed = [call_args[0][0] for call_args in console.print.call_args_list]
        assert any("[green]✓ Restart triggered.[/green]" in text for text in printed)


def test_screen_restart_declined():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Confirm, "ask", return_value=False) as mock_ask, \
         patch.object(setup_tui._setup, "run_restart") as mock_run_restart:
        
        setup_tui.screen_restart(console)

        mock_ask.assert_called_once()
        mock_run_restart.assert_not_called()
        printed = [call_args[0][0] for call_args in console.print.call_args_list]
        assert any("[dim]Cancelled.[/dim]" in text for text in printed)


@pytest.mark.asyncio
async def test_create_tenant_flow_user_retry_success():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_prompt_ask, \
         patch.object(setup_tui.Confirm, "ask") as mock_confirm_ask, \
         patch.object(setup_tui, "_prompt_password_confirm") as mock_pwd_confirm, \
         patch("core.user_management.create_tenant", new_callable=AsyncMock) as mock_create_tenant, \
         patch("core.user_management.create_user", new_callable=AsyncMock) as mock_create_user:

        mock_prompt_ask.side_effect = ["My Tenant", "my-tenant", "admin_user", "admin_user_retry"]
        mock_pwd_confirm.side_effect = ["adminpass", "adminpass_retry"]
        mock_confirm_ask.return_value = True
        mock_create_tenant.return_value = {"id": 42, "name": "My Tenant", "slug": "my-tenant"}
        mock_create_user.side_effect = [
            ValueError("Username 'admin_user' already exists."),
            {"id": 100, "username": "admin_user_retry", "role": "master", "tenant_id": 42}
        ]

        await setup_tui._create_tenant_flow(console)

        mock_create_tenant.assert_called_once_with("My Tenant", "my-tenant")
        assert mock_create_user.call_count == 2
        mock_create_user.assert_any_call("admin_user", "adminpass", role="master", tenant_id=42)
        mock_create_user.assert_any_call("admin_user_retry", "adminpass_retry", role="master", tenant_id=42)
        
        printed = [call_args[0][0] for call_args in console.print.call_args_list]
        assert any("created with master user" in text for text in printed)


@pytest.mark.asyncio
async def test_create_tenant_flow_user_retry_all_fail():
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    with patch.object(setup_tui.Prompt, "ask") as mock_prompt_ask, \
         patch.object(setup_tui.Confirm, "ask") as mock_confirm_ask, \
         patch.object(setup_tui, "_prompt_password_confirm") as mock_pwd_confirm, \
         patch("core.user_management.create_tenant", new_callable=AsyncMock) as mock_create_tenant, \
         patch("core.user_management.create_user", new_callable=AsyncMock) as mock_create_user:

        mock_prompt_ask.side_effect = ["My Tenant", "my-tenant", "admin_user", "admin_user_retry1", "admin_user_retry2"]
        mock_pwd_confirm.side_effect = ["adminpass", "adminpass_retry1", "adminpass_retry2"]
        mock_confirm_ask.return_value = True
        mock_create_tenant.return_value = {"id": 42, "name": "My Tenant", "slug": "my-tenant"}
        mock_create_user.side_effect = ValueError("Username already exists.")

        await setup_tui._create_tenant_flow(console)

        mock_create_tenant.assert_called_once_with("My Tenant", "my-tenant")
        assert mock_create_user.call_count == 3
        
        printed = [call_args[0][0] for call_args in console.print.call_args_list]
        assert any("master user creation failed after multiple attempts" in text for text in printed)
        assert any("Please retry adding a user" in text for text in printed)


def test_screen_override_routes_db_unavailable(monkeypatch):
    """Verify route override prints error when database is unavailable."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    monkeypatch.setattr(setup_tui, "_db_available", lambda: False)

    setup_tui.screen_override_routes(console)

    assert console.print.called
    args = console.print.call_args[0]
    assert "DATABASE_URL must be set to run route overrides." in args[0]


def test_screen_override_routes_declined(monkeypatch):
    """Verify route override cancels when user declines confirmation."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    monkeypatch.setattr(setup_tui, "_db_available", lambda: True)

    with patch.object(setup_tui.Confirm, "ask", return_value=False) as mock_ask, \
         patch("subprocess.run") as mock_run:
        
        setup_tui.screen_override_routes(console)

        mock_ask.assert_called_once()
        mock_run.assert_not_called()
        printed = [call_args[0][0] for call_args in console.print.call_args_list if call_args[0]]
        assert any("[dim]Cancelled.[/dim]" in text for text in printed)


def test_screen_override_routes_success(monkeypatch):
    """Verify route override script executes successfully when confirmed."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    monkeypatch.setattr(setup_tui, "_db_available", lambda: True)

    with patch.object(setup_tui.Confirm, "ask", return_value=True) as mock_ask, \
         patch("subprocess.run") as mock_run:
        
        mock_run.return_value = MagicMock(returncode=0)
        
        setup_tui.screen_override_routes(console)

        mock_ask.assert_called_once()
        mock_run.assert_called_once()
        printed = [call_args[0][0] for call_args in console.print.call_args_list if call_args[0]]
        assert any("Route override completed successfully." in text for text in printed)


def test_screen_override_routes_failure(monkeypatch):
    """Verify route override script handles non-zero exit codes."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    monkeypatch.setattr(setup_tui, "_db_available", lambda: True)

    with patch.object(setup_tui.Confirm, "ask", return_value=True) as mock_ask, \
         patch("subprocess.run") as mock_run:
        
        mock_run.return_value = MagicMock(returncode=1)
        
        setup_tui.screen_override_routes(console)

        mock_ask.assert_called_once()
        mock_run.assert_called_once()
        printed = [call_args[0][0] for call_args in console.print.call_args_list if call_args[0]]
        assert any("Route override failed with exit code 1." in text for text in printed)


def test_screen_guided_setup_single_step_not_forced_yes(monkeypatch):
    """Picking one step (e.g. admin) from Guided Setup must NOT force -y.

    Regression test: args.yes used to be set True unconditionally, which made
    picking 'admin' interactively silently mint and rotate a new admin
    password instead of prompting, and made 'llm' a silent no-op.
    """
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    captured = {}

    def _fake_run_admin(args):
        captured["args"] = args
        return 0

    with patch.object(setup_tui, "menu", side_effect=["7", "b"]), \
         patch.object(setup_tui._setup, "run_admin", side_effect=_fake_run_admin):
        setup_tui.screen_guided_setup(console)

    assert captured["args"].yes is False


def test_screen_guided_setup_run_all_forces_yes(monkeypatch):
    """Choosing 'Run all steps' still forces -y so the pipeline's tui step is skipped."""
    setup_tui = _load_module("terminal/tui/setup_tui.py", "setup_tui")
    console = MagicMock()

    captured = {}

    def _fake_run_all(args):
        captured["args"] = args
        return 0

    with patch.object(setup_tui, "menu", side_effect=["a", "b"]), \
         patch.object(setup_tui._setup, "run_all", side_effect=_fake_run_all):
        setup_tui.screen_guided_setup(console)

    assert captured["args"].yes is True
    assert captured["args"].force is True
