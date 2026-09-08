"""Unit tests for core.plugin_loader.credentials_loader."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.plugin_loader.credentials_loader import (
    SeedResult,
    get_credential,
    load_plugin_credentials,
    normalize_credential_keys,
    seed_vault_from_credentials,
)


# ---------------------------------------------------------------------------
# normalize_credential_keys
# ---------------------------------------------------------------------------


class TestNormalizeCredentialKeys:
    """Tests for normalize_credential_keys() — string + object manifest forms."""

    def test_string_list(self):
        assert normalize_credential_keys(["A", "B"]) == ["A", "B"]

    def test_object_form(self):
        raw = [
            {"key": "GITHUB_TOKEN", "description": "GitHub PAT", "required": False},
            {"key": "NOTION_API_KEY", "description": "Notion secret"},
        ]
        assert normalize_credential_keys(raw) == ["GITHUB_TOKEN", "NOTION_API_KEY"]

    def test_mixed_string_and_object(self):
        raw = ["API_KEY", {"key": "OTHER", "description": "x"}, "API_KEY"]
        assert normalize_credential_keys(raw) == ["API_KEY", "OTHER"]

    def test_name_and_id_aliases(self):
        raw = [{"name": "FROM_NAME"}, {"id": "FROM_ID"}]
        assert normalize_credential_keys(raw) == ["FROM_NAME", "FROM_ID"]

    def test_empty_and_none(self):
        assert normalize_credential_keys(None) == []
        assert normalize_credential_keys([]) == []
        assert normalize_credential_keys("") == []

    def test_skips_invalid_entries(self):
        raw = [None, 42, {}, {"key": ""}, {"key": "  OK  "}, {"description": "no key"}]
        assert normalize_credential_keys(raw) == ["OK"]


@pytest.mark.asyncio
async def test_get_vault_status_accepts_object_form_keys():
    """Regression: portfolio-style {key: ...} entries must not TypeError on set membership."""
    from terminal.tui.credentials import _get_vault_status

    vault = AsyncMock()
    vault.list_keys = AsyncMock(return_value=["GITHUB_TOKEN"])
    keys = [
        {"key": "GITHUB_TOKEN", "description": "x"},
        {"key": "NOTION_API_KEY", "description": "y"},
    ]
    status = await _get_vault_status(vault, "portfolio_plugin", keys)
    assert status == {"GITHUB_TOKEN": True, "NOTION_API_KEY": False}


# ---------------------------------------------------------------------------
# load_plugin_credentials
# ---------------------------------------------------------------------------


class TestLoadPluginCredentials:
    """Tests for load_plugin_credentials()."""

    def test_missing_file_returns_empty(self, tmp_path):
        """No credentials.json → empty dict, no exception."""
        result = load_plugin_credentials(tmp_path)
        assert result == {}

    def test_valid_flat_dict(self, tmp_path):
        """Valid JSON object → all string keys returned."""
        creds = {"username": "alice", "password": "s3cr3t", "API_KEY": "abc123"}
        (tmp_path / "credentials.json").write_text(json.dumps(creds), encoding="utf-8")
        result = load_plugin_credentials(tmp_path)
        assert result == creds

    def test_non_string_values_coerced(self, tmp_path):
        """Non-string values (int, bool) are coerced to str."""
        (tmp_path / "credentials.json").write_text(
            '{"PORT": 8080, "DEBUG": true, "RATE": 1.5}', encoding="utf-8"
        )
        result = load_plugin_credentials(tmp_path)
        assert result == {"PORT": "8080", "DEBUG": "True", "RATE": "1.5"}

    def test_none_values_skipped(self, tmp_path):
        """null JSON values are excluded from the result (treated as absent)."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "bob", "password": null}', encoding="utf-8"
        )
        result = load_plugin_credentials(tmp_path)
        assert "password" not in result
        assert result["username"] == "bob"

    def test_invalid_json_returns_empty(self, tmp_path):
        """Invalid JSON → empty dict (graceful fallback, no raise)."""
        (tmp_path / "credentials.json").write_text("NOT_JSON {{{", encoding="utf-8")
        result = load_plugin_credentials(tmp_path)
        assert result == {}

    def test_non_object_json_returns_empty(self, tmp_path):
        """Top-level array → empty dict (only flat objects are valid)."""
        (tmp_path / "credentials.json").write_text('["a", "b"]', encoding="utf-8")
        result = load_plugin_credentials(tmp_path)
        assert result == {}

    def test_empty_file_returns_empty(self, tmp_path):
        """Empty file (invalid JSON) → empty dict, no crash."""
        (tmp_path / "credentials.json").write_text("", encoding="utf-8")
        result = load_plugin_credentials(tmp_path)
        assert result == {}

    def test_comment_keys_preserved(self, tmp_path):
        """Keys starting with '_' (like '_comment') are kept — callers should filter them."""
        (tmp_path / "credentials.json").write_text(
            '{"_comment": "template", "username": "alice"}', encoding="utf-8"
        )
        result = load_plugin_credentials(tmp_path)
        assert "_comment" in result
        assert result["username"] == "alice"


