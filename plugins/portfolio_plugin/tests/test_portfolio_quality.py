"""Content quality gates for bake (stubs, card body sanitize, dedupe)."""

from __future__ import annotations

from plugins.portfolio_plugin.compose.quality import (
    _strip_html_comments,
    _truncate_at_boundary,
    dedupe_layout_cards,
    filter_docs_for_context,
    filter_projects_for_layout,
    is_portfolio_worthy_project,
    is_stub_summary,
    sanitize_card_body,
)


def test_stub_summary_detection():
    assert is_stub_summary("GitHub repository CooLguNxDD/leetcode")
    assert is_stub_summary("CooLguNxDD/Fisoul")
    assert is_stub_summary(
        "This template provides a minimal setup to get React working in Vite with HMR."
    )
    assert not is_stub_summary(
        "Andrew Liang has been the primary builder of Helix’s AI layer: "
        "the systems that let operators talk to Helix in plain language."
    )


def test_filter_projects_drops_stubs_keeps_primary():
    rows = [
        {
            "slug": "leetcode",
            "name": "leetcode",
            "summary": "GitHub repository CooLguNxDD/leetcode",
            "tags": [],
        },
        {
            "slug": "whiskers-ai",
            "name": "Whiskers Agent AI",
            "summary": "Andrew built the MCP + agentic AI layer for enterprise messaging at scale.",
            "tags": ["primary", "AI"],
            "metrics": [{"label": "Commits", "value": "395"}],
        },
        {
            "slug": "pullfrog",
            "name": "pullfrog",
            "summary": "Open-source model-agnostic BYOK GitHub bot that runs in GitHub Actions for PR review.",
            "tags": ["TypeScript"],
        },
    ]
    out = filter_projects_for_layout(rows)
    slugs = {p["slug"] for p in out}
    assert "leetcode" not in slugs
    assert "whiskers-ai" in slugs
    assert "pullfrog" in slugs
    assert is_portfolio_worthy_project(rows[1])
    assert not is_portfolio_worthy_project(rows[0])


def test_filter_keeps_operator_selected_stub_with_links():
    """Hand-kept inventory (no Notion / thin README) still belongs in the tank."""
    row = {
        "slug": "fisoul",
        "name": "Fisoul",
        "summary": "CooLguNxDD/Fisoul",
        "tags": ["C#"],
        "links": [{"label": "github", "href": "https://github.com/CooLguNxDD/Fisoul"}],
        "context_sources": [
            {
                "id": "disc:github:CooLguNxDD/Fisoul",
                "kind": "github",
                "ref": "CooLguNxDD/Fisoul",
            }
        ],
    }
    assert is_portfolio_worthy_project(row)
    assert filter_projects_for_layout([row])[0]["slug"] == "fisoul"


def test_filter_keeps_manual_prose_without_external_sources():
    """Manual upsert with no github/notion sources must still clear the gate."""
    row = {
        "slug": "side-quest",
        "name": "Side Quest",
        "summary": "A hand-written case study about shipping a small multiplayer prototype.",
        "tags": ["game"],
        "metrics": [],
        "links": [],
        "context_sources": [],
    }
    assert is_portfolio_worthy_project(row)
    assert "side-quest" in {p["slug"] for p in filter_projects_for_layout([row])}


def test_sanitize_card_body_rejects_chrome():
    assert sanitize_card_body("**Period Covered:** September 2025 – July 2026") is None
    assert sanitize_card_body("| Field | Detail |") is None
    assert sanitize_card_body("│  Operator / staff (Helix UI)      │") is None
    assert sanitize_card_body(
        "prevent feedback loops where the AI's own comments trigger another review."
    ) is None
    good = (
        "Andrew Liang has been the primary builder of Helix’s AI layer: "
        "the systems that let operators and internal tools talk to Helix "
        "in plain language, safely acting on contact data."
    )
    assert sanitize_card_body(good) is not None
    assert "primary builder" in (sanitize_card_body(good) or "")
    # Skip period-covered header and recover real prose
    mixed = (
        "**Period Covered:** September 2025 – July 2026\n\n"
        "Andrew has made sustained, high-impact contributions to the Whiskers Agent "
        "distributed platform over approximately ten months of full-stack work."
    )
    cleaned = sanitize_card_body(mixed)
    assert cleaned is not None
    assert "Period Covered" not in cleaned
    assert "high-impact" in cleaned


