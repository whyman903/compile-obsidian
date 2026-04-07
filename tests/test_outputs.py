from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from compile.cli import main
from compile.outputs import generate_canvas, generate_marp
from compile.workspace import init_workspace


class TestGenerateMarp:
    def test_returns_body_and_frontmatter(self) -> None:
        body, fm = generate_marp("My Deck", "# Slide 1\n---\n# Slide 2")
        assert fm["marp"] is True
        assert fm["paginate"] is True
        assert "# Slide 1" in body
        assert "---" in body
        assert "# Slide 2" in body
        # Body should NOT contain its own frontmatter block
        assert body.count("---") == 1  # only the slide separator

    def test_custom_theme(self) -> None:
        _, fm = generate_marp("Deck", "content", theme="gaia")
        assert fm["theme"] == "gaia"

    def test_paginate_enabled(self) -> None:
        _, fm = generate_marp("Deck", "content")
        assert fm["paginate"] is True


class TestGenerateCanvas:
    def test_basic_nodes(self) -> None:
        nodes = [
            {"text": "Node A"},
            {"text": "Node B"},
        ]
        result = json.loads(generate_canvas("Test", nodes))
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["type"] == "text"
        assert result["nodes"][0]["text"] == "Node A"
        assert result["nodes"][1]["y"] > result["nodes"][0]["y"]

    def test_edges(self) -> None:
        nodes = [
            {"id": "a", "text": "A"},
            {"id": "b", "text": "B"},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = json.loads(generate_canvas("Test", nodes, edges))
        assert len(result["edges"]) == 1
        assert result["edges"][0]["fromNode"] == "a"
        assert result["edges"][0]["toNode"] == "b"

    def test_file_node(self) -> None:
        nodes = [{"file": "wiki/articles/topic.md"}]
        result = json.loads(generate_canvas("Test", nodes))
        assert result["nodes"][0]["type"] == "file"
        assert result["nodes"][0]["file"] == "wiki/articles/topic.md"

    def test_explicit_positions(self) -> None:
        nodes = [
            {"text": "A", "x": 100, "y": 200},
            {"text": "B", "x": 300, "y": 400},
        ]
        result = json.loads(generate_canvas("Test", nodes))
        assert result["nodes"][0]["x"] == 100
        assert result["nodes"][0]["y"] == 200
        assert result["nodes"][1]["x"] == 300
        assert result["nodes"][1]["y"] == 400

    def test_empty(self) -> None:
        result = json.loads(generate_canvas("Empty", []))
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_edge_label(self) -> None:
        nodes = [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}]
        edges = [{"from": "a", "to": "b", "label": "relates to"}]
        result = json.loads(generate_canvas("Test", nodes, edges))
        assert result["edges"][0]["label"] == "relates to"

    def test_index_based_edges(self) -> None:
        nodes = [{"text": "First"}, {"text": "Second"}, {"text": "Third"}]
        edges = [{"from": 0, "to": 1}, {"from": 1, "to": 2}]
        result = json.loads(generate_canvas("Test", nodes, edges))
        # Edges should resolve to the auto-generated node IDs
        node_ids = [n["id"] for n in result["nodes"]]
        assert result["edges"][0]["fromNode"] == node_ids[0]
        assert result["edges"][0]["toNode"] == node_ids[1]
        assert result["edges"][1]["fromNode"] == node_ids[1]
        assert result["edges"][1]["toNode"] == node_ids[2]

    def test_rejects_non_list_nodes(self) -> None:
        with pytest.raises(ValueError, match="nodes must be a JSON array of objects"):
            generate_canvas("Bad", {"text": "oops"})  # type: ignore[arg-type]

    def test_rejects_out_of_range_edge_indices(self) -> None:
        with pytest.raises(ValueError, match="edge target index 2 is out of range"):
            generate_canvas("Bad", [{"text": "Only"}], [{"from": 0, "to": 2}])


class TestRenderCommands:
    def test_render_marp_preserves_leading_slide_separator(self, tmp_path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["render", "marp", "Deck", "--path", str(tmp_path), "--body", "---\n# Slide 1"],
        )

        assert result.exit_code == 0
        content = (tmp_path / "wiki" / "outputs" / "Deck.md").read_text()
        assert "\n# Deck\n" not in content
        assert content.rstrip().endswith("---\n# Slide 1")

    def test_render_canvas_rejects_invalid_payload_shape(self, tmp_path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()

        result = runner.invoke(
            main,
            [
                "render",
                "canvas",
                "Bad",
                "--path",
                str(tmp_path),
                "--nodes",
                '{"text":"not an array"}',
            ],
        )

        assert result.exit_code != 0
        assert "Invalid canvas payload" in result.output
