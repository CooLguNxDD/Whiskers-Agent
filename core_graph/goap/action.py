"""
GOAP Action class modeling a route as a planning action.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoapAction:
    """
    Represents an action in the Goal-Oriented Action Planning (GOAP) space.
    Derived from a route candidate.
    """

    operation_id: str
    plugin_id: str
    intent: str
    preconditions: frozenset[str]
    effects: frozenset[str]
    effect_fields: tuple[tuple[str, str], ...]
    cost: float = 1.0
    candidate: dict | None = field(default=None, compare=False, hash=False)
    model: str | None = None
    precondition_params: tuple[tuple[str, str], ...] = ()

    def effect_field_map(self) -> dict[str, str]:
        """
        Return a dictionary mapping effect facts to their bound field names.
        """
        return dict(self.effect_fields)

    def precondition_param_map(self) -> dict[str, str]:
        """
        Return a dictionary mapping precondition facts to their real parameter names.
        """
        return dict(self.precondition_params)
