"""Unit tests for fishTank specimen builder (bounded numerics, school, highlights)."""

from __future__ import annotations

from plugins.portfolio_plugin.compose.fish import build_fish_specimens, build_fish_tank_block


def _proj(
    slug: str,
    *,
    name: str | None = None,
    summary: str = "A substantive project summary with enough prose to clear the quality bar for portfolio layout membership and cards.",
    tags: list[str] | None = None,
    sort_order: int | None = None,
    metrics: list[dict] | None = None,
) -> dict:
    p: dict = {
        "slug": slug,
        "name": name or slug,
        "summary": summary,
        "tags": tags or ["primary", "MCP"],
        "metrics": metrics or [{"label": "tools", "value": "12"}],
        "links": [{"label": "repo", "href": "https://github.com/example/x"}],
        "context_sources": [{"id": f"disc:github:{slug}", "kind": "github"}],
    }
    if sort_order is not None:
        p["sort_order"] = sort_order
    return p


def test_numerics_in_unit_interval_for_rich_and_empty():
    projects = [
        _proj("oct-mcp", sort_order=0, tags=["primary", "ai", "MCP"]),
        # true thin stub: github placeholder, no metrics/sources, no primary
        {
            "slug": "thin",
            "name": "thin",
            "summary": "GitHub repository foo/bar",
            "tags": ["side"],
            "metrics": [],
            "links": [],
            "context_sources": [],
        },
        _proj("goap", sort_order=5, tags=["ai", "LangGraph"]),
    ]
    fish = build_fish_specimens(projects, highlight_slugs=["oct-mcp"])
    slugs = {f["slug"] for f in fish}
    assert "oct-mcp" in slugs
    assert "thin" not in slugs
    for f in fish:
        for k in ("size", "depth", "speed", "glow"):
            assert 0.0 <= f[k] <= 1.0, (f["slug"], k, f[k])
        assert f["species"] in {"ai", "devops", "mobile", "platform"}
        assert 0 <= f["school"] <= 15


def test_sort_order_zero_is_top_rank_not_falsy():
    fish = build_fish_specimens(
        [
            _proj("top", sort_order=0),
            _proj("mid", sort_order=10),
        ]
    )
    by = {f["slug"]: f for f in fish}
    assert by["top"]["depth"] < by["mid"]["depth"]


def test_highlight_lowers_depth_raises_glow():
    base = build_fish_specimens([_proj("oct-mcp", sort_order=8)])
    hl = build_fish_specimens(
        [_proj("oct-mcp", sort_order=8)], highlight_slugs=["oct-mcp"]
    )
    assert hl[0]["depth"] < base[0]["depth"]
    assert hl[0]["glow"] >= 0.85


def test_school_dense_from_zero_and_stable():
    projects = [
        _proj("a", tags=["primary", "MCP"]),
        _proj("b", tags=["primary", "MCP"]),
        _proj("c", tags=["primary", "devops", "aws"]),
    ]
    a = build_fish_specimens(projects)
    b = build_fish_specimens(projects)
    schools = sorted({f["school"] for f in a})
    assert schools[0] == 0
    assert a == b


def test_build_fish_tank_block_none_when_empty():
    assert build_fish_tank_block([]) is None
    # non-primary short stub with no metrics/sources is filtered out
    assert (
        build_fish_tank_block(
            [
                {
                    "slug": "x",
                    "name": "x",
                    "summary": "short",
                    "tags": [],
                    "metrics": [],
                    "links": [],
                    "context_sources": [],
                }
            ]
        )
        is None
    )


def test_build_fish_tank_block_shape():
    blk = build_fish_tank_block([_proj("oct-mcp")], highlight_slugs=["oct-mcp"])
    assert blk is not None
    assert blk["type"] == "fishTank"
    assert blk["props"]["renderer"] == "webgl"
    assert len(blk["props"]["fish"]) >= 1


