"""Unit tests for plugins.jules_plugin.review_fleet (shared Jules fleet templates)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from plugins.jules_plugin.review_fleet import (
    ALL_ROLES,
    build_configs,
    parse_roles,
    plugin_skill_path,
)
from plugins.jules_plugin.review_fleet.templates import build_roles
from plugins.jules_plugin.MCPTools.review_fleet_tools import (
    julesbuild_review_fleet,
    julesfire_review_fleet,
)


def test_parse_roles_all_and_subset():
    assert parse_roles(None) == []
    assert parse_roles("") == []
    assert parse_roles("all") == list(ALL_ROLES)
    assert parse_roles("backend-b,docs,backend-b") == ["backend-b", "docs"]


def test_parse_roles_unknown_raises():
    with pytest.raises(ValueError, match="unknown role"):
        parse_roles("frontend-a,not-a-role")


def test_build_configs_diff_rich_prompts_not_labels():
    configs = build_configs(
        roles=["frontend-b", "docs"],
        repo="CooLguNxDD/OpenCat-Mcp-Full",
        branch="feat-branch",
        require_plan_approval=False,
        automation_mode="AUTOMATION_MODE_UNSPECIFIED",
        frontend_path="frontend/",
        backend_path=".",
        docs_path=".",
        mode="diff",
        base_branch="main",
    )
    assert len(configs) == 2
    assert {c["role"] for c in configs} == {"frontend-b", "docs"}
    for c in configs:
        assert c["mode"] == "diff"
        assert c["baseBranch"] == "main"
        assert "frontend-b" != c["prompt"] and "docs" != c["prompt"]
        assert len(c["prompt"]) > 200
        assert "diff" in c["prompt"].lower() or "main" in c["prompt"]
        assert "CODE_HEALTH" in c["prompt"] or "DOC_GAPS" in c["prompt"]
        sc = json.loads(c["sourceContext"])
        assert sc["source"] == "sources/github/CooLguNxDD/OpenCat-Mcp-Full"
        assert sc["githubRepoContext"]["startingBranch"] == "feat-branch"
        assert "diff vs main" in c["title"]
        assert "main...HEAD" in c["prompt"]
        assert "NEVER fall back to two-dot" in c["prompt"]
        assert "git merge-base main HEAD" in c["prompt"]
        assert "git fetch --unshallow origin" in c["prompt"]
        assert "STOP" in c["prompt"]


def test_build_configs_full_mode_title():
    configs = build_configs(
        roles=["backend-a"],
        repo="owner/repo",
        branch="main",
        require_plan_approval=True,
        automation_mode="AUTO_CREATE_PR",
        frontend_path=".",
        backend_path="api/",
        docs_path=".",
        mode="full",
        base_branch=None,
    )
    assert len(configs) == 1
    c = configs[0]
    assert c["baseBranch"] is None
    assert "full deep scan" in c["title"]
    assert "exhaustive deep scan" in c["prompt"].lower() or "Deep-scan" in c["prompt"]
    assert c["requirePlanApproval"] is True
    assert c["automationMode"] == "AUTO_CREATE_PR"
    assert "api/" in c["prompt"]


def test_role_report_names_stable():
    roles = build_roles("fe/", "be/", "docs/")
    assert roles["frontend-a"]["report_name"] == "CODE_HEALTH_FRONTEND_A.md"
    assert roles["frontend-b"]["report_name"] == "CODE_HEALTH_FRONTEND_B.md"
    assert roles["backend-a"]["report_name"] == "CODE_HEALTH_BACKEND_A.md"
    assert roles["backend-b"]["report_name"] == "CODE_HEALTH_BACKEND_B.md"
    assert roles["docs"]["report_name"] == "DOC_GAPS_REPORT.md"
    assert "Accessibility" in roles["frontend-b"]["extra_criteria"]
    assert "SSRF" in roles["backend-b"]["extra_criteria"]


def test_plugin_skill_path_exists():
    path = plugin_skill_path()
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert "julescreate_session" in text
    assert "CODE_HEALTH_FRONTEND_B.md" in text
    assert "review_fleet" in text
    assert "optional" in text.lower()
    assert "julesfire_review_fleet" in text


@pytest.mark.asyncio
async def test_build_review_fleet_roles_optional_empty():
    """Empty roles → no configs (no automatic fleet)."""
    out = await julesbuild_review_fleet(roles="")
    assert out["status"] == "ok"
    assert out["roles"] == []
    assert out["configs"] == []


@pytest.mark.asyncio
async def test_build_review_fleet_roles_optional_none_default():
    out = await julesbuild_review_fleet()
    assert out["status"] == "ok"
    assert out["configs"] == []


@pytest.mark.asyncio
async def test_build_review_fleet_with_roles():
    out = await julesbuild_review_fleet(
        roles="docs,backend-b",
        mode="diff",
        base="main",
        repo="CooLguNxDD/OpenCat-Mcp-Full",
        branch="feat",
    )
    assert out["status"] == "ok"
    assert out["roles"] == ["docs", "backend-b"]
    assert len(out["configs"]) == 2
    for c in out["configs"]:
        assert len(c["prompt"]) > 100
        assert c["prompt"] not in ("docs", "backend-b")


@pytest.mark.asyncio
async def test_fire_review_fleet_empty_roles_noop():
    """Empty roles must not call Jules API."""
    with patch(
        "plugins.jules_plugin.MCPTools.review_fleet_tools._post_session",
        new_callable=AsyncMock,
    ) as post:
        out = await julesfire_review_fleet(roles="")
    assert out["status"] == "ok"
    assert out["fired"] == 0
    assert out["sessions"] == []
    post.assert_not_called()


@pytest.mark.asyncio
async def test_fire_review_fleet_posts_per_role():
    async def fake_post(body):
        return {
            "status": "ok",
            "session": {"id": "s1", "url": "https://jules.google.com/session/s1"},
        }

    with patch(
        "plugins.jules_plugin.MCPTools.review_fleet_tools._post_session",
        new_callable=AsyncMock,
        side_effect=fake_post,
    ) as post:
        out = await julesfire_review_fleet(
            roles="docs",
            mode="full",
            repo="owner/repo",
            branch="main",
        )
    assert out["status"] == "ok"
    assert out["fired"] == 1
    assert post.await_count == 1
    body = post.await_args.args[0]
    assert "prompt" in body and len(body["prompt"]) > 50
    assert isinstance(body.get("sourceContext"), dict)
