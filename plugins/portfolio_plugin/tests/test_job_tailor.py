"""Unit tests for deterministic job-layout tailor (anti clone-across-bakes)."""

from __future__ import annotations

from plugins.portfolio_plugin.compose.job_tailor import (
    _score_blob,
    content_fingerprint,
    job_tokens,
    matched_project_slugs,
    tailor_layout_for_job,
)


def _base_layout() -> dict:
    return {
        "version": 1,
        "meta": {
            "audience": "default",
            "theme": "neon",
            "generatedAt": "2026-01-01T00:00:00Z",
            "curationLabel": "Agentically curated · 4 project(s) scoped · grounded",
        },
        "blocks": [
            {
                "type": "hero",
                "id": "h1",
                "props": {
                    "name": "Andrew Liang (the cat)",
                    "tagline": "AI Systems Architect · Cloud Infra",
                    "pitch": (
                        "High-impact full-stack systems builder across Whiskers Agent. "
                        "Reduced AWS costs from $4k → $2.8k/mo (−30%)."
                    ),
                    "links": [{"label": "GitHub", "href": "https://github.com/x"}],
                },
            },
            {
                "type": "kpiGrid",
                "id": "kpi-master",
                "props": {
                    "items": [
                        {"label": "context reduction", "value": "~98.7%"},
                        {"label": "AWS cost", "value": "−30%"},
                    ]
                },
            },
            {
                "type": "card",
                "id": "card-helix-devops",
                "props": {
                    "title": "Helix DevOps & Infrastructure",
                    "summary": "multi-region AWS deploy platform",
                    "tags": ["aws", "devops"],
                },
            },
            {
                "type": "card",
                "id": "card-helix-ai",
                "props": {
                    "title": "Helix AI Platform",
                    "summary": "MCP + operator AI layer and agents",
                    "tags": ["ai", "mcp", "llm"],
                },
            },
            {
                "type": "flowAnim",
                "id": "fa1",
                "props": {"title": "Agent System Flow", "nodes": [], "edges": []},
            },
            {
                "type": "comparison",
                "id": "compare-projects",
                "props": {
                    "title": "Project contrast",
                    "columns": [{"label": "A"}, {"label": "B"}],
                    "rows": [{"label": "x", "cells": ["1", "2"]}],
                },
            },
            {
                "type": "prose",
                "id": "prose-helix-devops",
                "props": {"markdown": "### Deep dive · DevOps\n\ninfra"},
            },
            {
                "type": "prose",
                "id": "prose-helix-ai",
                "props": {"markdown": "### Deep dive · AI\n\nagents"},
            },
            {
                "type": "starStory",
                "id": "starStory-6",
                "props": {
                    "situation": "144 MCP tools blew past every client's context window",
                    "task": "narrow tools",
                    "action": "pgvector",
                    "result": "98.7%",
                },
            },
        ],
    }


def test_hero_preserved_job_hidden_in_meta():
    """Job title/company stay off hero; live only in meta (and ?j= short_id)."""
    base = _base_layout()
    base_hero = next(b for b in base["blocks"] if b["type"] == "hero")["props"]
    out = tailor_layout_for_job(
        base,
        company="A.Team",
        role="Senior Independent AI Engineer / Architect",
        job_text="Build production AI agent architectures and RAG systems",
    )
    assert out is not None
    hero = next(b for b in out["blocks"] if b["type"] == "hero")
    # Visible hero unchanged
    assert hero["props"]["name"] == "Andrew Liang (the cat)"
    assert hero["props"]["tagline"] == base_hero["tagline"]
    assert hero["props"]["pitch"] == base_hero["pitch"]
    assert "A.Team" not in hero["props"]["tagline"]
    assert "A.Team" not in hero["props"]["pitch"]
    assert "Senior Independent" not in hero["props"]["tagline"]
    assert "tailored for" not in (hero["props"]["tagline"] or "").lower()
    assert "grounded proof includes" not in (hero["props"]["pitch"] or "").lower()
    # Silent job provenance
    assert out["meta"]["jobCompany"] == "A.Team"
    assert "AI Engineer" in out["meta"]["jobRole"]
    assert out["meta"]["tailored"] is True
    assert hero["props"]["links"]


