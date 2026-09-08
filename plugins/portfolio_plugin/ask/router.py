"""Deterministic ask router — classify a visitor question into a patch plan.

No LLM. The model authors prose downstream; it never decides what changes.
Prompt-steered patching already exists on the ``?j=`` path and does not hold
(the agent still reaches for whole-page compose), so classification lives here
as a server function and the FlowSpec's tool globs enforce it.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from plugins.portfolio_plugin.ask.recommend import recommend_projects, score_question
from plugins.portfolio_plugin.ask.targets import canonical_block_id, resolve_targets

logger = logging.getLogger("whiskers.plugins.portfolio.ask.router")

# Intent vocabulary. ``bake`` hands off to portfolio_bake_v1 and stops.
INTENTS = frozenset(
    {
        "bake",
        "focus_fish",
        "add_fish",
        "discover",
        "patch_blocks",
        "answer_only",
        "recommend",
    }
)

# Caps how many lexically-matched projects spawn into the tank in one
# "add_fish" turn — mirrors the discovery-spawn cap in ask_tools.py so a
# batch question ("game and devops projects") doesn't just add the single
# top-scored match while stranding the rest as focus-only pills.
MAX_BATCH_ADD_FISH = 4

_STAR_RE = re.compile(
    r"\b(star story|tell me a story|walk me through|challenge|situation|"
    r"what happened|how did you handle)\b",
    re.I,
)
_ARCH_RE = re.compile(
    r"\b(architect\w*|design(?:ed)?|diagram|topology|data ?flow|pipeline|"
    r"system design|how does it work)\b",
    re.I,
)


@dataclass(frozen=True)
class AskPlan:
    """What one ask turn is allowed to change."""

    intent: str = "answer_only"
    focus_slug: str = ""
    add_slugs: tuple[str, ...] = ()
    block_steps: tuple[dict[str, Any], ...] = ()
    target_ids: tuple[str, ...] = ()
    highlight_slugs: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    ranked_slugs: tuple[str, ...] = ()
    pool_slugs: tuple[str, ...] = ()
    recommend_slugs: tuple[str, ...] = ()
    recommendations: tuple[dict[str, Any], ...] = ()
    pool_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view for the MCP tool boundary / FlowSpec blackboard."""
        return {
            "intent": self.intent,
            "focus_slug": self.focus_slug,
            "add_slugs": list(self.add_slugs),
            "block_steps": [dict(s) for s in self.block_steps],
            "target_ids": list(self.target_ids),
            "highlight_slugs": list(self.highlight_slugs),
            "confidence": round(float(self.confidence), 4),
            "reason": self.reason,
            "ranked_slugs": list(self.ranked_slugs),
            "pool_slugs": list(self.pool_slugs),
            "recommend_slugs": list(self.recommend_slugs),
            "recommendations": [dict(r) for r in self.recommendations if isinstance(r, dict)],
            "pool_id": self.pool_id,
        }


def _ask_settings() -> dict[str, Any]:
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    cfg = SETTINGS.get("ask") if isinstance(SETTINGS, dict) else None
    return dict(cfg) if isinstance(cfg, dict) else {}


def _confidence_floor() -> float:
    try:
        return float(_ask_settings().get("confidence_floor", 0.35))
    except (TypeError, ValueError):
        return 0.35


def _max_patch_blocks() -> int:
    try:
        return max(1, min(5, int(_ask_settings().get("max_patch_blocks", 3))))
    except (TypeError, ValueError):
        return 3


def _is_bake_intent(question: str) -> bool:
    """True when the question is really a job bake (company + role present)."""
    try:
        from plugins.portfolio_plugin.agents.bake import _parse_job_signals

        sig = _parse_job_signals(question, {}) or {}
        return bool(sig.get("company")) and bool(sig.get("role"))
    except (ImportError, TypeError, ValueError, KeyError, AttributeError) as exc:
        # fail-open: a parse miss is an ask, not a bake
        logger.debug("ask router: bake signal parse fail-open: %s", exc)
        return False


