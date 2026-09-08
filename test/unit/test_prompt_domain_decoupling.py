"""Guard: core planner/builder/goal prompts must not hardcode a domain literal in source —
the literal lives only in utils.server_config.PLATFORM_DESCRIPTION (config-driven)."""
import inspect

from core_graph.prompts import planner_prompt, instruction_prompt, builder_prompt
from core_graph.prompts import goap_goal_prompt, goal_check_prompt
from utils.server_config import PLATFORM_DESCRIPTION

# builder_prompt/goap_goal_prompt/goal_check_prompt have a clean import (no fallback) so their
# source must never mention the domain literal at all.
STRICT_PROMPT_MODULES = [builder_prompt, goap_goal_prompt, goal_check_prompt]

# planner_prompt/instruction_prompt keep an `except (ImportError, AttributeError)` resilience
# fallback — so their source legitimately still contains the literal in that unreachable branch.
# For these, assert the *live* prompt template interpolates the config-driven constant instead
# of hardcoding it.
INTERPOLATING_PROMPT_MODULES = [planner_prompt, instruction_prompt]


def test_no_hardcoded_domain_literal_in_strict_prompt_sources():
    for mod in STRICT_PROMPT_MODULES:
        src = inspect.getsource(mod)
        assert "Whiskers Agent" not in src, f"{mod.__name__} still hardcodes 'Whiskers Agent'"


def test_interpolating_prompts_reference_platform_description_constant():
    for mod in INTERPOLATING_PROMPT_MODULES:
        src = inspect.getsource(mod)
        assert "{PLATFORM_DESCRIPTION}" in src, (
            f"{mod.__name__} should interpolate PLATFORM_DESCRIPTION instead of a hardcoded literal"
        )


def test_platform_description_default():
    assert PLATFORM_DESCRIPTION in ("the Whiskers Agent MCP platform", "the Whiskers Agent platform")


def test_rendered_prompts_still_mention_platform_by_default():
    from core_graph.prompts.planner_prompt import PLANNER_PROMPT
    from core_graph.prompts.instruction_prompt import INSTRUCTION_PROMPT
    from core_graph.prompts.builder_prompt import BUILDER_PROMPT
    from core_graph.prompts.goap_goal_prompt import GOAP_GOAL_PROMPT
    from core_graph.prompts.goal_check_prompt import GOAL_CHECK_PROMPT

    for rendered in (PLANNER_PROMPT, INSTRUCTION_PROMPT, BUILDER_PROMPT, GOAP_GOAL_PROMPT, GOAL_CHECK_PROMPT):
        assert PLATFORM_DESCRIPTION in rendered