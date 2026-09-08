"""
Unit tests for GOAP collection mapping, regression prevention, and chained search->fetch planning.
"""

import copy
import pytest
from core_graph.goap.derive import derive_action, _is_collection_op
from core_graph.goap.integrate import (
    goap_plan_from_goal,
    fill_literal_args,
    detect_fanout_count,
    _inject_defensive_fanout,
    _normalize_item_bindings,
    goap_steps_to_state,
    inject_literal_list_fanout,
    collapse_homogeneous_fanout,
    derive_parallel_groups,
)
from core_graph.goap.planner import GoapPlan


def _proxy_chain_steps():
    return [
        {"operation_id": "proxy_Notion-Dev__notion-search", "args": {}, "arg_bindings": {}},
        {"operation_id": "proxy_Notion-Dev__notion-fetch", "args": {}, "arg_bindings": {"id": "$steps[0].id"}},
    ]


# --------------------------------------------------------------------------- #
# Fan-out count detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q,expected", [
    ("search for cat page, pick 5 of them and fetch the 5 pages", 5),
    ("fetch five pages and summarize", 5),
    ("get both records", 2),
    ("fetch all of them", None),       # unbounded → caller fans over everything
    ("search for cats", None),         # no count
])
def test_detect_fanout_count(q, expected):
    assert detect_fanout_count(q) == expected


# --------------------------------------------------------------------------- #
# Defensive fan-out injection
# --------------------------------------------------------------------------- #

def test_defensive_fanout_rewrites_chain_with_limit():
    """search→fetch chain gains for_each over producer results, $item.id binding, capped."""
    steps = _proxy_chain_steps()
    _inject_defensive_fanout(steps, {}, limit=5)
    fetch = steps[1]
    assert fetch["for_each"] == "$steps[0].results"
    assert fetch["arg_bindings"]["id"] == "$item.id"
    assert fetch["for_each_limit"] == 5


def test_llm_fanout_proxy_match_and_binding_normalized():
    """LLM fan_out with a bare op name matches the proxy-prefixed step and binds per item."""
    steps = _proxy_chain_steps()
    gp = GoapPlan(actions=[], steps=copy.deepcopy(steps), cost=0.0)
    cands = [{"operation_id": s["operation_id"]} for s in steps]
    frag = goap_steps_to_state(
        gp, cands,
        fan_out={"operation": "notion-fetch", "over": "$steps[0].results"},
        working_memory={}, limit=5,
    )
    fetch = frag["plan"][1]
    assert fetch["for_each"] == "$steps[0].results"
    assert fetch["for_each_limit"] == 5
    # normalization pass must rewrite the producer ref to per-item form
    assert fetch["arg_bindings"]["id"] == "$item.id"


def test_pending_fetch_queue_fans_over_remaining_ids():
    """A goal-loop pending_fetch_ids queue fans the fetch step over exactly those ids."""
    steps = _proxy_chain_steps()
    _inject_defensive_fanout(steps, {"pending_fetch_ids": ["a", "b", "c"]}, limit=5)
    fetch = steps[1]
    assert fetch["for_each_values"] == ["a", "b", "c"]
    assert fetch["arg_bindings"]["id"] == "$item"
    assert "for_each" not in fetch


def test_normalize_item_bindings_only_for_steps_refs():
    """$item bindings and non-producer refs are left untouched."""
    step = {"for_each": "$steps[0].results", "arg_bindings": {"id": "$item", "q": "literal"}}
    _normalize_item_bindings(step)
    assert step["arg_bindings"] == {"id": "$item", "q": "literal"}


def test_proxy_search_emits_id_effect():
    """
    Candidate op proxy_Notion-AndrewDev-2__notion-search, method "CALL",
    parameters {"query": {"required": True}} -> derive_action(...):
    "have:id" in action.effects and action.effect_field_map()["have:id"] == "id".
    """
    candidate = {
        "operation_id": "proxy_Notion-AndrewDev-2__notion-search",
        "plugin_id": "proxy_Notion-AndrewDev-2",
        "method": "CALL",
        "parameters": {
            "query": {"required": True}
        }
    }
    action = derive_action(candidate)
    assert "have:id" in action.effects
    assert action.effect_field_map()["have:id"] == "id"


