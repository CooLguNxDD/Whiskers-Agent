"""
World state primitive for Goal-Oriented Action Planning (GOAP).
"""
from typing import Iterable


class WorldState:
    """
    An immutable value object representing the world state in GOAP search.
    """

    _facts: frozenset[str]

    def __init__(self, facts: Iterable[str] = ()) -> None:
        """
        Initialize the WorldState with an iterable of facts.
        """
        self._facts = frozenset(facts)

    @property
    def facts(self) -> frozenset[str]:
        """
        Get the underlying immutable set of facts.
        """
        return self._facts

    def has(self, fact: str) -> bool:
        """
        Check if a specific fact is present in the world state.
        """
        return fact in self._facts

    def satisfies(self, preconditions: Iterable[str]) -> bool:
        """
        Check if all specified preconditions are satisfied by this world state.
        """
        return all(fact in self._facts for fact in preconditions)

    def missing(self, preconditions: Iterable[str]) -> set[str]:
        """
        Return the subset of preconditions that are not present in this world state.
        """
        return {fact for fact in preconditions if fact not in self._facts}

    def apply(self, effects: Iterable[str]) -> "WorldState":
        """
        Return a new WorldState with the given effects unioned in.
        """
        return WorldState(self._facts.union(effects))

    def __eq__(self, other: object) -> bool:
        """
        Determine if two WorldState objects are equal based solely on their facts.
        """
        if not isinstance(other, WorldState):
            return NotImplemented
        return self._facts == other._facts

    def __hash__(self) -> int:
        """
        Compute a hash based solely on the facts.
        """
        return hash(self._facts)

    def __repr__(self) -> str:
        """
        Return a concise string representation of the WorldState.
        """
        return f"WorldState({set(self._facts)})"
