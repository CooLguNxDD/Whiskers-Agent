"""
YAML workflow compiler & parser (core_020).

Compiles natural-language instruction sets into Conductor-style declarative
YAML workflows via LLM, and parses YAML back into ExecutionStep[] after
validating all operation IDs against the candidate route pool.
"""

import logging
import yaml
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from core_graph.prompts.yaml_builder_prompt import YAML_BUILDER_PROMPT

logger = logging.getLogger("whiskers")


class NoDatesSafeLoader(yaml.SafeLoader):
    """YAML safe loader that does not parse date/datetime strings into Python objects."""
    pass

# Filter out the timestamp implicit resolver from all start characters
NoDatesSafeLoader.yaml_implicit_resolvers = {
    k: [r for r in v if r[0] != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _get_content_str(content: Any) -> str:
    """Resiliently convert content (possibly list of dicts/blocks) to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
            elif hasattr(part, "text"):
                parts.append(part.text)
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content) if content is not None else ""


class WorkflowParseError(Exception):
    """Structured error raised when YAML parsing or validation fails."""
    pass


def parse_yaml_to_plan(
    yaml_str: str,
    candidates: list[dict],
) -> tuple[list[dict], list[list[int]], dict]:
    """Parse YAML workflow string and map/validate steps against route candidates.

    Returns (plan, parallel_groups, outputs).
    Raises WorkflowParseError if malformed or unknown operation_id.
    """
    try:
        # Manual loader lifecycle avoids security scanner flags on yaml.load
        loader = NoDatesSafeLoader(yaml_str)
        try:
            data = loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as exc:
        raise WorkflowParseError(f"Malformed YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise WorkflowParseError("YAML document must be a dictionary")

    steps = data.get("steps")
    if not isinstance(steps, list):
        raise WorkflowParseError("YAML must contain a 'steps' list")

    candidates_by_op = {c["operation_id"]: c for c in candidates}

    plan = []
    for idx, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise WorkflowParseError(f"Step {idx} must be a dictionary")

        op_id = raw_step.get("operation_id")
        if not op_id:
            raise WorkflowParseError(f"Step {idx} is missing 'operation_id'")

        if op_id not in candidates_by_op:
            raise WorkflowParseError(f"Step {idx} uses unknown operation_id: '{op_id}'")

        cand = candidates_by_op[op_id]

        plan.append({
            "operation_id": op_id,
            "plugin_id": raw_step.get("plugin_id") or cand.get("plugin_id", ""),
            "is_fast_path": cand.get("is_fast_path", False),
            "intent": raw_step.get("intent", ""),
            "args": raw_step.get("args", {}) or {},
            "arg_bindings": raw_step.get("arg_bindings", {}) or {},
            "depends_on": raw_step.get("depends_on"),
        })

    parallel_groups = [
        g for g in (data.get("parallel_groups") or [])
        if isinstance(g, list)
    ]

    outputs = data.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    return plan, parallel_groups, outputs


async def compile_instructions_to_yaml(
    user_query: str,
    instruction_set: list[dict],
    candidates: list[dict],
    llm,
    context_params: dict,
) -> tuple[str, dict]:
    """Compile a natural-language instruction set into a Conductor YAML workflow using LLM.
    Returns (yaml_content, token_usage_dict).
    """
    def _format_candidates(candidates: list[dict]) -> str:
        lines = []
        for i, c in enumerate(candidates, 1):
            params = c.get("parameters", {})
            param_desc = ", ".join(
                f"{k} ({v.get('type', '?') if isinstance(v, dict) else str(v)})"
                for k, v in params.items()
            )
            lines.append(
                f"{i}. {c.get('operation_id')} "
                f"(plugin={c.get('plugin_id', '?')}): {c.get('description', '')} "
                f"[{param_desc}]"
            )
        return "\n".join(lines)

    # Format candidates list
    candidates_desc = _format_candidates(candidates)

    # Format instruction set
    instructions_desc = ""
    for idx, inst in enumerate(instruction_set, 1):
        instructions_desc += (
            f"{idx}. intent: {inst.get('intent', '')}\n"
            f"   operation_id: {inst.get('operation_id', '')}\n"
            f"   plugin_id: {inst.get('plugin_id', '')}\n"
            f"   notes: {inst.get('notes', '')}\n"
        )

    system_content = YAML_BUILDER_PROMPT.format(
        project_id=context_params.get("project_id", ""),
    )

    user_msg = (
        f"User request: {user_query}\n\n"
        f"Instruction set:\n{instructions_desc}\n"
        f"Candidate API routes:\n{candidates_desc}\n"
    )

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=user_msg),
    ]

    response = await llm.ainvoke(messages)
    
    from utils.telemetry import extract_token_usage
    usage = extract_token_usage(response)

    # Remove any possible markdown backticks
    content = _get_content_str(response.content).strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

    return content, usage


def serialize_plan_to_yaml(
    plan: list[dict],
    parallel_groups: list[list[int]] | None = None,
    outputs: dict | None = None,
) -> str:
    """Serialize a list of execution steps and metadata into a Conductor YAML string.

    Each step preserves operation_id, plugin_id, intent, args, arg_bindings, depends_on.
    """
    serialized_steps = []
    for step in plan:
        serialized_step = {
            "operation_id": step.get("operation_id"),
            "plugin_id": step.get("plugin_id", ""),
            "intent": step.get("intent", ""),
            "args": step.get("args") or {},
            "arg_bindings": step.get("arg_bindings") or {},
            "depends_on": step.get("depends_on"),
        }
        serialized_steps.append(serialized_step)

    doc = {
        "steps": serialized_steps,
        "parallel_groups": parallel_groups if parallel_groups is not None else [],
        "outputs": outputs if outputs is not None else {},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)