def test_list_op_emits_id_and_resource_id():
    """
    Candidate listRecords, method "GET", path "/records" ->
    effects include have:id and have:record_id, both mapped to "id".
    """
    candidate = {
        "operation_id": "listRecords",
        "plugin_id": "test_plugin",
        "method": "GET",
        "path": "/records",
        "parameters": {}
    }
    action = derive_action(candidate)
    assert "have:id" in action.effects
    assert "have:record_id" in action.effects
    assert action.effect_field_map()["have:id"] == "id"
    assert action.effect_field_map()["have:record_id"] == "id"


def test_get_by_id_does_not_emit_id_effect():
    """
    Candidate getRecord, method "GET", path_template "/records/{record_id}" ->
    have:record_id NOT in effects and have:id NOT in effects; have:record IS in effects.
    """
    candidate = {
        "operation_id": "getRecord",
        "plugin_id": "test_plugin",
        "method": "GET",
        "path_template": "/records/{record_id}",
        "parameters": {}
    }
    action = derive_action(candidate)
    assert "have:record_id" not in action.effects
    assert "have:id" not in action.effects
    assert "have:record" in action.effects


def test_chained_search_fetch_binding():
    """
    Verify that search followed by fetch automatically resolves bindings correctly using the derived id effects.
    """
    candidates = [
        {
            "operation_id": "proxy_X__notion-search",
            "plugin_id": "proxy_X",
            "method": "CALL",
            "path": "notion-search",
            "description": "search",
            "parameters": {
                "query": {"required": True}
            }
        },
        {
            "operation_id": "proxy_X__notion-fetch",
            "plugin_id": "proxy_X",
            "method": "CALL",
            "path": "notion-fetch",
            "description": "fetch",
            "parameters": {
                "id": {"required": True}
            }
        }
    ]

    gplan = goap_plan_from_goal(
        ["did:proxy_X__notion-fetch"],
        [],
        {},
        candidates,
        seed_values={"query": "cat"}
    )

    assert gplan is not None
    assert len(gplan.steps) == 2
    assert gplan.steps[0]["operation_id"] == "proxy_X__notion-search"
    assert gplan.steps[1]["operation_id"] == "proxy_X__notion-fetch"
    assert gplan.steps[1]["arg_bindings"] == {"id": "$steps[0].id"}


def test_fetch_consumes_id_when_not_required():
    """Fetch with loose JSON-schema id (not required) still derives have:id precondition."""
    candidate = {
        "operation_id": "proxy_X__notion-fetch",
        "plugin_id": "proxy_X",
        "method": "CALL",
        "parameters": {
            "properties": {"id": {}},
            "required": [],
        },
    }
    action = derive_action(candidate)
    assert "have:id" in action.preconditions


def test_chained_search_fetch_binding_loose_schema():
    """Search→fetch chain binds id even when fetch schema does not mark id required."""
    candidates = [
        {
            "operation_id": "proxy_X__notion-search",
            "plugin_id": "proxy_X",
            "method": "CALL",
            "description": "search",
            "parameters": {"query": {"required": True}},
        },
        {
            "operation_id": "proxy_X__notion-fetch",
            "plugin_id": "proxy_X",
            "method": "CALL",
            "description": "fetch",
            "parameters": {
                "properties": {"id": {}},
                "required": [],
            },
        },
    ]
    gplan = goap_plan_from_goal(
        ["did:proxy_X__notion-fetch"],
        [],
        {},
        candidates,
        seed_values={"query": "cat"},
    )
    assert gplan is not None
    assert len(gplan.steps) == 2
    assert gplan.steps[0]["operation_id"] == "proxy_X__notion-search"
    assert gplan.steps[1]["operation_id"] == "proxy_X__notion-fetch"
    assert gplan.steps[1]["arg_bindings"] == {"id": "$steps[0].id"}
    assert gplan.steps[1]["depends_on"] == 0


def test_get_by_id_no_spurious_id_precondition():
    """getRecord GET with record_id path param must not gain a have:id precondition."""
    candidate = {
        "operation_id": "getRecord",
        "plugin_id": "test_plugin",
        "method": "GET",
        "path_template": "/records/{record_id}",
        "parameters": {},
    }
    action = derive_action(candidate)
    assert "have:id" not in action.preconditions


