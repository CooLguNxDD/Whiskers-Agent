"""Typed shapes for core memory records (dict-compatible)."""

from __future__ import annotations

from typing import Any, TypedDict


class MemoryRecord(TypedDict, total=False):
    """Generic memory hit returned by search/list."""

    id: int
    content: str
    tags: list[str]
    tier: str
    similarity: float
    created_at: str | None
    metadata: dict[str, Any]


class PlanRecipe(TypedDict, total=False):
    """Learned successful plan recipe."""

    id: int
    content: str
    op_ids: list[str]
    plugin_ids: list[str]
    similarity: float
    metadata: dict[str, Any]


class AntiPattern(TypedDict, total=False):
    """Learned failed plan sequence to avoid."""

    id: int
    content: str
    plan_ops: list[str]
    outcome: str
    detail: str
    similarity: float
    metadata: dict[str, Any]