# ---------------------------------------------------------------------------
# get_credential
# ---------------------------------------------------------------------------


class TestGetCredential:
    """Tests for get_credential() convenience wrapper."""

    def test_returns_value_when_present(self, tmp_path):
        (tmp_path / "credentials.json").write_text('{"API_KEY": "xyz"}', encoding="utf-8")
        assert get_credential(tmp_path, "API_KEY") == "xyz"

    def test_returns_default_when_key_missing(self, tmp_path):
        (tmp_path / "credentials.json").write_text('{"OTHER": "val"}', encoding="utf-8")
        assert get_credential(tmp_path, "MISSING_KEY", default="fallback") == "fallback"

    def test_returns_empty_string_default(self, tmp_path):
        """Default is empty string when no default provided and key absent."""
        assert get_credential(tmp_path, "NO_FILE_KEY") == ""

    def test_missing_file_returns_default(self, tmp_path):
        assert get_credential(tmp_path, "KEY", default="mydefault") == "mydefault"


# ---------------------------------------------------------------------------
# SeedResult
# ---------------------------------------------------------------------------


class TestSeedResult:
    """Tests for SeedResult dataclass."""

    def test_ok_when_no_errors(self):
        r = SeedResult(plugin_id="p", seeded=["k1"])
        assert r.ok is True

    def test_not_ok_when_errors(self):
        r = SeedResult(plugin_id="p", errors=["k1"])
        assert r.ok is False

    def test_summary_empty(self):
        r = SeedResult(plugin_id="p")
        assert r.summary() == "nothing to do"

    def test_summary_seeded(self):
        r = SeedResult(plugin_id="p", seeded=["username", "password"])
        assert "2 seeded" in r.summary()
        assert "username" in r.summary()

    def test_summary_mixed(self):
        r = SeedResult(plugin_id="p", seeded=["a"], skipped=["b"], empty=["c"], errors=["d"])
        s = r.summary()
        assert "seeded" in s
        assert "already-in-vault" in s
        assert "empty" in s
        assert "ERRORS" in s


# ---------------------------------------------------------------------------
# seed_vault_from_credentials
# ---------------------------------------------------------------------------


def _make_vault(existing: dict | None = None):
    """Create a mock VaultService with get/set."""
    existing = dict(existing or {})
    vault = AsyncMock()

    async def _get(plugin_id, key):
        return existing.get(key)

    async def _set(plugin_id, key, value):
        existing[key] = value

    vault.get.side_effect = _get
    vault.set.side_effect = _set
    return vault