def test_specimens_never_emit_null_optionals():
    """CatPortfolio's Zod mirror types the optional fish fields as
    ``.optional()``, which accepts ``undefined`` but rejects ``null``. A single
    null fails the whole discriminatedUnion, so the SPA drops the entire baked
    layout and renders the master snapshot instead. Omit, never null."""
    projects = [
        # No summary, no links, no context_sources -> every optional is absent.
        {"slug": "bare", "name": "Bare Project", "tags": ["primary"], "sort_order": 1},
        _proj("rich"),
    ]
    fish = build_fish_specimens(projects)
    assert fish, "expected specimens"
    optional_keys = {"blurb", "description", "detailRef", "link"}
    for f in fish:
        nulls = [k for k, v in f.items() if v is None]
        assert nulls == [], f"{f['slug']} emitted null for {nulls}"
        # Required keys must still always be present.
        assert {"slug", "title", "species", "size", "depth", "speed", "glow",
                "school", "tags", "metrics"} <= set(f)
        assert set(f) - optional_keys <= set(f)


def test_fish_tank_block_props_have_no_nulls():
    blk = build_fish_tank_block([_proj("oct-mcp")], highlight_slugs=["oct-mcp"])
    assert blk is not None
    assert [k for k, v in blk["props"].items() if v is None] == []


def _dated_proj(slug: str, *, started_on: str | None, ended_on: str | None, **kw) -> dict:
    p = _proj(slug, **kw)
    p["started_on"] = started_on
    p["ended_on"] = ended_on
    return p


def test_dated_projects_get_distinct_chronological_depth():
    projects = [
        _dated_proj("old", started_on="2019-01-01", ended_on="2020-01-01"),
        _dated_proj("new", started_on="2025-01-01", ended_on="2026-01-01"),
    ]
    fish = build_fish_specimens(projects)
    by = {f["slug"]: f for f in fish}
    # newest = shallow (smaller depth)
    assert by["new"]["depth"] < by["old"]["depth"]
    assert by["old"]["startYear"] < by["new"]["startYear"]
    assert by["old"]["endYear"] < by["new"]["endYear"]


def test_ongoing_project_omits_end_year():
    fish = build_fish_specimens(
        [
            _dated_proj("ongoing", started_on="2024-01-01", ended_on=None),
            _dated_proj("done", started_on="2019-01-01", ended_on="2020-01-01"),
        ]
    )
    by = {f["slug"]: f for f in fish}
    assert "endYear" not in by["ongoing"]
    assert "startYear" in by["ongoing"]


def test_undated_projects_fall_back_to_sort_order_depth():
    fish = build_fish_specimens(
        [
            _proj("top", sort_order=0),
            _proj("mid", sort_order=10),
        ]
    )
    by = {f["slug"]: f for f in fish}
    assert "startYear" not in by["top"]
    assert "endYear" not in by["top"]
    assert by["top"]["depth"] < by["mid"]["depth"]


def test_no_startyear_endyear_null_ever_emitted():
    projects = [
        _dated_proj("partial", started_on="2024-01-01", ended_on=None),
        _proj("undated"),
    ]
    fish = build_fish_specimens(projects)
    for f in fish:
        assert f.get("startYear") is not None if "startYear" in f else True
        assert f.get("endYear") is not None if "endYear" in f else True


def test_tank_time_span_present_with_two_dated_projects():
    blk = build_fish_tank_block(
        [
            _dated_proj("a", started_on="2019-01-01", ended_on="2020-01-01"),
            _dated_proj("b", started_on="2025-01-01", ended_on="2026-01-01"),
        ]
    )
    assert blk is not None
    assert "timeSpan" in blk["props"]
    span = blk["props"]["timeSpan"]
    assert span["min"] < span["max"]


def test_tank_time_span_absent_with_fewer_than_two_dated_projects():
    blk = build_fish_tank_block(
        [
            _dated_proj("a", started_on="2019-01-01", ended_on="2020-01-01"),
            _proj("undated"),
        ]
    )
    assert blk is not None
    assert "timeSpan" not in blk["props"]


