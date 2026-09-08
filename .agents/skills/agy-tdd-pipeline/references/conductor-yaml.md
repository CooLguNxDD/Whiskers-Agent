# Conductor YAML Guide

## Schema
- `pipeline`: Root object.
- `stages`: List of steps.
  - `id`: Unique identifier.
  - `agent`: `agy-flash`.
  - `goal`: Path to `.md` prompt.
  - `depends_on`: List of stage IDs.
  - `jules_gate`: Boolean (CI check).
  - `on_fail`: `retry(max=N)`.

## Best Practices
- Keep goals in `goals/` folder.
- Use `depends_on` to enforce strict TDD order.
- One stage = One MTU.
