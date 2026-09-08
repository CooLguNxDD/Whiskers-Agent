"""
MTU-2 acceptance tests — poll_specs registry store.

Ground truth for the process-level poll-spec store that mirrors the skill
registry. Specs are declared in a plugin manifest ("poll_specs") and loaded at
plugin load; the wait-step injector reads them via get_all_poll_specs().
"""

import pytest

from core.plugin_loader.skill_registry import (
    set_plugin_poll_specs,
    get_plugin_poll_specs,
    get_all_poll_specs,
    clear,
)


SPEC = {
    "after": "post_add_export_request",
    "poll_op": "get_get_export_requests",
    "before": "post_download_export",
    "match": {"id": "$create.id"},
    "until": {"field": "status", "equals": "COMPLETED"},
    "fail_on": ["FAILED", "EXPIRED"],
    "interval_s": 60,
    "max_polls": 30,
}


@pytest.fixture(autouse=True)
def _isolate():
    clear()
    yield
    clear()


def test_set_and_get_by_plugin():
    set_plugin_poll_specs("report_plugin", [SPEC])
    got = get_plugin_poll_specs("report_plugin")
    assert isinstance(got, list)
    assert got[0]["poll_op"] == "get_get_export_requests"


def test_get_unknown_plugin_returns_empty():
    assert get_plugin_poll_specs("nope") == []


def test_get_all_flattens_across_plugins():
    set_plugin_poll_specs("a", [SPEC])
    set_plugin_poll_specs("b", [{"after": "x", "poll_op": "y"}])
    allspecs = get_all_poll_specs()
    assert isinstance(allspecs, list)
    poll_ops = {s.get("poll_op") for s in allspecs}
    assert poll_ops == {"get_get_export_requests", "y"}


def test_clear_named_only():
    set_plugin_poll_specs("a", [SPEC])
    set_plugin_poll_specs("b", [{"after": "x", "poll_op": "y"}])
    clear("a")
    assert get_plugin_poll_specs("a") == []
    assert get_plugin_poll_specs("b")[0]["poll_op"] == "y"


def test_empty_specs_is_safe():
    set_plugin_poll_specs("a", [])
    assert get_plugin_poll_specs("a") == []
    set_plugin_poll_specs("", [SPEC])  # blank name ignored
    assert get_all_poll_specs() == []


def test_wait_step_fields_present_on_execution_step():
    # ExecutionStep is a total=False TypedDict; the wait contract keys must be
    # declared so type-checkers/readers know the shape. Runtime check: the
    # annotations include the new keys.
    from core_graph.states import ExecutionStep
    ann = ExecutionStep.__annotations__
    for k in ("kind", "match", "until", "fail_on", "interval_s", "max_polls"):
        assert k in ann, f"ExecutionStep missing wait field: {k}"


def test_poll_spec_contract_keys():
    """Wait-step injector contract: after/poll_op/until/fail_on/interval/max_polls."""
    for k in ("after", "poll_op", "until", "fail_on", "interval_s", "max_polls"):
        assert k in SPEC
    assert SPEC["until"]["field"] == "status"
    assert SPEC["until"]["equals"] == "COMPLETED"
