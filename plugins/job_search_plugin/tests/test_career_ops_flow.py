"""
Tests for the career_ops_apply_v1 FlowSpec and its slot-bridging adapter ops.

Covers: the JSON parses under FlowSpec's fail-closed schema, every stage's
``reads`` resolves to something an earlier stage (or inputs_schema) actually
provides (the regression guard for the slot-rename class of bug this flow
exists to fix), claim scoping doesn't steal portfolio-only turns, the bake
cycle guard holds, and the two new adapter ops behave correctly in isolation.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from core_graph.subgraphs.specialist.flow_spec import parse_flow_spec

FLOW_PATH = (
    Path(__file__).resolve().parents[1] / "flow_specs" / "career_ops_apply_v1.json"
)


def _load_spec():
    data = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
    return parse_flow_spec(data, owner="job_search_plugin")


def test_flow_spec_parses():
    spec = _load_spec()
    assert spec.flow_id == "career_ops_apply_v1"
    assert len(spec.stages) == 11


def test_slot_chain_is_fully_resolved():
    """Every stage's reads must come from inputs_schema or an earlier stage's writes."""
    spec = _load_spec()
    available = set((spec.inputs_schema or {}).get("properties", {}).keys())
    for stage in spec.stages:
        unresolved = [r for r in stage.reads if r not in available]
        assert not unresolved, (
            f"stage '{stage.id}' reads {unresolved} which no earlier stage writes "
            f"and inputs_schema doesn't declare"
        )
        available.update(stage.writes)


def test_every_stage_read_matches_the_target_ops_parameter_names():
    """A stage's ``reads`` are passed to the op verbatim as keyword arguments.

    ``test_slot_chain_is_fully_resolved`` only proves a slot is *on the board* by
    the time a stage runs — it cannot see that the callee names that same value
    something else. A stage reading a slot the op has no parameter for dies at
    ``execute_operation`` with a schema-validation/TypeError, which ``on_fail:
    continue`` then swallows into a silent no-op stage. That is precisely how the
    liveness stage shipped dead, so assert the two halves actually line up.

    Signatures are read with ``ast`` rather than ``inspect``: the ops are behind
    ``@mcp.tool`` and this must assert on the declared signature, not on whatever
    the decorator happens to return.
    """
    import ast

    plugins_root = FLOW_PATH.resolve().parents[3]
    sigs: dict[str, tuple[list[str], list[str]]] = {}
    for mod in plugins_root.rglob("MCPTools/*.py"):
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
                for d in node.decorator_list
            ):
                continue
            params = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            n_defaults = len(node.args.defaults)
            required = [a.arg for a in node.args.args[: len(node.args.args) - n_defaults]]
            sigs[node.name] = (params, required)

    spec = _load_spec()
    available = set((spec.inputs_schema or {}).get("properties", {}).keys()) | {"goal"}
    for stage in spec.stages:
        plugin_id, _, op_id = stage.op.partition("/")
        fn_name = op_id[len(plugin_id) + 2:]
        assert fn_name in sigs, f"stage '{stage.id}' op {stage.op!r} has no @mcp.tool named {fn_name!r}"
        params, required = sigs[fn_name]

        # Only slots actually on the board get passed (flow_runner filters on `in board.slots`).
        passed = [r for r in stage.reads if r in available]
        unexpected = [r for r in passed if r not in params]
        assert not unexpected, (
            f"stage '{stage.id}' reads {unexpected}, which {fn_name}() has no parameter for — "
            f"it accepts {params}"
        )
        missing = [r for r in required if r not in passed]
        assert not missing, (
            f"stage '{stage.id}' never supplies {fn_name}()'s required parameter(s) {missing}"
        )
        available.update(stage.writes)


def test_claims_do_not_steal_portfolio_turns():
    spec = _load_spec()
    assert not spec.claims.matches(
        goal="tell me about your portfolio projects", goal_class=None
    )
    assert not spec.claims.matches(
        goal="redesign my portfolio layout", goal_class="redesign"
    )
    assert not spec.claims.matches(goal="bake portfolio for SRE at Acme", goal_class="bake_for_job")
    assert spec.claims.matches(goal="apply to this job posting", goal_class=None)


def test_bake_stage_never_reads_job_application_job_id_or_provider():
    """Cycle guard: bake must never be handed the fields that route it back into
    job_search_plugin.get_job_details (see enrich_tools._auto_bake_portfolio).
    """
    spec = _load_spec()
    bake = next(s for s in spec.stages if s.id == "bake")
    assert "job_application_job_id" not in bake.reads
    assert "provider" not in bake.reads


def test_bake_and_cover_pdf_stages_use_qualified_op_ids():
    spec = _load_spec()
    for stage in spec.stages:
        plugin_id, _, op_id = stage.op.partition("/")
        assert op_id.startswith(f"{plugin_id}__"), f"stage '{stage.id}' op {stage.op!r} not qualified"


# ─── resolve_pipeline_signals ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_maps_ats_fields_directly():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    res = await resolve_pipeline_signals(
        goal="apply to this job",
        title="Backend Engineer",
        company="Acme",
        clean_description="Build things.",
        source_url="https://boards.greenhouse.io/acme/jobs/555",
        job_id="555",
    )
    assert res["status"] == "ok"
    assert res["role"] == "Backend Engineer"
    assert res["company"] == "Acme"
    assert res["job_description"] == "Build things."
    assert res["offer_text"] == "Build things."
    assert res["posting_url"] == "https://boards.greenhouse.io/acme/jobs/555"
    assert res["url"] == "https://boards.greenhouse.io/acme/jobs/555"
    assert res["role_title"] == "Backend Engineer"
    assert res["job_id"] == "555"


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_falls_back_when_company_role_blank():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    fallback = {
        "status": "ok",
        "company": "Acme",
        "role": "SRE",
        "job_description": "On-call rotation and infra.",
    }
    with mock.patch(
        "core.route_registry.execute.execute_operation",
        new_callable=mock.AsyncMock,
        return_value=fallback,
    ) as mock_exec:
        res = await resolve_pipeline_signals(
            goal="bake portfolio for SRE at Acme",
            clean_description="On-call rotation and infra.",
        )

    assert res["status"] == "ok"
    assert res["company"] == "Acme"
    assert res["role"] == "SRE"
    mock_exec.assert_awaited_once()
    args, kwargs = mock_exec.await_args
    assert args[0] == "portfolio_plugin"
    assert args[1] == "portfolio_plugin__resolve_bake_job_signals"


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_fallback_prefers_job_description_over_goal():
    """Live-observed regression: resolve_bake_job_signals's own regex parse
    ("ROLE at COMPANY") runs against goal-or-job_description and prefers goal
    when both are non-empty. A generic instruction goal ("apply to this job")
    never carries that pattern — the actual JD text does — so the fallback
    dispatch must send job_description as the primary parse target, not goal.
    """
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    fallback = {"status": "ok", "company": "TestCo", "role": "Senior Backend Engineer", "job_description": ""}
    with mock.patch(
        "core.route_registry.execute.execute_operation",
        new_callable=mock.AsyncMock,
        return_value=fallback,
    ) as mock_exec:
        res = await resolve_pipeline_signals(
            goal="apply to this job",
            clean_description="Senior Backend Engineer at TestCo. Requirements: Python.",
        )

    assert res["status"] == "ok"
    args, kwargs = mock_exec.await_args
    assert args[2]["goal"] == "Senior Backend Engineer at TestCo. Requirements: Python."
    assert args[2]["goal"] != "apply to this job"


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_synthesizes_job_id_when_missing():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    res = await resolve_pipeline_signals(
        goal="apply",
        title="SRE",
        company="Acme",
        clean_description="Pasted JD text.",
        source_url="",
        job_id="",
    )
    assert res["status"] == "ok"
    assert res["job_id"] and len(res["job_id"]) == 16


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_missing_job_signals_error():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    with mock.patch(
        "core.route_registry.execute.execute_operation",
        new_callable=mock.AsyncMock,
        return_value={"status": "error", "error": "unresolvable"},
    ):
        res = await resolve_pipeline_signals(goal="apply", clean_description="")

    assert res["status"] == "error"
    assert res["error"] == "missing_job_signals"


# ─── prepare_application_record ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_application_record_always_drafted():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import prepare_application_record

    with mock.patch.dict("os.environ", {"CATPORTFOLIO_PUBLIC_DOMAIN": "portfolio.cat.io"}):
        res = await prepare_application_record(
            portfolio_job_id="abc123",
            job_id="555",
            company="Acme",
            role="SRE",
            verdict={"fit_score": 0.8, "mismatch_reasons": []},
        )
    assert res["status"] == "ok"
    assert res["application_status"] == "drafted"
    assert res["portfolio_url"] == "https://portfolio.cat.io/?j=abc123"
    assert "fit_score=0.8" in res["notes"]


@pytest.mark.asyncio
async def test_prepare_application_record_omits_localhost_portfolio_url():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import prepare_application_record

    with mock.patch.dict("os.environ", {"CATPORTFOLIO_PUBLIC_DOMAIN": "localhost:11000"}):
        res = await prepare_application_record(
            portfolio_job_id="abc123",
            job_id="555",
            company="Acme",
            role="SRE",
        )
    assert res["status"] == "ok"
    assert res["application_status"] == "drafted"
    assert res["portfolio_url"] == ""
    assert "localhost" not in (res["portfolio_url"] or "")


@pytest.mark.asyncio
async def test_prepare_application_record_empty_bake_still_ok():
    """A failed/degraded bake must not fail_closed links — resume is still produced."""
    from plugins.job_search_plugin.MCPTools.pipeline_tools import prepare_application_record

    res = await prepare_application_record(
        portfolio_job_id="",
        job_id="555",
        company="CorGTA",
        role="Intermediate Fullstack Developer",
        verdict={"fit_score": 0.4, "mismatch_reasons": ["agency unnamed client"]},
        via="CorGTA",
        end_employer="",
        employment_class="unknown",
    )
    assert res["status"] == "ok"
    assert res["application_status"] == "drafted"
    assert res["portfolio_url"] == ""
    assert "via=CorGTA" in res["notes"]


@pytest.mark.asyncio
async def test_prepare_application_record_no_bake_no_url():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import prepare_application_record

    res = await prepare_application_record(job_id="555", company="Acme", role="SRE")
    assert res["status"] == "ok"
    assert res["portfolio_url"] == ""
    assert res["application_status"] == "drafted"


# ─── sync_application_status accepts application_status alias ────────────


@pytest.mark.asyncio
async def test_sync_application_status_accepts_application_status_alias():
    from plugins.job_search_plugin.MCPTools.application_tools import sync_application_status

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.application_tools.store.upsert_application_status",
        new_callable=mock.AsyncMock,
        return_value=({"id": 1, "status": "drafted"}, True),
    ) as mock_upsert:
        res = await sync_application_status(
            applicant_profile_id=1, job_id="555", application_status="drafted"
        )

    assert res["status"] == "ok"
    mock_upsert.assert_awaited_once()
    args, kwargs = mock_upsert.await_args
    assert args[2] == "drafted"


# ─── alias-key regression on existing tool envelopes ──────────────────────


def test_bake_portfolio_for_job_source_sets_portfolio_job_id_alias():
    """Regression: enrich_tools._auto_bake_portfolio (and the links stage) read
    portfolio_job_id first, falling back to short_id — the ok envelope must set both.
    A full bake run needs the compose pipeline mocked end-to-end; that's already
    covered by portfolio_plugin's own bake tests, so this pins the specific line
    rather than re-mocking the whole pipeline here.
    """
    import inspect

    from plugins.portfolio_plugin.MCPTools import bake_tools

    src = inspect.getsource(bake_tools.bake_portfolio_for_job)
    assert '"portfolio_job_id": short_id' in src


# ─── run_career_ops_pipeline (direct MCP dispatch surface) ────────────────


@pytest.mark.asyncio
async def test_run_career_ops_pipeline_dispatches_run_flow_with_resolved_inputs():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import run_career_ops_pipeline

    fake_flow = object()
    raw_envelope = {
        "status": "ok",
        "flow_id": "career_ops_apply_v1",
        "summary": "Flow 'career_ops_apply_v1' completed (9 stage(s)).",
        "phases": [{"phase": "ingest", "status": "ok"}],
        "carry": {
            "company": "Acme",
            "role": "SRE",
            "short_id": "abc123",
            "portfolio_job_id": "abc123",
            "tailored_text": "resume text",
            # Simulate the oversized field that crashed serialization live —
            # must never appear in the curated result.
            "layout": {"blocks": ["x"] * 10_000},
        },
    }
    with mock.patch(
        "core_graph.subgraphs.specialist.flow_registry.get_flow", return_value=fake_flow
    ) as mock_get_flow, mock.patch(
        "core_graph.subgraphs.specialist.flow_runner.run_flow",
        new_callable=mock.AsyncMock,
        return_value=raw_envelope,
    ) as mock_run_flow, mock.patch(
        "core_graph.mcp_tool._caller_scopes", return_value=["group:job_search_plugin:pipeline"]
    ):
        res = await run_career_ops_pipeline(
            goal="apply to this job",
            applicant_profile_id=7,
            url="https://boards.greenhouse.io/acme/jobs/555",
        )

    assert res["status"] == "ok"
    assert res["company"] == "Acme"
    assert res["short_id"] == "abc123"
    assert res["tailored_text"] == "resume text"
    assert "layout" not in res
    assert "carry" not in res
    mock_get_flow.assert_called_once_with("career_ops_apply_v1")
    mock_run_flow.assert_awaited_once()
    args, kwargs = mock_run_flow.await_args
    assert args[0] is fake_flow
    assert args[1] == "apply to this job"
    assert kwargs["caller_scopes"] == ["group:job_search_plugin:pipeline"]
    assert kwargs["inputs"]["applicant_profile_id"] == 7
    assert kwargs["inputs"]["url"] == "https://boards.greenhouse.io/acme/jobs/555"


@pytest.mark.asyncio
async def test_run_career_ops_pipeline_blocked_indeed_does_not_fail_closed():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import run_career_ops_pipeline

    blocked = {
        "status": "error",
        "error": "needs_browser_scrape",
        "needs_browser_scrape": True,
        "is_potential_ghost_job": False,
        "provider": "indeed",
        "job_id": "ded0d7160e9a67f0",
        "clean_description": "",
    }
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools.fetch_job_posting",
        new_callable=mock.AsyncMock,
        return_value=blocked,
    ), mock.patch(
        "core_graph.subgraphs.specialist.flow_runner.run_flow",
        new_callable=mock.AsyncMock,
    ) as mock_run_flow:
        res = await run_career_ops_pipeline(
            goal="apply to this job",
            applicant_profile_id=7,
            url="https://ca.indeed.com/viewjob?jk=ded0d7160e9a67f0",
        )

    mock_run_flow.assert_not_called()
    assert res["status"] == "ok"
    assert res["needs_browser_scrape"] is True
    assert res["is_potential_ghost_job"] is False
    assert res["job_id"] == "ded0d7160e9a67f0"
    assert res["application_status"] == "drafted"
    assert res["portfolio_url"] == ""


@pytest.mark.asyncio
async def test_resolve_pipeline_signals_uses_via_when_end_employer_unnamed():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import resolve_pipeline_signals

    res = await resolve_pipeline_signals(
        goal="apply to this job",
        title="Intermediate Fullstack Developer (Typescript / React / Node)",
        company="",
        clean_description="support one of our clients in a contract capacity",
        source_url="https://ca.indeed.com/viewjob?jk=ded0d7160e9a67f0",
        job_id="ded0d7160e9a67f0",
        via="CorGTA",
        posting_entity="CorGTA",
        end_employer="",
        employment_class="unknown",
        location="Montréal, QC • Remote (Canada)",
    )
    assert res["status"] == "ok"
    assert res["company"] == "CorGTA"
    assert res["via"] == "CorGTA"
    assert res["end_employer"] is None
    assert res["role"].startswith("Intermediate Fullstack")
    assert res["url"].startswith("https://ca.indeed.com")
    assert res["role_title"] == res["role"]


def test_links_stage_does_not_require_portfolio_job_id():
    spec = _load_spec()
    links = next(s for s in spec.stages if s.id == "links")
    assert "portfolio_url" not in links.required_outputs
    assert "portfolio_job_id" not in links.required_outputs
    assert links.on_fail == "continue"


@pytest.mark.asyncio
async def test_run_career_ops_pipeline_errors_when_flow_unregistered():
    from plugins.job_search_plugin.MCPTools.pipeline_tools import run_career_ops_pipeline

    with mock.patch("core_graph.subgraphs.specialist.flow_registry.get_flow", return_value=None):
        res = await run_career_ops_pipeline(goal="apply", applicant_profile_id=1)

    assert res == {"status": "error", "error": "flow_not_registered"}


@pytest.mark.asyncio
async def test_tailor_cover_letter_returns_cover_letter_text_alias():
    from plugins.job_search_plugin.MCPTools.enrich_tools import tailor_cover_letter

    mock_llm_response = mock.MagicMock()
    mock_llm_response.content = "Dear Hiring Manager, ..."
    mock_llm = mock.MagicMock()
    mock_llm.ainvoke = mock.AsyncMock(return_value=mock_llm_response)

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile",
        new_callable=mock.AsyncMock,
        return_value={"base_resume_text": "resume", "cover_letter_template": None},
    ), mock.patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=mock_llm
    ):
        res = await tailor_cover_letter(applicant_profile_id=1, job_description="JD text")

    assert res["tailored_text"] == "Dear Hiring Manager, ..."
    assert res["cover_letter_text"] == "Dear Hiring Manager, ..."
