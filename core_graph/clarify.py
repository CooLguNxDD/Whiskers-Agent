"""
Helper module to build structured clarify questions for the user interface.
"""

import re
from typing import Any


def format_clarification_hint(answer: Any) -> str:
    """Fold a clarify answer into a refined-query hint. A list/tuple of picked
    operations becomes an ordered-chain directive; a single string passes through."""
    if isinstance(answer, (list, tuple)):
        ops = [str(x) for x in answer if str(x).strip()]
        if not ops:
            return ""
        return "perform these operations in order: " + ", ".join(ops)
    return str(answer)


def build_clarify_questions(
    candidates: list[dict] | None = None,
    missing_params: list[str] | None = None,
    llm_questions: list[dict] | None = None,
) -> list[dict]:
    """
    Build structured clarify questions from LLM questions, missing parameters, or candidates.

    This function normalizes clarifying questions returned by the LLM, falls back
    to asking for missing parameters if any, or falls back to selecting between candidates.
    """
    res = []

    # 1. Use LLM-generated questions if present
    if llm_questions:
        for q in llm_questions[:3]:
            header = str(q.get("header") or "Clarify")[:12]
            question = str(q.get("question") or "Could you clarify?")
            multi_select = bool(q.get("multiSelect", False))
            opts = []
            for opt in (q.get("options") or [])[:4]:
                label = str(opt.get("label") or "")
                description = str(opt.get("description") or "")
                value = str(opt.get("value") or opt.get("label") or "")
                if label:
                    opts.append({
                        "label": label,
                        "description": description,
                        "value": value
                    })
            val = q.get("value")
            question_dict = {
                "header": header,
                "question": question,
                "multiSelect": multi_select,
                "options": opts
            }
            if val is not None:
                question_dict["value"] = str(val)
            if opts or val is not None:
                res.append(question_dict)

    # 2. Else if missing_params -> free-text questions
    if not res and missing_params:
        for param in missing_params:
            header = str(param)[:12]
            res.append({
                "header": header,
                "question": f"Provide {param}",
                "multiSelect": False,
                "options": [],
                "value": param
            })

    # 3. Else -> deterministic "Which operation did you mean?" using candidates
    if not res and candidates:
        def humanize(op_id: str) -> str:
            """Convert an operation ID into a human-readable title-cased label."""
            # Insert spaces before capital letters, replace underscores/dashes with spaces
            s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', op_id)
            s = s.replace('_', ' ').replace('-', ' ')
            return s.strip().title()

        opts = []
        for cand in candidates[:5]:
            op_id = cand.get("operation_id") or ""
            method = cand.get("method") or ""
            path = cand.get("path") or ""
            desc = cand.get("description") or ""

            label = humanize(op_id) if op_id else "Run action"
            desc_parts = []
            if method and path:
                desc_parts.append(f"{method.upper()} {path}")
            if desc:
                desc_parts.append(desc)
            description = " — ".join(desc_parts)

            opts.append({
                "label": label,
                "description": description,
                "value": op_id
            })

        if opts:
            res.append({
                "header": "Action",
                "question": "Which operation(s) should I use? Select every step you want, in order.",
                "multiSelect": True,
                "options": opts
            })

    return res
