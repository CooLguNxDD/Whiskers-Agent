---
name: merge-request
description: '**WORKFLOW SKILL** — Create and manage GitHub pull requests for the Whiskers Agent Server project. USE FOR: diff-based code review, structured PR descriptions, PR submission. Runs diff-review skill first. DO NOT USE FOR: read-only diff analysis without PR creation (use diff-review skill).'
---

# Pull Request Management Skill

## Description
This skill handles the end-to-end process of creating and managing pull requests (PRs) in GitHub using github MCP tools. It focuses on code review workflows, diff analysis, and structured PR submissions for the Whiskers Agent Server project (Python / FastMCP / LangGraph).

## Purpose
- Automate PR creation with standardized formatting
- Perform code reviews by analyzing diffs between branches
- Ensure consistent PR descriptions covering all change categories
- Integrate with github workflows for efficient code collaboration

## Workflow Steps

### 1. Branch Information Retrieval
- Fetch the current branch name using github MCP tools or `git branch --show-current`
- Identify the target branch (typically `develop` or `main`)

### 2. Diff Analysis
- Compare changes between current branch and target branch
- Analyze file modifications, additions, and deletions
- Categorize changes by type:
  - **Server / entrypoint**: `whiskers_mcp.py`, `agent.py`
  - **Tool modules**: `MCPTools/*.py` — new tools, updated logic
  - **LangGraph flows**: `MCPTools/langgraph_flows/` — graph, states, schemas
  - **Auth / OAuth**: `MCPTools/auth.py`, `oauth_provider.py`, `oauth_routes.py`
  - **Tools generator**: `Tools/tools_generator.py`, `Tools/tools_config.example.yaml`
  - **Dependencies**: `requirements.txt`
  - **Documentation**: `.github/copilot-instruction.md`, `.github/*/SKILL.md`

### 3. Code Review Process
- Run the **diff-review** skill to generate a structured review before PR creation
- Identify potential issues, improvements, or best practices violations
- Display review findings in the chat interface
- Do not automatically push review comments to github

### 4. PR Submission
- **Title format**: `MCP-[XXXX]: [Short Description of Changes]` where XXXX is the branch/ticket number
- Create pull request with the structured description below:

  ```
  ## Overview

  ### Tool changes (ignore if none)
    - list of new or updated MCP tools

  ### LangGraph / agent changes (ignore if none)
    - list of changes to flows, states, or tool schemas

  ### Auth / OAuth changes (ignore if none)
    - list of changes to auth.py, oauth_provider, or oauth_routes

  ### Configuration / context changes (ignore if none)
    - list of changes to context.py, .env_sample, or requirements.txt

  ### Documentation updates (ignore if none)
    - copilot-instruction.md, SKILL.md files updated

  ### New dependencies (ignore if none)
    - list of new packages added to requirements.txt
  ```

- Submit PR to github repository

## Tools Used
- github MCP tools for branch operations, diff retrieval, and PR management
- Local git commands for branch information

## Applicable Files
- All project files, with special focus on:
  - `MCPTools/` for tool module changes
  - `MCPTools/langgraph_flows/` for agent flow changes
  - `MCPTools/context.py` for shared config / auth
  - `requirements.txt` for dependency changes
  - `.github/copilot-instruction.md` for documentation sync

## Best Practices
- Always run the diff-review skill before creating the PR
- Verify `copilot-instruction.md` is updated for any new tools, files, or patterns
- Verify `tool_schemas.py` is updated for any new tools used by the LangGraph agent
- Use clear, concise language in PR descriptions
- Follow project conventions for branch naming (`feature/`, `fix/`, `chore/`)