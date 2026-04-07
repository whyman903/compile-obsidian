from __future__ import annotations

import json
from pathlib import Path

import pytest

from compile.outputs import generate_canvas, generate_marp


class TestGenerateMarp:
    def test_basic_deck(self) -> None:
        result = generate_marp("My Deck", "# Slide 1\n---\n# Slide 2")
        assert "marp: true" in result
        assert "title: \"My Deck\"" in result
        assert "# Slide 1" in result
        assert "---" in result
        assert "# Slide 2" in result

    def test_custom_theme(self) -> None:
        result = generate_marp("Deck", "content", theme="gaia")
        assert "theme: gaia" in result

    def test_paginate_enabled(self) -> None:
        result = generate_marp("Deck", "content")
        assert "paginate: true" in result


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
