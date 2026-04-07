"""Rich output format generators: Marp slides, matplotlib charts, Obsidian Canvas."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# Marp slide decks
# ---------------------------------------------------------------------------

def generate_marp(title: str, body: str, *, theme: str = "default") -> str:
    """Wrap *body* in Marp frontmatter.  Returns a complete markdown string.

    The caller is responsible for including ``---`` slide separators in *body*.
    """
    header = (
        f"---\n"
        f"marp: true\n"
        f"theme: {theme}\n"
        f"paginate: true\n"
        f"title: \"{title}\"\n"
        f"---\n\n"
    )
    return header + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Matplotlib charts
# ---------------------------------------------------------------------------

def generate_chart(title: str, script: str, output_dir: Path) -> Path:
    """Execute a matplotlib *script* and save the resulting image.

    The script receives a pre-set ``OUTPUT_PATH`` variable pointing to the
    target ``.png`` file.  If the script does not call ``savefig`` itself the
    wrapper will call it automatically.

    Returns the path to the saved image.
    """
    from compile.text import slugify

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title) or "chart"
    dest = output_dir / f"{slug}.png"
    counter = 1
    while dest.exists():
        dest = output_dir / f"{slug}-{counter}.png"
        counter += 1

    # Build a wrapper script that sets OUTPUT_PATH and auto-saves
    wrapper = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        f"OUTPUT_PATH = {str(dest)!r}\n"
        "\n"
        f"{script}\n"
        "\n"
        "# Auto-save if the script hasn't already\n"
        "import os\n"
        "if not os.path.exists(OUTPUT_PATH):\n"
        "    plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')\n"
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(wrapper)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Chart script failed (exit {result.returncode}):\n{result.stderr}"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not dest.exists():
        raise RuntimeError("Chart script ran but did not produce an image.")

    return dest


# ---------------------------------------------------------------------------
# Obsidian Canvas
# ---------------------------------------------------------------------------

def generate_canvas(
    title: str,
    nodes: list[dict],
    edges: list[dict] | None = None,
) -> str:
    """Build an Obsidian ``.canvas`` JSON string.

    Each node dict should contain at minimum:
        - ``text``: the card content (markdown string)
    Optional keys: ``id``, ``x``, ``y``, ``width``, ``height``, ``color``,
    ``file`` (for file-reference cards instead of text cards).

    Each edge dict should contain:
        - ``from``: source node id
        - ``to``: target node id
    Optional keys: ``id``, ``fromSide``, ``toSide``, ``label``.

    If positions are omitted, nodes are laid out in a vertical stack.
    """
    canvas_nodes = []
    y_cursor = 0
    default_width = 300
    default_height = 120
    gap = 40

    for i, node in enumerate(nodes):
        nid = node.get("id") or str(uuid.uuid4())[:8]
        x = node.get("x", 0)
        y = node.get("y", y_cursor)
        w = node.get("width", default_width)
        h = node.get("height", default_height)

        entry: dict = {
            "id": nid,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
        }

        if "file" in node:
            entry["type"] = "file"
            entry["file"] = node["file"]
        else:
            entry["type"] = "text"
            entry["text"] = node.get("text", "")

        if "color" in node:
            entry["color"] = node["color"]

        canvas_nodes.append(entry)

        # Advance cursor only when the node didn't provide explicit y
        if "y" not in node:
            y_cursor += h + gap

        # Store the resolved id back so edges can reference it
        node["id"] = nid

    canvas_edges = []
    for edge in edges or []:
        eid = edge.get("id") or str(uuid.uuid4())[:8]
        canvas_edges.append({
            "id": eid,
            "fromNode": edge["from"],
            "toNode": edge["to"],
            "fromSide": edge.get("fromSide", "bottom"),
            "toSide": edge.get("toSide", "top"),
            **({"label": edge["label"]} if "label" in edge else {}),
        })

    return json.dumps({"nodes": canvas_nodes, "edges": canvas_edges}, indent=2)
