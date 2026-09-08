"""Tests for ask-mode fishpool recommendations."""

from __future__ import annotations

from plugins.portfolio_plugin.ask.recommend import (
    recommend_projects,
    score_question,
)


def test_score_question_weights():
    """score_question produces documented field weights (5/3/3/2/1)."""
    # 5.0 for exact slug or slug word
    proj_slug = {"slug": "fishpool-backend", "name": "Other", "summary": "None"}
    assert score_question(proj_slug, {"fishpool"}) == 5.0
    assert score_question(proj_slug, {"fishpool-backend"}) == 5.0

    # 3.0 for name word boundary
    proj_name = {"slug": "oct", "name": "Whiskers Agent Server", "summary": "None"}
    assert score_question(proj_name, {"whiskers"}) == 3.0

    # 3.0 for tag match
    proj_tag = {"slug": "proj", "name": "Proj", "tags": ["kubernetes", "terraform"]}
    assert score_question(proj_tag, {"kubernetes"}) == 3.0

    # 2.0 for substring in slug (when not a whole slug word)
    proj_sub = {"slug": "microservices", "name": "App", "summary": "None"}
    assert score_question(proj_sub, {"service"}) == 2.0

    # 1.0 for summary word boundary
    proj_sum = {"slug": "proj", "name": "Proj", "summary": "Observability and metrics pipelines"}
    assert score_question(proj_sum, {"observability"}) == 1.0

    # 0.0 when tokens empty or invalid input
    assert score_question(proj_slug, set()) == 0.0
    assert score_question(None, {"test"}) == 0.0  # type: ignore[arg-type]


def test_near_miss_beats_more_recent():
    """A near-miss (token-scoring) project beats a more recent non-matching one."""
    older_matching = {
        "slug": "old-ai",
        "name": "Old AI Project",
        "tags": ["ai", "langgraph"],
        "started_on": "2020-01-01",
        "ended_on": "2021-01-01",
        "sort_order": 10,
    }
    newer_non_matching = {
        "slug": "new-infra",
        "name": "New Infra Work",
        "tags": ["devops", "terraform"],
        "started_on": "2024-01-01",
        "ended_on": None,
        "sort_order": 0,
    }
    rows = [newer_non_matching, older_matching]
    existing, pool = recommend_projects(rows, ranked=[], tokens={"ai"}, in_tank=set())

    assert len(pool) == 2
    assert pool[0]["slug"] == "old-ai"
    assert pool[0]["reason"] in ("closest tag match", "closest name match")
    assert pool[1]["slug"] == "new-infra"
    assert pool[1]["reason"] == "recent work"


def test_recency_fills_when_no_project_scores():
    """Recency fallback orders by ended_on desc (None=ongoing first), started_on desc, sort_order asc."""
    p_ended_old = {
        "slug": "p-old",
        "name": "Old",
        "started_on": "2020-01-01",
        "ended_on": "2021-01-01",
        "sort_order": 10,
    }
    p_ended_med_so10 = {
        "slug": "p-med-10",
        "name": "Med 10",
        "started_on": "2022-01-01",
        "ended_on": "2023-01-01",
        "sort_order": 10,
    }
    p_ended_med_so5 = {
        "slug": "p-med-5",
        "name": "Med 5",
        "started_on": "2022-01-01",
        "ended_on": "2023-01-01",
        "sort_order": 5,
    }
    p_ongoing_started_2023 = {
        "slug": "p-on-2023",
        "name": "Ongoing 2023",
        "started_on": "2023-01-01",
        "ended_on": None,
        "sort_order": 10,
    }
    p_ongoing_started_2024 = {
        "slug": "p-on-2024",
        "name": "Ongoing 2024",
        "started_on": "2024-01-01",
        "ended_on": None,
        "sort_order": 10,
    }

    rows = [
        p_ended_old,
        p_ended_med_so10,
        p_ended_med_so5,
        p_ongoing_started_2023,
        p_ongoing_started_2024,
    ]
    existing, pool = recommend_projects(rows, ranked=[], tokens=set(), in_tank=set(), limit=10)

    slug_order = [p["slug"] for p in pool]
    # Expected order:
    # 1. p-on-2024 (ongoing, started 2024)
    # 2. p-on-2023 (ongoing, started 2023)
    # 3. p-med-5 (ended 2023, sort_order 5)
    # 4. p-med-10 (ended 2023, sort_order 10)
    # 5. p-old (ended 2021)
    assert slug_order == [
        "p-on-2024",
        "p-on-2023",
        "p-med-5",
        "p-med-10",
        "p-old",
    ]


