"""
Unit tests for skill_file_store (disk load + sha256 validation helpers).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.plugin_loader.skill_file_store import (
    load_skill_from_disk,
    skill_content_hash,
    _safe_skill_key,
    _safe_plugin_id,
)


def test_skill_content_hash_stable():
    assert skill_content_hash("hello") == skill_content_hash("hello")
    assert skill_content_hash("hello").startswith("sha256:")
    assert skill_content_hash("hello") != skill_content_hash("hello!")
    assert skill_content_hash("") == skill_content_hash("")


def test_safe_plugin_id():
    assert _safe_plugin_id("portfolio_plugin") is True
    assert _safe_plugin_id("../etc") is False
    assert _safe_plugin_id("a/b") is False
    assert _safe_plugin_id("") is False


def test_safe_skill_key():
    assert _safe_skill_key("skills/live-layout-enrichment.md") is True
    assert _safe_skill_key("skills/wf/SKILL.md") is True
    assert _safe_skill_key("../secrets") is False
    assert _safe_skill_key("/etc/passwd") is False
    assert _safe_skill_key("") is False
    assert _safe_skill_key("skills/../../etc/passwd") is False


def test_load_skill_from_disk_strips_frontmatter_and_hashes(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    skill_dir = plugin_dir / "skills"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "wf.md"
    skill_path.write_text(
        "---\nname: wf\n---\n\n# Body\nDo the thing.\n",
        encoding="utf-8",
    )

    with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path):
        result = load_skill_from_disk("demo_plugin", "skills/wf.md")

    assert result["ok"] is True
    assert result["on_disk"] is True
    assert result["error"] is None
    assert result["content"].startswith("# Body")
    assert "Do the thing" in result["content"]
    assert result["content_hash"] == skill_content_hash(result["content"])
    assert "name: wf" not in result["content"]


def test_load_skill_from_disk_missing_file(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)

    with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path):
        result = load_skill_from_disk("demo_plugin", "skills/nope.md")

    assert result["ok"] is False
    assert result["on_disk"] is False
    assert "not found" in (result["error"] or "")


def test_load_skill_from_disk_rejects_traversal(tmp_path):
    with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path):
        result = load_skill_from_disk("demo_plugin", "../other/secret.md")
    assert result["ok"] is False
    assert result["content"] is None


def test_load_skill_drift_detection(tmp_path):
    """DB content hash vs FS hash differ when bodies differ."""
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    skill_dir = plugin_dir / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "a.md").write_text("# disk version\n", encoding="utf-8")

    with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path):
        disk = load_skill_from_disk("demo_plugin", "skills/a.md")

    db_body = "# db override\n"
    assert disk["ok"]
    assert skill_content_hash(db_body) != disk["content_hash"]


@pytest.mark.asyncio
async def test_reload_plugin_skills_single_key(tmp_path):
    """POST reload writes disk content into DB and reports matching hashes."""
    from starlette.requests import Request
    from api import plugin_routes as pr

    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    skill_dir = plugin_dir / "skills"
    skill_dir.mkdir(parents=True)
    body = "# from disk\nstep one\n"
    (skill_dir / "wf.md").write_text(body, encoding="utf-8")

    record = type("R", (), {"id": "demo_plugin"})()
    set_calls: list[tuple] = []

    async def fake_set(plugin_id, key, content):
        set_calls.append((plugin_id, key, content))

    async def fake_get_skills(plugin_id):
        return {k: c for _, k, c in set_calls} if set_calls else {}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/plugins/session_gated/demo_plugin/skills/reload",
        "headers": [],
        "query_string": b"",
        "path_params": {"plugin_id": "demo_plugin"},
    }

    async def receive():
        import json
        data = json.dumps({"key": "skills/wf.md"}).encode()
        return {"type": "http.request", "body": data, "more_body": False}

    req = Request(scope, receive)

    with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path), patch.object(
        pr._db_registry, "get_by_id", return_value=record
    ), patch.object(pr._db_registry, "set_skill", side_effect=fake_set), patch.object(
        pr._db_registry, "get_skills", side_effect=fake_get_skills
    ), patch(
        "core.plugin_loader.skill_registry.set_plugin_skills"
    ):
        from api.plugin_routes import skills_config as sc

        sc._plugin_loader._relay_manifests = {
            "demo_plugin": {"skills": ["skills/wf.md"]}
        }
        # load_skill_from_disk uses PROJECT_ROOT from skill_file_store; also patch via api import path
        with patch(
            "api.plugin_routes.skills_config.load_skill_from_disk",
            wraps=load_skill_from_disk,
        ):
            # Ensure load uses tmp_path
            with patch("core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path):
                resp = await pr.reload_plugin_skills(req)

    assert resp.status_code == 200
    import json
    payload = json.loads(resp.body.decode())
    assert payload["plugin_id"] == "demo_plugin"
    assert len(payload["reloaded"]) == 1
    assert payload["reloaded"][0]["key"] == "skills/wf.md"
    assert payload["reloaded"][0]["in_sync"] is True
    assert payload["reloaded"][0]["content_hash"] == skill_content_hash(body)
    assert set_calls == [("demo_plugin", "skills/wf.md", body)]


@pytest.mark.asyncio
async def test_reload_plugin_skills_missing_file_404(tmp_path):
    from starlette.requests import Request
    from api import plugin_routes as pr

    record = type("R", (), {"id": "demo_plugin"})()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [],
        "query_string": b"",
        "path_params": {"plugin_id": "demo_plugin"},
    }

    async def receive():
        import json
        return {"type": "http.request", "body": json.dumps({"key": "skills/missing.md"}).encode(), "more_body": False}

    req = Request(scope, receive)

    from api.plugin_routes import skills_config as sc

    sc._plugin_loader._relay_manifests = {"demo_plugin": {"skills": []}}
    with patch.object(pr._db_registry, "get_by_id", return_value=record), patch(
        "core.plugin_loader.skill_file_store.PROJECT_ROOT", tmp_path
    ):
        (tmp_path / "plugins" / "demo_plugin").mkdir(parents=True)
        resp = await pr.reload_plugin_skills(req)

    assert resp.status_code == 404
    import json
    payload = json.loads(resp.body.decode())
    assert payload["failed"]
    assert payload["reloaded"] == []
