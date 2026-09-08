# Recognized free-text / search parameter names eligible for last-resort
# deterministic fill from the original user query when the LLM produced
# empty seed_values for a leaf single-input search-style tool.
#
# Intentionally excludes heavy/structured instruction fields (``prompt``,
# ``instruction``, ``task``, ``name``). Dumping the raw user_query (or a bare
# fan-out label like "frontend-b") into those params creates Jules/agent
# sessions with no real evaluation brief.
_SEARCH_PARAM_NAMES: set[str] = {
    "query", "q", "search", "search_query", "keyword", "term", "text",
}

# Params that hold multi-line agent/session instructions (not search keywords).
# Weak short labels for these are enriched or left for step_resolver / need_input.
_INSTRUCTION_PARAM_NAMES: set[str] = {
    "prompt", "instruction", "task", "task_description", "agent_prompt",
}