def test_seed_values_not_polluting_foreign_step():
    """Seed query must not leak into fetch step that only accepts id."""
    candidates = [
        {
            "operation_id": "proxy_X__notion-search",
            "plugin_id": "proxy_X",
            "parameters": {"query": {"required": True}},
        },
        {
            "operation_id": "proxy_X__notion-fetch",
            "plugin_id": "proxy_X",
            "parameters": {"id": {"required": False}},
        },
    ]
    steps = [
        {"operation_id": "proxy_X__notion-search", "args": {}, "arg_bindings": {}},
        {
            "operation_id": "proxy_X__notion-fetch",
            "args": {},
            "arg_bindings": {"id": "$steps[0].id"},
        },
    ]
    fill_literal_args(steps, candidates, {"query": "cat"}, {})
    assert steps[0]["args"].get("query") == "cat"
    assert "query" not in steps[1].get("args", {})


# --------------------------------------------------------------------------- #
# Plugin-glued naming (no delimiter between plugin name and verb) + camelCase
# id params, e.g. Jules dynamic tools: "julesget_session" / "juleslist_sessions"
# with a required "sessionId" param instead of a literal "id".
# --------------------------------------------------------------------------- #

def _jules_list_candidate():
    return {
        "operation_id": "jules_plugin__juleslist_sessions",
        "plugin_id": "jules_plugin",
        "method": "CALL",
        "description": "List Sessions",
        "parameters": {
            "properties": {"pageSize": {}, "pageToken": {}},
            "required": [],
        },
    }


def _jules_get_candidate():
    return {
        "operation_id": "jules_plugin__julesget_session",
        "plugin_id": "jules_plugin",
        "method": "CALL",
        "description": "Get a Session",
        "parameters": {
            "properties": {"sessionId": {}},
            "required": ["sessionId"],
        },
    }


def test_jules_glued_list_emits_session_effects():
    """juleslist_sessions (verb glued onto the plugin name) must still be
    recognized as a collection op and emit have:id, have:session_id, and the
    naive lowerCamelCase bridge fact have:sessionId (the exact literal fact
    julesget_session's unmodified precondition below expects)."""
    action = derive_action(_jules_list_candidate())
    assert "have:id" in action.effects
    assert "have:session_id" in action.effects
    assert "have:sessionId" in action.effects
    fields = action.effect_field_map()
    assert fields["have:id"] == "id"
    assert fields["have:session_id"] == "id"
    assert fields["have:sessionId"] == "id"


def test_jules_glued_get_precondition_unchanged():
    """julesget_session's required "sessionId" param stays the plain,
    unmodified have:sessionId precondition (same default as any other
    required param not in _ID_ALIASES, e.g. "projectId") — the fix bridges
    the chain via the producer's effects (see test above), not by
    generalizing this precondition, since that would collide with params
    like "projectId" that are intentionally satisfied verbatim from seeded
    context defaults."""
    action = derive_action(_jules_get_candidate())
    assert "have:sessionId" in action.preconditions
    assert "have:session_id" not in action.preconditions


def test_jules_chain_plans_list_then_get_serialized():
    """Regression for the reported bug: list_sessions and get_session must
    chain (list first, get bound to $steps[0].id) and NOT be co-scheduled
    into the same parallel group."""
    candidates = [_jules_list_candidate(), _jules_get_candidate()]
    gplan = goap_plan_from_goal(
        ["did:jules_plugin__julesget_session"],
        [],
        {},
        candidates,
    )
    assert gplan is not None
    assert len(gplan.steps) == 2
    assert gplan.steps[0]["operation_id"] == "jules_plugin__juleslist_sessions"
    assert gplan.steps[1]["operation_id"] == "jules_plugin__julesget_session"
    assert gplan.steps[1]["arg_bindings"] == {"sessionId": "$steps[0].id"}
    assert gplan.steps[1]["depends_on"] == 0
    assert derive_parallel_groups(gplan.steps) == [[0], [1]]


def test_jules_weak_sessionid_seed_still_chains_list_then_get():
    """LLM often seeds sessionId='Jules' from free text; that must NOT skip list."""
    from core_graph.goap.integrate import repair_collection_consumer_chain, sanitize_seed_values

    candidates = [_jules_list_candidate(), _jules_get_candidate()]
    weak = sanitize_seed_values({"sessionId": "Jules"}, {})
    assert "sessionId" not in weak

    gplan = goap_plan_from_goal(
        ["did:jules_plugin__julesget_session"],
        [],
        {},
        candidates,
        seed_values={"sessionId": "Jules"},
    )
    assert gplan is not None
    assert gplan.steps[0]["operation_id"] == "jules_plugin__juleslist_sessions"
    assert gplan.steps[1]["operation_id"] == "jules_plugin__julesget_session"
    assert gplan.steps[1]["arg_bindings"] == {"sessionId": "$steps[0].id"}


