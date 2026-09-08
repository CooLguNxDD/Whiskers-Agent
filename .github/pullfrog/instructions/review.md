# Review instructions (whiskers) — KEEP IT CHEAP

You are doing a **focused PR review**, not a repo audit or archaeology session.

## Hard budget (do not exceed)

- **Max 40 tool turns** of exploration after checkout, then submit the review.
- **Caveman**: Activate caveman mode and skills for code review
- **Only read the PR diff and files touched by the PR** (plus at most 1–2 nearby callers if needed for a bug).
- **Do not** walk the whole monorepo, “learn the stack”, or populate learnings files.
- **Do not** re-read large docs (README, CONTRIBUTING, AGENTS) unless the PR edits them.
- **Do not** run full test suites or installs unless the PR clearly requires verifying a single command already in CI.
- Cap inline comments at **20**; body at **~400 words**. Prefer severity over volume.
- If the PR is large, sample the riskiest paths (auth, money, migrations, API contracts) and say what you skipped.

## Focus

1. Correctness bugs in the **diff**
2. Security / authz / data leaks in the **diff**
3. Migrations / API contract breaks in the **diff**
4. Missing tests **for new logic in the diff**

Skip: formatting, style nits linters own, drive-by architecture lectures, historical CI lore.

## Output

- Submit GitHub PR review (inline where useful).
- Short preamble: what the PR does + 3–7 bullets of findings (or “LGTM with nits”) + fix suggestion.
- Then **stop**. No follow-up turns after `submit_review` / equivalent.