def test_existing_pool_split_respects_in_tank():
    """Projects in in_tank go to existing_recs, others to pool_recs."""
    rows = [
        {"slug": "tank-1", "name": "Tank 1"},
        {"slug": "tank-2", "name": "Tank 2"},
        {"slug": "pool-1", "name": "Pool 1"},
        {"slug": "pool-2", "name": "Pool 2"},
    ]
    in_tank = {"tank-1", "tank-2"}
    existing, pool = recommend_projects(rows, ranked=[], tokens=set(), in_tank=in_tank)

    assert [p["slug"] for p in existing] == ["tank-1", "tank-2"]
    assert [p["slug"] for p in pool] == ["pool-1", "pool-2"]
    assert all(p["in_tank"] is True for p in existing)
    assert all(p["in_tank"] is False for p in pool)


def test_empty_rows_returns_empty_tuple():
    """Empty rows or rows without slug return ([], [])."""
    assert recommend_projects([], ranked=[], tokens=set(), in_tank=set()) == ([], [])
    assert recommend_projects(
        [{"name": "No Slug", "summary": "Test"}], ranked=[], tokens=set(), in_tank=set()
    ) == ([], [])


def test_non_empty_rows_with_zero_token_overlap_returns_entries():
    """Non-empty rows with zero token overlap never returns both empty lists."""
    rows = [
        {
            "slug": "solo-project",
            "name": "Solo Project",
            "summary": "Multiplayer survival game in Unity 6.",
            "tags": ["game", "unity"],
        }
    ]
    existing, pool = recommend_projects(
        rows, ranked=[], tokens={"kubernetes", "devops"}, in_tank=set()
    )
    assert not (len(existing) == 0 and len(pool) == 0)
    assert len(pool) == 1
    assert pool[0]["slug"] == "solo-project"
    assert pool[0]["reason"] == "recent work"


def test_limit_honoured_on_both_lists():
    """limit caps existing_recs and pool_recs independently."""
    rows = [
        {"slug": f"tank-{i}", "name": f"Tank {i}"} for i in range(10)
    ] + [
        {"slug": f"pool-{i}", "name": f"Pool {i}"} for i in range(10)
    ]
    in_tank = {f"tank-{i}" for i in range(10)}
    existing, pool = recommend_projects(rows, ranked=[], tokens=set(), in_tank=in_tank, limit=3)

    assert len(existing) == 3
    assert len(pool) == 3


def test_wire_dict_format_and_reasons():
    """Returned items are trimmed wire dicts with correct reasons and clipped blurbs."""
    long_summary = (
        "This is a very long inventory summary description for testing word boundary "
        "clipping behavior in recommend_projects. It should be clipped cleanly to 200 "
        "characters maximum without cutting off a word midway or returning the entire "
        "lengthy inventory text block."
    )
    rows = [
        {
            "slug": "proj-tag",
            "name": "Project Tag",
            "tags": ["specialtag"],
            "summary": long_summary,
        },
        {
            "slug": "proj-name",
            "name": "UniqueNamedProject",
            "tags": ["other"],
            "summary": "Short summary",
        },
        {
            "slug": "proj-summary",
            "name": "Another Project",
            "tags": ["other"],
            "summary": "SpecialSummaryWord here",
        },
    ]

    # Tag match
    _, pool_tag = recommend_projects(rows, [], {"specialtag"}, in_tank=set())
    assert pool_tag[0]["slug"] == "proj-tag"
    assert pool_tag[0]["reason"] == "closest tag match"
    assert len(pool_tag[0]["blurb"]) <= 200
    assert not pool_tag[0]["blurb"].endswith(" ")

    # Name match
    _, pool_name = recommend_projects(rows, [], {"uniquenamedproject"}, in_tank=set())
    assert pool_name[0]["slug"] == "proj-name"
    assert pool_name[0]["reason"] == "closest name match"

    # Summary match
    _, pool_sum = recommend_projects(rows, [], {"specialsummaryword"}, in_tank=set())
    assert pool_sum[0]["slug"] == "proj-summary"
    assert pool_sum[0]["reason"] == "closest match"


def test_ranked_position_priority():
    """Projects in ranked list appear ahead of non-ranked recency fallback when score is 0."""
    p1 = {"slug": "p1", "started_on": "2024-01-01", "ended_on": None}
    p2 = {"slug": "p2", "started_on": "2020-01-01", "ended_on": "2021-01-01"}
    p3 = {"slug": "p3", "started_on": "2019-01-01", "ended_on": "2020-01-01"}

    rows = [p1, p2, p3]
    # Ranked explicitly prioritizes older p3
    ranked = [p3]

    _, pool = recommend_projects(rows, ranked=ranked, tokens=set(), in_tank=set())
    assert [p["slug"] for p in pool] == ["p3", "p1", "p2"]
