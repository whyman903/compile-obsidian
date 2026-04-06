from __future__ import annotations

from pathlib import Path

from compile.evidence import load_evidence
from compile.ingest import (
    _extract_target_snippets,
    _filter_analysis_for_target,
    _load_runtime_store,
    _merge_duplicate_knowledge_pages,
    _prune_batch_operations,
    _rewrite_weak_entity_pages,
    _rewrite_wikilinks,
    _sync_existing_pages_to_db,
    ingest_sources,
)
from compile.resolve import TitleResolver
from compile.source_packet import extract_source_packet
from compile.store import EvidenceDatabase
from compile.workspace import init_workspace, read_wiki_page


def test_extract_source_packet_chunks_markdown_sections(tmp_path: Path) -> None:
    workspace_root = tmp_path
    raw_path = workspace_root / "raw" / "note.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "# Planner-Executor Notes\n\n"
        "## Setup\nThe planner proposes steps.\n\n"
        "## Results\nTool execution catches failures quickly.\n"
    )

    packet = extract_source_packet(raw_path, workspace_root)

    assert packet.title == "Planner-Executor Notes"
    assert packet.source_type == "md"
    assert len(packet.chunks) >= 2
    assert any("Setup" in chunk.label for chunk in packet.chunks)
    assert any("Results" in chunk.label for chunk in packet.chunks)


def test_extract_source_packet_pdf_uses_anthropic_native_reader(tmp_path: Path) -> None:
    workspace_root = tmp_path
    raw_path = workspace_root / "raw" / "paper.pdf"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"%PDF-1.4\n%stub\n")

    packet = extract_source_packet(raw_path, workspace_root)

    assert packet.title == "Paper"
    assert packet.source_type == "pdf"
    assert packet.metadata["pdf_reader"] == "anthropic_native"
    assert "Anthropic's native PDF reader" in packet.analysis_text
    assert packet.chunks == []


def test_evidence_database_resolves_aliases_and_finds_chunk_snippets(tmp_path: Path) -> None:
    db = EvidenceDatabase(tmp_path / ".compile" / "evidence.db")
    db.register_aliases(
        "source",
        "Brief Is Better Non-Monotonic Chain-of-Thought Budget Effects in Function-Calling Language Agents",
        [
            "Brief Is Better: Non-Monotonic Chain-of-Thought Budget Effects in Function-Calling Language Agents",
            "brief is better non monotonic chain of thought budget effects in function calling language agents",
        ],
    )
    resolver = TitleResolver.from_catalog(db, [])

    assert resolver.resolve(
        "Brief Is Better: Non-Monotonic Chain-of-Thought Budget Effects in Function-Calling Language Agents",
        "source",
    ) == "Brief Is Better Non-Monotonic Chain-of-Thought Budget Effects in Function-Calling Language Agents"

    packet = extract_source_packet(_write_markdown(tmp_path / "raw" / "brief.md", "# Brief\n\nBudgeting reasoning tokens improves tool routing."), tmp_path)
    db.upsert_source_packet(packet)
    snippets = db.get_source_chunk_snippets(["budgeting", "tool"], limit=3)

    assert snippets
    assert "tool routing" in snippets[0]["text"].lower()


def test_resolver_canonical_title_title_cases_new_titles(tmp_path: Path) -> None:
    db = EvidenceDatabase(tmp_path / ".compile" / "evidence.db")
    resolver = TitleResolver.from_catalog(db, [])

    assert resolver.canonical_title("architectural context", "concept") == "Architectural Context"
    assert resolver.canonical_title("llm agent memory", "concept") == "LLM Agent Memory"
    assert resolver.canonical_title("Hu et al.", "entity") == "Hu Et Al"
    assert resolver.canonical_title("gpt oss 120b", "entity") == "GPT OSS 120B"
    assert resolver.canonical_title("qwen2", "entity") == "QWEN2"


def test_extract_target_snippets_filters_pdf_metadata_placeholder() -> None:
    snippets = _extract_target_snippets(
        "Retrieval-Augmented Generation",
        "concept",
        {
            "_source_text": (
                "PDF source named Retrieval-Augmented Generation. "
                "Content extraction is deferred to Anthropic's native PDF reader during analysis."
            ),
        },
    )

    assert snippets == []