# --- Job-scoped roster trimming (opt-in via roster_limit/min_relevance) ----


def test_no_roster_limit_keeps_full_inventory_backcompat():
    projects = [_proj(f"p{i}") for i in range(6)]
    fish = build_fish_specimens(projects, job_tokens={"kubernetes"})
    assert {f["slug"] for f in fish} == {p["slug"] for p in projects}


def test_roster_limit_trims_to_relevant_projects_only():
    projects = [
        _proj(
            "helix-ai",
            summary="Agentic AI platform with LangGraph orchestration and MCP tools.",
            tags=["ai", "langgraph"],
        ),
        _proj(
            "helix-devops",
            summary="Kubernetes infra pipeline for continuous deployment automation.",
            tags=["aws", "devops"],
        ),
        _proj(
            "random-repo",
            summary="A random side project unrelated to anything in the posting.",
            tags=["misc"],
        ),
        _proj(
            "other-repo",
            summary="Another unrelated repository, just discovery noise.",
            tags=["misc"],
        ),
    ]
    fish = build_fish_specimens(
        projects,
        job_tokens={"agentic", "langgraph", "mcp"},
        roster_limit=3,
        min_relevance=1,
    )
    slugs = {f["slug"] for f in fish}
    assert "helix-ai" in slugs
    assert "random-repo" not in slugs
    assert "other-repo" not in slugs


def test_roster_limit_with_zero_matches_falls_back_to_full_inventory():
    projects = [_proj(f"p{i}") for i in range(3)]
    fish = build_fish_specimens(
        projects,
        job_tokens={"quantumcomputingxyz"},
        roster_limit=1,
        min_relevance=5,
    )
    assert {f["slug"] for f in fish} == {p["slug"] for p in projects}


def test_roster_limit_keeps_highlight_slug_even_below_threshold():
    projects = [
        _proj("glow-but-weak", summary="Mentions kubernetes exactly once here."),
        _proj("strong-match", summary="Kubernetes kubernetes kubernetes devops platform infra."),
        _proj("no-match", summary="Nothing relevant in this summary at all whatsoever."),
    ]
    fish = build_fish_specimens(
        projects,
        highlight_slugs=["glow-but-weak"],
        job_tokens={"kubernetes"},
        roster_limit=2,
        min_relevance=3,
    )
    slugs = {f["slug"] for f in fish}
    assert "glow-but-weak" in slugs
    assert "no-match" not in slugs


def test_roster_limit_does_not_pin_primary_tag():
    """No project is exempt by tag — a primary-tagged row with zero JD overlap
    is dropped just like any other unrelated row when others do match."""
    projects = [
        _proj("primary-unrelated", tags=["primary"], summary="Totally unrelated content with zero JD overlap words."),
        _proj("matched-a", tags=["side"], summary="Kubernetes devops infra platform automation pipeline."),
        _proj("matched-b", tags=["side"], summary="Kubernetes devops infra platform continuous delivery."),
    ]
    fish = build_fish_specimens(
        projects,
        job_tokens={"kubernetes", "devops", "infra", "platform"},
        roster_limit=2,
        min_relevance=2,
    )
    slugs = {f["slug"] for f in fish}
    assert "primary-unrelated" not in slugs
    assert {"matched-a", "matched-b"} <= slugs


def test_tank_time_span_derives_from_trimmed_roster():
    projects = [
        _dated_proj(
            "old-unrelated",
            started_on="2015-01-01",
            ended_on="2016-01-01",
            summary="Unrelated content, no JD overlap at all in this text.",
        ),
        _dated_proj(
            "match-a",
            started_on="2023-01-01",
            ended_on="2024-01-01",
            summary="Kubernetes devops infra platform pipeline automation.",
        ),
        _dated_proj(
            "match-b",
            started_on="2025-01-01",
            ended_on="2026-01-01",
            summary="Kubernetes devops infra platform continuous delivery.",
        ),
    ]
    blk = build_fish_tank_block(
        projects,
        job_tokens={"kubernetes", "devops", "infra", "platform"},
        roster_limit=2,
        min_relevance=2,
    )
    assert blk is not None
    fish_slugs = {f["slug"] for f in blk["props"]["fish"]}
    assert "old-unrelated" not in fish_slugs
    span = blk["props"]["timeSpan"]
    # Span reflects only the two matched (2023/2024, 2025/2026) projects, not
    # the excluded 2015 row.
    assert span["min"] >= 2023.0

