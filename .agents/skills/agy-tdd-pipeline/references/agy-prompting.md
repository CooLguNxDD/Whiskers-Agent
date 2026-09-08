# Agy Prompting Patterns

## Patterns
- **Golden Rule:** If the worker fails, provide more context or tighter constraints.
- **Pattern: Reference File.** "Follow the pattern in `src/utils/logger.py`."
- **Pattern: Explicit Paths.** "Write the service to `src/services/auth.py` and the test to `test/unit/test_auth.py`."

## Anti-Patterns
- **The "Do Everything" Prompt:** Leads to drift. Use MTUs.
- **Implicit Knowledge:** Don't assume the worker knows the library versions. List them in CONTEXT.
- **Loose Acceptance:** "It should work" -> "Command `npm test` returns exit code 0."
