"""
Backend drift guard test for graph topology code generation.
Ensures that graphTopology.gen.ts is in sync with core_graph/graph_spec.py.
"""

import sys
from pathlib import Path

# Add project root and scripts directory to sys.path so we can import modules
project_root = Path(__file__).resolve().parent.parent.parent
scripts_dir = project_root / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from gen_graph_topology import build_forced_on_topology, render_ts


def test_graph_codegen_drift_guard():
    """
    Assert that the committed graphTopology.gen.ts matches the freshly rendered TS.
    """
    topology = build_forced_on_topology()
    expected_content = render_ts(topology)

    gen_file_path = (
        project_root
        / "frontend"
        / "cat-admin-frontend"
        / "src"
        / "components"
        / "goap"
        / "graphTopology.gen.ts"
    )

    assert gen_file_path.exists(), (
        f"Generated file does not exist at {gen_file_path}. "
        "Please run: python scripts/gen_graph_topology.py and commit the result"
    )

    with open(gen_file_path, "r", encoding="utf-8") as f:
        committed_content = f.read()

    # Normalize line endings to avoid CRLF/LF mismatch on Windows/Linux
    expected_normalized = expected_content.replace("\r\n", "\n")
    committed_normalized = committed_content.replace("\r\n", "\n")

    assert expected_normalized == committed_normalized, (
        "graph_spec.py changed but graphTopology.gen.ts is stale — "
        "run: python scripts/gen_graph_topology.py and commit the result"
    )


def test_render_ts_deterministic():
    """
    Assert that render_ts produces the identical output when called multiple times.
    """
    topology = build_forced_on_topology()
    output1 = render_ts(topology)
    output2 = render_ts(topology)
    assert output1 == output2, "render_ts output is non-deterministic"