def test_jules_dual_goal_with_weak_seed_repaired_to_list_first():
    """Dual did:get + did:list with weak seed previously ordered get then list."""
    from core_graph.goap.integrate import repair_collection_consumer_chain

    candidates = [_jules_get_candidate(), _jules_list_candidate()]  # get ranked first
    gplan = goap_plan_from_goal(
        [
            "did:jules_plugin__julesget_session",
            "did:jules_plugin__juleslist_sessions",
        ],
        [],
        {},
        candidates,
        seed_values={"sessionId": "Jules"},
    )
    assert gplan is not None
    steps = repair_collection_consumer_chain(list(gplan.steps), candidates)
    assert steps[0]["operation_id"] == "jules_plugin__juleslist_sessions"
    assert steps[1]["operation_id"] == "jules_plugin__julesget_session"
    assert steps[1]["arg_bindings"].get("sessionId") == "$steps[0].id"
    assert derive_parallel_groups(steps) == [[0], [1]]


def test_jules_real_sessionid_in_memory_allows_solo_get():
    """A trusted working_memory sessionId may plan get without list."""
    candidates = [_jules_list_candidate(), _jules_get_candidate()]
    real_id = "sess_abc123def456"
    gplan = goap_plan_from_goal(
        ["did:jules_plugin__julesget_session"],
        [],
        {"sessionId": real_id},
        candidates,
        seed_values={"sessionId": real_id},
    )
    assert gplan is not None
    assert len(gplan.steps) == 1
    assert gplan.steps[0]["operation_id"] == "jules_plugin__julesget_session"


def test_chain_repair_reorders_get_before_list_plan():
    """Post-plan repair: reverse order without bindings becomes list→get bound."""
    from core_graph.goap.integrate import repair_collection_consumer_chain

    candidates = [_jules_list_candidate(), _jules_get_candidate()]
    broken = [
        {
            "operation_id": "jules_plugin__julesget_session",
            "plugin_id": "jules_plugin",
            "args": {"sessionId": "Jules"},
            "arg_bindings": {},
            "depends_on": None,
        },
        {
            "operation_id": "jules_plugin__juleslist_sessions",
            "plugin_id": "jules_plugin",
            "args": {},
            "arg_bindings": {},
            "depends_on": None,
        },
    ]
    fixed = repair_collection_consumer_chain(broken, candidates)
    assert fixed[0]["operation_id"] == "jules_plugin__juleslist_sessions"
    assert fixed[1]["operation_id"] == "jules_plugin__julesget_session"
    assert fixed[1]["arg_bindings"]["sessionId"] == "$steps[0].id"
    assert "sessionId" not in (fixed[1].get("args") or {})


@pytest.mark.parametrize("operation_id,plugin_id", [
    ("budget_report", "finance_plugin"),
    ("listen_events", "notify_plugin"),
    ("target_update", "crm_plugin"),
])
def test_verb_glued_prefix_no_false_positive(operation_id, plugin_id):
    """Words that merely contain a verb-like substring (budget~get,
    listen~list, target~get) must not be misdetected as collection/by-id ops
    just because a plugin_id happens to be present."""
    assert _is_collection_op(operation_id, plugin_id) is False
    candidate = {
        "operation_id": operation_id,
        "plugin_id": plugin_id,
        "method": "POST",
        "parameters": {"properties": {"payload": {}}, "required": ["payload"]},
    }
    action = derive_action(candidate)
    assert "have:id" not in action.effects


# --------------------------------------------------------------------------- #
# Literal list fan-out and homogeneous collapse tests
# --------------------------------------------------------------------------- #

def test_inject_literal_list_fanout_len_3():
    candidates = [
        {
            "operation_id": "createSession",
            "parameters": {
                "prompt": {"required": True}
            }
        }
    ]
    steps = [
        {"operation_id": "createSession", "args": {"prompt": "dummy"}, "arg_bindings": {}}
    ]
    seed_values = {"prompt": ["A", "B", "C"]}
    inject_literal_list_fanout(steps, seed_values, candidates)
    assert steps[0]["for_each_values"] == ["A", "B", "C"]
    assert steps[0]["arg_bindings"]["prompt"] == "$item"
    assert "prompt" not in steps[0]["args"]