def test_sanitize_card_body_skips_readme_badge_wall():
    """A GitHub README opening with a shields.io badge wall must not surface
    the badge markdown as the card body — it should skip past to real prose."""
    readme = (
        "# CooLguNxDD/Fisoul\n\n"
        "# Fisoul 🐟⚔️\n\n"
        "[![Unity 6](https://img.shields.io/badge/Unity-6000.x-black?style=flat&logo=unity)](https://unity.com/)\n"
        "[![Networking](https://img.shields.io/badge/Network-FishNet-blue)](https://fish-networking.gitbook.io/)\n"
        "[![Transport](https://img.shields.io/badge/Transport-FishyUnityTransport-green)](https://github.com/FirstGearGames/FishNet)\n\n"
        "A competitive multiplayer fishing-combat game built in Unity 6 using "
        "the DOTS/ECS architecture with FishNet networking for authoritative "
        "server simulation across dozens of concurrent players."
    )
    cleaned = sanitize_card_body(readme)
    assert cleaned is not None
    assert "img.shields.io" not in cleaned
    assert "![" not in cleaned
    assert "competitive multiplayer fishing-combat" in cleaned


def test_strip_html_comments_complete_and_unterminated():
    assert "kept" in _strip_html_comments("kept <!-- secret --> prose")
    assert "<!--" not in _strip_html_comments("hello <!-- unclosed")
    assert "<!" not in _strip_html_comments("A long body <!-- comment that would slice to <!")


def test_sanitize_card_body_strips_html_comments_before_truncate():
    readme = (
        "Andrew built the Whiskers Agent MCP gateway that lets operators talk to "
        "Helix in plain language and route tools across tenants. "
        "<!-- leftover README chrome that used to leak as a trailing <! "
        + ("x" * 80)
        + "\n"
        "More production prose about retrieval and agent tooling."
    )
    cleaned = sanitize_card_body(readme)
    assert cleaned is not None
    assert "<!--" not in cleaned
    assert not cleaned.rstrip().endswith("<!")
    assert "Whiskers Agent MCP gateway" in cleaned


def test_truncate_at_boundary_no_op_when_short():
    assert _truncate_at_boundary("short text.", 600) == "short text."


def test_truncate_at_boundary_prefers_sentence_end():
    text = "First sentence here. Second sentence that pushes well past the cutoff point for sure." + "x" * 40
    out = _truncate_at_boundary(text, 40)
    assert out == "First sentence here."
    assert not out.endswith("…")


def test_truncate_at_boundary_falls_back_to_word_boundary_with_ellipsis():
    # No sentence-ending punctuation within the window at all, so the cut
    # must land on a whitespace boundary — never mid-token — and mark itself.
    text = "no punctuation here just a long unbrokenrunofwordslikethis continuing on and on and on"
    out = _truncate_at_boundary(text, 30)
    assert out.endswith("…")
    stripped = out[:-1]
    assert stripped == stripped.rstrip()  # no trailing space before the ellipsis
    for word in stripped.split():
        assert word in text.split()  # every kept token is a real, whole word


def test_sanitize_card_body_never_cuts_mid_word_on_long_paragraph():
    """A long-form project summary (this is exactly the Helix AI Platform /
    Platform Engineering card shape) must truncate at a real boundary, not
    silently stop mid-word with no indication anything was cut."""
    long_paragraph = (
        "Andrew Liang has been the primary builder of Helix's AI layer: "
        "the systems that let operators and internal tools talk to Helix "
        "in plain language, safely act on contact and account data, and "
        "automate code review with multiple AI coding agents across the "
        "period reviewed, spanning roughly five months of continuous work "
        "on gateway infrastructure, retrieval pipelines, and agent tooling "
        "that now backs every operator-facing AI surface in production "
        "and every internal automation the platform team relies on daily "
        "for triage, summarization, and safe multi-tenant data access, "
        "with an on-call rotation and audit trail covering every request."
    )
    assert len(long_paragraph) > 600
    cleaned = sanitize_card_body(long_paragraph)
    assert cleaned is not None
    assert len(cleaned) <= 601  # 600 + possible "…"
    # The historical bug: text silently stopped mid-word ("...and auto").
    assert not cleaned.rstrip("…").endswith(("auto", "aut", "au"))
    last_word = cleaned.rstrip("…").split()[-1]
    assert last_word.isalpha() or last_word.endswith((".", "!", "?"))


