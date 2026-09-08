"""Unwrap a CatPortfolio Ask ``run_graph`` wrapper into a visitor turn.

CatPortfolio embeds the real question plus a page skeleton inside a long
assistant prompt. The graph must classify and seed FlowSpec boards from the
visitor sentence — never from the wrapper's hardcoded ``scoped_ask``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_VISITOR_RE = re.compile(
    r"Visitor Question:\s*(.+?)(?:\n\s*\n|\n\s*\[System:|\n\s*Page context:|\Z)",
    re.I | re.S,
)
_PAGE_CTX_RE = re.compile(r"Page context:\s*", re.I)
_FLOW_DEBUG_RE = re.compile(r"^Flow '.+' (completed|failed)\b")


@dataclass(frozen=True)
class VisitorTurn:
    """Parsed CatPortfolio ask turn — visitor sentence + page skeleton."""

    question: str
    goal_class: str
    view: str = "text"
    block_index: list[dict[str, Any]] | None = None
    tank_slugs: list[str] | None = None
    dag: dict[str, Any] | None = None
    time_span: dict[str, Any] | None = None
    add_slugs: list[str] | None = None
    visitor_session_id: str | None = None


def parse_visitor_turn(user_message: str) -> VisitorTurn | None:
    """Return a ``VisitorTurn`` when *user_message* is a CatPortfolio wrapper.

    Bare playground / CLI queries return ``None``. ``goal_class`` is classified
    from the visitor sentence via ``classify_specialist_goal`` — the wrapper's
    ``goal_class=scoped_ask`` is ignored so a bake question is not stolen.
    """
    raw = str(user_message or "")
    if "Visitor Question:" not in raw:
        return None
    if "Page context:" not in raw and "CatPortfolio ask turn" not in raw:
        return None

    match = _VISITOR_RE.search(raw)
    question = (match.group(1).strip() if match else "").strip()
    if not question:
        return None

    page = _parse_page_context(raw)
    from plugins.portfolio_plugin.compose.recipes import classify_specialist_goal

    return VisitorTurn(
        question=question,
        goal_class=classify_specialist_goal(question),
        view=str(page.get("view") or "text"),
        block_index=_list_of_dicts(page.get("block_index")),
        tank_slugs=_list_of_str(page.get("tank_slugs")),
        dag=page.get("dag") if isinstance(page.get("dag"), dict) else None,
        time_span=page.get("time_span") if isinstance(page.get("time_span"), dict) else None,
        add_slugs=_list_of_str(page.get("add_slugs")),
        visitor_session_id=(
            str(page.get("visitor_session_id")).strip()
            if page.get("visitor_session_id")
            else None
        ),
    )


def is_flow_debug_summary(text: str | None) -> bool:
    """True when *text* is the FlowSpec envelope debug line, not visitor prose."""
    return bool(_FLOW_DEBUG_RE.match((text or "").strip()))


def fallback_answer_markdown(
    *,
    question: str = "",
    focus_slug: str = "",
    highlight_slugs: list[str] | tuple[str, ...] | None = None,
    answer_markdown: str | None = None,
    recommendations: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    pool: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> str:
    """Guarantee visitor-facing markdown even when the agentic answer stage continues.

    Keeps a real ``answer_markdown`` when the agent wrote one; replaces the
    ``Flow '…' completed`` debug string (and empty output) with a grounded
    fallback — fish pool candidates first (they're spawnable via
    ``spawn_pooled_fish``), then in-tank recommendations, then highlight
    slugs / focus.
    """
    existing = (answer_markdown or "").strip()
    if existing and not is_flow_debug_summary(existing):
        return existing
    pool_slugs = _slug_list(pool)
    if pool_slugs:
        listed = ", ".join(f"`{s}`" for s in pool_slugs[:8])
        return (
            f"I don't have a project for that. Closest from the inventory: "
            f'{listed} — say "add them to the tank" and I\'ll spawn them.'
        )
    rec_names: list[str] = []
    for rec in recommendations or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or rec.get("slug") or "").strip()
        if name:
            rec_names.append(name)
    if rec_names:
        listed = ", ".join(f"`{n}`" for n in rec_names[:8])
        return f"Not a direct match — you could ask about {listed}."
    slugs = [str(s).strip() for s in (highlight_slugs or []) if str(s).strip()]
    focus = str(focus_slug or "").strip()
    if slugs:
        listed = ", ".join(f"`{s}`" for s in slugs[:8])
        return f"Here's what stands out for that question: {listed}."
    if focus:
        return f"The closest project for that question is `{focus}`."
    q = str(question or "").strip()
    if q:
        return (
            "I don't have a grounded project match for that yet — ask about a "
            "specific system, or paste a job description to bake a layout."
        )
    return "I couldn't draft an answer for that turn."


def visitor_plugin_context(turn: VisitorTurn) -> dict[str, Any]:
    """FlowSpec board inputs for ``portfolio_ask_v1`` / bake."""
    return {
        "question": turn.question,
        "view": turn.view,
        "block_index": list(turn.block_index or []),
        "tank_slugs": list(turn.tank_slugs or []),
        "dag": turn.dag,
        "time_span": turn.time_span,
        "add_slugs": list(turn.add_slugs or []),
        "visitor_session_id": turn.visitor_session_id or "",
    }


def _parse_page_context(raw: str) -> dict[str, Any]:
    """Decode the JSON object after ``Page context:``; empty dict on miss."""
    marker = _PAGE_CTX_RE.search(raw)
    if marker is None:
        return {}
    tail = raw[marker.end():]
    brace = tail.find("{")
    if brace < 0:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail[brace:])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    out = [v for v in value if isinstance(v, dict)]
    return out


def _list_of_str(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(v) for v in value if v is not None and str(v).strip()]


def _slug_list(entries: Any) -> list[str]:
    """Pull non-empty slug values out of a list of recommendation dicts."""
    if not isinstance(entries, (list, tuple)):
        return []
    slugs: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "").strip()
        if slug:
            slugs.append(slug)
    return slugs