def test_rewrite_wikilinks_strips_unresolved_targets(tmp_path: Path) -> None:
    db = EvidenceDatabase(tmp_path / ".compile" / "evidence.db")
    db.register_aliases("concept", "Planner-Executor Loops", ["planner executor loops"])
    resolver = TitleResolver.from_catalog(db, [])

    content = "See [[planner executor loops]] and [[missing page|a missing page]]."

    rewritten = _rewrite_wikilinks(content, resolver)

    assert "[[Planner-Executor Loops]]" in rewritten
    assert "[[missing page|a missing page]]" not in rewritten
    assert "a missing page" in rewritten


def test_page_catalog_prunes_missing_paths(tmp_path: Path) -> None:
    db = EvidenceDatabase(tmp_path / ".compile" / "evidence.db")
    db.sync_page(
        path="concepts/One.md",
        title="One",
        page_type="concept",
        status="seed",
        summary="one",
        source_ids=[],
    )
    db.sync_page(
        path="concepts/Two.md",
        title="Two",
        page_type="concept",
        status="seed",
        summary="two",
        source_ids=[],
    )

    db.prune_page_catalog(["concepts/One.md"])

    assert {entry["path"] for entry in db.page_catalog()} == {"concepts/One.md"}


def test_ingest_sources_groups_shared_concept_updates(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Debugging Systems", "Test workspace.")
    raw_a = _write_markdown(
        config.raw_dir / "source-a.md",
        "# Source A\n\nPlanner executor loops improve recovery.\n",
    )
    raw_b = _write_markdown(
        config.raw_dir / "source-b.md",
        "# Source B\n\nPlanner executor loops improve reliability.\n",
    )

    class DummyBatchCompiler:
        def analyze_source_packet(self, packet):
            if packet.raw_path.endswith("source-a.md"):
                return {
                    "title": "Source A",
                    "summary": "Source A summary.",
                    "key_claims": [{"text": "Planner executor loops improve recovery.", "concepts": ["Planner-Executor Loops"], "entities": []}],
                    "concepts": ["Planner-Executor Loops"],
                    "entities": [],
                    "open_questions": [],
                    "metrics": [],
                    "equations": [],
                    "limitations": [],
                    "methods": [],
                    "tags": ["planner"],
                }
            return {
                "title": "Source B",
                "summary": "Source B summary.",
                "key_claims": [{"text": "Planner executor loops improve reliability.", "concepts": ["Planner-Executor Loops"], "entities": []}],
                "concepts": ["Planner-Executor Loops"],
                "entities": [],
                "open_questions": [],
                "metrics": [],
                "equations": [],
                "limitations": [],
                "methods": [],
                "tags": ["planner"],
            }

        def write_page(self, operation, source_analysis, existing_content, related_page_titles, raw_source_path=""):
            title = operation["title"]
            page_type = operation["page_type"]
            summary = source_analysis.get("summary") or "summary"
            return (
                "---\n"
                f"title: {title}\n"
                f"type: {page_type}\n"
                f"summary: {summary}\n"
                "---\n\n"
                f"# {title}\n\n"
                f"{summary}\n"
            )

    touched = ingest_sources(config, DummyBatchCompiler(), [raw_a, raw_b], max_workers=2)

    concept_page = read_wiki_page(config, "concepts/Planner-Executor Loops.md")
    source_a_page = read_wiki_page(config, "sources/Source A.md")
    source_b_page = read_wiki_page(config, "sources/Source B.md")
    db = EvidenceDatabase(config.evidence_db_path)
    catalog_titles = {entry["title"] for entry in db.page_catalog()}

    assert concept_page is not None
    assert source_a_page is not None
    assert source_b_page is not None
    assert "Planner-Executor Loops" in catalog_titles
    assert "Source A" in catalog_titles
    assert "Source B" in catalog_titles
    assert "concepts/Planner-Executor Loops.md" in touched


def test_ingest_sources_uses_global_batch_planner_once(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Debugging Systems", "Test workspace.")
    raw_a = _write_markdown(config.raw_dir / "source-a.md", "# Source A\n\nPlanner executor loops improve recovery.\n")
    raw_b = _write_markdown(config.raw_dir / "source-b.md", "# Source B\n\nPlanner executor loops improve reliability.\n")

    class PlannerCompiler:
        def __init__(self) -> None:
            self.plan_calls = 0

        def analyze_source_packet(self, packet):
            title = "Source A" if packet.raw_path.endswith("source-a.md") else "Source B"
            claim = "Planner executor loops improve recovery." if title == "Source A" else "Planner executor loops improve reliability."
            return {
                "title": title,
                "summary": f"{title} summary.",
                "key_claims": [{"text": claim, "concepts": ["Planner-Executor Loops"], "entities": []}],
                "concepts": ["Planner-Executor Loops"],
                "entities": [],
                "open_questions": [],
                "metrics": [],
                "equations": [],
                "limitations": [],
                "methods": [],
                "tags": ["planner"],
            }

        def plan_batch_updates(self, batch_analyses, page_catalog, evidence_context=None):
            self.plan_calls += 1
            assert len(batch_analyses) == 2
            assert any(item["title"] == "Planner-Executor Loops" for item in evidence_context["candidate_pages"])
            return [
                {
                    "action": "create",
                    "title": "Planner-Executor Loops",
                    "page_type": "concept",
                    "reason": "Synthesize the shared planner-executor concept.",
                    "key_points": ["Both sources reinforce planner-executor loops."],
                }
            ]

        def write_page(self, operation, source_analysis, existing_content, related_page_titles, raw_source_path=""):
            title = operation["title"]
            page_type = operation["page_type"]
            support_count = len(source_analysis.get("supporting_sources", []))
            return (
                "---\n"
                f"title: {title}\n"
                f"type: {page_type}\n"
                f"summary: support_count={support_count}\n"
                "---\n\n"
                f"# {title}\n\n"
                f"support_count={support_count}\n"
            )

    compiler = PlannerCompiler()
    touched = ingest_sources(config, compiler, [raw_a, raw_b], max_workers=2)

    assert compiler.plan_calls == 1
    assert "concepts/Planner-Executor Loops.md" in touched
    concept_page = read_wiki_page(config, "concepts/Planner-Executor Loops.md")
    assert concept_page is not None and "support_count=2" in concept_page


def test_prune_batch_operations_skips_near_duplicate_fresh_concepts(tmp_path: Path) -> None:
    store = load_evidence(tmp_path / ".compile" / "evidence.json")
    operations = [
        {
            "title": "Bug Reproduction",
            "page_type": "concept",
            "source_ids": ["source_a"],
            "key_points": ["Reproduce the bug concretely before diagnosing it."],
        },
        {
            "title": "Concrete Reproduction",
            "page_type": "concept",
            "source_ids": ["source_a"],
            "key_points": ["Reproduce the bug concretely before diagnosing it."],
        },
        {
            "title": "Planner-Executor Architecture",
            "page_type": "concept",
            "source_ids": ["source_b"],
            "key_points": ["Separate diagnosis from execution."],
        },
    ]

    pruned = _prune_batch_operations(operations, store, existing_titles=set(), source_count=1)
    concept_titles = [item["title"] for item in pruned if item["page_type"] == "concept"]

    assert "Planner-Executor Architecture" in concept_titles
    assert len([title for title in concept_titles if title in {"Bug Reproduction", "Concrete Reproduction"}]) == 1


def test_filter_analysis_for_entity_drops_irrelevant_metrics() -> None:
    filtered = _filter_analysis_for_target(
        "SWE-Bench",
        "entity",
        {
            "source_id": "source_a",
            "title": "Source A",
            "summary": "Summary",
            "entities": ["SWE-Bench"],
            "key_claims": [{"text": "SWE-Bench is the standard benchmark.", "entities": ["SWE-Bench"]}],
            "metrics": [
                {"label": "code coverage threshold", "value": "80%+", "context": "Organizations that can afford more autonomy"},
                {"label": "autonomous issue resolution rate", "value": "about 50%", "context": "Best agents solving real-world GitHub issues according to SWE-Bench results"},
            ],
        },
    )

    assert filtered is not None
    assert filtered["metrics"] == [
        {"label": "autonomous issue resolution rate", "value": "about 50%", "context": "Best agents solving real-world GitHub issues according to SWE-Bench results"}
    ]


def test_filter_analysis_for_entity_prefers_entity_specific_mentions() -> None:
    filtered = _filter_analysis_for_target(
        "Aider",
        "entity",
        {
            "source_id": "source_a",
            "title": "Source A",
            "summary": "Tool-first debugging agents prioritize concrete reproduction before explanation.",
            "entities": ["Aider"],
            "key_claims": [
                {
                    "text": "Tool-first debugging agents prioritize concrete reproduction before explanation.",
                    "entities": ["Aider"],
                }
            ],
            "_source_text": (
                "Tool-first debugging agents prioritize concrete reproduction before explanation. "
                "Aider and Claude Code are examples of systems that provide this tool suite."
            ),
            "metrics": [],
        },
    )

    assert filtered is not None
    assert filtered["summary"] == "Aider and Claude Code are examples of systems that provide this tool suite."
    assert filtered["key_claims"][0]["text"] == "Aider and Claude Code are examples of systems that provide this tool suite."


def test_ingest_sources_merges_existing_supporting_sources_across_batches(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Debugging Systems", "Test workspace.")
    raw_a = _write_markdown(config.raw_dir / "source-a.md", "# Source A\n\nPlanner executor loops improve recovery.\n")
    raw_b = _write_markdown(config.raw_dir / "source-b.md", "# Source B\n\nPlanner executor loops improve reliability.\n")

    class MergeCompiler:
        def analyze_source_packet(self, packet):
            if packet.raw_path.endswith("source-a.md"):
                return {
                    "title": "Source A",
                    "summary": "Source A summary.",
                    "key_claims": [{"text": "Planner executor loops improve recovery.", "concepts": ["Planner-Executor Loops"], "entities": []}],
                    "concepts": ["Planner-Executor Loops"],
                    "entities": [],
                    "open_questions": [],
                    "metrics": [],
                    "equations": [],
                    "limitations": [],
                    "methods": [],
                    "tags": ["planner"],
                }
            return {
                "title": "Source B",
                "summary": "Source B summary.",
                "key_claims": [{"text": "Planner executor loops improve reliability.", "concepts": ["Planner-Executor Loops"], "entities": []}],
                "concepts": ["Planner-Executor Loops"],
                "entities": [],
                "open_questions": [],
                "metrics": [],
                "equations": [],
                "limitations": [],
                "methods": [],
                "tags": ["planner"],
            }

        def plan_batch_updates(self, batch_analyses, page_catalog, evidence_context=None):
            return [
                {
                    "action": "update",
                    "title": "Planner-Executor Loops",
                    "page_type": "concept",
                    "reason": "Keep the shared concept current.",
                    "key_points": ["Merge supporting evidence."],
                }
            ]

        def write_page(self, operation, source_analysis, existing_content, related_page_titles, raw_source_path=""):
            title = operation["title"]
            page_type = operation["page_type"]
            supporting = [item["title"] for item in source_analysis.get("supporting_sources", [])]
            summary = ",".join(sorted(supporting))
            return (
                "---\n"
                f"title: {title}\n"
                f"type: {page_type}\n"
                f"summary: {summary}\n"
                "---\n\n"
                f"# {title}\n\n"
                f"supporting={summary}\n"
            )

    compiler = MergeCompiler()
    ingest_sources(config, compiler, [raw_a], max_workers=1)
    ingest_sources(config, compiler, [raw_b], max_workers=1)

    concept_page = read_wiki_page(config, "concepts/Planner-Executor Loops.md")
    assert concept_page is not None
    assert "supporting=Source A,Source B" in concept_page


def test_merge_duplicate_knowledge_pages_consolidates_to_grounded_title(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Debugging Systems", "Test workspace.")
    raw_path = _write_markdown(
        config.raw_dir / "source-a.md",
        "# Tool-First Agent Architectures For Bug Reproduction\n\n"
        "Tool-first debugging agents bias toward concrete reproduction before explanation. "
        "Aider and Claude Code are examples of systems that provide this tool suite. "
        "A major advantage of tool-first architectures is that they produce verifiable intermediate results. "
        "An agent should first create a minimal reproduction of the bug, then analyze why it occurs.\n",
    )
    packet = extract_source_packet(raw_path, config.workspace_root)
    db = EvidenceDatabase(config.evidence_db_path)
    db.upsert_source_packet(packet)
    analysis = {
        "title": "Tool-First Agent Architectures For Bug Reproduction",
        "summary": "Tool-first debugging agents prioritize concrete reproduction through logs and tests before explanation.",
        "concepts": ["Bug Reproduction", "Tool-First Architectures"],
        "entities": ["Aider"],
        "key_claims": [
            {
                "text": "Tool-first architectures produce verifiable intermediate results.",
                "concepts": ["Tool-First Architectures"],
                "entities": [],
            },
            {
                "text": "An agent should first create a minimal reproduction of the bug, then analyze why it occurs.",
                "concepts": ["Bug Reproduction"],
                "entities": [],
            },
        ],
        "open_questions": [],
        "metrics": [],
        "equations": [],
        "limitations": [],
        "methods": [],
        "tags": ["tool-first"],
    }
    db.upsert_analysis(packet.source_id, analysis, summary=analysis["summary"])

    shared_summary = "Tool-first debugging agents prioritize concrete reproduction through logs and tests before explanation."
    for title in ("Bug Reproduction", "Tool-First Architectures"):
        path = config.wiki_dir / "concepts" / f"{title}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"title: {title}\n"
            "type: concept\n"
            "status: seed\n"
            f"summary: {shared_summary}\n"
            "sources:\n"
            "  - Tool-First Agent Architectures For Bug Reproduction\n"
            "source_ids:\n"
            f"  - {packet.source_id}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"{shared_summary}\n"
        )

    notes_path = config.wiki_dir / "outputs" / "Notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(
        "---\n"
        "title: Notes\n"
        "type: output\n"
        "status: stable\n"
        "summary: Notes\n"
        "---\n\n"
        "# Notes\n\n"
        "See [[Bug Reproduction]].\n"
    )

    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)

    touched = _merge_duplicate_knowledge_pages(config, store, db=db)

    assert "concepts/Tool-First Architectures.md" in touched
    assert not (config.wiki_dir / "concepts" / "Bug Reproduction.md").exists()
    canonical = read_wiki_page(config, "concepts/Tool-First Architectures.md")
    assert canonical is not None and "Bug Reproduction" in canonical
    notes = read_wiki_page(config, "outputs/Notes.md")
    assert notes is not None and "[[Tool-First Architectures|Bug Reproduction]]" in notes


def test_rewrite_weak_entity_pages_uses_target_specific_summary(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Debugging Systems", "Test workspace.")
    raw_path = _write_markdown(
        config.raw_dir / "source-a.md",
        "# Tool-First Agent Architectures For Bug Reproduction\n\n"
        "Tool-first debugging agents bias toward concrete reproduction before explanation. "
        "Aider and Claude Code are examples of systems that provide this tool suite.\n",
    )
    packet = extract_source_packet(raw_path, config.workspace_root)
    db = EvidenceDatabase(config.evidence_db_path)
    db.upsert_source_packet(packet)
    analysis = {
        "title": "Tool-First Agent Architectures For Bug Reproduction",
        "summary": "Tool-first debugging agents prioritize concrete reproduction before explanation.",
        "concepts": ["Tool-First Architectures"],
        "entities": ["Aider"],
        "key_claims": [
            {
                "text": "Tool-first debugging agents prioritize concrete reproduction before explanation.",
                "concepts": ["Tool-First Architectures"],
                "entities": ["Aider"],
            }
        ],
        "open_questions": [],
        "metrics": [],
        "equations": [],
        "limitations": [],
        "methods": [],
        "tags": ["tool-first"],
    }
    db.upsert_analysis(packet.source_id, analysis, summary=analysis["summary"])

    weak_entity_path = config.wiki_dir / "entities" / "Aider.md"
    weak_entity_path.parent.mkdir(parents=True, exist_ok=True)
    weak_entity_path.write_text(
        "---\n"
        "title: Aider\n"
        "type: entity\n"
        "status: seed\n"
        "summary: Tool-first debugging agents prioritize concrete reproduction before explanation.\n"
        "sources:\n"
        "  - Tool-First Agent Architectures For Bug Reproduction\n"
        "source_ids:\n"
        f"  - {packet.source_id}\n"
        "---\n\n"
        "# Aider\n\n"
        "Generic summary.\n"
    )

    class EntityRewriteCompiler:
        def write_page(self, operation, source_analysis, existing_content, related_page_titles, raw_source_path=""):
            summary = str(source_analysis.get("summary") or "").strip()
            title = operation["title"]
            page_type = operation["page_type"]
            return (
                "---\n"
                f"title: {title}\n"
                f"type: {page_type}\n"
                "status: seed\n"
                f"summary: {summary}\n"
                "sources:\n"
                "  - Tool-First Agent Architectures For Bug Reproduction\n"
                "source_ids:\n"
                f"  - {packet.source_id}\n"
                "---\n\n"
                f"# {title}\n\n"
                f"{summary}\n"
            )

    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)

    touched = _rewrite_weak_entity_pages(config, EntityRewriteCompiler(), store, db=db)

    assert touched == ["entities/Aider.md"]
    rewritten = read_wiki_page(config, "entities/Aider.md")
    assert rewritten is not None
    assert "Aider and Claude Code are examples of systems that provide this tool suite." in rewritten


def _write_markdown(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
