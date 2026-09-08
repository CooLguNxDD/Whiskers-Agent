"""Generic session blackboard for flow-runner stages.

Promoted from ``plugins/portfolio_plugin/compose/blackboard.py`` (the
``PortfolioDraft``/``DraftStore`` pair) so any plugin's ``FlowSpec`` stages can
share intermediate state without a plugin-specific store. Slot shape is
intentionally an open ``dict[str, Any]`` — a ``StageSpec.writes`` name is just
a key into it — since the whole point is that stages are declared in JSON and
core cannot know their payload shapes ahead of time.

Portfolio's ``PortfolioDraft`` is unaffected by this promotion: it keeps its
typed dataclass and its own store for the legacy (non-flow) pipeline. Flow
stages that need portfolio-shaped fields simply write flat keys here
(``evidence_pack``, ``layout``, ...) instead of dataclass attributes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Blackboard:
    """In-progress cross-stage state for one flow-runner session."""

    session_id: str
    goal: str
    goal_class: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    stage_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Updates the blackboard's modified timestamp to defer eviction."""
        self.updated_at = time.time()

    def get(self, key: str, default: Any = None) -> Any:
        return self.slots.get(key, default)

    def write(self, **values: Any) -> None:
        """Merge ``values`` into slots and refresh the eviction clock."""
        self.slots.update(values)
        self.touch()

    def to_public(self) -> dict[str, Any]:
        """JSON-safe snapshot for MCP / debugging (slot keys only, not values)."""
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "goal_class": self.goal_class,
            "slot_keys": sorted(self.slots.keys()),
            "stage_count": len(self.stage_history),
        }


class BlackboardStore:
    """Bounded in-process blackboard sessions (mirrors GoapAgent session store pattern)."""

    def __init__(self, max_size: int = 64, ttl_s: float = 3600.0) -> None:
        self._max = max_size
        self._ttl = ttl_s
        self._boards: dict[str, Blackboard] = {}

    def create(self, goal: str, session_id: str | None = None, **kwargs: Any) -> Blackboard:
        """Instantiate and register a new Blackboard within the bounded store."""
        self._evict()
        sid = session_id or f"flow-{uuid.uuid4().hex[:12]}"
        board = Blackboard(session_id=sid, goal=goal, **kwargs)
        self._boards[sid] = board
        while len(self._boards) > self._max:
            oldest = min(self._boards.values(), key=lambda b: b.created_at)
            self._boards.pop(oldest.session_id, None)
        return board

    def get(self, session_id: str) -> Blackboard | None:
        """Retrieve an active Blackboard by session id after evicting stale entries."""
        self._evict()
        return self._boards.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._boards.pop(session_id, None) is not None

    def _evict(self) -> None:
        now = time.time()
        dead = [k for k, b in self._boards.items() if now - b.updated_at > self._ttl]
        for k in dead:
            self._boards.pop(k, None)


_STORE: BlackboardStore | None = None


def get_blackboard_store() -> BlackboardStore:
    """Process-wide BlackboardStore singleton."""
    global _STORE
    if _STORE is None:
        _STORE = BlackboardStore()
    return _STORE