def test_inject_literal_list_fanout_len_1_list():
    candidates = [
        {
            "operation_id": "createSession",
            "parameters": {
                "prompt": {"required": True}
            }
        }
    ]
    steps = [
        {"operation_id": "createSession", "args": {}, "arg_bindings": {}}
    ]
    seed_values = {"prompt": ["A"]}
    inject_literal_list_fanout(steps, seed_values, candidates)
    # len-1 list is not fanned out (inject_literal_list_fanout requires len > 1)
    assert "for_each_values" not in steps[0]
    fill_literal_args(steps, candidates, seed_values, {})
    # A list-valued seed is never injected as a raw scalar arg (guards against proto errors
    # like "title is not repeating, cannot start list"). The param stays unfilled here;
    # the step_resolver resolves it at runtime or the single value flows via working_memory.
    assert not isinstance(steps[0]["args"].get("prompt"), list)


def test_inject_literal_list_fanout_limit():
    candidates = [
        {
            "operation_id": "createSession",
            "parameters": {
                "prompt": {"required": True}
            }
        }
    ]
    steps = [
        {"operation_id": "createSession", "args": {"prompt": "dummy"}, "arg_bindings": {}}
    ]
    seed_values = {"prompt": ["A", "B", "C"]}
    inject_literal_list_fanout(steps, seed_values, candidates, limit=2)
    assert steps[0]["for_each_values"] == ["A", "B"]
    assert steps[0]["for_each_limit"] == 2


def test_inject_literal_list_fanout_already_fanned():
    candidates = [
        {
            "operation_id": "createSession",
            "parameters": {
                "prompt": {"required": True}
            }
        }
    ]
    steps = [
        {"operation_id": "createSession", "args": {"prompt": "dummy"}, "arg_bindings": {}, "for_each_values": ["X"]}
    ]
    seed_values = {"prompt": ["A", "B", "C"]}
    inject_literal_list_fanout(steps, seed_values, candidates)
    assert steps[0]["for_each_values"] == ["X"]
    assert "prompt" in steps[0]["args"]


def test_collapse_homogeneous_fanout_three_steps():
    steps = [
        {"operation_id": "createSession", "args": {"prompt": "A"}, "arg_bindings": {}, "depends_on": None},
        {"operation_id": "createSession", "args": {"prompt": "B"}, "arg_bindings": {}, "depends_on": None},
        {"operation_id": "createSession", "args": {"prompt": "C"}, "arg_bindings": {}, "depends_on": None},
        {"operation_id": "runSession", "args": {"sessionId": "$steps[2].id"}, "arg_bindings": {"sessionId": "$steps[2].id"}, "depends_on": [2]}
    ]
    collapsed = collapse_homogeneous_fanout(steps)
    assert len(collapsed) == 2

    assert collapsed[0]["operation_id"] == "createSession"
    assert collapsed[0]["for_each_values"] == ["A", "B", "C"]
    assert collapsed[0]["arg_bindings"]["prompt"] == "$item"
    assert "prompt" not in collapsed[0].get("args", {})

    assert collapsed[1]["operation_id"] == "runSession"
    assert collapsed[1]["arg_bindings"]["sessionId"] == "$steps[0].id"
    assert collapsed[1]["args"]["sessionId"] == "$steps[0].id"
    assert collapsed[1]["depends_on"] == [0]


def test_regression_comma_string_not_split():
    candidates = [
        {
            "operation_id": "sendMessage",
            "parameters": {
                "message": {"required": True}
            }
        }
    ]
    steps = [
        {"operation_id": "sendMessage", "args": {}, "arg_bindings": {}}
    ]
    seed_values = {"message": "Hi, results ready"}
    inject_literal_list_fanout(steps, seed_values, candidates)
    assert "for_each_values" not in steps[0]
    fill_literal_args(steps, candidates, seed_values, {})
    assert steps[0]["args"]["message"] == "Hi, results ready"


# --------------------------------------------------------------------------- #
# Regression: list-valued seed_values must NOT be injected as scalar step args
# (the Jules "title is not repeating, cannot start list" proto error)
# --------------------------------------------------------------------------- #

def _jules_candidate():
    """Minimal julescreate_session-like candidate with prompt (required) + title (optional)."""
    return {
        "operation_id": "julescreate_session",
        "plugin_id": "proxy_Jules",
        "parameters": {
            "properties": {
                "prompt": {"type": "string"},
                "title": {"type": "string"},
                "sourceContext": {"type": "string"},
            },
            "required": ["prompt"],
        },
    }