def test_bare_badge_line_rejected_outright():
    badge_only = "[![Unity 6](https://img.shields.io/badge/Unity-6000.x-black?style=flat&logo=unity)](https://unity.com/)"
    assert sanitize_card_body(badge_only) is None


def test_badge_with_nested_parens_in_url_is_recognized():
    """shields.io badge query strings routinely contain their own parens
    (e.g. "Agent-Model_Context_Protocol_(MCP)") — a naive [^)]* regex stops at
    the first ')' inside the url and leaves a mangled tail as "real" content."""
    nested_badge = (
        "[![Protocol](https://img.shields.io/badge/Agent-Model_Context_Protocol_(MCP)-green)]()"
    )
    readme = (
        nested_badge + "\n\n"
        "> **High-Performance Procedural 2D/3D Voxel Engine & Agentic "
        "World-Crafting Pipeline for Unity DOTS**\n\n"
        "This system generates fully explorable voxel worlds at runtime "
        "using a deterministic agentic pipeline driven by large language "
        "model planning and procedural noise synthesis."
    )
    cleaned = sanitize_card_body(readme)
    assert cleaned is not None
    assert "img.shields.io" not in cleaned
    assert "green)" not in cleaned
    assert "High-Performance" in cleaned or "voxel worlds" in cleaned


def test_dedupe_layout_cards_by_title():
    blocks = [
        {
            "type": "card",
            "id": "card-helix-ai",
            "props": {
                "title": "Helix AI Platform",
                "body": "Andrew Liang has been the primary builder of Helix’s AI layer for internal tooling.",
            },
        },
        {
            "type": "card",
            "id": "proj-helix-ai",
            "props": {
                "title": "Helix AI Platform",
                "body": "| Field | Detail |",
            },
        },
        {"type": "hero", "id": "h1", "props": {"name": "A"}},
    ]
    out = dedupe_layout_cards(blocks)
    cards = [b for b in out if b.get("type") == "card"]
    assert len(cards) == 1
    assert cards[0]["id"] == "card-helix-ai"
    assert "primary builder" in (cards[0]["props"].get("body") or "")
    assert any(b.get("type") == "hero" for b in out)


def test_dedupe_refills_body_from_project_summary():
    blocks = [
        {
            "type": "card",
            "id": "card-whiskers-platform",
            "props": {
                "title": "Whiskers Agent Platform Engineering",
                "body": "**Period Covered:** September 2025 – July 2026",
            },
        }
    ]
    projects = [
        {
            "slug": "whiskers-platform",
            "name": "Whiskers Agent Platform Engineering",
            "summary": (
                "Andrew has made sustained, high-impact contributions to the Whiskers Agent "
                "distributed platform over approximately ten months of full-stack work."
            ),
            "tags": ["primary"],
        }
    ]
    out = dedupe_layout_cards(blocks, projects=projects)
    body = (out[0].get("props") or {}).get("body") or ""
    assert "Period Covered" not in body
    assert "high-impact" in body


def test_filter_docs_prefers_case_study():
    docs = [
        {
            "ref": "CooLguNxDD/leetcode",
            "kind": "github",
            "excerpt": "GitHub repository CooLguNxDD/leetcode",
        },
        {
            "ref": "local:helix_AI_contribution.md#part1",
            "kind": "case_study",
            "excerpt": (
                "Andrew built a multi-region deployment platform for team messaging. "
                "The stack uses MCP gateways and pgvector for routing."
            ),
        },
    ]
    out = filter_docs_for_context(docs, limit=5)
    assert out[0]["kind"] == "case_study"
    assert all(d.get("kind") != "github" or "GitHub repository" not in d.get("excerpt", "") for d in out)