# Visitor glue. Keep in sync with CatPortfolio ``matchFish.ts`` STOPWORDS.
# A long summary contains these constantly; they must not pick a fish.
_ASK_STOPWORDS = frozenset(
    {
        # articles / determiners
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        # pronouns
        "i",
        "im",
        "ive",
        "id",
        "me",
        "my",
        "mine",
        "we",
        "us",
        "our",
        "you",
        "your",
        "yours",
        "they",
        "them",
        "their",
        "it",
        "its",
        # auxiliaries / modals
        "is",
        "am",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "having",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "dont",
        "didnt",
        "cant",
        "wont",
        # question words
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        # ask / portfolio verbs
        "tell",
        "show",
        "describe",
        "explain",
        "walk",
        "talk",
        "ask",
        "give",
        "list",
        "see",
        "look",
        "looking",
        "know",
        "knew",
        "want",
        "wanted",
        "need",
        "needed",
        "get",
        "got",
        "getting",
        "make",
        "made",
        "use",
        "used",
        "using",
        "about",
        "regarding",
        "related",
        # portfolio filler
        "work",
        "worked",
        "working",
        "works",
        "built",
        "build",
        "building",
        "project",
        "projects",
        "experience",
        "experiences",
        "portfolio",
        "resume",
        "site",
        "page",
        "thing",
        "things",
        "stuff",
        "one",
        "ones",
        "info",
        "information",
        "detail",
        "details",
        # conjunctions / prepositions
        "and",
        "or",
        "but",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "of",
        "on",
        "in",
        "at",
        "to",
        "by",
        "as",
        "if",
        "than",
        "then",
        "so",
        "not",
        "no",
        "nor",
        "through",
        "across",
        "around",
        "between",
        "after",
        "before",
        "during",
        "while",
        "because",
        # quantity / fillers
        "any",
        "some",
        "more",
        "much",
        "many",
        "most",
        "other",
        "another",
        "such",
        "same",
        "like",
        "just",
        "also",
        "too",
        "very",
        "really",
        "quite",
        "still",
        "even",
        "only",
        "please",
        "maybe",
        "actually",
        "basically",
        "something",
        "anything",
        "everything",
        "nothing",
        "here",
        "there",
        "now",
        "again",
        "well",
        "yeah",
        "yes",
        "ok",
        "okay",
        "hey",
        "hi",
        "hello",
    }
)


def _ask_tokens(question: str) -> set[str]:
    """Question tokens with visitor glue stripped — ask focus only."""
    from plugins.portfolio_plugin.compose.job_tailor import job_tokens

    return {t for t in job_tokens("", "", question) if t not in _ASK_STOPWORDS}


_score_question = score_question


