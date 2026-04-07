from __future__ import annotations

import json
from pathlib import Path

import pytest

from compile.outputs import generate_canvas, generate_marp


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
