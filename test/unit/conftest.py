"""Shared fixtures for test/unit/.

Autouse cleanup for the model-role layer (core_graph/model_roles/): its pool
snapshot has a 5s TTL and its registry is a process-wide singleton, both of
which can leak across tests that don't explicitly reset them (see
test_planner_decompose_hints.py's autouse pool/policy stub — it patches
core.llm_config_service.list_pool, not core.llm.pool_manager.list_pool, which
is what the snapshot actually reads).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    from core_graph.model_roles.registry import _reset_model_roles_for_tests
    from core_graph.model_roles.resolver import invalidate_pool_snapshot

    invalidate_pool_snapshot()
    _reset_model_roles_for_tests()
    yield
    invalidate_pool_snapshot()
    _reset_model_roles_for_tests()
