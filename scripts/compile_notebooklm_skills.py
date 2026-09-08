"""
Compiles NotebookLM skills.
"""
import os
from pathlib import Path
from datetime import datetime

# Repo root relative to scripts/compile_notebooklm_skills.py
workspace_root = Path(__file__).parent.parent.resolve()
skills_dir = workspace_root / ".claude" / "skills"
docs_dir = workspace_root / ".claude" / "docs"
out_dir = docs_dir / "notebooklm-skills"

def get_skill_content(skill_name: str) -> str:
    """
    Retrieves the content of a specific skill.
    """
    folder = skills_dir / skill_name
    skill_file = folder / "SKILL.md"
    
    # If SKILL.md is large, it contains the actual content
    if skill_file.exists() and skill_file.stat().st_size > 1000:
        return skill_file.read_text(encoding="utf-8")
        
    # Otherwise, look for other markdown files in the folder
    md_files = list(folder.glob("*.md"))
    guide_files = [f for f in md_files if f.name != "SKILL.md"]
    if guide_files:
        # Sort by size to pick the largest guide file, just in case
        guide_files.sort(key=lambda f: f.stat().st_size, reverse=True)
        return guide_files[0].read_text(encoding="utf-8")
        
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")
        
    raise FileNotFoundError(f"No skill file found in {folder}")

def compile_bundle(filename: str, title: str, sections: list):
    """
    Compiles a bundle of skills.
    """
    output_path = out_dir / filename
    today = datetime.now().strftime("%Y-%m-%d")
    
    content_parts = [
        f"# {title} — {filename}",
        f"**Compiled:** {today}",
        "**Notebook:** Fast Mcp server Documentation",
        "",
        ""
    ]
    
    for label, filepath_or_skillname in sections:
        content_parts.append("---")
        content_parts.append("")
        content_parts.append(f"## Skill: {label}")
        content_parts.append("")
        
        if label.endswith(".md"):
            # It's a file path
            p = workspace_root / filepath_or_skillname
            text = p.read_text(encoding="utf-8")
        else:
            # It's a skill name
            text = get_skill_content(filepath_or_skillname)
            
        content_parts.append(text.strip())
        content_parts.append("")
        
    compiled_text = "\n".join(content_parts)
    output_path.write_text(compiled_text, encoding="utf-8")
    print(f"Compiled {filename} successfully ({len(compiled_text)} chars).")

# Compile 00-index
sections_00 = [
    ("CLAUDE.md", "CLAUDE.md"),
    ("context.md", ".claude/docs/context.md"),
    ("notebooklm-plugin-eventbus-manager-pattern.md", ".claude/docs/notebooklm-plugin-eventbus-manager-pattern.md")
]
compile_bundle("notebooklm-skills-00-index.md", "Whiskers Agent Skills Bundle", sections_00)

# Compile 01-core
sections_01 = [
    ("plugin-system", "plugin-system"),
    ("mcp-server-setup", "mcp-server-setup"),
    ("langgraph-chain", "langgraph-chain"),
    ("http-route-registry", "http-route-registry"),
    ("llm-provider-enum", "llm-provider-enum"),
    ("dynamic-tools-plugin", "dynamic-tools-plugin"),
    ("tools-builder-generator", "tools-builder-generator"),
    ("achieve-pipeline", "achieve-pipeline")
]
compile_bundle("notebooklm-skills-01-core.md", "Whiskers Agent Skills Bundle", sections_01)

# Compile 02-auth-data
sections_02 = [
    ("oauth-two-layer", "oauth-two-layer"),
    ("oauth-handler", "oauth-handler"),
    ("sqlAlchemy-orm-query-builder", "sqlAlchemy-orm-query-builder"),
    ("Alembic-migration", "Alembic-migration"),
    ("error-handling-wrapper", "error-handling-wrapper")
]
compile_bundle("notebooklm-skills-02-auth-data.md", "Whiskers Agent Skills Bundle", sections_02)

# Compile 03-devops
sections_03 = [
    ("pytest-docker", "pytest-docker"),
    ("merge-request", "merge-request"),
    ("diff-review", "diff-review"),
    ("architecture-audit", "architecture-audit"),
    ("logger-convention", "logger-convention"),
    ("analytics-telemetry", "analytics-telemetry"),
    ("notion-writeup", "notion-writeup"),
    ("export-report", "export-report"),
    ("agy-tdd-pipeline", "agy-tdd-pipeline")
]
compile_bundle("notebooklm-skills-03-devops.md", "Whiskers Agent Skills Bundle", sections_03)

# Compile 04-frontend-openapi
sections_04 = [
    ("react-app-guide", "react-app-guide"),
    ("react_generator", "react_generator"),
    ("openapi-pipeline", "openapi-pipeline")
]
compile_bundle("notebooklm-skills-04-frontend-openapi.md", "Whiskers Agent Skills Bundle", sections_04)
