"""
Goal parsing logic.
"""
import json
import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage

from core.goal_spec import GoalSpec, TaskSpec, TaskType
from core.llm_provider_management import LLMProvider, get_chat_llm, _LLM_AVAILABLE
from core.model_conductor import assign_models

logger = logging.getLogger("whiskers")

SYSTEM_PROMPT = """You are a goal decomposition engine. Break down the user's goal into a structured JSON execution plan.
Your response MUST be ONLY valid JSON, with NO markdown fences or additional text.

The JSON must conform to the following schema:
{
  "intent": "One sentence summary of the goal.",
  "tasks": [
    {
      "id": "task_0",
      "task_type": "data_retrieval | reasoning | code_execution | formatting | api_call",
      "intent": "What this specific task does",
      "depends_on": [] // list of task id strings (e.g. ["task_0"]) this task depends on
    }
  ],
  "success_criteria": "One sentence describing what done looks like."
}

Important Rules:
- Single-step goals produce exactly one task with depends_on=[].
- "depends_on" must be a list of task id strings (not indices).
- Output ONLY valid JSON, no markdown fences.
"""


def _make_fallback_spec(raw: str, context: dict) -> GoalSpec:
    """Creates a fallback GoalSpec with a single task when LLM is unavailable or parsing fails."""
    task = TaskSpec(
        id="task_0",
        task_type=TaskType.REASONING,
        intent=raw,
        depends_on=[]
    )
    return GoalSpec(
        raw_goal=raw,
        intent=raw,
        tasks=[task],
        success_criteria="Complete the goal as described",
        context=context
    )


async def parse_goal(raw: str, context: dict | None = None) -> GoalSpec:
    """Parses a natural language goal into a structured GoalSpec using an LLM.
    
    Args:
        raw: The raw natural language goal.
        context: Optional execution context dictionary.
        
    Returns:
        A fully decomposed GoalSpec.
    """
    if not raw or not raw.strip():
        raise ValueError("raw goal must be non-empty")
        
    ctx = context or {}
    
    if not _LLM_AVAILABLE:
        spec = _make_fallback_spec(raw, ctx)
        return assign_models(spec)
        
    provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        logger.warning(f"GoalParser: Invalid LLM_PROVIDER '{provider_str}', using fallback.")
        spec = _make_fallback_spec(raw, ctx)
        return assign_models(spec)
        
    model = os.environ.get("LLM_MODEL", "")
    llm = get_chat_llm(provider, model)
    
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Goal: {raw}\nContext: {json.dumps(ctx)}")
    ]
    
    try:
        response = await llm.ainvoke(messages)
        content = response.content.strip()
        
        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content.rsplit("\n", 1)[0]
            elif content.endswith("```json"):
                 content = content[:-7]
        content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()
            
        data = json.loads(content)
        
        tasks = []
        for t in data.get("tasks", []):
            tasks.append(
                TaskSpec(
                    id=t["id"],
                    task_type=TaskType(t["task_type"]),
                    intent=t["intent"],
                    depends_on=t.get("depends_on", [])
                )
            )
            
        spec = GoalSpec(
            raw_goal=raw,
            intent=data.get("intent", raw),
            tasks=tasks,
            success_criteria=data.get("success_criteria", "Complete the goal as described"),
            context=ctx
        )
        logger.info(f"GoalParser: decomposed '{raw[:60]}' into {len(tasks)} tasks")
        
    except Exception as e:
        logger.warning(f"GoalParser: Failed to parse LLM response: {e}. Using fallback.")
        spec = _make_fallback_spec(raw, ctx)
        
    return assign_models(spec)
