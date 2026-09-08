"""Unit tests for core MemoryRegistry."""

import pytest

from core.memory.registry import (
    MemoryNamespace,
    get_memory_registry,
    plugin_collection,
    reset_memory_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_memory_registry_for_tests()
    yield
    reset_memory_registry_for_tests()


def test_core_namespaces_preseeded():
    reg = get_memory_registry()
    assert reg.get("global_memory") is not None
    assert reg.get("plan_recipes").backend == "memory"
    assert reg.get("plan_anti_patterns").owner == "core"


def test_plugin_register_and_anti_squat():
    reg = get_memory_registry()
    ns = MemoryNamespace(
        owner="jules_plugin",
        name="notes",
        collection=plugin_collection("jules_plugin", "notes"),
        backend="memory",
        description="Jules notes",
    )
    reg.register_namespace(ns)
    assert reg.get("jules_plugin__notes").owner == "jules_plugin"

    with pytest.raises(ValueError):
        reg.register_namespace(
            MemoryNamespace(
                owner="jules_plugin",
                name="steal",
                collection="global_memory",
                backend="memory",
            )
        )

    with pytest.raises(ValueError):
        reg.register_namespace(
            MemoryNamespace(
                owner="jules_plugin",
                name="bad",
                collection="other_plugin__x",
                backend="memory",
            )
        )


def test_unregister_owner():
    reg = get_memory_registry()
    reg.register_entries(
        "search_plugin",
        [{"name": "default", "backend": "search"}],
        replace=True,
    )
    assert reg.get("search_plugin__default") is not None
    n = reg.unregister_owner("search_plugin")
    assert n >= 1
    assert reg.get("search_plugin__default") is None
    # core remains
    assert reg.get("global_memory") is not None


def test_register_entries_normalize():
    reg = get_memory_registry()
    out = reg.register_entries(
        "foo_plugin",
        [{"name": "docs", "backend": "search", "description": "docs"}],
    )
    assert len(out) == 1
    assert out[0].collection == "foo_plugin__docs"
    assert out[0].backend == "search"