def test_list_seed_not_injected_as_scalar_arg():
    """fill_literal_args must skip list-valued seed entries — they are fan-out drivers,
    not scalar args. Injecting them causes proto validation errors on JSON APIs."""
    cand = _jules_candidate()
    # Simulates LLM emitting prompt as a list fanout driver and title as lists (bug scenario)
    seed_values = {
        "prompt": ["task A", "task B", "task C"],
        "title": ["Title A", "Title B", "Title C"],
    }
    step = {"operation_id": "julescreate_session", "args": {}, "arg_bindings": {"prompt": "$item"}}
    fill_literal_args([step], [cand], seed_values, {})
    # Neither list must end up in step["args"] as a raw list
    assert not isinstance(step["args"].get("prompt"), list), "prompt list must not be injected as arg"
    assert not isinstance(step["args"].get("title"), list), "title list must not be injected as arg"


def test_list_seed_fanout_bound_param_skipped_in_defensive_pass():
    """Defensive pass in fill_literal_args must skip params already bound via $item."""
    cand = _jules_candidate()
    seed_values = {"prompt": ["task A", "task B", "task C"]}
    # Step already has prompt as $item binding (inject_literal_list_fanout wired it)
    step = {
        "operation_id": "julescreate_session",
        "args": {},
        "arg_bindings": {"prompt": "$item"},
        "for_each_values": ["task A", "task B", "task C"],
    }
    fill_literal_args([step], [cand], seed_values, {})
    # The defensive pass must NOT overwrite the fanout binding with the raw list
    assert step["args"].get("prompt") is None or not isinstance(step["args"].get("prompt"), list)
    # arg_bindings must still point to $item
    assert step["arg_bindings"]["prompt"] == "$item"


def test_inject_literal_list_fanout_skips_title_scalar():
    """inject_literal_list_fanout must not create for_each_values on a non-list seed value.
    Only params with list values of length > 1 are fanned out."""
    cand = _jules_candidate()
    seed_values = {
        "prompt": ["task A", "task B"],  # list → fan-out
        "title": "My Session",           # scalar → not fanned
    }
    steps = [{"operation_id": "julescreate_session", "args": {}, "arg_bindings": {}}]
    inject_literal_list_fanout(steps, seed_values, [cand])
    # Only prompt triggers for_each_values; title must remain untouched
    assert steps[0].get("for_each_values") == ["task A", "task B"]
    assert steps[0]["arg_bindings"].get("prompt") == "$item"
    assert "title" not in steps[0].get("arg_bindings", {})


def test_full_jules_fanout_pipeline_no_list_in_args():
    """End-to-end: after collapse_homogeneous_fanout + inject_literal_list_fanout +
    fill_literal_args, the fanned step must have for_each_values for prompt but
    title must never appear as a list in args."""
    cand = _jules_candidate()
    # 3 steps the GOAP planner would produce when it treats each session separately
    steps = [
        {"operation_id": "julescreate_session", "args": {"prompt": "task A"}, "arg_bindings": {}},
        {"operation_id": "julescreate_session", "args": {"prompt": "task B"}, "arg_bindings": {}},
        {"operation_id": "julescreate_session", "args": {"prompt": "task C"}, "arg_bindings": {}},
    ]
    # collapse_homogeneous_fanout should collapse these into 1 fanned step
    collapsed = collapse_homogeneous_fanout(steps)
    assert len(collapsed) == 1
    assert collapsed[0]["for_each_values"] == ["task A", "task B", "task C"]
    assert collapsed[0]["arg_bindings"]["prompt"] == "$item"

    # Now simulate the LLM also producing title as a list in seed_values (the bug)
    seed_values = {
        "prompt": ["task A", "task B", "task C"],
        "title": ["Title A", "Title B", "Title C"],
    }
    inject_literal_list_fanout(collapsed, seed_values, [cand])
    fill_literal_args(collapsed, [cand], seed_values, {})

    # After all processing: no raw list must appear in step args
    for step in collapsed:
        for param, val in step.get("args", {}).items():
            assert not isinstance(val, list), (
                f"Step arg '{param}' must be scalar, got list: {val}"
            )