def test_pitch_not_overwritten_with_metrics():
    """Hero pitch is not rewritten to inject KPI chrome."""
    out = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="AI Engineer",
        job_text="AI",
    )
    pitch = next(b for b in out["blocks"] if b["type"] == "hero")["props"]["pitch"]
    assert "grounded proof includes" not in pitch
    assert "For A.Team" not in pitch
    # Original compose pitch preserved
    assert "Whiskers Agent" in pitch or "AWS" in pitch


def test_retitles_generic_flow_and_comparison_without_job_title():
    out = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="AI Engineer",
        job_text="agents llm",
    )
    flow = next(b for b in out["blocks"] if b["type"] == "flowAnim")
    cmp_ = next(b for b in out["blocks"] if b["type"] == "comparison")
    assert flow["props"]["title"] != "Agent System Flow"
    # Domain-based only — not "AI Engineer systems map" / "for A.Team"
    assert "AI Engineer" not in flow["props"]["title"]
    assert "A.Team" not in flow["props"]["title"]
    assert "A.Team" not in cmp_["props"]["title"]
    assert "AI Engineer" not in cmp_["props"]["title"]


def test_ai_jd_floats_ai_card_above_devops():
    out = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="AI Engineer",
        job_text="LLM agents MCP RAG production AI systems orchestration",
    )
    cards = [b for b in out["blocks"] if b["type"] == "card"]
    assert cards[0]["id"] == "card-helix-ai"
    # prose follows card preference when id matches
    proses = [b for b in out["blocks"] if b["type"] == "prose"]
    assert proses[0]["id"] == "prose-helix-ai"


def test_meta_stamps_and_fingerprint():
    out = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="AI Engineer",
        job_text="production AI",
        audience="hiring-manager",
    )
    meta = out["meta"]
    assert meta["jobCompany"] == "A.Team"
    assert meta["jobRole"] == "AI Engineer"
    assert meta["tailored"] is True
    assert meta["jobBriefHash"]
    # Visible label is neutral — no employer/role spam
    assert "A.Team" not in meta["curationLabel"]
    assert "AI Engineer" not in meta["curationLabel"]
    assert "Matched layout" in meta["curationLabel"]
    assert meta["contentFingerprint"] == content_fingerprint(out)


def test_two_jobs_differ_by_matching_not_hero_copy():
    a = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="Senior AI Engineer",
        job_text="AI agents LLM",
    )
    b = tailor_layout_for_job(
        _base_layout(),
        company="Clipster",
        role="Mobile Engineer",
        job_text="iOS Android mobile app",
    )
    ha = next(x for x in a["blocks"] if x["type"] == "hero")["props"]
    hb = next(x for x in b["blocks"] if x["type"] == "hero")["props"]
    # Heroes stay identical (brand page); jobs differ in meta + card order
    assert ha["pitch"] == hb["pitch"]
    assert ha["tagline"] == hb["tagline"]
    assert a["meta"]["jobCompany"] != b["meta"]["jobCompany"]
    assert a["meta"]["contentFingerprint"] != b["meta"]["contentFingerprint"]


_PROJECTS = [
    {"slug": "helix-ai", "name": "Helix AI Platform", "summary": "MCP + operator AI agents", "tags": ["ai", "mcp", "llm"]},
    {"slug": "helix-devops", "name": "Helix DevOps", "summary": "multi-region AWS deploys", "tags": ["aws", "devops"]},
    {"slug": "helix-mobile", "name": "Helix Mobile", "summary": "React Native app", "tags": ["mobile", "ios"]},
]