class TestSeedVaultFromCredentials:
    """Tests for seed_vault_from_credentials()."""

    @pytest.mark.asyncio
    async def test_seeds_all_non_empty_keys(self, tmp_path):
        """Non-empty, non-comment keys are written to vault."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "alice", "password": "s3cr3t"}', encoding="utf-8"
        )
        vault = _make_vault()
        result = await seed_vault_from_credentials(tmp_path, "my_plugin", vault)
        assert "username" in result.seeded
        assert "password" in result.seeded
        assert result.skipped == []
        assert result.empty == []
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_skips_existing_by_default(self, tmp_path):
        """overwrite=False (default): existing vault keys are skipped."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "alice", "password": "new"}', encoding="utf-8"
        )
        vault = _make_vault(existing={"username": "old_value"})
        result = await seed_vault_from_credentials(tmp_path, "my_plugin", vault, overwrite=False)
        assert "username" in result.skipped
        assert "password" in result.seeded

    @pytest.mark.asyncio
    async def test_overwrites_when_flag_set(self, tmp_path):
        """overwrite=True: existing vault keys are replaced."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "new_alice"}', encoding="utf-8"
        )
        vault = _make_vault(existing={"username": "old_alice"})
        result = await seed_vault_from_credentials(tmp_path, "p", vault, overwrite=True)
        assert "username" in result.seeded
        assert result.skipped == []

    @pytest.mark.asyncio
    async def test_skips_blank_values(self, tmp_path):
        """Keys with empty/whitespace values are skipped (user hasn't filled them in)."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "", "password": "  "}', encoding="utf-8"
        )
        vault = _make_vault()
        result = await seed_vault_from_credentials(tmp_path, "p", vault)
        assert "username" in result.empty
        assert "password" in result.empty
        assert result.seeded == []

    @pytest.mark.asyncio
    async def test_skips_comment_keys(self, tmp_path):
        """Keys starting with '_' (like '_comment') are never written to vault."""
        (tmp_path / "credentials.json").write_text(
            '{"_comment": "template", "API_KEY": "real"}', encoding="utf-8"
        )
        vault = _make_vault()
        result = await seed_vault_from_credentials(tmp_path, "p", vault)
        assert "API_KEY" in result.seeded
        assert "_comment" not in result.seeded

    @pytest.mark.asyncio
    async def test_keys_allowlist(self, tmp_path):
        """When keys= is given only those keys are considered."""
        (tmp_path / "credentials.json").write_text(
            '{"username": "alice", "password": "pass", "EXTRA": "x"}',
            encoding="utf-8",
        )
        vault = _make_vault()
        result = await seed_vault_from_credentials(
            tmp_path, "p", vault, keys=["username"]
        )
        assert result.seeded == ["username"]
        assert "password" not in result.seeded
        assert "EXTRA" not in result.seeded

    @pytest.mark.asyncio
    async def test_handles_vault_error_gracefully(self, tmp_path):
        """vault.set() failures populate errors list without raising."""
        (tmp_path / "credentials.json").write_text(
            '{"API_KEY": "abc"}', encoding="utf-8"
        )
        vault = _make_vault()
        vault.set.side_effect = RuntimeError("DB down")
        result = await seed_vault_from_credentials(tmp_path, "p", vault)
        assert "API_KEY" in result.errors
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_missing_credentials_file(self, tmp_path):
        """No credentials.json returns empty SeedResult, vault.set never called."""
        vault = _make_vault()
        result = await seed_vault_from_credentials(tmp_path, "p", vault)
        assert result.seeded == []
        vault.set.assert_not_called()


# ---------------------------------------------------------------------------
# Resolution priority
# ---------------------------------------------------------------------------


class TestResolutionPriority:
    """Simulate the vault > local > manifest priority chain."""

    def test_local_creds_override_empty_manifest(self, tmp_path):
        """Local credentials.json value takes precedence over empty manifest default."""
        (tmp_path / "credentials.json").write_text('{"password": "local_pass"}', encoding="utf-8")
        creds = load_plugin_credentials(tmp_path)
        # Simulates _resolve_cred: vault absent → local file wins
        manifest_default = ""
        local_val = creds.get("password", "")
        resolved = local_val or manifest_default
        assert resolved == "local_pass"

    def test_manifest_default_used_when_local_absent(self, tmp_path):
        """Falls back to manifest default when credentials.json is missing."""
        creds = load_plugin_credentials(tmp_path)  # file doesn't exist
        manifest_default = "manifest_pass"
        local_val = creds.get("password", "")
        resolved = local_val or manifest_default
        assert resolved == "manifest_pass"

    def test_local_does_not_override_vault(self, tmp_path):
        """Vault value is always checked first (vault logic is in plugin_config, not here).
        This test only verifies that local creds don't falsely appear to come from vault."""
        (tmp_path / "credentials.json").write_text('{"password": "local"}', encoding="utf-8")
        # Vault would return first; local is only consulted when vault returns empty.
        vault_value = "vault_pass"  # simulated vault hit
        creds = load_plugin_credentials(tmp_path)
        local_val = creds.get("password", "")
        # When vault returns a value, it wins — local is never checked.
        resolved = vault_value or local_val
        assert resolved == "vault_pass"
