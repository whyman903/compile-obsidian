"""Integration test for the compile pipeline.

This test requires ANTHROPIC_API_KEY to be set and makes real API calls.
Run with: uv run pytest -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest


@pytest.fixture
def workspace(tmp_path: Path):
    """Create a temporary workspace."""
    from compile.workspace import init_workspace

    config = init_workspace(tmp_path, "Test Topic", "A test workspace for CI.")
    return config


def test_workspace_init(workspace):
    """Workspace init creates the expected structure."""
    config = workspace
    assert (config.wiki_dir / "index.md").exists()
    assert (config.wiki_dir / "overview.md").exists()
    assert (config.wiki_dir / "log.md").exists()
    assert (config.wiki_dir / "dashboards").exists()
    assert (config.workspace_root / "WIKI.md").exists()
    assert (config.workspace_root / ".obsidian" / "app.json").exists()
    assert (config.workspace_root / ".obsidian" / "graph.json").exists()
    assert (config.workspace_root / ".obsidian" / "core-plugins.json").exists()
    assert config.compile_dir.exists()
    assert config.raw_dir.exists()
    assert (config.compile_dir / "config.yaml").exists()
    assert (config.compile_dir / "state.json").exists()


def test_workspace_status(workspace):
    """Status reports correct counts."""
    from compile.workspace import get_status

    info = get_status(workspace)
    assert info["topic"] == "Test Topic"
    assert info["raw_files"] == 0
    assert info["wiki_pages"] >= 3  # index, overview, log


def test_text_extraction(tmp_path: Path):
    """Text extraction works for markdown files."""
    from compile.text import extract_text

    md_file = tmp_path / "test.md"
    md_file.write_text("# My Title\n\nSome content here.")
    title, text = extract_text(md_file)
    assert title == "My Title"
    assert "Some content" in text


def test_image_stub_extraction(tmp_path: Path):
    from compile.text import extract_text

    image_file = tmp_path / "system-diagram.png"
    image_file.write_bytes(b"fake-image-bytes")

    title, text = extract_text(image_file)

    assert title == "System Diagram"
    assert "metadata-only" in text
    assert "png" in text


def test_pdf_analysis_uses_anthropic_document_block(tmp_path: Path, monkeypatch) -> None:
    from compile.compiler import Compiler
    from compile.config import Config
    from compile.source_packet import extract_source_packet

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = raw_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n")

    packet = extract_source_packet(pdf_path, tmp_path)
    config = Config(
        topic="Test Topic",
        anthropic_api_key="test-key",
        workspace_root=tmp_path,
    )
    compiler = Compiler(config)

    captured: dict[str, object] = {}

    def fake_post(url: str, *, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json_module.dumps(
                            {
                                "title": "Paper",
                                "summary": "PDF summary.",
                                "key_claims": [],
                                "methods": [],
                                "metrics": [],
                                "equations": [],
                                "limitations": [],
                                "concepts": [],
                                "entities": [],
                                "open_questions": [],
                                "tags": [],
                            }
                        ),
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    json_module = json
    monkeypatch.setattr(httpx, "post", fake_post)

    analysis = compiler.analyze_source_packet(packet)

    assert analysis["title"] == "Paper"
    payload = captured["json"]
    assert isinstance(payload, dict)
    message = payload["messages"][0]
    content = message["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert content[1]["type"] == "text"
    assert "Analyze the attached PDF directly" in content[1]["text"]


def test_ingest_refreshes_overview_without_model_api(workspace):
    """Overview is refreshed from current wiki state after ingest."""
    config = workspace
    from compile.ingest import ingest_source
    from compile.workspace import read_wiki_page

    source_file = config.raw_dir / "test-source.md"
    source_file.write_text("# Test Source\n\nA source about planner executor loops.")

    class DummyCompiler:
        def analyze_source(self, title: str, text: str):
            return {
                "title": "Test Source",
                "summary": "A source summary for overview refresh.",
                "key_claims": ["Planner executor loops improve recovery."],
                "concepts": ["Planner-Executor Loops"],
                "entities": [],
                "open_questions": ["How should the planner recover from bad priors?"],
                "tags": ["test"],
            }

        def plan_wiki_updates(self, source_analysis, index_content, existing_pages):
            return [
                {
                    "action": "create",
                    "path": "sources/Test Source.md",
                    "title": "Test Source",
                    "page_type": "source",
                    "reason": "Record the source.",
                    "key_points": [],
                },
                {
                    "action": "create",
                    "path": "concepts/Planner-Executor Loops.md",
                    "title": "Planner-Executor Loops",
                    "page_type": "concept",
                    "reason": "Track the concept.",
                    "key_points": [],
                },
                {
                    "action": "create",
                    "path": "questions/Planner Recovery.md",
                    "title": "Planner Recovery",
                    "page_type": "question",
                    "reason": "Track the open question.",
                    "key_points": [],
                },
            ]

        def write_page(self, operation, source_analysis, existing_content, related_page_titles, raw_source_path=""):
            title = operation["title"]
            page_type = operation["page_type"]
            summary = source_analysis["summary"]
            extra = ""
            if page_type == "concept":
                extra = "\nSee [[Test Source]]."
            elif page_type == "question":
                extra = "\nInvestigate planner failure modes."
            return (
                f"---\n"
                f"title: {title}\n"
                f"type: {page_type}\n"
                f"summary: {summary}\n"
                f"---\n\n"
                f"# {title}\n\n"
                f"{summary}{extra}\n"
            )

        def write_index(self, pages_by_type):
            return (
                "---\n"
                "title: Index\n"
                "type: index\n"
                "---\n\n"
                "# Test Topic — Index\n\n"
                "## Sources\n\n"
                "- [[Test Source]] — A source summary for overview refresh.\n"
            )

    ingest_source(config, DummyCompiler(), source_file)

    overview = read_wiki_page(config, "overview.md")
    assert overview is not None
    assert "Source notes: 1" in overview
    assert "Concept pages: 1" in overview
    assert "[[Planner-Executor Loops]]" in overview
    assert "[[Test Source]]" in overview
    assert "[[Planner Recovery]]" in overview
    assert "[[Index]]" in overview
    assert "[[Log]]" in overview


def test_save_output_refreshes_navigation_pages(workspace):
    from compile.query import _save_output
    from compile.workspace import read_wiki_page

    output_path = _save_output(
        workspace,
        "What changed?",
        "## Current synthesis\n\nA saved answer.\n",
    )

    assert output_path == "outputs/what-changed.md"

    index_text = read_wiki_page(workspace, "index.md")
    overview_text = read_wiki_page(workspace, "overview.md")
    log_text = read_wiki_page(workspace, "log.md")

    assert index_text is not None and '[[What changed?]]' in index_text
    assert overview_text is not None and "Saved outputs: 1" in overview_text
    assert overview_text is not None and '[[What changed?]]' in overview_text
    assert (workspace.wiki_dir / "dashboards" / "Map of Content.md").exists()
    assert log_text is not None and "query | What changed?" in log_text


def test_collect_pages_by_type_ignores_frontmatter_list_items_for_summary(workspace):
    from compile.workspace import collect_pages_by_type

    page_path = workspace.wiki_dir / "concepts" / "frontmatter-summary.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        "---\n"
        "title: Frontmatter Summary\n"
        "type: concept\n"
        "sources:\n"
        "  - Example Source\n"
        "---\n\n"
        "# Frontmatter Summary\n\n"
        "Actual body summary line.\n"
        "- Supporting detail.\n"
    )

    pages_by_type = collect_pages_by_type(workspace)
    entry = next(item for item in pages_by_type["concepts"] if item["title"] == "Frontmatter Summary")

    assert entry["summary"] == "Actual body summary line."


def test_collect_pages_by_type_ignores_managed_section_markers(workspace):
    from compile.workspace import collect_pages_by_type

    page_path = workspace.wiki_dir / "concepts" / "managed-page.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(
        "---\n"
        "title: Managed Page\n"
        "type: concept\n"
        "---\n\n"
        "# Managed Page\n\n"
        "## Definition\n"
        "<!-- compile:section id=definition -->\n"
        "Actual managed summary line.\n"
        "<!-- /compile:section -->\n"
    )

    pages_by_type = collect_pages_by_type(workspace)
    entry = next(item for item in pages_by_type["concepts"] if item["title"] == "Managed Page")

    assert entry["summary"] == "Actual managed summary line."


def test_write_page_backfills_sparse_knowledge_sections(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(
        compiler,
        "compile_page_artifact",
        lambda **_kwargs: {
            "title": "Chain-of-Thought Reasoning",
            "page_type": "concept",
            "status": "seed",
            "summary": "",
            "tags": ["chain-of-thought"],
            "sources": [],
            "source_ids": [],
            "cssclasses": [],
            "sections": [
                {"id": "definition", "heading": "Definition", "body": "-"},
                {"id": "evidence", "heading": "Evidence", "body": ""},
                {"id": "limitations", "heading": "Limitations", "body": "-"},
                {"id": "open_questions", "heading": "Open Questions", "body": ""},
                {"id": "related", "heading": "Related", "body": "-"},
            ],
        },
    )

    rendered = compiler.write_page(
        operation={
            "action": "create",
            "title": "Chain-of-Thought Reasoning",
            "page_type": "concept",
            "status": "seed",
            "key_points": ["Brief reasoning outperforms both zero-CoT and long-CoT baselines."],
        },
        source_analysis={
            "title": "Brief Is Better",
            "summary": "Brief reasoning budgets outperform both zero-CoT and long-CoT baselines in function-calling tasks.",
            "key_claims": [
                "32 tokens improve accuracy from 44.0% to 64.0%.",
                "256-token reasoning drops accuracy to 25.0%."
            ],
            "metrics": [{"label": "Accuracy at d=32", "value": "64.0%", "context": "BFCL v3"}],
            "equations": [{"latex": "d^* = \\min\\{d : \\mathrm{correct}(d)\\}", "meaning": "Oracle optimal budget"}],
            "open_questions": ["How well do these budget effects generalize beyond function calling?"],
            "concepts": ["Function Calling"],
            "entities": ["Berkeley Function Calling Leaderboard"],
            "limitations": ["Only one benchmark is evaluated."],
        },
        existing_content=None,
        related_page_titles=["Brief Is Better", "Function Calling", "Berkeley Function Calling Leaderboard"],
    )

    assert "> [!warning] Provisional Synthesis" in rendered
    assert "This page is currently backed by 1 source: [[Brief Is Better]]." in rendered
    assert "## Definition" in rendered
    assert "## Evidence" in rendered
    assert "## Limitations" in rendered
    assert "| Accuracy at d=32 | 64.0% | BFCL v3 |" not in rendered
    assert "\n-\n" not in rendered


def test_write_page_promotes_multi_source_knowledge_pages(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(
        compiler,
        "compile_page_artifact",
        lambda **_kwargs: {
            "title": "Planner-Executor Loops",
            "page_type": "concept",
            "status": "stable",
            "summary": "Jointly supported planner/executor pattern.",
            "tags": ["planner"],
            "sources": ["Source A", "Source B"],
            "source_ids": ["source_a", "source_b"],
            "cssclasses": ["concept", "stable"],
            "sections": [
                {"id": "definition", "heading": "Definition", "body": "Planner-executor loops separate global search from tool execution."},
                {"id": "claims_by_source", "heading": "Claims by Source", "body": "- [[Source A]]: improves recovery.\n- [[Source B]]: improves reliability."},
                {"id": "agreements", "heading": "Agreements", "body": "- Both sources say planner/executor separation improves long-horizon debugging."},
                {"id": "tensions", "heading": "Tensions", "body": "- Latency remains a cost."},
                {"id": "key_numbers", "heading": "Key Numbers", "body": "- No explicit metrics."},
                {"id": "open_questions", "heading": "Open Questions", "body": "- When is the extra planning overhead justified?"},
                {"id": "related", "heading": "Related", "body": "- [[Source A]]\n- [[Source B]]"},
            ],
        },
    )

    rendered = compiler.write_page(
        operation={
            "action": "create",
            "title": "Planner-Executor Loops",
            "page_type": "concept",
            "status": "seed",
            "key_points": ["Both sources reinforce planner-executor loops."],
        },
        source_analysis={
            "title": "Planner-Executor Loops",
            "summary": "Planner/executor loops are jointly supported across the batch.",
            "supporting_sources": [
                {"title": "Source A", "summary": "Improves recovery."},
                {"title": "Source B", "summary": "Improves reliability."},
            ],
            "key_claims": [
                {"text": "Planner/executor loops improve recovery.", "source_title": "Source A"},
                {"text": "Planner/executor loops improve reliability.", "source_title": "Source B"},
            ],
        },
        existing_content=None,
        related_page_titles=["Source A", "Source B"],
    )

    assert "status: stable" in rendered
    assert "> [!note] Supporting Sources" in rendered
    assert "Provisional Synthesis" not in rendered


def test_write_page_normalizes_source_equations_and_callouts(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(
        compiler,
        "compile_page_artifact",
        lambda **_kwargs: {
            "title": "Brief Is Better",
            "page_type": "source",
            "status": "stable",
            "summary": "Short summary.",
            "tags": ["reasoning"],
            "sources": ["raw/brief-is-better.pdf"],
            "source_ids": ["source_1"],
            "cssclasses": ["source", "stable"],
            "sections": [
                {"id": "core_contribution", "heading": "Core Contribution", "body": ""},
                {"id": "claims", "heading": "Claims", "body": ""},
                {"id": "key_numbers", "heading": "Key Numbers", "body": ""},
                {"id": "equations", "heading": "Equations", "body": ""},
                {"id": "method_setup", "heading": "Method / Setup", "body": ""},
                {"id": "limitations", "heading": "Limitations", "body": ""},
                {"id": "open_questions", "heading": "Open Questions", "body": ""},
            ],
        },
    )

    rendered = compiler.write_page(
        operation={"action": "create", "title": "Brief Is Better", "page_type": "source"},
        source_analysis={
            "title": "Brief Is Better",
            "summary": "Brief reasoning budgets outperform longer ones.",
            "key_claims": [{"text": "32 tokens outperform both 0 and 256 tokens."}],
            "methods": ["Evaluate Qwen2.5 on BFCL v3 with a budget sweep."],
            "metrics": [{"label": "Accuracy at d=32", "value": "64.0%", "context": "BFCL v3"}],
            "equations": [{"latex": "d^* = \\min\\{d : \\mathrm{correct}(d)\\}", "meaning": "Oracle optimal budget"}],
            "limitations": ["Single benchmark only."],
            "open_questions": ["Will this hold outside function calling?"],
        },
        existing_content=None,
        related_page_titles=[],
        raw_source_path="raw/brief-is-better.pdf",
    )

    assert "> [!note] Raw Artifact" in rendered
    assert "### Oracle Optimal Budget" in rendered
    assert "$$\nd^* = \\min\\{d : \\mathrm{correct}(d)\\}\n$$" in rendered
    assert "> [!warning] Limitations" in rendered
    assert "> [!open-question] Open questions" in rendered


def test_write_page_adapts_source_shape_for_argumentative_material(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(
        compiler,
        "compile_page_artifact",
        lambda **_kwargs: {
            "title": "On Moral Attention",
            "page_type": "source",
            "status": "stable",
            "summary": "",
            "tags": ["philosophy"],
            "sources": ["raw/on-moral-attention.md"],
            "source_ids": ["source_1"],
            "cssclasses": ["source", "stable"],
            "sections": [],
        },
    )

    rendered = compiler.write_page(
        operation={"action": "create", "title": "On Moral Attention", "page_type": "source"},
        source_analysis={
            "title": "On Moral Attention",
            "summary": "The essay argues that moral attention is a precondition for practical judgment.",
            "source_profile": {
                "source_kind": "philosophy_text",
                "evidence_mode": "argumentative",
                "time_orientation": "timeless",
                "recommended_page_roles": ["source", "concept", "question", "comparison"],
                "recommended_section_family": "argument_note",
            },
            "key_claims": [
                {"text": "Moral attention determines which features of a situation become salient."},
                {"text": "Practical failure often begins with distorted attention rather than faulty inference."},
            ],
            "limitations": ["The essay is interpretive rather than empirical."],
            "open_questions": ["How should moral attention be trained in institutional settings?"],
        },
        existing_content=None,
        related_page_titles=[],
        raw_source_path="raw/on-moral-attention.md",
    )

    assert "## Synopsis" in rendered
    assert "## Thesis" in rendered
    assert "## Arguments" in rendered
    assert "## Objections" in rendered
    assert "## Distinctions" in rendered


def test_analyze_source_backfills_profile_and_atoms(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(
        compiler,
        "_call_json",
        lambda **_kwargs: {
            "title": "Daily Notes",
            "summary": "A short journal entry about debugging friction and tool fatigue.",
            "key_claims": [{"text": "I lost time switching between tools."}],
            "concepts": ["Tool Fatigue"],
            "entities": ["Debugger"],
            "open_questions": ["How can the workflow reduce switching costs?"],
            "methods": [],
            "metrics": [],
            "equations": [],
            "limitations": [],
            "tags": ["journal"],
        },
    )

    analysis = compiler.analyze_source("Daily Notes", "Today I lost time switching between tools.")

    assert analysis["source_profile"]["source_kind"] in {"journal_entry", "theoretical_paper"}
    assert analysis["source_profile"]["evidence_mode"] in {"reflective", "mixed"}
    assert analysis["evidence_atoms"]


def test_source_pages_bypass_patch_mode_on_update(workspace, monkeypatch):
    from compile.compiler import Compiler
    from compile.config import Config

    config = Config(
        topic=workspace.topic,
        description=workspace.description,
        anthropic_api_key="test-key",
        workspace_root=workspace.workspace_root,
    )
    compiler = Compiler(config)

    monkeypatch.setattr(compiler, "_compile_source_page_draft", lambda **_kwargs: {"title": "Source", "page_type": "source", "status": "stable", "summary": "ok", "sections": []})
    monkeypatch.setattr(compiler, "_compile_page_patch", lambda **_kwargs: {"frontmatter_updates": {"summary": "patched"}, "section_patches": []})

    payload = compiler.compile_page_artifact(
        operation={"page_type": "source", "title": "Source"},
        source_analysis={"title": "Source"},
        existing_content="---\ntitle: Source\ntype: source\n---\n\n# Source\n\n## Core Contribution\n<!-- compile:section id=core_contribution -->\nold\n<!-- /compile:section -->\n",
        related_page_titles=[],
        raw_source_path="raw/source.md",
    )

    assert payload["page_type"] == "source"
    assert payload["summary"] == "ok"


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_full_ingest_loop(workspace):
    """Full pipeline: add source, ingest, verify wiki pages are created."""
    config = workspace
    from compile.compiler import Compiler
    from compile.ingest import ingest_source
    from compile.workspace import get_status, list_wiki_pages

    # Write a test source
    source_file = config.raw_dir / "test-source.md"
    source_file.write_text(
        "# Planner-Executor Debugging Agents\n\n"
        "Planner-executor debugging agents separate diagnosis from action. "
        "These systems usually keep a scratchpad of hypotheses, then test them by running tools. "
        "The strongest benefit is reliability under long tasks, because the planner can recover from dead ends. "
        "The tradeoff is latency and higher orchestration cost when the tool graph gets wide."
    )

    compiler = Compiler(config)
    touched = ingest_source(config, compiler, source_file)

    assert len(touched) >= 2  # At minimum: source page + index
    assert any("sources/" in p for p in touched)

    # Verify wiki grew
    info = get_status(config)
    assert info["processed"] == 1
    assert info["wiki_pages"] > 3  # More than just init pages

    # Verify pages have content
    pages = list_wiki_pages(config)
    source_pages = [p for p in pages if p.startswith("sources/")]
    assert len(source_pages) >= 1
