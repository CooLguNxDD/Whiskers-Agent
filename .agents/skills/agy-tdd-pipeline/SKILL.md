---
name: agy-tdd-pipeline
description: Orchestrate the TDD Pipeline using Codex (Planner) + Agy/Grok (Flash Workers) + Jules (CI Gate).
---

# Agy TDD Pipeline (Compact)

## Core Workflow: Red-Green-Refactor Loop
1. **Decompose:** Split goal into **Minimum Testable Units (MTUs)**.
2. **Acceptance:** Define binary pass/fail criteria for the current MTU.
3. **Execute:** Fire `agy -p "$(cat goals/mtu-N.md)"` (fallback to `grok -p` then `opencode run`).
4. **Verify:** Run local tests. Optional: Gate with **Jules** for cloud CI.
5. **Synthesize:** 
   - **Pass:** Stage change, move to next MTU.
   - **Fail:** Refine prompt (context/constraints), retry (max 3).

## Agy Prompt Template
```markdown
CONTEXT: [Repo structure, existing code, tech stack, conventions]
GOAL: [Single MTU target]
ACCEPTANCE: [Objective binary criteria, test commands]
CONSTRAINTS: [Immutable files, naming patterns, output paths]
EXPECTED ARTIFACTS: [Explicit file list]
```

## Multi-Stage Orchestration (conductor.yaml)
```yaml
pipeline:
  name: feature-name
  stages:
    - id: mtu-1
      agent: agy-flash
      goal: goals/mtu-1.md
      acceptance: pytest tests/test_1.py
      jules_gate: true # CI checkpoint
      on_fail: retry(max=3)
```

## Key Principles
- **Stateless Workers:** Agy gets 100% of context in the prompt.
- **Test Integrity:** Never fix tests to make worker code pass; fix the prompt.
- **MTU Size:** If failure signal is noise, the MTU is too large.
- **Routing:** Reasoning in Codex Pro (High Cost) -> Execution in Agy Flash (High Volume) or Grok CLI.

## Troubleshooting
- **Local Pass / Jules Fail:** Environment delta (env vars, DB drift). Fix env, re-trigger Jules.
- **Worker Loop:** If 3 retries fail, escalate to Codex for architectural re-evaluation.
