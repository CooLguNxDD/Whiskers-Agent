# Contributing Guidelines

Thank you for your interest in contributing to the Whiskers Agent MCP Server! To maintain a clean and readable project history, we enforce a strict commit and pull request naming convention.

## Naming Conventions for Commits and Pull Requests

All pull requests (PRs) and commit titles must follow this format:

```text
[<Component name>:<Type>] <Feature/Change description>
```

### Format Details

- **`Component name`**: The name of the module or component being updated (e.g., `core`, `core_graph`, `frontend`, `terminal`, `whiskers-vscode`, `plugins/search`).
- **`Type`**: The type of change being introduced. Common values include:
  - `Feature`: For new features or additions.
  - `Fix`: For bug fixes.
  - `Refactor`: For code refactoring without behavior changes.
  - `Docs`: For documentation updates.
  - `Test`: For adding or correcting tests.
  - `Chore`: For maintenance tasks, dependency updates, etc.
- **`Feature/Change description`**: A concise, clear summary of the changes in sentence case.

### Examples

- **Adding a new feature to the Core module:**
  `["core":"Feature"] Add OAuth 2.0 PKCE support`
- **Fixing a bug in the Frontend:**
  `["frontend":"Fix"] Correct alignment of the terminal session sidebar`
- **Refactoring code in the Core Graph:**
  `["core_graph":"Refactor"] Optimize parallel group topological sort`
- **Updating documentation:**
  `["docs":"Docs"] Update setup instructions in setup guide`

---

## Development Workflow

1. **Keep Commits Clean**: Stage your changes incrementally. As defined in our developer rules, **NEVER commit unstaged changes**; only stage and commit changes per logical step.
2. **Sync with CLAUDE.md**: Ensure any architectural changes, new patterns, new files, or new dependencies are documented in `CLAUDE.md` and the relevant skill files.
