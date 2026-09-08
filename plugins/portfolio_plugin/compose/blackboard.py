"""Portfolio draft blackboard for the specialist stack."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PortfolioDraft:
    """In-progress portfolio discover/compose/bake state for one specialist session."""

    session_id: str
    goal: str
    audience: str = "default"
    theme: str | None = None
    query: str = ""
    resolved_sources: list[dict] = field(default_factory=list)
    context_docs: list[dict] = field(default_factory=list)
    index_report: dict | None = None
    sections: list[dict] = field(default_factory=list)
    sources_meta: list[dict] = field(default_factory=list)
    layout: dict | None = None
    validation_errors: list[str] = field(default_factory=list)
    short_id: str | None = None
    job_signals: dict = field(default_factory=dict)
    phase: str = "init"  # init|discover|index|compose|validate|bake|done
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Updates the draft's internal modified timestamp to defer eviction."""
        self.updated_at = time.time()

    def to_public(self) -> dict[str, Any]:
        """JSON-safe snapshot for MCP / debugging."""
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "audience": self.audience,
            "theme": self.theme,
            "query": self.query,
            "phase": self.phase,
            "section_count": len(self.sections),
            "doc_count": len(self.context_docs),
            "has_layout": self.layout is not None,
            "short_id": self.short_id,
            "validation_errors": list(self.validation_errors),
        }


class DraftStore:
    """Bounded in-process draft sessions (mirrors GoapAgent session store pattern)."""

    def __init__(self, max_size: int = 64, ttl_s: float = 3600.0) -> None:
        self._max = max_size
        self._ttl = ttl_s
        self._drafts: dict[str, PortfolioDraft] = {}

    def create(self, goal: str, session_id: str | None = None, **kwargs: Any) -> PortfolioDraft:
        """Instantiates and registers a new PortfolioDraft within the bounded process store."""
        self._evict()
        sid = session_id or f"draft-{uuid.uuid4().hex[:12]}"
        draft = PortfolioDraft(session_id=sid, goal=goal, **kwargs)
        self._drafts[sid] = draft
        while len(self._drafts) > self._max:
            oldest = min(self._drafts.values(), key=lambda d: d.created_at)
            self._drafts.pop(oldest.session_id, None)
        return draft

    def get(self, session_id: str) -> PortfolioDraft | None:
        """Retrieves an active PortfolioDraft by its session identifier after evicting stale drafts."""
        self._evict()
        return self._drafts.get(session_id)

    def delete(self, session_id: str) -> bool:
        """Removes a PortfolioDraft from the store and returns its existence boolean."""
        return self._drafts.pop(session_id, None) is not None

    def _evict(self) -> None:
        now = time.time()
        dead = [k for k, d in self._drafts.items() if now - d.updated_at > self._ttl]
        for k in dead:
            self._drafts.pop(k, None)


_STORE: DraftStore | None = None


def get_draft_store() -> DraftStore:
    """Process-wide draft store singleton."""
    global _STORE
    if _STORE is None:
        _STORE = DraftStore()
    return _STORE
