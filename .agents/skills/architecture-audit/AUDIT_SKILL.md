---
name: architecture-audit
description: '**WORKFLOW SKILL** — Audit any project''s codebase architecture. USE FOR: discovering system patterns, validating context/auth patterns, checking flow/component structure, updating local documentation (CLAUDE.md/skills), and syncing findings to external tools. DO NOT USE FOR: general coding tasks or one-off searches.'
---

# Architecture Audit Skill

## Description
This skill performs a comprehensive architecture and infrastructure audit of a project. It discovers code patterns, validates them against design principles, updates local documentation (e.g., `CLAUDE.md`, instructions, or skill files), and optionally syncs findings to external documentation tools or project boards (e.g., Notion, Jira, Wiki).

## Configuration / Project Targets (Template)
*Note: Replace these placeholders with your project-specific resources/endpoints.*
- **Project Documentation Root**: `<insert-documentation-url-or-path>`
- **Project Overview (Update Target)**: `<insert-overview-page-url-or-path>`
- **Sprint / Task Board**: `<insert-task-board-url>`

## Purpose
- Discover and catalog all architectural patterns in the current codebase.
- Validate discovered patterns against project design principles (e.g., modularity, defensive calling, type-safety, boundaries).
- Filter out anti-patterns and flag code violations.
- Update local repository documentation (e.g., structure, guidelines, files).
- Optionally write engineering guidelines to external shared documentation spaces.
- Serve as a reusable audit process that can be run periodically.
- Create an audit log in memory with sections for patterns, anti-patterns, and access issues.
- Log a summary of findings at the end of the workflow.

## When to Use
- After significant feature merges or architectural changes.
- When onboarding new contributors (generating up-to-date guidelines).
- Before major refactors (baseline assessment).
- Periodically to keep documentation in sync with code.

## Workflow Steps

### Phase 1: Data Gathering (Discovery)

#### 1.1 Codebase Scan
Scan key codebase areas to discover patterns and components:

| Area | Target Paths | What to Look For |
|---|---|---|
| Entrypoint / Initialization | E.g., `app.py`, `index.js`, `main.go` | Entrypoint setup, initialization sequence, environment configuration, middleware, global error/signal handlers. |
| Core Infrastructure & Config | E.g., `core/`, `config/` | Shared contexts, database/LLM configuration, authentication/authorization wrapper functions, routing registration. |
| Component / Plugin Loader | E.g., `loaders/`, `plugins/` | Discovery logic, registry mechanisms, lifecycle hooks, interface specifications. |
| Modules & Feature Code | E.g., `src/features/`, `plugins/` | Core business logic, controller/handler patterns, input/boundary validations, helper/utility usage. |
| Database & Persistence | E.g., `db/`, `models/` | DB client configuration, schema/ORM models, migrations, caching mechanisms. |
| Scripts & Tools | E.g., `scripts/`, `tools/` | Code generators, automation/deployment scripts, setup utilities. |
| Configuration / Dependencies | E.g., `package.json`, `requirements.txt`, `.env` | Active packages/libraries, environment variable contracts. |

#### 1.2 Version Control & Issue Analysis (Optional)
If repository API tools (e.g., GitHub, GitLab) are available:
- Fetch recently merged pull/merge requests or commits (e.g., last 30 commits/PRs).
- Analyze for new system additions, config changes, dependency updates, and refactoring trends.
- Note: If credentials or APIs are unavailable, proceed with local codebase scan only.

### Phase 2: Code Filtering & Architectural Alignment

#### 2.1 Core Principles to INCLUDE (Project Standards)
Identify and document patterns adhering to the project's standards, such as:
- **Modularity & Decoupling** — Separation of concerns, clean interfaces, and proper package/plugin boundaries.
- **Input & Boundary Validation** — Validating fields, formats, and permissions at boundaries before processing.
- **Shared Context & State Management** — Reuse of singletons, configuration registries, or context contexts instead of redundant declarations.
- **Structured Error Handling & Responses** — Uniform response structures and exception handling across modules.
- **Defensive External Operations** — Use of retry logic, circuit breakers, or safe wrapper utilities for database and HTTP client operations.

#### 2.2 Anti-Patterns to FLAG
Identify and flag code patterns that violate project standards, such as:
- **Redundant Instances** — Multiple/duplicate configurations, client instantiations, or singletons.
- **Unwrapped/Raw External Calls** — Making raw network or database operations without going through project-approved safe handlers.
- **Hardcoded Secrets & Environment Variables** — Hardcoding API URLs, credentials, or environment values in code.
- **Blocking Asynchronous Loops** — Synchronous I/O operations inside asynchronous execution paths.
- **Bypassing Boundary Validations** — Missing inputs validation before calling backend APIs/databases.
- **Improper Imports** — Circular references or violations of layered architecture rules.

### Phase 3: Documentation Updates

#### 3.1 Update Local Project Guides
Update repository-level documentation (such as `CLAUDE.md` or general guidelines):
- **Project Structure** — Document new files, directories, and structural changes.
- **Available Tools / Components** — Update inventories of modules, tools, and endpoints.
- **Tech Stack & Dependencies** — Note updated dependencies or requirements.
- **Programming Patterns** — Update patterns and conventions sections with recent additions.
- **Key Architectural Rules** — Document new rules/constraints.

#### 3.2 Update Specific Skills/Guidelines
If a newly discovered architectural pattern or subsystem is complex enough to warrant dedicated instructions:
- Create or update dedicated documentation or skill files (e.g., `.claude/skills/<skill-name>/SKILL.md`).
- Document description, setup workflow, and code examples.

#### 3.3 Sync to External Documentation (Optional)
If integration tools are available (e.g., Notion, Confluence, Wiki APIs):
- Update the **Project Overview** or central documentation page.
- Create an **Architecture Audit log** entry under the main audit page (e.g., "Audit — [Date]").

## Output Format

### Console Summary
After completion, output a brief summary of the audit findings:
```
Architecture Audit Complete:
- Patterns discovered: X
- Anti-patterns flagged: Y
- Files updated: CLAUDE.md, [list of updated/created skill/doc files]
- External documentation sync: [updated / skipped / credentials unavailable]
- Git/PR analysis: [analyzed N changes / skipped]
```

### Audit Log Structure
Maintain an in-memory or file-based audit log following this format:
```markdown
# Architecture Audit — [Date]

## Stats
- Total components/endpoints: N
- Anti-patterns flagged: N
- Critical warnings: N

## Verified Patterns
### Component/Plugin Architecture
### Configuration & State Management
### Security & Auth Flow
### External Service Boundaries
### Data & Persistence Layer

## Anti-Patterns Found
### High Priority (must fix)
### Medium/Advisory Priority
### Code Style & Guidelines Violations

## Recent Changes (from Git/PR logs)
```

## Dependencies
- **Required**: Local file system access, file search tools.
- **Optional**: Repository API access (GitHub/GitLab), external documentation APIs (Notion/Confluence).
- **Reference**: Core repository documentation (e.g., `CLAUDE.md` or `README.md`).

