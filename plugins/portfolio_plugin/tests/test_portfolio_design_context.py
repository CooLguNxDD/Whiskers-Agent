"""
Unit tests for the portfolio design-context tools and audience template helper.
"""

import hashlib
import inspect

import pytest

import plugins.portfolio_plugin.schema.ui_layout_schema as ui_layout_schema
from plugins.portfolio_plugin.compose.composer import (
    AUDIENCE_TEMPLATES,
    get_audience_template,
    infer_audience_from_job_signals,
)
from plugins.portfolio_plugin.MCPTools.portfolio_tools import (
    get_design_context,
    get_layout_schema_fingerprint,
)


def test_get_audience_template_known_audience():
    """Known audience returns its base template."""
    audience, template = get_audience_template("peer")
    assert audience == "peer"
    assert template["sections"] == AUDIENCE_TEMPLATES["peer"]["sections"]


def test_get_audience_template_unknown_falls_back_to_default():
    """Unknown audience normalizes to default."""
    audience, template = get_audience_template("alien")
    assert audience == "default"
    assert template["sections"] == AUDIENCE_TEMPLATES["default"]["sections"]


def test_infer_audience_recruiter_signals():
    """JD emphasizing measurable impact/results/delivery maps to recruiter."""
    audience, star_query = infer_audience_from_job_signals(
        "We need measurable impact and results delivery from this hire."
    )
    assert audience == "recruiter"
    assert star_query == AUDIENCE_TEMPLATES["recruiter"]["star_query"]


def test_infer_audience_hiring_manager_signals():
    """JD emphasizing ownership/tradeoffs/leadership maps to hiring-manager."""
    audience, _ = infer_audience_from_job_signals(
        "Looking for ownership, tradeoffs and leadership in service delivery."
    )
    assert audience == "hiring-manager"


def test_infer_audience_peer_signals():
    """JD emphasizing architecture/systems/design maps to peer."""
    audience, _ = infer_audience_from_job_signals(
        "Deep dive into distributed systems architecture and design."
    )
    assert audience == "peer"


def test_infer_audience_falls_back_to_default_on_no_overlap():
    """JD with no keyword overlap falls back to default."""
    audience, star_query = infer_audience_from_job_signals(
        "We're hiring a barista for the coffee shop."
    )
    assert audience == "default"
    assert star_query == AUDIENCE_TEMPLATES["default"]["star_query"]


def test_infer_audience_folds_in_extracted_skills():
    """extracted_skills contribute to the overlap score alongside the JD text."""
    audience, _ = infer_audience_from_job_signals(
        "Great company culture.", extracted_skills=["ownership", "tradeoffs", "leadership"]
    )
    assert audience == "hiring-manager"


def test_get_audience_template_merges_manifest_settings(monkeypatch):
    """Manifest settings.audiences overrides merge onto the base template."""
    from plugins.portfolio_plugin.compose import composer

    monkeypatch.setattr(
        composer, "SETTINGS", {"audiences": {"peer": {"max_projects": 99}}}
    )
    audience, template = get_audience_template("peer")
    assert audience == "peer"
    assert template["max_projects"] == 99
    # Base keys still present
    assert "star_query" in template


@pytest.mark.asyncio
async def test_get_design_context_shape():
    """Design context includes template, tokens, and the mirror's block types."""
    res = await get_design_context(audience="recruiter")
    assert res["status"] == "ok"
    assert res["audience"] == "recruiter"
    assert res["audience_template"]["sections"] == AUDIENCE_TEMPLATES["recruiter"]["sections"]
    assert res["supported_block_types"] == sorted(ui_layout_schema.BLOCK_TYPES)
    assert isinstance(res["design_tokens"], dict)
    assert "theme_defs" in res
    assert "mocha" in res["theme_defs"]
    assert "vars" in res["theme_defs"]["mocha"]
    assert "hero" in res
    assert res["schema_docstring"] == ui_layout_schema.__doc__
    catalog = res["block_catalog"]
    assert {e["type"] for e in catalog} == set(ui_layout_schema.BLOCK_TYPES)
    assert all("band" in e and "grounding" in e for e in catalog)


@pytest.mark.asyncio
async def test_get_design_context_unknown_audience_defaults():
    """Unknown audience falls back to default rather than erroring."""
    res = await get_design_context(audience="nope")
    assert res["status"] == "ok"
    assert res["audience"] == "default"


@pytest.mark.asyncio
async def test_get_layout_schema_fingerprint():
    """Fingerprint matches a locally computed sha256 of the mirror source."""
    res = await get_layout_schema_fingerprint()
    assert res["status"] == "ok"
    assert res["block_types"] == sorted(ui_layout_schema.BLOCK_TYPES)
    expected = hashlib.sha256(
        inspect.getsource(ui_layout_schema).encode("utf-8")
    ).hexdigest()
    assert res["sha256"] == expected