def test_safe_int_and_float_fallback_behavior():
    from plugins.portfolio_plugin.MCPTools.bake_tools import _safe_int, _safe_float
    assert _safe_int("", 8) == 8
    assert _safe_int("none", 8) == 8
    assert _safe_int(None, 8) == 8
    assert _safe_int("unlimited", 8) == 8
    assert _safe_int("5", 8) == 5
    assert _safe_int(3, 8) == 3

    assert _safe_float("", 2.0) == 2.0
    assert _safe_float("none", 2.0) == 2.0
    assert _safe_float(None, 2.0) == 2.0
    assert _safe_float("unlimited", 2.0) == 2.0
    assert _safe_float("1.5", 2.0) == 1.5
    assert _safe_float(3.14, 2.0) == 3.14


def test_build_fish_tank_block_roster_dedup_regression():
    from plugins.portfolio_plugin.compose.fish import build_fish_specimens, build_fish_tank_block, _tank_time_span, _resolve_roster
    projects = [
        _dated_proj("old-unrelated", started_on="2015-01-01", ended_on="2016-01-01", summary="No overlap at all in this text."),
        _dated_proj("match-a", started_on="2023-01-01", ended_on="2024-01-01", summary="Kubernetes devops infra platform pipeline automation."),
        _dated_proj("match-b", started_on="2025-01-01", ended_on="2026-01-01", summary="Kubernetes devops infra platform continuous delivery."),
    ]
    job_tokens = {"kubernetes", "devops", "infra"}

    # "Old way" methodology lock
    roster = _resolve_roster(projects, highlight_slugs=None, job_tokens=job_tokens, roster_limit=2, min_relevance=2)
    span_bounds = _tank_time_span(roster)
    time_span = {"min": round(span_bounds[0], 2), "max": round(span_bounds[1], 2)} if span_bounds else None
    fish_old = build_fish_specimens(projects, job_tokens=job_tokens, time_span=time_span, roster_limit=2, min_relevance=2)

    # "New way" dedup structure
    blk = build_fish_tank_block(projects, job_tokens=job_tokens, roster_limit=2, min_relevance=2)
    assert blk is not None
    fish_new = blk["props"]["fish"]

    assert len(fish_new) == len(fish_old)
    for f_new, f_old in zip(fish_new, fish_old):
        assert f_new["slug"] == f_old["slug"]

    if span_bounds:
        assert blk["props"]["timeSpan"] == time_span

    # Same without job tokens
    blk_no_tokens = build_fish_tank_block(projects)
    assert blk_no_tokens is not None
    fish_no_tokens_old = build_fish_specimens(projects)

    assert len(blk_no_tokens["props"]["fish"]) == len(fish_no_tokens_old)
    for f_new, f_old in zip(blk_no_tokens["props"]["fish"], fish_no_tokens_old):
        assert f_new["slug"] == f_old["slug"]
    assert blk_no_tokens["props"]["timeSpan"]["min"] >= 2015.0


def test_score_blob_ignores_none_tags():
    from plugins.portfolio_plugin.compose.job_tailor import _score_blob, _project_blob
    proj = _proj("has-missing", summary="A good project without keyword.", tags=[None, "ai", "missing-keyword-absent"])
    blob = _project_blob(proj)

    # Prior to fix, None would become "None" and match the "none" token
    score = _score_blob(blob, {"none"})
    assert score == 0.0, f"Expected 0.0 match for 'none' token, got {score}. Blob was: {blob}"
