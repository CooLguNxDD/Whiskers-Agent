# Build instructions (whiskers)

- Match existing monorepo patterns (server / client / e2e layout).
- Prefer the package manager and scripts already used in the touched package.
- Keep PRs focused; avoid drive-by refactors.
- Never force-push to `main` or `develop`.
- When fixing CI, prefer minimal diffs and include `[pullfrog-ci-fix]` in the commit message.
- Do not commit secrets, `.env` files, or credentials.
