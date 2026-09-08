"""
Compiles NotebookLM CatPortfolio documentation bundles.
"""
import os
from pathlib import Path
from datetime import datetime

workspace_root = Path(__file__).parent.parent.resolve()
cat_portfolio_root = Path(os.environ.get("CAT_PORTFOLIO_ROOT", str(workspace_root.parent / "CatPortfolio")))
docs_dir = workspace_root / ".claude" / "docs"
out_dir = docs_dir / "notebooklm-catportfolio"

def compile_bundle(filename: str, title: str, sections: list):
    """
    Compiles a bundle of files and headers from CatPortfolio.
    """
    os.makedirs(out_dir, exist_ok=True)
    output_path = out_dir / filename
    today = datetime.now().strftime("%Y-%m-%d")
    
    content_parts = [
        f"# {title} — {filename}",
        f"**Compiled:** {today}",
        "**Notebook:** Whiskers Agent project",
        f"**Repository:** CatPortfolio ({cat_portfolio_root})",
        "",
        ""
    ]
    
    for label, relative_path in sections:
        content_parts.append("---")
        content_parts.append("")
        content_parts.append(f"## Source: {label} ({relative_path})")
        content_parts.append("")
        
        p = cat_portfolio_root / relative_path
        if p.exists():
            text = p.read_text(encoding="utf-8")
            ext = p.suffix.lower()
            if ext == ".py":
                content_parts.append(f"```python\n{text.strip()}\n```")
            elif ext in (".ts", ".tsx"):
                content_parts.append(f"```typescript\n{text.strip()}\n```")
            elif ext in (".json", ".yaml", ".yml"):
                content_parts.append(f"```yaml\n{text.strip()}\n```")
            else:
                content_parts.append(text.strip())
        else:
            content_parts.append(f"*File not found: {relative_path}*")
            
        content_parts.append("")
        
    compiled_text = "\n".join(content_parts)
    output_path.write_text(compiled_text, encoding="utf-8")
    print(f"Compiled {filename} successfully ({len(compiled_text)} chars).")

# 1. catportfolio_architecture.md
sections_arch = [
    ("CatPortfolio Agent & Architecture Guide", "CLAUDE.md"),
    ("CatPortfolio Visual & Layout Design Spec", "DESIGN.md"),
    ("CatPortfolio Readme", "README.md"),
    ("CatPortfolio Design System Spec", "design/design.md"),
    ("Package Configuration", "package.json"),
    ("Docker & Nginx Deployment Configuration", "Dockerfile"),
    ("Nginx Reverse Proxy Config", "nginx.conf")
]
compile_bundle("catportfolio_architecture.md", "CatPortfolio Architecture & Design System", sections_arch)

# 2. catportfolio_layout_engine.md
sections_layout = [
    ("Master Layout Configuration", "design/layout.yaml"),
    ("Layout Fragments Definition", "design/fragments.json"),
    ("Portfolio Sources Schema & Config", "design/sources.yaml"),
    ("Mirror Manifest", "design/mirror-manifest.json"),
    ("Layout Compiler Script", "scripts/compile-layout.ts"),
    ("Layout Generation Script", "scripts/gen-layout.ts"),
    ("Fragments Generator Script", "scripts/gen-fragments.ts"),
    ("Sources Schema Validator Script", "scripts/sources-schema.ts")
]
compile_bundle("catportfolio_layout_engine.md", "CatPortfolio Dynamic Layout Engine & YAML Pipeline", sections_layout)

# 3. catportfolio_frontend_blocks.md
sections_frontend = [
    ("App Entrypoint", "src/App.tsx"),
    ("Router Setup", "src/router.tsx"),
    ("Layout Renderer Engine", "src/render/LayoutRenderer.tsx"),
    ("Block Component Registry", "src/render/registry.ts"),
    ("Block Error Boundary", "src/render/BlockErrorBoundary.tsx"),
    ("Fish Tank Canvas & Swimming Physics", "src/blocks/FishTankCanvas.tsx"),
    ("Fish Tank Block Wrapper", "src/blocks/FishTank.tsx"),
    ("Fish Tank Layout Mapper", "src/blocks/fishTankLayout.ts"),
    ("Fish Tank Visual Tokens", "src/blocks/fishTankTokens.ts"),
    ("Fish Layout Data Transformer", "src/blocks/fishFromLayout.ts"),
    ("Composite Layout Block", "src/blocks/Composite.tsx"),
    ("Architecture Diagram Block", "src/blocks/ArchDiagram.tsx"),
    ("MCP Sandbox Interactive Block", "src/blocks/McpSandbox.tsx"),
    ("Cost Simulator Interactive Block", "src/blocks/CostSimulator.tsx"),
    ("Hero Section Block", "src/blocks/Hero.tsx"),
    ("Scene 2D Block", "src/blocks/Scene2d.tsx"),
    ("Star Story Block", "src/blocks/StarStory.tsx"),
    ("Root State Store", "src/store/index.ts"),
    ("Fish Tank State Slice", "src/store/fishTankSlice.ts"),
    ("Layout State Slice", "src/store/layoutSlice.ts"),
    ("Harness API Client", "src/api/harness.ts"),
    ("Whiskers Agent API Client", "src/api/octClient.ts")
]
compile_bundle("catportfolio_frontend_blocks.md", "CatPortfolio Frontend Dynamic Blocks & React Engine", sections_frontend)

if __name__ == "__main__":
    print("All CatPortfolio bundles compiled successfully.")
