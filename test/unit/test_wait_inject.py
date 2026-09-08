"""
MTU-3 acceptance tests — inject_wait_steps.

Ground truth for the deterministic wait-step injector. Given a plan and the
process-level poll specs, it inserts a `kind:"wait"` poll step between an async
"create" op and its "download" consumer, rewriting the spec's $create.* match
refs and shifting later $steps[i] refs.
"""

import pytest

from core.plugin_loader.skill_registry import set_plugin_poll_specs, clear
from core_graph.goap.wait_inject import inject_wait_steps


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

CANDS = [
    {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "method": "POST"},
    {"operation_id": "get_get_export_requests", "plugin_id": "report_plugin", "method": "GET", "is_fast_path": True},
    {"operation_id": "post_download_export", "plugin_id": "report_plugin", "method": "POST"},
]


@pytest.fixture(autouse=True)
def _isolate():
    clear()
    set_plugin_poll_specs("report_plugin", [SPEC])
    yield
    clear()


def test_inserts_wait_between_create_and_download():
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 1}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin",
         "arg_bindings": {"exportRequestId": "$steps[0].id"}},
    ]
    out = inject_wait_steps(plan, CANDS)
    assert len(out) == 3
    assert out[0]["operation_id"] == "post_add_export_request"
    assert out[1].get("kind") == "wait"
    assert out[1]["operation_id"] == "get_get_export_requests"
    assert out[2]["operation_id"] == "post_download_export"


def test_wait_step_carries_poll_metadata():
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 7}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin",
         "arg_bindings": {"exportRequestId": "$steps[0].id"}},
    ]
    wait = inject_wait_steps(plan, CANDS)[1]
    assert wait["until"] == {"field": "status", "equals": "COMPLETED"}
    assert wait["fail_on"] == ["FAILED", "EXPIRED"]
    assert wait["interval_s"] == 60
    assert wait["max_polls"] == 30
    # $create.id rewritten to reference the create step's index (0)
    assert wait["match"] == {"id": "$steps[0].id"}
    # poll op inherits projectId from the create step so it can query
    assert wait.get("args", {}).get("projectId") == 7


def test_download_binding_to_create_unchanged():
    # download references the create step (index 0, before the insertion point),
    # so its binding must stay $steps[0].id.
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 1}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin",
         "arg_bindings": {"exportRequestId": "$steps[0].id"}},
    ]
    out = inject_wait_steps(plan, CANDS)
    assert out[2]["arg_bindings"]["exportRequestId"] == "$steps[0].id"


def test_forward_refs_after_insertion_are_shifted():
    # A step AFTER the insertion point that references another post-insertion
    # step must have its index bumped by 1.
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 1}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin",
         "arg_bindings": {"exportRequestId": "$steps[0].id"}},
        {"operation_id": "consume_download", "plugin_id": "report_plugin",
         "arg_bindings": {"csv": "$steps[1].data"}},  # refs the download step (index 1 -> 2)
    ]
    out = inject_wait_steps(plan, CANDS + [{"operation_id": "consume_download", "plugin_id": "report_plugin"}])
    assert len(out) == 4
    # download moved to index 2; the consumer ref must now be $steps[2].data
    assert out[3]["arg_bindings"]["csv"] == "$steps[2].data"


def test_no_spec_match_is_noop():
    clear()  # no poll specs registered
    plan = [{"operation_id": "list_records", "plugin_id": "fake_plugin"}]
    assert inject_wait_steps(plan, CANDS) == plan


def test_create_present_but_poll_op_absent_from_candidates_noop():
    # If the poll op isn't a candidate this turn, do not inject.
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 1}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin"},
    ]
    cands_no_poll = [c for c in CANDS if c["operation_id"] != "get_get_export_requests"]
    out = inject_wait_steps(plan, cands_no_poll)
    assert len(out) == 2
    assert all(s.get("kind") != "wait" for s in out)


def test_idempotent_when_wait_already_present():
    plan = [
        {"operation_id": "post_add_export_request", "plugin_id": "report_plugin", "args": {"projectId": 1}},
        {"operation_id": "get_get_export_requests", "plugin_id": "report_plugin", "kind": "wait",
         "until": {"field": "status", "equals": "COMPLETED"}},
        {"operation_id": "post_download_export", "plugin_id": "report_plugin"},
    ]
    out = inject_wait_steps(plan, CANDS)
    assert sum(1 for s in out if s.get("kind") == "wait") == 1