def test_jules_role_label_fanout_prompts_enriched_from_user_query():
    """Fleet-style role labels must not ship as bare prompts to julescreate_session.

    Regression: GOAP fanned prompt=["frontend-b","backend-a","docs"] and Jules
    sessions were created with those strings only — agents had no review brief.
    """
    from core_graph.goap.integrate import is_weak_instruction_value

    cand = _jules_candidate()
    roles = ["frontend-b", "backend-a", "docs"]
    steps = [
        {"operation_id": "julescreate_session", "args": {"prompt": r}, "arg_bindings": {}}
        for r in roles
    ]
    collapsed = collapse_homogeneous_fanout(steps)
    assert len(collapsed) == 1
    assert collapsed[0]["for_each_values"] == roles

    uq = (
        "Call the tool to create or fire Jules code review agents on branch "
        "new-minio-artifact-for-goap-agent comparing against main"
    )
    fill_literal_args(collapsed, [cand], {"prompt": roles}, {}, user_query=uq)

    fe = collapsed[0]["for_each_values"]
    assert len(fe) == 3
    for role, prompt in zip(roles, fe):
        assert role in prompt
        assert "new-minio-artifact-for-goap-agent" in prompt
        assert not is_weak_instruction_value(prompt), f"still weak: {prompt!r}"


def test_id_alias_derive_action():
    """derive_action for post_send_user_message with userId yields have:id preconditions and maps it."""
    candidate = {
        "operation_id": "fake_plugin__post_send_user_message",
        "plugin_id": "fake_plugin",
        "method": "POST",
        "parameters": {
            "properties": {
                "projectId": {"type": "string"},
                "userId": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["projectId", "userId", "text"]
        }
    }
    action = derive_action(candidate)
    assert "have:id" in action.preconditions
    assert "have:userId" not in action.preconditions
    assert "have:projectId" in action.preconditions
    assert action.precondition_param_map()["have:id"] == "userId"


def test_id_alias_plan_compile():
    """A full derive_actions + plan + _compile_steps orders search then send, setting arg_bindings correctly."""
    from core_graph.goap.derive import derive_actions
    from core_graph.goap.planner import plan, _compile_steps
    from core_graph.goap.world_state import WorldState

    candidates = [
        {
            "operation_id": "semantic_search_records",
            "plugin_id": "fake_plugin",
            "method": "GET",
            "description": "search records",
            "parameters": {
                "query": {"type": "string", "required": True}
            }
        },
        {
            "operation_id": "fake_plugin__post_send_user_message",
            "plugin_id": "fake_plugin",
            "method": "POST",
            "description": "send message",
            "parameters": {
                "properties": {
                    "projectId": {"type": "string"},
                    "userId": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["projectId", "userId", "text"]
            }
        }
    ]

    actions = derive_actions(candidates)
    initial_world = WorldState(["have:projectId", "have:query", "have:text"])
    goal = ["did:fake_plugin__post_send_user_message"]

    gplan = plan(goal, initial_world, actions)
    assert gplan is not None
    assert len(gplan.steps) == 2
    assert gplan.steps[0]["operation_id"] == "semantic_search_records"
    assert gplan.steps[1]["operation_id"] == "fake_plugin__post_send_user_message"
    assert gplan.steps[1]["arg_bindings"]["userId"] == "$steps[0].id"


def test_id_alias_build_seed_world():
    """build_seed_world with a seed/working_memory/seed_values key matching an alias adds have:id."""
    from core_graph.goap.integrate import build_seed_world
    
    world = build_seed_world([], {}, {"userId": 1504})
    assert world.satisfies({"have:id"}) is True


def test_goap_max_expansions_limit():
    """plan() returns None if it exceeds max_expansions."""
    from core_graph.goap.planner import plan
    from core_graph.goap.world_state import WorldState
    from core_graph.goap.action import GoapAction

    action1 = GoapAction(
        preconditions=frozenset(["have:A"]),
        effects=frozenset(["have:B"]),
        cost=1.0,
        operation_id="op1",
        plugin_id="plugin",
        intent="op1",
        candidate={},
        model=None,
        effect_fields=(),
    )
    goal = ["have:B"]
    world = WorldState(["have:A"])
    actions = [action1]

    # With max_expansions = 0, it should return None
    gplan = plan(goal, world, actions, max_expansions=0)
    assert gplan is None

    # With max_expansions = 2, it should succeed
    gplan = plan(goal, world, actions, max_expansions=2)
    assert gplan is not None




