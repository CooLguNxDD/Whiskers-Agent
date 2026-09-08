"""Unit tests for core_graph/goap/goal_loop.py helper functions."""

from core_graph.goap.goal_loop import (
    DEFAULT_MAX_ITERATIONS,
    MAX_RESEARCH_NOTES,
    RESEARCH_NOTE_CHARS,
    RESEARCH_NOTES_BUDGET,
    reset_turn_fragment,
    fold_working_memory,
    parse_goal_check,
    decide_goal_loop,
    append_research_note,
    format_research_notes,
)


def test_reset_turn_fragment():
    # 1. Reset with default max_iterations
    initial_state = {"goal": "g", "working_memory": {"x": 1}, "user_query": "who is the best girl"}
    res = reset_turn_fragment(initial_state)

    assert res["plan"] == []
    assert res["candidates"] == []
    assert res["current_step_index"] == 0
    assert res["step_results"] == []
    assert res["response"] is None
    assert res["retry_count"] == 0
    assert res["selected"] is None
    assert res["parallel_groups"] == []
    assert res["yaml_workflow"] == ""
    assert res["instruction_set"] == []
    assert res["workflow_plan_id"] is None
    assert res["payload"] is None
    assert res["resolved_args"] is None
    assert res["unresolved_required"] == []
    assert res["summary"] is None
    assert res["iterations"] == 0
    assert res["max_iterations"] == 20
    assert res["working_memory"] == {"x": 1}
    assert res["goal"] == "g"
    assert res["goal_loop_decision"] is None
    assert res["original_query"] == "who is the best girl"    # stamped from user_query
    assert res["research_notes"] == []                        # cross-round log cleared per turn

    # Make sure initial_state was not mutated and working_memory is a copy
    initial_state["working_memory"]["x"] = 99
    assert res["working_memory"] == {"x": 1}

    # 2. Reset with custom max_iterations
    state_with_max = {"goal": "g", "working_memory": {"x": 1}, "max_iterations": 9}
    res_max = reset_turn_fragment(state_with_max)
    assert res_max["max_iterations"] == 9


def test_reset_turn_fragment_original_query_survives_goal_loop_rewrite():
    """The goal loop's `continue` path re-enters at decompose/embedder, skipping
    turn_init/reset_turn_fragment entirely — so once stamped, original_query is never
    re-derived from a later, rewritten user_query within the same turn. This test
    documents that reset_turn_fragment always stamps from whatever user_query is
    present at the time it runs (i.e. only meaningful at turn start, not mid-loop)."""
    state = {"goal": "g", "user_query": "search for the other members and compare"}
    res = reset_turn_fragment(state)
    # reset_turn_fragment itself has no special-casing — it is turn_init's one-time
    # call site (not the goal-loop continue path) that makes this "once per turn".
    assert res["original_query"] == "search for the other members and compare"


def test_append_research_note_and_trim_oldest():
    notes = []
    for i in range(MAX_RESEARCH_NOTES + 2):
        notes = append_research_note(
            notes,
            iteration=i + 1,
            directive=f"d{i}",
            ops=[f"op{i}"],
            step_results=[{"i": i}],
        )
    assert len(notes) == MAX_RESEARCH_NOTES
    assert notes[0]["iteration"] == 3  # first two trimmed
    assert notes[-1]["directive"] == f"d{MAX_RESEARCH_NOTES + 1}"


def test_append_research_note_char_caps_findings():
    huge = [{"blob": "y" * (RESEARCH_NOTE_CHARS + 2000)}]
    notes = append_research_note([], 1, "q", ["op"], huge)
    assert len(notes[0]["findings"]) <= RESEARCH_NOTE_CHARS


