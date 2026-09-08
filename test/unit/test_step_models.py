from core_graph.goap.integrate import assign_step_models, _classify_step_task


def _pool():
    return [
        {"name": "fast", "provider": "openai", "model": "m1", "strength": 1.0, "is_active": True},
        {"name": "strong", "provider": "openai", "model": "m2", "strength": 5.0, "is_active": True},
    ]


def test_assigns_by_complexity():
    pool = [
        {"name": "fast", "provider": "openai", "model": "m1", "strength": 1.0, "is_active": True},
        {"name": "strong", "provider": "openai", "model": "m2", "strength": 5.0, "is_active": True},
        {"name": "off", "provider": "openai", "model": "m3", "strength": 9.0, "is_active": False},
    ]
    steps = [
        {"operation_id": "search", "args": {}, "arg_bindings": {}},
        {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}, "depends_on": 0},
    ]
    assign_step_models(steps, pool)
    assert steps[0]["model"] == "fast"
    assert steps[1]["model"] == "strong"


def test_preserves_existing_model():
    pool = [
        {"name": "fast", "provider": "openai", "model": "m1", "strength": 1.0, "is_active": True},
        {"name": "strong", "provider": "openai", "model": "m2", "strength": 5.0, "is_active": True},
    ]
    steps = [{"operation_id": "x", "model": "custom", "arg_bindings": {}}]
    assign_step_models(steps, pool)
    assert steps[0]["model"] == "custom"


def test_empty_pool_noop():
    steps = [{"operation_id": "x", "arg_bindings": {}}]
    assign_step_models(steps, [])
    assert steps[0].get("model") is None


def test_strategy_off_assigns_nothing():
    steps = [
        {"operation_id": "search", "arg_bindings": {}},
        {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}, "depends_on": 0},
    ]
    assign_step_models(steps, _pool(), {"strategy": "off"})
    assert steps[0].get("model") is None
    assert steps[1].get("model") is None


def test_strategy_strength_explicit_matches_complexity():
    steps = [
        {"operation_id": "search", "arg_bindings": {}},
        {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}, "depends_on": 0},
    ]
    assign_step_models(steps, _pool(), {"strategy": "strength"})
    assert steps[0]["model"] == "fast"
    assert steps[1]["model"] == "strong"


def test_strategy_explicit_override_and_fallback():
    steps = [
        {"operation_id": "search", "arg_bindings": {}},
        {"operation_id": "other", "arg_bindings": {"id": "$x"}, "depends_on": 0},
    ]
    policy = {"strategy": "explicit", "op_overrides": {"search": "strong"}}
    assign_step_models(steps, _pool(), policy)
    assert steps[0]["model"] == "strong"           # explicit override
    assert steps[1]["model"] == "strong"           # unmapped -> strength_pick (complex)


def test_strategy_explicit_unknown_name_falls_back():
    steps = [{"operation_id": "search", "arg_bindings": {}}]
    policy = {"strategy": "explicit", "op_overrides": {"search": "ghost"}}
    assign_step_models(steps, _pool(), policy)
    assert steps[0]["model"] == "fast"             # bad name -> strength_pick (simple)


def test_strategy_task_type_maps_by_verb():
    steps = [
        {"operation_id": "search_records", "arg_bindings": {}},
        {"operation_id": "summarize_notes", "arg_bindings": {}},
    ]
    policy = {"strategy": "task_type", "task_type_map": {"data_retrieval": "fast", "reasoning": "strong"}}
    assign_step_models(steps, _pool(), policy)
    assert steps[0]["model"] == "fast"
    assert steps[1]["model"] == "strong"


def test_strategy_task_type_inactive_name_falls_back():
    steps = [{"operation_id": "search_records", "arg_bindings": {}}]
    policy = {"strategy": "task_type", "task_type_map": {"data_retrieval": "ghost"}}
    assign_step_models(steps, _pool(), policy)
    assert steps[0]["model"] == "fast"             # mapped name not active -> strength_pick


def test_classify_step_task():
    assert _classify_step_task({"operation_id": "proxy_X__notion-search"}) == "data_retrieval"
    assert _classify_step_task({"operation_id": "summarize_thread"}) == "reasoning"
    assert _classify_step_task({"operation_id": "create_record"}) == "formatting"