def _text_block_steps(
    question: str,
    slug: str,
    block_index: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map a question shape onto 1-N block steps for the text view.

    Locked mapping: a named project updates its ``card``; a STAR-shaped ask
    updates/inserts a ``starStory``; an architecture ask updates an existing
    ``archDiagram``/``flowAnim``. A vague domain question produces no steps —
    the caller falls back to highlight-only so the page does not churn.
    """
    steps: list[dict[str, Any]] = []
    targets: list[str] = []

    def _add(block_type: str, *, insertable: bool) -> None:
        if len(steps) >= limit:
            return
        found = resolve_targets(block_index, block_type, slug)
        if found:
            block_id = found[0]
        elif insertable:
            block_id = canonical_block_id(block_type, slug)
        else:
            return
        if block_id in targets:
            return
        targets.append(block_id)
        steps.append(
            {
                "block_type": block_type,
                "block_id": block_id,
                "slugs": [slug],
                "query": question,
                "top_k": 1 if block_type == "starStory" else 4,
            }
        )

    if not slug:
        return steps, targets

    _add("card", insertable=True)
    if _STAR_RE.search(question or ""):
        _add("starStory", insertable=True)
    if _ARCH_RE.search(question or ""):
        # Architecture blocks are grounded rebuilds, never blind inserts —
        # an archDiagram with no existing source would be invented content.
        _add("archDiagram", insertable=False)
        _add("flowAnim", insertable=False)
    return steps, targets


def _recommend_split(
    rows: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    tokens: set[str],
    in_tank: set[str],
    *,
    limit: int = 4,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]:
    """Partition candidates into (recommend_slugs, pool_slugs, recommendations)."""
    existing_recs, pool_recs = recommend_projects(
        rows, ranked, tokens, in_tank=in_tank, limit=limit
    )
    recs = tuple(
        dict(p)
        for p in (*existing_recs, *pool_recs)
        if isinstance(p, dict) and p.get("slug")
    )
    return (
        tuple(str(p["slug"]) for p in existing_recs if isinstance(p, dict) and p.get("slug")),
        tuple(str(p["slug"]) for p in pool_recs if isinstance(p, dict) and p.get("slug")),
        recs,
    )


def _normalize_add_slugs(raw: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Deduped, lowercased, capped client-supplied add slugs."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        slug = str(item or "").strip().lower()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
        if len(out) >= MAX_BATCH_ADD_FISH:
            break
    return tuple(out)


def _stash_pool_recs(
    session_id: str | None,
    recs: tuple[dict[str, Any], ...],
    *,
    tenant_id: int,
) -> str:
    """Stage not-in-tank recommendations so an add-chip turn (or an explicit
    ``spawn_pooled_fish`` call) can take them. Returns the stashed pool_id, or
    ``""`` when there was nothing to stash or the stash failed open.
    """
    pool_recs = [dict(r) for r in recs if isinstance(r, dict) and r.get("slug") and not r.get("in_tank")]
    if not pool_recs:
        return ""
    sid = str(session_id or "").strip() or uuid.uuid4().hex[:16]
    try:
        from plugins.portfolio_plugin.ask.fish_pool import stash_pool

        return stash_pool(sid, pool_recs, tenant_id=int(tenant_id or 1))
    except Exception as exc:
        logger.debug("ask router: fish pool stash fail-open: %s", exc)
        return ""


def _ground_add_slugs(
    requested: tuple[str, ...],
    *,
    rows: list[dict[str, Any]],
    visitor_session_id: str | None,
) -> tuple[str, ...]:
    """Keep only slugs present in inventory or the visitor's fish pool."""
    inventory = {
        str(p.get("slug") or "").strip().lower()
        for p in rows
        if isinstance(p, dict) and p.get("slug")
    }
    pooled: set[str] = set()
    sid = str(visitor_session_id or "").strip()
    if sid and requested:
        try:
            from plugins.portfolio_plugin.ask.fish_pool import take_from_session

            taken = take_from_session(sid, list(requested))
        except Exception as exc:
            logger.debug("ask router: fish pool take fail-open: %s", exc)
            taken = []
        pooled = {
            str(p.get("slug") or "").strip().lower()
            for p in taken
            if isinstance(p, dict) and p.get("slug")
        }
    return tuple(s for s in requested if s in inventory or s in pooled)


async def route_ask(
    question: str,
    *,
    tenant_id: int = 1,
    view: str = "text",
    block_index: list[dict[str, Any]] | None = None,
    tank_slugs: list[str] | None = None,
    add_slugs: list[str] | tuple[str, ...] | None = None,
    visitor_session_id: str | None = None,
) -> AskPlan:
    """Classify *question* into an ``AskPlan``.

    ``block_index`` is the client's ``[{"id","type","slug"?}]`` skeleton and
    ``tank_slugs`` the specimens currently in the tank — neither requires the
    caller to ship a whole layout. ``add_slugs`` short-circuits to
    ``intent="add_fish"`` after inventory/pool validation (never trust a
    client-supplied slug into the roster).
    """
    from plugins.portfolio_plugin.compose.job_tailor import matched_project_slugs
    from plugins.portfolio_plugin.compose.ranking import rank_projects_by_query
    from plugins.portfolio_plugin.store import list_projects

    q = str(question or "").strip()
    if not q:
        return AskPlan(intent="answer_only", reason="empty question")

    requested = _normalize_add_slugs(add_slugs)
    if not requested and _is_bake_intent(q):
        # Hand off to portfolio_bake_v1 — a real bake is the one allowed
        # whole-page rebuild. An explicit add_slugs chip wins over bake.
        return AskPlan(intent="bake", reason="company+role parsed from question")

    index = [e for e in (block_index or []) if isinstance(e, dict)]
    in_tank = {str(s).strip().lower() for s in (tank_slugs or []) if str(s).strip()}
    view_n = "tank" if str(view or "").strip().lower() == "tank" else "text"

    try:
        rows = await list_projects(tenant_id=int(tenant_id)) or []
    except Exception as exc:
        logger.warning("ask router: list_projects fail-open: %s", exc)
        rows = []

    if requested:
        grounded = _ground_add_slugs(
            requested, rows=rows, visitor_session_id=visitor_session_id
        )
        tank_ids = resolve_targets(index, "fishTank")
        if not grounded:
            return AskPlan(
                intent="answer_only",
                reason="add_slugs not in inventory or fish pool",
            )
        if not tank_ids:
            return AskPlan(
                intent="answer_only",
                reason="no fishTank block to add into",
            )
        return AskPlan(
            intent="add_fish",
            focus_slug=grounded[0],
            add_slugs=grounded,
            target_ids=tuple(tank_ids),
            highlight_slugs=grounded,
            reason=f"explicit add_slugs ({len(grounded)})",
        )

    try:
        ranked = await rank_projects_by_query(rows, q, tenant_id=int(tenant_id), top_k=12)
    except Exception as exc:
        logger.debug("ask router: rank fail-open: %s", exc)
        ranked = list(rows)

    tokens = _ask_tokens(q)
    highlights = tuple(matched_project_slugs(ranked, tokens, limit=5))
    ranked_slugs = tuple(
        str(p.get("slug") or "").strip().lower()
        for p in ranked
        if isinstance(p, dict) and p.get("slug")
    )

    # Ask focus is lexical, not bake-ranker order. A dense employer row can
    # outrank a clean tag hit ("ai") in rank_projects_by_query; using that
    # top as the chosen fish is how "your ai project" spawned the wrong one
    # or fell through to discovery.
    top = None
    confidence = 0.0
    for project in rows:
        if not isinstance(project, dict) or not project.get("slug"):
            continue
        score = _score_question(project, tokens)
        if score > confidence:
            confidence = score
            top = project
    top_slug = str(top.get("slug") or "").strip().lower() if top else ""
    floor = _confidence_floor()

    if not top_slug or confidence < floor:
        # Never invent a specimen. Below the floor the honest move is a
        # discovery job (when enabled) or a plain prose answer. Compute
        # recommendations first so a discovery miss can still show chips.
        recommend_slugs, pool_slugs, recommendations = _recommend_split(
            rows, ranked, tokens, in_tank, limit=4
        )
        pool_id = _stash_pool_recs(
            visitor_session_id, recommendations, tenant_id=int(tenant_id)
        )
        if bool(_ask_settings().get("discovery_fallback")):
            return AskPlan(
                intent="discover",
                confidence=confidence,
                highlight_slugs=highlights,
                ranked_slugs=ranked_slugs,
                recommend_slugs=recommend_slugs,
                pool_slugs=pool_slugs,
                recommendations=recommendations,
                pool_id=pool_id,
                reason=f"no project above confidence floor {floor}",
            )
        if not recommend_slugs and not pool_slugs:
            return AskPlan(
                intent="answer_only",
                confidence=confidence,
                highlight_slugs=highlights,
                ranked_slugs=ranked_slugs,
                reason=f"no project above confidence floor {floor}; discovery disabled",
            )
        return AskPlan(
            intent="recommend",
            confidence=confidence,
            highlight_slugs=highlights,
            ranked_slugs=ranked_slugs,
            recommend_slugs=recommend_slugs,
            pool_slugs=pool_slugs,
            recommendations=recommendations,
            pool_id=pool_id,
            reason=f"no project above confidence floor {floor}; recommending nearest matches",
        )

    if view_n == "tank":
        if top_slug in in_tank:
            # Focus is instant client-side; the tank block is still rebuilt so
            # the dossier blurb is scored against this question. add_slugs
            # MUST stay empty here — top_slug is already in_tank, so this
            # intent must never respawn it (overlay.py enforces this too).
            tank_ids = resolve_targets(index, "fishTank")
            return AskPlan(
                intent="focus_fish",
                focus_slug=top_slug,
                target_ids=tuple(tank_ids),
                highlight_slugs=highlights or (top_slug,),
                confidence=confidence,
                ranked_slugs=ranked_slugs,
                reason="top-ranked project already in tank",
            )
        tank_ids = resolve_targets(index, "fishTank")
        if not tank_ids:
            recommend_slugs, pool_slugs, recommendations = _recommend_split(
                rows, ranked, tokens, in_tank, limit=4
            )
            pool_id = _stash_pool_recs(
                visitor_session_id, recommendations, tenant_id=int(tenant_id)
            )
            if not recommend_slugs and not pool_slugs:
                return AskPlan(
                    intent="answer_only",
                    focus_slug=top_slug,
                    confidence=confidence,
                    highlight_slugs=highlights,
                    ranked_slugs=ranked_slugs,
                    reason="tank view requested but layout has no fishTank block",
                )
            return AskPlan(
                intent="recommend",
                focus_slug=top_slug,
                confidence=confidence,
                highlight_slugs=highlights,
                ranked_slugs=ranked_slugs,
                recommend_slugs=recommend_slugs,
                pool_slugs=pool_slugs,
                recommendations=recommendations,
                pool_id=pool_id,
                reason="tank view requested but layout has no fishTank block; recommending nearest matches",
            )
        # A batch question ("game and devops projects") lexically matches more
        # than one row — spawn every matched one that isn't already in the
        # tank, not just the single top-scored project. top_slug always leads
        # (it drives focus + the dossier blurb) even if matched_project_slugs
        # scored it lower than another hit.
        add_slugs = tuple(
            dict.fromkeys(
                (top_slug, *(s for s in highlights if s and s not in in_tank))
            )
        )[:MAX_BATCH_ADD_FISH]
        return AskPlan(
            intent="add_fish",
            focus_slug=top_slug,
            add_slugs=add_slugs,
            target_ids=tuple(tank_ids),
            highlight_slugs=highlights or (top_slug,),
            confidence=confidence,
            ranked_slugs=ranked_slugs,
            reason=f"grounded project(s) absent from tank ({len(add_slugs)})",
        )

    # Text-mode ask still patches the tank when the project is not already
    # swimming — both views share the working layout, so a spawned fish
    # appears after the visitor switches to tank. Reserve that slot against
    # the budget before spending it on text blocks, so a patch_blocks +
    # add_fish overlay never exceeds max_patch_blocks in total. `add` may
    # carry several slugs (batch match) but it is still one fishTank block,
    # so the budget reservation is 0/1, not len(add).
    add = () if top_slug in in_tank else tuple(
        dict.fromkeys((top_slug, *(s for s in highlights if s and s not in in_tank)))
    )[:MAX_BATCH_ADD_FISH]
    add_block_count = 1 if add else 0
    steps, targets = _text_block_steps(
        q, top_slug, index, limit=max(1, _max_patch_blocks() - add_block_count)
    )
    if not steps and not add:
        return AskPlan(
            intent="answer_only",
            confidence=confidence,
            highlight_slugs=highlights,
            ranked_slugs=ranked_slugs,
            reason="vague/domain question — highlight only, no block replacement",
        )
    if not steps and add:
        tank_ids = resolve_targets(index, "fishTank")
        if tank_ids:
            return AskPlan(
                intent="add_fish",
                focus_slug=top_slug,
                add_slugs=add,
                target_ids=tuple(tank_ids),
                highlight_slugs=highlights or (top_slug,),
                confidence=confidence,
                ranked_slugs=ranked_slugs,
                reason="text ask: grounded project absent from tank",
            )
    return AskPlan(
        intent="patch_blocks",
        focus_slug=top_slug,
        add_slugs=add,
        block_steps=tuple(steps),
        target_ids=tuple(targets),
        highlight_slugs=highlights or (top_slug,),
        confidence=confidence,
        ranked_slugs=ranked_slugs,
        reason="named project resolved to text blocks",
    )


def plan_from_dict(data: dict[str, Any] | None) -> AskPlan:
    """Rebuild an ``AskPlan`` from its ``to_dict`` form (FlowSpec stage hop)."""
    d = data if isinstance(data, dict) else {}
    intent = str(d.get("intent") or "answer_only")
    if intent not in INTENTS:
        intent = "answer_only"
    steps = [s for s in (d.get("block_steps") or []) if isinstance(s, dict)]
    try:
        confidence = float(d.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return AskPlan(
        intent=intent,
        focus_slug=str(d.get("focus_slug") or ""),
        add_slugs=tuple(str(s) for s in (d.get("add_slugs") or []) if s),
        block_steps=tuple(steps),
        target_ids=tuple(str(t) for t in (d.get("target_ids") or []) if t),
        highlight_slugs=tuple(str(s) for s in (d.get("highlight_slugs") or []) if s),
        confidence=confidence,
        reason=str(d.get("reason") or ""),
        ranked_slugs=tuple(str(s) for s in (d.get("ranked_slugs") or []) if s),
        pool_slugs=tuple(str(s) for s in (d.get("pool_slugs") or []) if s),
        recommend_slugs=tuple(str(s) for s in (d.get("recommend_slugs") or []) if s),
        recommendations=tuple(
            dict(r)
            for r in (d.get("recommendations") or [])
            if isinstance(r, dict) and r.get("slug")
        ),
        pool_id=str(d.get("pool_id") or ""),
    )
