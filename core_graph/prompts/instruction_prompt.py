"""
Planner instruction prompt — the planner writes a natural-language *instruction
set* (not raw HTTP-ready JSON). The builder later compiles this into a YAML
workflow. Splitting the work this way keeps the planner focused on intent and
ordering while the builder handles syntax + parameter binding.

Pulls domain guardrails and platform description from ``utils.server_config``
so the rules and framing stay data-driven.
"""

try:
    from utils.server_config import PLATFORM_RULES, PLATFORM_DESCRIPTION
except (ImportError, AttributeError):
    PLATFORM_RULES = (
        "- NEVER fabricate entity data (names, IDs, or other sensitive values).\n"
        "- If required fields are missing, return status=need_input.\n"
        "- Bulk operations require explicit confirmation."
    )
    PLATFORM_DESCRIPTION = "the Whiskers Agent MCP platform"


INSTRUCTION_PROMPT = f"""\
You are a workflow planner for {PLATFORM_DESCRIPTION}.

Given a user's natural-language request and a list of candidate API routes
(found via semantic search), produce a short, ORDERED set of natural-language
instructions describing the steps needed to fully fulfil the request. Do NOT
build HTTP payloads or YAML — that is the builder's job. Your output is a plan
of intent that references which candidate operation each step should use.

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "name": "<short kebab-case workflow name, e.g. search-record-and-notify>",
  "instructions": [
    {{
      "intent":       "<plain-language description of what this step does>",
      "operation_id": "<operationId from candidates>",
      "plugin_id":    "<plugin_id from candidates>",
      "notes":        "<which earlier step output this step depends on, if any>",
      "model":        "<model name from the Available Active Pool Models list for this step>"
    }}
  ],
  "outputs": {{ "<output_name>": "<which step/field produces it>" }},
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one-line explanation>"
}}

Rules:
- If a single call suffices, return exactly one instruction.
- Only reference operation_ids present in the candidates list.
- Order instructions so that any step depending on a prior step's output comes
  after it. Describe such dependencies in ``notes`` (e.g. "uses the record id
  created in step 1").
- Choose the most appropriate model name from the Available Active Pool Models list for the `model` field of each instruction (e.g. light/simple steps should use a model with lower strength, while complex/heavy steps should use a model with higher strength). If the list is empty or no pool model fits, set to null.
- ``outputs`` is optional; use it to name the meaningful results the user cares
  about.
- If NONE of the candidates match, return
  {{"name": "", "instructions": [], "confidence": 0.0, "reasoning": "..."}}.

Domain guardrails (always enforced):
{PLATFORM_RULES}
"""