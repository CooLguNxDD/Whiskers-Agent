"""Opaque-ID seed hygiene for GOAP world seeding.

LLM goal extraction often fabricates id-like params (e.g. sessionId="Jules"
from "get first Jules session"). Those seeds falsely satisfy have:{param}
preconditions and break collection→by-id chaining. Reject weak free-text
values for id-shaped keys unless they look like real identifiers or already
exist in working_memory from a prior successful step.

Also guards agent/session ``prompt``-style fields: bare role labels
(``frontend-b``, ``docs``) are not executable task briefs and must be
enriched from the user query rather than passed through verbatim.
"""
from __future__ import annotations

import re
from typing import Any

from core_graph.goap.integrate._shared import (
    _INSTRUCTION_PARAM_NAMES,
    _SEARCH_PARAM_NAMES,
)

# Param names that look like resource identifiers (not free-text search seeds).
_ID_PARAM_RE = re.compile(
    r"^(id|.*_id|.*Id|.*ID|.*_ID)$"
)

# Known fleet / review role labels that must never be the entire agent prompt.
_ROLE_LABEL_RE = re.compile(
    r"^(frontend|backend|docs|ui|api|security|review)(-[a-z0-9]+)?$",
    re.IGNORECASE,
)

# Lookup imperative with no topic payload — "search up!", "look it up", "google that".
# Must not match "search up Mika" / "look up the Tea Party" (those have a subject).
_BARE_SEARCH_FOLLOWUP_RE = re.compile(
    r"""
    ^\s*
    (?:(?:please|pls|can\s+you|could\s+you|would\s+you)\s+)*
    (?:
        (?:search|look)(?:\s+(?:it|that|this|them|her|him))?\s*up
        | (?:google|find|search|look)(?:\s+(?:it|that|this|them|her|him))?
    )
    (?:\s+(?:please|pls|for\s+me|real\s+quick|thanks))?
    [\s!.?]*
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Acceptable id-like value shapes (UUID, numeric, long opaque token, path id).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
_NUMERIC_RE = re.compile(r"^\d{3,}$")
_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# Opaque tokens: long enough alnum with at least one digit (or mixed case + digit)
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{12,}$")


def is_id_like_param(name: str) -> bool:
    """True when a parameter name looks like a resource id (sessionId, page_id, id)."""
    if not name or not isinstance(name, str):
        return False
    return bool(_ID_PARAM_RE.match(name))


def looks_like_real_id(value: Any) -> bool:
    """True when a scalar value looks like a real resource id, not free text."""
    if value is None or isinstance(value, (list, dict, bool)):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    s = str(value).strip()
    if not s or len(s) < 3:
        return False
    # Spaces / multi-word natural language → reject
    if " " in s or "\n" in s or "\t" in s:
        return False
    if _UUID_RE.match(s) or _HEX_RE.match(s) or _NUMERIC_RE.match(s):
        return True
    if _PATH_ID_RE.match(s):
        return True
    # Short pure-alpha words like "Jules" are free text, not ids
    if s.isalpha() and len(s) < 16:
        return False
    if _OPAQUE_TOKEN_RE.match(s) and any(c.isdigit() for c in s):
        return True
    return False


def is_bare_search_followup(value: Any) -> bool:
    """True when text is only a lookup imperative, with no search topic."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    return bool(_BARE_SEARCH_FOLLOWUP_RE.match(s))


def topic_from_history(history) -> str | None:
    """Newest user turn that is not itself a bare search follow-up."""
    for turn in reversed(history or []):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        content = str(turn.get("content") or "").strip()
        if not content or is_bare_search_followup(content):
            continue
        if role in ("user", "human"):
            return content
    return None


def resolve_search_query(
    user_query: str | None,
    *,
    history=None,
    last_summary: str | None = None,
) -> str | None:
    """Search-arg fallback: keep a topical query, replace a bare 'search up!' from context."""
    uq = (user_query or "").strip()
    if not is_bare_search_followup(uq):
        return uq or None
    topic = topic_from_history(history)
    if topic:
        return topic
    summary = (last_summary or "").strip()
    if summary:
        return summary.split("\n", 1)[0][:200]
    return None


def apply_followup_search_topic(
    seed_values: dict | None,
    user_query: str | None,
    *,
    history=None,
    last_summary: str | None = None,
) -> dict:
    """Replace a bare lookup-imperative query seed with the prior-turn topic.

    Must run *before* GOAP world seeding: ``have:query`` is a precondition of
    ``web_search``, so dropping the seed without substituting would miss the
    plan entirely. A non-imperative seed the LLM already extracted is kept.
    When the current utterance is not a follow-up, leftover imperative seeds
    are still stripped.
    """
    seeds = dict(seed_values or {})
    topical = None
    if is_bare_search_followup(user_query or ""):
        resolved = resolve_search_query(
            user_query, history=history, last_summary=last_summary
        )
        if resolved and not is_bare_search_followup(resolved):
            topical = resolved
    replaced = False
    for k in list(seeds):
        if k not in _SEARCH_PARAM_NAMES:
            continue
        if is_bare_search_followup(seeds[k]):
            if topical:
                seeds[k] = topical
                replaced = True
            else:
                del seeds[k]
    if topical and not replaced and not any(k in _SEARCH_PARAM_NAMES for k in seeds):
        seeds["query"] = topical
    return seeds


def is_instruction_param(name: str) -> bool:
    """True when a parameter holds a multi-line agent/session instruction."""
    if not name or not isinstance(name, str):
        return False
    return name in _INSTRUCTION_PARAM_NAMES or name.lower() in _INSTRUCTION_PARAM_NAMES


def is_weak_instruction_value(value: Any) -> bool:
    """True when a value is a bare label, not a usable agent task brief.

    Role tokens (``frontend-b``, ``docs``), single short words, and other
    one-token seeds are weak. Multi-line or substantial prose is rich enough.
    """
    if value is None or isinstance(value, (list, dict, bool)):
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return True
    # Substantial / multi-line instructions are fine
    if "\n" in s or len(s) >= 80:
        return False
    if _ROLE_LABEL_RE.match(s):
        return True
    # Single short token (no whitespace) — e.g. "alpha", "docs", "backend-a"
    if " " not in s and "\t" not in s and len(s) <= 40:
        return True
    # Very short multi-word labels without task language
    words = s.split()
    if len(words) <= 2 and len(s) < 48:
        return True
    return False


def enrich_instruction_value(value: Any, user_query: str | None) -> Any:
    """Expand a weak instruction seed into a task brief grounded in user_query.

    When the LLM fans out Jules sessions on role labels (frontend-a, docs, …)
    the bare label is not an executable prompt. Fold the original user request
    around it so the agent receives the real task (branch, base, review mode)
    plus a role focus — not only the label string.
    """
    if not is_weak_instruction_value(value):
        return value
    label = str(value).strip() if value is not None else ""
    uq = (user_query or "").strip()
    if not uq:
        return value
    # Already the full query (or equal) — nothing useful to prepend
    if not label or label == uq:
        return uq
    if uq.startswith(label) and len(uq) > len(label) + 20:
        # rare: label is a prefix of the query
        return uq
    return (
        f"{uq}\n\n"
        f"---\n"
        f"Session focus / role: **{label}**.\n"
        f"Expand this into a complete, self-contained agent brief for that focus:\n"
        f"- evaluation criteria and scope for this role\n"
        f"- branch / base / diff constraints if mentioned above\n"
        f"- expected report artifacts (file names when conventional)\n"
        f"- report-only: do not modify production source files\n"
        f"Do NOT treat the bare role name as the entire task."
    )


def sanitize_seed_values(
    seed_values: dict | None,
    working_memory: dict | None = None,
) -> dict:
    """Drop weak id-like seeds that would falsely satisfy GOAP preconditions.

    Keeps a seed when:
    - the key is not id-like (query, prompt, pageSize, …), or
    - the value already exists under the same key in working_memory, or
    - the value looks like a real resource id.
    """
    if not seed_values:
        return {}
    wm = working_memory or {}
    out: dict = {}
    for k, v in seed_values.items():
        if not is_id_like_param(k):
            # "query": "search up!" is not a topic — drop so fill_literal_args
            # can substitute the prior-turn subject instead of searching the verb.
            if k in _SEARCH_PARAM_NAMES and is_bare_search_followup(v):
                continue
            out[k] = v
            continue
        # Trusted if already present in working memory (prior step / resume)
        if k in wm and wm.get(k) is not None:
            out[k] = v
            continue
        if looks_like_real_id(v):
            out[k] = v
            continue
        # Drop weak id seed (e.g. sessionId="Jules")
    return out


def strip_failed_seed_keys(
    seed_values: dict | None,
    working_memory: dict | None,
    failed_args: dict | None,
) -> tuple[dict, dict]:
    """Remove keys that appeared in a failed step's resolved args from seeds/memory.

    Returns (new_seed_values, new_working_memory).
    """
    seeds = dict(seed_values or {})
    mem = dict(working_memory or {})
    for k in (failed_args or {}):
        seeds.pop(k, None)
        # Only strip id-like keys from working_memory so we don't erase useful
        # non-id context (query terms, etc.) that may still help replanning.
        if is_id_like_param(k):
            mem.pop(k, None)
    return seeds, mem
