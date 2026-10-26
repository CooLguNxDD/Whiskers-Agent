---
name: notion-writeup
description: '**WORKFLOW SKILL** — Write or update the Whiskers Agent project documentation on Notion. USE FOR: publishing feature summaries, architecture findings, tool inventories, sprint notes, or any structured project update to the MCP Project Notion page (https://www.notion.so/MCP-Project-3352783caabd801d9a05ecdf106def00). DO NOT USE FOR: local file edits or GitHub PR descriptions.'
---

# Notion Write-Up Skill

## Description
This skill structures and publishes project documentation to the Whiskers Agent Project Notion page. It collects context from the codebase and conversation, formats it into well-structured Notion blocks, and appends or updates content using the `mcp_notion-me_*` tools.

## Default Page
- **main notion page**: https://www.notion.so/MCP-Project-3352783caabd801d9a05ecdf106def00
- **Non Technical documentation URL**: https://www.notion.so/Why-We-need-an-Whiskers Agent-MCP-agentic-server-April-2026-3352783caabd8193acfad2d2b4c6af24
- **Technical documentation URL**: https://www.notion.so/Project-Overview-2026-04-01-3352783caabd817eafb4db2ac8fa8d29
- **Change Log URL**: https://www.notion.so/33a2783caabd8104aa83c064019bb7ac
- **Page ID**: `3352783c-aabd-801d-9a05-ecdf106def00`
- **Change Log Page ID**: `33a2783c-aabd-8104-aa83-c064019bb7ac`

## Purpose
- Publish feature summaries after completing development work
- Sync architecture audit findings to Notion
- Maintain a living project overview (tools inventory, tech stack, patterns)
- Share sprint notes and progress updates with the team
- should update both the non-technical and technical documentation pages as needed
- Ensure the Notion documentation is always up-to-date and reflects the current state of the project
- add new sections or pages as the project evolves

## When to Use
- After completing a feature or bug fix — write a summary to Notion
- After running the architecture-audit skill — publish findings
- When asked to "document this" or "write this up to Notion"
- Periodically for project status updates

## Workflow Steps

### Phase 1: Gather Context

#### 1.1 Determine Write-Up Type
Ask or infer the type:
| Type | Content |
|---|---|
| **Feature Summary** | What was built, which tools changed, how to use it |
| **Architecture Audit** | Patterns found, anti-patterns flagged, recommendations |
| **Tool Inventory** | Full list of MCP tools, parameters, return shapes |
| **Sprint / Progress Note** | What was done, what's next, blockers |
| **General Update** | Free-form project notes |

#### 1.2 Collect Source Material
- Read `.github/copilot-instruction.md` for the current tool list and patterns
- Read relevant `MCPTools/*.py` files for tool signatures and docstrings
- Read `MCPTools/langgraph_flows/graph.py` for agent flow details
- Use conversation context (user's description of changes)

### Phase 2: Structure the Content

#### 2.1 Standard Section Template
Structure content using this hierarchy:
```
## [Section Title]           ← H2 heading block
### Overview                 ← H3 heading block
[1-3 sentence summary]       ← paragraph block

### [Sub-section]            ← H3 heading block
- bullet point               ← bulleted_list_item block
- bullet point

### Code / Config Example    ← H3 heading (if applicable)
[code block]                 ← code block
```

#### 2.2 Feature Summary Format
```
## [Feature Name] — [Date]

### Overview
[What was built and why]

### Tools Added / Modified
- `tool_name(param1, param2)` — [description]

### Key Patterns Used
- [Pattern name]: [brief explanation]

### How to Use
[Usage example or prompt]

### Files Changed
- `MCPTools/filename.py` — [what changed]
```

#### 2.3 Architecture Audit Format
```
## Architecture Audit — [Date]

### Summary
[High-level finding]

### Verified Patterns
- [Pattern]: [description]

### Anti-Patterns / Violations
- [Issue]: [file/location] — [recommendation]

### Documentation Updated
- copilot-instruction.md ✓
- Skills updated ✓
```

#### 2.4 Tool Inventory Format
```
## Tool Inventory — [Date]

### Record Management
| Tool | Required Params | Description |
|------|-----------------|-------------|
| create_record | given_name, last_name, phone | Creates a resource |
...

### Communication
...
```

### Phase 3: Publish to Notion

#### 3.1 Fetch the Target Page
```
Use: mcp_notion-me_notion-fetch
Page ID: 3352783caabd801d9a05ecdf106def00
```
- Confirm the page is accessible
- Note existing sections to avoid duplication

#### 3.2 Append New Content
```
Use: mcp_notion-me_notion-update-page  (for page properties)
Use: mcp_notion-me_notion-create-pages (for new child page)
Use: mcp_notion-me_notion-create-comment (for lightweight notes)
```

**Preferred approach — append as a new child page:**
- Create a child page under the MCP Project page
- Title: `[Type] — [YYYY-MM-DD]` (e.g. `Feature Summary — 2026-04-01`)
- Body: structured blocks as designed in Phase 2

**Alternative — append blocks to existing page:**
- Use `mcp_notion-me_notion-update-page` to append content blocks
- Add a dated H2 heading as a separator

#### 3.3 Confirm Publication
- Verify the page was created/updated successfully
- Return the Notion URL of the created/updated content

## Tools Used
| Tool | Purpose |
|---|---|
| `mcp_notion-me_notion-fetch` | Read existing Notion page content |
| `mcp_notion-me_notion-create-pages` | Create a new child page with content |
| `mcp_notion-me_notion-update-page` | Update page properties or append blocks |
| `mcp_notion-me_notion-create-comment` | Add a quick comment to the page |
| `mcp_notion-me_notion-search` | Find existing pages to avoid duplicates |

## Best Practices
- **Date every entry** — always include the date (`YYYY-MM-DD`) in section titles
- **Be concise** — Notion pages should be scannable; avoid prose walls
- **Link to code** — reference GitHub PR or commit links when relevant
- **No secrets** — never write API keys, passwords, or tokens to Notion
- **Check for duplicates** — search before creating to avoid repeated sections
- **Structured > freeform** — use tables, bullets, and code blocks over paragraphs

## Example Prompts
- "Write up what I just built to Notion"
- "Document the OAuth feature to the MCP project page"
- "Publish the architecture audit findings to Notion"
- "Add a progress note to the Notion MCP project page"
- "Update the Notion page with the current tool inventory"