def test_format_research_notes_order_and_budget():
    notes = [
        {"iteration": 1, "directive": "a", "ops": ["x"], "findings": "fa"},
        {"iteration": 2, "directive": "b", "ops": ["y"], "findings": "fb"},
    ]
    text = format_research_notes(notes)
    assert text.index("[round 1]") < text.index("[round 2]")
    assert "fa" in text and "fb" in text

    # Over budget → keep newest tail
    fat = [
        {
            "iteration": i,
            "directive": f"d{i}",
            "ops": [],
            "findings": "Z" * 500,
        }
        for i in range(1, 30)
    ]
    capped = format_research_notes(fat, budget=800)
    assert len(capped) <= 800
    assert format_research_notes(None) == ""
    assert format_research_notes([]) == ""
    # silence unused import if budget constant is only used for documentation
    assert RESEARCH_NOTES_BUDGET > 0


def test_fold_working_memory():
    # 1. Simple fold with data dictionary
    mem = {}
    step_results = [{"status": "ok", "data": {"record_id": 7}}]
    res = fold_working_memory(mem, step_results)
    assert res == {"record_id": 7}
    assert mem == {}  # Not mutated

    # 2. Nested under result
    mem = {}
    step_results = [{"status": "ok", "result": {"record_id": 8}}]
    res = fold_working_memory(mem, step_results)
    assert res == {"record_id": 8}

    # 3. Non-_id keys ignored unless already present in working_memory
    mem = {"existing_key": "old"}
    step_results = [
        {
            "status": "ok",
            "data": {
                "record_id": 10,
                "some_name": "John Doe",  # ends with name, ignored
                "existing_key": "new",    # present in working_memory, updated
            }
        }
    ]
    res = fold_working_memory(mem, step_results)
    assert res == {"existing_key": "new", "record_id": 10}


def test_parse_goal_check():
    # 1. None/missing
    assert parse_goal_check(None) == (False, "", "")
    assert parse_goal_check({}) == (False, "", "")

    # 2. Complete dictionary
    assert parse_goal_check({"done": True, "reason": "r", "next_hint": "n"}) == (True, "r", "n")

    # 3. Type coercion
    assert parse_goal_check({"done": 1, "reason": None, "next_hint": 123}) == (True, "", "123")


def test_decide_goal_loop():
    # 1. Continue scenario
    assert decide_goal_loop("g", 1, 5, False) == "continue"

    # 2. Done scenarios
    assert decide_goal_loop("g", 5, 5, False) == "done"
    assert decide_goal_loop(None, 1, 5, False) == "done"
    assert decide_goal_loop("g", 1, 5, True) == "done"


def test_fold_working_memory_envelopes():
    mem = {"page_id": None}
    step_results = [
        {
            "route": "CALL proxy_X__notion-search",
            "data": {
                "results": [
                    {"id": "page-123", "url": "https://notion.so/page-123", "title": "Cat Info"}
                ]
            }
        }
    ]
    res = fold_working_memory(mem, step_results)
    assert res.get("id") == "page-123"
    assert res.get("url") == "https://notion.so/page-123"
    assert res.get("notion_id") == "page-123"
    assert res.get("page_id") == "page-123"


def test_goal_loop_carry_resolves_next_turn():
    from core_graph.node.helpers import resolve_args_from_context

    working_mem = {"id": None}
    step_results_turn_1 = [
        {
            "route": "CALL proxy_X__notion-search",
            "data": {
                "results": [
                    {"id": "notion-page-uuid", "url": "https://notion.so/notion-page-uuid"}
                ]
            }
        }
    ]

    new_mem = fold_working_memory(working_mem, step_results_turn_1)
    assert new_mem.get("id") == "notion-page-uuid"

    step = {
        "operation_id": "proxy_X__notion-fetch",
        "plugin_id": "proxy_X",
        "args": {},
        "arg_bindings": {"id": "$steps[0].id"}
    }
    route = {
        "parameters": {
            "id": {"required": True}
        }
    }

    prior_results = step_results_turn_1

    resolved_args, unresolved = resolve_args_from_context(
        step=step,
        step_results=prior_results,
        known_params={},
        route=route,
        working_memory=new_mem
    )

    assert resolved_args.get("id") == "notion-page-uuid"
    assert not unresolved