def test_short_tech_token_ai_is_kept_and_word_bounded():
    tokens = job_tokens("", "", "tell me about your ai project")
    assert "ai" in tokens
    assert _score_blob("Helix AI — LangGraph agents", tokens) >= 1
    assert _score_blob("available wait details email", {"ai"}) == 0


def test_highlight_slugs_are_real_project_slugs():
    """Curation must name rows the fish tank can match.

    Card block ids cannot supply this: the layout agent invents ids like
    'proj-ai' and 'card-helix-devops-infra' that match no project row.
    """
    tokens = job_tokens("A.Team", "AI Engineer", "LLM agents MCP RAG production AI systems")
    slugs = matched_project_slugs(_PROJECTS, tokens)
    assert slugs, "fish tank curation is dead without highlightSlugs"
    assert slugs[0] == "helix-ai"
    known = {p["slug"] for p in _PROJECTS}
    assert set(slugs) <= known


def test_score_blob_is_word_bounded_for_longer_tokens_too():
    """'api' must not match inside 'rapid', 'ops' must not match inside
    'develops' — substring matching for tokens >2 chars inflated scores with
    unrelated hits."""
    assert _score_blob("rapid prototyping and iteration", {"api"}) == 0
    assert _score_blob("the team develops software", {"ops"}) == 0
    assert _score_blob("REST api integration work", {"api"}) >= 1


def test_no_highlight_slugs_when_nothing_matches():
    """Every fish glowing is no curation at all — zero-score projects excluded."""
    tokens = job_tokens("Zzzq", "Pastry Chef", "croissant lamination proofing bread")
    assert matched_project_slugs(_PROJECTS, tokens) == []


def test_tailor_does_not_stamp_highlight_slugs():
    """It only sees blocks, so it must not guess at project identity."""
    out = tailor_layout_for_job(
        _base_layout(),
        company="A.Team",
        role="AI Engineer",
        job_text="LLM agents MCP RAG production AI systems orchestration",
    )
    assert "highlightSlugs" not in out["meta"]


def test_empty_company_and_role_fail_open():
    base = _base_layout()
    out = tailor_layout_for_job(base, company="", role="", job_text="x")
    assert out is base or out == base


def test_none_layout_fail_open():
    assert tailor_layout_for_job(None, company="X", role="Y") is None

def test_rank_projects_for_tank_limit():
    from plugins.portfolio_plugin.compose.job_tailor import rank_projects_for_tank, job_tokens

    projects = [
        {"slug": "a", "name": "Project A", "summary": "Python backend"},
        {"slug": "b", "name": "Project B", "summary": "React frontend"},
        {"slug": "c", "name": "Project C", "summary": "DevOps infra Python"},
    ]

    tokens = job_tokens("TechCorp", "Python Engineer", "Backend systems")

    # Should enforce limit correctly
    kept = rank_projects_for_tank(projects, tokens, limit=2)
    assert len(kept) == 2
    assert [p["slug"] for p in kept] == ["a", "c"]

    # Should enforce lower limit
    kept2 = rank_projects_for_tank(projects, tokens, limit=1)
    assert len(kept2) == 1
    assert [p["slug"] for p in kept2] == ["a"]

def test_rank_projects_for_tank_always_slugs():
    from plugins.portfolio_plugin.compose.job_tailor import rank_projects_for_tank, job_tokens

    projects = [
        {"slug": "a", "name": "Project A", "summary": "Python backend"},
        {"slug": "b", "name": "Project B", "summary": "React frontend"},
        {"slug": "c", "name": "Project C", "summary": "DevOps infra Python"},
    ]

    tokens = job_tokens("TechCorp", "Python Engineer", "Backend systems")

    # 'a' and 'c' match and score higher.
    # We want 'b' to get in, so we pass always_slugs=["b"]
    # We set limit=3 so it isn't cut off by limit (since the loop adds in order of score)
    kept = rank_projects_for_tank(projects, tokens, limit=3, always_slugs=["b"])
    assert "b" in [p["slug"] for p in kept]
