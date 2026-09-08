"""
Compiles all research reports from .claude/research_report into a single research package markdown file.
"""
import os
from pathlib import Path
from datetime import datetime

workspace_root = Path(__file__).parent.parent.resolve()
reports_dir = workspace_root / ".claude" / "research_report"
docs_dir = workspace_root / ".claude" / "docs"
out_dir = docs_dir / "notebooklm-research"
output_path = out_dir / "notebooklm-research-reports-package.md"

def main():
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    
    content_parts = [
        "# Whiskers Agent Research Reports Package — notebooklm-research-reports-package.md",
        f"**Compiled:** {today}",
        "**Notebook:** Whiskers Agent project",
        "",
        ""
    ]
    
    reports_dirs = [
        workspace_root / ".claude" / "code-review-reports",
        workspace_root / ".claude" / "research_report"
    ]
    report_files = []
    for rdir in reports_dirs:
        if rdir.exists():
            for p in sorted(rdir.rglob("*.md")):
                report_files.append(p)
                
    for report_file in report_files:
        rel = report_file.relative_to(workspace_root)
        content_parts.append("---")
        content_parts.append("")
        content_parts.append(f"## Research & Review Report: {report_file.name} ({rel})")
        content_parts.append("")
        text = report_file.read_text(encoding="utf-8")
        content_parts.append(text.strip())
        content_parts.append("")
        
    compiled_text = "\n".join(content_parts)
    output_path.write_text(compiled_text, encoding="utf-8")
    print(f"Compiled {len(report_files)} reports into {output_path.name} successfully ({len(compiled_text)} chars).")

if __name__ == "__main__":
    main()
