---
description: Run critique_region constraints on an indexed world region
argument-hint: "<region / anchor name> [world_id=default]"
---

Args: $ARGUMENTS

1. Parse region/anchor and world_id.
2. Call Whiskers Agent MCP `critique_region(world_id, anchor_name=..., k=3)`.
3. Report pass/fail and each violation clearly.
4. Suggest fixes (move camps apart, add edges, re-craft) without bulk scene dumps.
