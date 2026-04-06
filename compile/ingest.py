from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from rich.console import Console
import yaml

from compile.compiler import Compiler
from compile.config import Config
from compile.evidence import (
    get_claims_for_concept,
    get_concepts_needing_synthesis,
    get_overlapping_concepts,
    get_source_count_for_title,
    get_source_ids_for_title,
    get_source_title_map,
    get_source_titles_for_title,
    sync_page_reference,
)
from compile.obsidian import ObsidianConnector
from compile.resolve import TitleResolver, canonicalize_question, derive_aliases
from compile.source_packet import SourcePacket, extract_source_packet, extract_url_packet, save_source_packet
from compile.store import EvidenceDatabase, normalize_alias_key
from compile.verify import verify_page_content
from compile.workspace import (
    append_log_entry,
    collect_pages_by_type,
    ensure_workspace_schema,
    list_wiki_pages,
    mark_processed,
    read_wiki_page,
    write_dashboards,
    write_index,
    write_overview,
)

console = Console()
GENERIC_KNOWLEDGE_TERMS = {
    "analysis",
    "approach",
    "benchmark",
    "case study",
    "conclusion",
    "evaluation",
    "experiment",
    "experiments",
    "finding",
    "findings",
    "framework",
    "implementation",
    "introduction",
    "method",
    "methods",
    "model",
    "motivation",
    "overview",
    "performance",
    "problem",
    "procedure",
    "result",
    "results",
    "setup",
    "study",
    "system",
    "task",
}
CONTENT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "these",
    "this",
    "to",
    "what",
    "when",
    "which",
    "with",
    "why",
}


def _load_runtime_store(db: EvidenceDatabase) -> Any:
    return db.materialize_evidence_store()


def ingest_source(config: Config, compiler: Compiler, raw_path: Path) -> list[str]:
    """Process a single raw source file through the full pipeline."""
    if not hasattr(compiler, "analyze_source_packet"):
        return _legacy_ingest_source(config, compiler, raw_path)
    return ingest_sources(config, compiler, [raw_path])


def ingest_url(config: Config, compiler: Compiler, url: str) -> list[str]:
    """Ingest a URL as a T0 raw source through the full pipeline."""
    console.print(f"\n[bold]Ingesting URL:[/bold] {url}")

    packet = extract_url_packet(url, config.workspace_root)
    packet_path = config.source_packets_dir / f"{packet.source_id}.json"
    save_source_packet(packet_path, packet)

    if not packet.full_text.strip():
        console.print(f"  [yellow]Warning: no text extracted from {url}.[/yellow]")

    # Feed the pre-built packet into the standard analysis + compilation pipeline
    packets = [packet]
    analyses_by_source_id = _analyze_source_packets(compiler, packets, max_workers=1)

    db = EvidenceDatabase(config.evidence_db_path)
    _sync_existing_pages_to_db(config, db)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())

    for pkt in packets:
        analysis = _canonicalize_analysis_terms(analyses_by_source_id[pkt.source_id], resolver)
        analyses_by_source_id[pkt.source_id] = analysis
        analysis_title = str(analysis.get("title") or pkt.title).strip() or pkt.title
        db.upsert_source_packet(pkt)
        db.upsert_analysis(
            pkt.source_id,
            analysis,
            summary=str(analysis.get("summary") or ""),
            warnings=list(analysis.get("analysis_warnings", []) or []) + list(pkt.warnings),
        )
        db.register_aliases(
            "source",
            analysis_title,
            derive_aliases(pkt.raw_title) + [pkt.title],
        )

    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())
    operations = _build_batch_operations(config, compiler, packets, analyses_by_source_id, store, db, resolver)
    page_targets = _group_operations_by_target(operations, packets, analyses_by_source_id)
    console.print(f"  Planned {len(page_targets)} unique page target(s)", style="dim")

    touched_paths = _compile_page_targets(
        config, compiler, store, db, resolver, page_targets, max_workers=1,
    )

    touched_paths.extend(_apply_quality_gates(config, store, touched_paths, db=db))
    console.print("  Refreshing navigation and dashboards...", style="dim")
    touched_paths.extend(refresh_navigation_and_dashboards(config, store, db=db))

    unique_paths = list(dict.fromkeys(touched_paths))
    source_titles = [analyses_by_source_id[pkt.source_id].get("title", pkt.title) for pkt in packets]
    append_log_entry(
        config,
        "ingest",
        f"URL ingest: {url}",
        unique_paths + [str(t) for t in source_titles],
    )
    unique_paths.append("log.md")

    console.print(f"  [bold green]Done.[/bold green] Touched {len(unique_paths)} pages.")
    return unique_paths


def ingest_sources(
    config: Config,
    compiler: Compiler,
    raw_paths: list[Path],
    *,
    max_workers: int = 4,
) -> list[str]:
    if not raw_paths:
        return []

    console.print(f"\n[bold]Ingesting batch:[/bold] {len(raw_paths)} source(s)")

    packets = _extract_source_packets(config, raw_paths, max_workers=max_workers)
    if not packets:
        return []

    analyses_by_source_id = _analyze_source_packets(compiler, packets, max_workers=max_workers)

    db = EvidenceDatabase(config.evidence_db_path)
    _sync_existing_pages_to_db(config, db)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())

    for packet in packets:
        analysis = _canonicalize_analysis_terms(analyses_by_source_id[packet.source_id], resolver)
        analyses_by_source_id[packet.source_id] = analysis
        analysis_title = str(analysis.get("title") or packet.title).strip() or packet.title
        db.upsert_source_packet(packet)
        db.upsert_analysis(
            packet.source_id,
            analysis,
            summary=str(analysis.get("summary") or ""),
            warnings=list(analysis.get("analysis_warnings", []) or []) + list(packet.warnings),
        )
        db.register_aliases(
            "source",
            analysis_title,
            derive_aliases(packet.raw_title) + [packet.title, Path(packet.raw_path).stem],
        )

    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())
    operations = _build_batch_operations(config, compiler, packets, analyses_by_source_id, store, db, resolver)
    page_targets = _group_operations_by_target(operations, packets, analyses_by_source_id)
    console.print(f"  Planned {len(page_targets)} unique page target(s)", style="dim")

    touched_paths = _compile_page_targets(
        config,
        compiler,
        store,
        db,
        resolver,
        page_targets,
        max_workers=max_workers,
    )

    touched_paths.extend(_apply_quality_gates(config, store, touched_paths, db=db))

    console.print("  Refreshing navigation and dashboards...", style="dim")
    touched_paths.extend(refresh_navigation_and_dashboards(config, store, db=db))

    unique_paths = list(dict.fromkeys(touched_paths))
    source_titles = [analyses_by_source_id[packet.source_id].get("title", packet.title) for packet in packets]
    append_log_entry(
        config,
        "ingest",
        f"Batch ingest ({len(packets)} sources)",
        unique_paths + [str(title) for title in source_titles],
    )
    unique_paths.append("log.md")

    for packet in packets:
        mark_processed(config, config.workspace_root / packet.raw_path, unique_paths)

    broken = _check_wikilinks(config, resolver=resolver)
    if broken:
        console.print(f"  [yellow]Warning: {len(broken)} unresolved wikilink(s) remain after normalization.[/yellow]")
        for link, source_page in broken[:5]:
            console.print(f"    [[{link}]] in {source_page}", style="yellow")

    console.print(f"  [bold green]Done.[/bold green] Touched {len(unique_paths)} pages.")
    return unique_paths


def _legacy_ingest_source(config: Config, compiler: Any, raw_path: Path) -> list[str]:
    console.print(f"\n[bold]Ingesting:[/bold] {raw_path.name}")

    console.print("  Extracting text...", style="dim")
    packet = extract_source_packet(raw_path, config.workspace_root)
    packet_path = config.source_packets_dir / f"{packet.source_id}.json"
    save_source_packet(packet_path, packet)
    title, text = packet.title, packet.analysis_text
    if not text.strip():
        console.print("  [yellow]Warning: no text extracted, marking as processed.[/yellow]")
        mark_processed(config, raw_path, [])
        return []

    console.print("  Analyzing source...", style="dim")
    analysis = compiler.analyze_source(title, text)
    analysis_title = analysis.get("title", title)
    analysis_warnings = list(analysis.get("analysis_warnings", []))

    source_id = packet.source_id
    db = EvidenceDatabase(config.evidence_db_path)
    db.upsert_source_packet(packet)
    db.upsert_analysis(
        source_id,
        analysis,
        summary=str(analysis.get("summary") or ""),
        warnings=analysis_warnings + list(packet.warnings),
    )
    db.register_aliases(
        "source",
        analysis_title,
        derive_aliases(packet.raw_title) + [packet.title, Path(packet.raw_path).stem],
    )
    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)

    console.print("  Planning wiki updates...", style="dim")
    index_content = read_wiki_page(config, "index.md") or ""
    evidence_context = _build_evidence_context(store, analysis)
    existing_pages = _gather_relevant_pages_via_connector(config, analysis, store)
    operations = _plan_wiki_updates(
        compiler,
        analysis,
        index_content=index_content,
        existing_pages=existing_pages,
        evidence_context=evidence_context,
    )

    touched_paths: list[str] = []
    all_page_titles = _all_page_titles(config)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())
    raw_relative = str(raw_path.relative_to(config.workspace_root)).replace("\\", "/")

    for operation in operations:
        page_path = str(operation.get("path", "")).replace("\\", "/").strip("/")
        if page_path.startswith("wiki/"):
            page_path = page_path[5:]
        if not page_path:
            page_path = _default_path_for_operation(operation)
        page_type = str(operation.get("page_type", "source")).strip() or "source"
        op_title = str(operation.get("title", "Untitled")).strip() or "Untitled"
        existing_content = read_wiki_page(config, page_path)
        related_titles = [item for item in all_page_titles if item != op_title]
        page_content = compiler.write_page(
            operation=operation,
            source_analysis=analysis,
            existing_content=existing_content,
            related_page_titles=related_titles,
            raw_source_path=raw_relative,
        )
        if page_type == "source":
            page_content = _ensure_source_provenance_block(page_content, raw_source_path=raw_relative)
        page_content = _rewrite_wikilinks(page_content, resolver)

        full_path = config.wiki_dir / page_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(page_content)
        _normalize_written_page(
            config,
            store,
            relative_path=page_path,
            title=op_title,
            page_type=page_type,
            fallback_sources=[analysis_title],
            fallback_source_ids=[source_id],
            db=db,
        )
        touched_paths.append(page_path)
        if op_title not in all_page_titles:
            all_page_titles.append(op_title)

    touched_paths.extend(_apply_quality_gates(config, store, touched_paths, db=db))
    touched_paths.extend(refresh_navigation_and_dashboards(config, store, db=db))

    unique_paths = list(dict.fromkeys(touched_paths))
    append_log_entry(config, "ingest", analysis_title, unique_paths + analysis_warnings)
    touched_paths = unique_paths + ["log.md"]

    mark_processed(config, raw_path, touched_paths)
    return touched_paths


def run_synthesis_pass(
    config: Config,
    compiler: Compiler,
    *,
    max_concepts: int = 3,
    max_comparisons: int = 3,
    cleanup_empty_notes: bool = True,
) -> list[str]:
    console.print("\n[bold]Running synthesis pass...[/bold]")
    db = EvidenceDatabase(config.evidence_db_path)
    _sync_existing_pages_to_db(config, db)
    store = _load_runtime_store(db)
    touched_paths: list[str] = []

    candidate_records = get_concepts_needing_synthesis(store)
    page_titles = _all_page_titles(config)
    rewrites = 0
    for record in candidate_records:
        if rewrites >= max_concepts:
            break
        existing_path = record.wiki_page_path or f"concepts/{record.name}.md"
        existing_content = read_wiki_page(config, existing_path)
        frontmatter, _ = _load_markdown_doc(existing_content or "")
        if existing_content and frontmatter.get("status") == "stable":
            continue

        claims = [
            {
                "text": claim.text,
                "source_id": claim.source_id,
                "source_title": claim.source_title,
                "confidence": claim.confidence,
                "concepts": claim.concepts,
                "entities": claim.entities,
            }
            for claim in get_claims_for_concept(store, record.name)
        ]
        if len({claim["source_id"] for claim in claims}) < 2:
            continue

        console.print(f"  Synthesizing concept: {record.name}", style="dim")
        page_content = compiler.synthesize_concept_page(
            concept_name=record.name,
            claims=claims,
            existing_content=existing_content,
            related_page_titles=[title for title in page_titles if title != record.name],
        )
        target_path = config.wiki_dir / existing_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(page_content)
        _normalize_written_page(
            config,
            store,
            relative_path=existing_path,
            title=record.name,
            page_type="concept",
            fallback_sources=get_source_titles_for_title(store, record.name, "concept"),
        )
        if record.name not in page_titles:
            page_titles.append(record.name)
        touched_paths.append(existing_path)
        rewrites += 1

    comparison_count = 0
    for left, right, _shared_sources in get_overlapping_concepts(store, limit=max_comparisons * 3):
        if comparison_count >= max_comparisons:
            break
        comparison_title = f"{left.name} vs {right.name}"
        comparison_path = f"outputs/{comparison_title}.md"
        claims = [
            {
                "text": claim.text,
                "source_id": claim.source_id,
                "source_title": claim.source_title,
                "confidence": claim.confidence,
            }
            for claim in get_claims_for_concept(store, left.name) + get_claims_for_concept(store, right.name)
        ]
        if len({claim["source_id"] for claim in claims}) < 2:
            continue

        console.print(f"  Writing comparison: {comparison_title}", style="dim")
        page_content = compiler.write_comparison_page(
            title=comparison_title,
            left_name=left.name,
            right_name=right.name,
            claims=claims,
            related_page_titles=[title for title in page_titles if title != comparison_title],
        )
        target_path = config.wiki_dir / comparison_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(page_content)
        _normalize_written_page(
            config,
            store,
            relative_path=comparison_path,
            title=comparison_title,
            page_type="comparison",
            fallback_sources=sorted(
                {
                    *get_source_titles_for_title(store, left.name, "concept"),
                    *get_source_titles_for_title(store, right.name, "concept"),
                }
            ),
        )
        if comparison_title not in page_titles:
            page_titles.append(comparison_title)
        touched_paths.append(comparison_path)
        comparison_count += 1

    if cleanup_empty_notes:
        connector = ObsidianConnector(config.workspace_root)
        moved_paths = connector.cleanup_empty_auxiliary_markdown_files()
        if moved_paths:
            console.print(
                f"  Cleaned {len(moved_paths)} empty auxiliary markdown file(s).",
                style="dim",
            )

    touched_paths.extend(_merge_duplicate_knowledge_pages(config, store, db=db))
    if touched_paths:
        _sync_existing_pages_to_db(config, db)
        store = _load_runtime_store(db)

    touched_paths.extend(_rewrite_weak_entity_pages(config, compiler, store, db=db))

    touched_paths.extend(_apply_quality_gates(config, store, db=db))
    touched_paths.extend(refresh_navigation_and_dashboards(config, store, db=db))

    unique_paths = list(dict.fromkeys(touched_paths))
    append_log_entry(config, "maint", "Synthesis pass", unique_paths or ["No synthesis changes were needed."])

    console.print(f"  [bold green]Done.[/bold green] Touched {len(unique_paths)} pages.")
    return unique_paths


def _extract_source_packets(config: Config, raw_paths: list[Path], *, max_workers: int) -> list[SourcePacket]:
    packets: list[SourcePacket] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(raw_paths)))) as executor:
        futures = {
            executor.submit(extract_source_packet, raw_path, config.workspace_root): raw_path
            for raw_path in raw_paths
        }
        for future in as_completed(futures):
            raw_path = futures[future]
            packet = future.result()
            if not packet.full_text.strip():
                console.print(f"  [yellow]Warning: no text extracted for {raw_path.name}.[/yellow]")
            packet_path = config.source_packets_dir / f"{packet.source_id}.json"
            save_source_packet(packet_path, packet)
            packets.append(packet)
    packets.sort(key=lambda item: item.raw_path)
    return packets


def _analyze_source_packets(
    compiler: Compiler,
    packets: list[SourcePacket],
    *,
    max_workers: int,
) -> dict[str, dict[str, Any]]:
    analyses: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(packets)))) as executor:
        futures = {
            executor.submit(_analyze_packet, compiler, packet): packet
            for packet in packets
        }
        for future in as_completed(futures):
            packet = futures[future]
            analysis = future.result()
            analysis.setdefault("_source_text", packet.full_text)
            analyses[packet.source_id] = analysis
            console.print(
                f"  Analyzed {packet.raw_path}: {analysis.get('title', packet.title)} "
                f"({len(analysis.get('key_claims', []))} claims, {len(analysis.get('concepts', []))} concepts)",
                style="dim",
            )
    return analyses


def _analyze_packet(compiler: Compiler, packet: SourcePacket) -> dict[str, Any]:
    if hasattr(compiler, "analyze_source_packet"):
        return compiler.analyze_source_packet(packet)
    return compiler.analyze_source(packet.title, packet.analysis_text)


def _canonicalize_analysis_terms(analysis: dict[str, Any], resolver: TitleResolver) -> dict[str, Any]:
    normalized = dict(analysis)
    normalized["concepts"] = [
        resolver.register(item, "concept")
        for item in _coerce_analysis_strings(analysis.get("concepts"))
    ]
    normalized["entities"] = [
        resolver.register(item, "entity")
        for item in _coerce_analysis_strings(analysis.get("entities"))
    ]
    normalized["open_questions"] = [
        resolver.register(canonicalize_question(item), "question")
        for item in _coerce_analysis_strings(analysis.get("open_questions"))
        if len(item.split()) >= 4
    ]

    claims: list[dict[str, Any]] = []
    for claim in analysis.get("key_claims", []) or []:
        if isinstance(claim, dict):
            payload = dict(claim)
            payload["concepts"] = [
                resolver.register(item, "concept")
                for item in _coerce_analysis_strings(payload.get("concepts"))
            ]
            payload["entities"] = [
                resolver.register(item, "entity")
                for item in _coerce_analysis_strings(payload.get("entities"))
            ]
            claims.append(payload)
        else:
            claims.append({"text": str(claim).strip()})
    normalized["key_claims"] = claims
    atoms: list[dict[str, Any]] = []
    for atom in analysis.get("evidence_atoms", []) or []:
        if not isinstance(atom, dict):
            continue
        payload = dict(atom)
        payload["themes"] = [
            resolver.register(item, "concept")
            for item in _coerce_analysis_strings(payload.get("themes") or payload.get("concepts"))
        ]
        payload["entities"] = [
            resolver.register(item, "entity")
            for item in _coerce_analysis_strings(payload.get("entities"))
        ]
        atoms.append(payload)
    if atoms:
        normalized["evidence_atoms"] = atoms
    return normalized


def _sync_existing_pages_to_db(config: Config, db: EvidenceDatabase) -> None:
    seen_paths: list[str] = []
    for page_path in list_wiki_pages(config):
        content = read_wiki_page(config, page_path)
        if not content:
            continue
        seen_paths.append(page_path)
        frontmatter, body = _load_markdown_doc(content)
        title = str(frontmatter.get("title") or _title_from_body(body) or Path(page_path).stem).strip()
        page_type = str(frontmatter.get("type") or _page_type_from_path(page_path)).strip() or "output"
        status = str(frontmatter.get("status") or _status_for_page(page_type, 0)).strip()
        summary = str(frontmatter.get("summary") or _summarize_body(body)).strip()
        source_ids = _coerce_list(frontmatter.get("source_ids"))
        aliases = derive_aliases(title)
        aliases.extend(_coerce_list(frontmatter.get("aliases")))
        aliases.extend(_extract_aliases_from_body(body))
        db.sync_page(
            path=page_path,
            title=title,
            page_type=page_type,
            status=status,
            summary=summary,
            source_ids=source_ids,
            aliases=list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
        )
    db.prune_page_catalog(seen_paths)


def _build_batch_operations(
    config: Config,
    compiler: Any,
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    store: Any,
    db: EvidenceDatabase,
    resolver: TitleResolver,
) -> list[dict[str, Any]]:
    pages_by_type = collect_pages_by_type(config)
    existing_titles = {entry["title"] for group in pages_by_type.values() for entry in group}
    batch_support = {
        "concept": _batch_term_supports(analyses_by_source_id, "concept", resolver),
        "entity": _batch_term_supports(analyses_by_source_id, "entity", resolver),
        "question": _batch_term_supports(analyses_by_source_id, "question", resolver),
    }
    candidate_pool = _build_batch_candidate_pool(
        packets,
        analyses_by_source_id,
        store,
        resolver,
        existing_titles=existing_titles,
        batch_support=batch_support,
    )
    page_catalog = _compact_page_catalog(db.page_catalog())
    evidence_context = _build_global_batch_evidence_context(
        store,
        candidate_pool,
        page_catalog,
    )

    planned_ops: list[dict[str, Any]] = []
    if hasattr(compiler, "plan_batch_updates"):
        try:
            planned_ops = compiler.plan_batch_updates(
                list(analyses_by_source_id.values()),
                page_catalog,
                evidence_context=evidence_context,
            )
        except TypeError:
            planned_ops = compiler.plan_batch_updates(
                list(analyses_by_source_id.values()),
                page_catalog,
            )

    operations = _normalize_planned_batch_operations(
        planned_ops,
        packets,
        analyses_by_source_id,
        store,
        resolver,
        existing_titles=existing_titles,
        candidate_pool=candidate_pool,
    )
    if not operations:
        operations = _fallback_batch_operations(
            packets,
            analyses_by_source_id,
            store,
            resolver,
            existing_titles=existing_titles,
            candidate_pool=candidate_pool,
        )
    operations = _ensure_mandatory_source_operations(
        operations,
        packets,
        analyses_by_source_id,
        resolver,
        existing_titles=existing_titles,
    )
    operations = _augment_batch_operations_with_high_confidence_targets(
        operations,
        packets,
        analyses_by_source_id,
        store,
        resolver,
        existing_titles=existing_titles,
        candidate_pool=candidate_pool,
    )
    operations = _prune_batch_operations(
        operations,
        store,
        existing_titles=existing_titles,
        source_count=len(packets),
    )
    return operations


def _compact_page_catalog(page_catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for entry in page_catalog:
        compact.append(
            {
                "title": str(entry.get("title") or ""),
                "page_type": str(entry.get("page_type") or entry.get("type") or ""),
                "status": str(entry.get("status") or ""),
                "summary": str(entry.get("summary") or "")[:180],
                "source_count": len(entry.get("source_ids") or []),
            }
        )
    return compact


def _build_global_batch_evidence_context(
    store: Any,
    candidate_pool: dict[tuple[str, str], dict[str, Any]],
    page_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    existing_lookup = {
        (str(entry.get("page_type") or ""), str(entry.get("title") or "")): entry
        for entry in page_catalog
    }
    candidates: list[dict[str, Any]] = []
    for (page_type, title), item in sorted(candidate_pool.items(), key=lambda pair: (pair[0][0], pair[0][1].lower())):
        existing = existing_lookup.get((page_type, title), {})
        existing_source_ids = get_source_ids_for_title(store, title, page_type)
        candidates.append(
            {
                "title": title,
                "page_type": page_type,
                "batch_source_count": len(item.get("source_ids", [])),
                "existing_source_count": len(existing_source_ids),
                "exists": bool(existing),
                "status": str(existing.get("status") or ""),
                "summary": str(existing.get("summary") or "")[:140],
                "batch_sources": list(item.get("source_titles", []))[:6],
                "sample_key_points": list(item.get("key_points", []))[:5],
            }
        )

    stable_pages = [
        {
            "title": str(entry.get("title") or ""),
            "page_type": str(entry.get("page_type") or ""),
            "source_count": len(entry.get("source_ids") or []),
            "summary": str(entry.get("summary") or "")[:120],
        }
        for entry in page_catalog
        if str(entry.get("status") or "") == "stable"
    ]

    return {
        "candidate_pages": candidates[:80],
        "existing_stable_pages": stable_pages[:50],
        "concepts_needing_synthesis": [
            {
                "name": record.name,
                "source_count": len(record.source_ids),
                "wiki_page_path": record.wiki_page_path,
            }
            for record in get_concepts_needing_synthesis(store)[:20]
        ],
    }


def _candidate_limits_for_analysis(analysis: dict[str, Any]) -> dict[str, int]:
    profile = analysis.get("source_profile") if isinstance(analysis.get("source_profile"), dict) else {}
    evidence_mode = str(profile.get("evidence_mode") or "").strip()
    source_kind = str(profile.get("source_kind") or "").strip()
    limits = {"concept": 4, "entity": 2, "question": 2}
    if evidence_mode in {"reflective", "narrative"} or source_kind == "journal_entry":
        limits = {"concept": 3, "entity": 1, "question": 1}
    elif evidence_mode == "argumentative":
        limits = {"concept": 4, "entity": 1, "question": 2}
    return limits


def _build_batch_candidate_pool(
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    store: Any,
    resolver: TitleResolver,
    *,
    existing_titles: set[str],
    batch_support: dict[str, dict[str, int]],
) -> dict[tuple[str, str], dict[str, Any]]:
    packet_map = {packet.source_id: packet for packet in packets}
    pool: dict[tuple[str, str], dict[str, Any]] = {}

    for packet in packets:
        analysis = analyses_by_source_id[packet.source_id]
        source_title = resolver.register(
            str(analysis.get("title") or packet.title),
            "source",
            aliases=[packet.raw_title, packet.title, Path(packet.raw_path).stem],
        )
        key = ("source", source_title)
        bucket = pool.setdefault(
            key,
            {
                "title": source_title,
                "page_type": "source",
                "source_ids": [],
                "source_titles": [],
                "key_points": [],
                "reason": "Maintain the source page for this raw artifact.",
            },
        )
        bucket["source_ids"].append(packet.source_id)
        bucket["source_titles"].append(source_title)

        limits = _candidate_limits_for_analysis(analysis)
        for page_type in ("concept", "entity", "question"):
            limit = int(limits.get(page_type, 0))
            if limit <= 0:
                continue
            for title in _prioritized_terms(
                analysis,
                page_type,
                resolver,
                store=store,
                existing_titles=existing_titles,
                batch_support=batch_support[page_type],
                limit=limit,
            ):
                canonical = resolver.register(
                    canonicalize_question(title) if page_type == "question" else title,
                    page_type,
                )
                entry = pool.setdefault(
                    (page_type, canonical),
                    {
                        "title": canonical,
                        "page_type": page_type,
                        "source_ids": [],
                        "source_titles": [],
                        "key_points": [],
                        "reason": "",
                    },
                )
                if packet.source_id not in entry["source_ids"]:
                    entry["source_ids"].append(packet.source_id)
                    entry["source_titles"].append(source_title)
                for point in _key_points_for_title(analysis, canonical, page_type):
                    if point not in entry["key_points"]:
                        entry["key_points"].append(point)

    for (page_type, title), entry in pool.items():
        if page_type == "source":
            continue
        source_titles = ", ".join(entry["source_titles"][:3])
        if len(entry["source_ids"]) >= 2:
            entry["reason"] = f"Global batch evidence suggests {title} should synthesize across {source_titles}."
        elif title in existing_titles:
            entry["reason"] = f"Update existing {page_type} page with new evidence from {source_titles}."
        else:
            entry["reason"] = f"Create focused {page_type} page from new batch evidence in {source_titles}."
    return pool


def _normalize_planned_batch_operations(
    planned_ops: list[dict[str, Any]],
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    store: Any,
    resolver: TitleResolver,
    *,
    existing_titles: set[str],
    candidate_pool: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    if not planned_ops:
        return []
    packet_map = {packet.source_id: packet for packet in packets}
    source_title_to_id = {
        resolver.canonical_title(str(analysis.get("title") or packet.title), "source"): source_id
        for source_id, packet in packet_map.items()
        for analysis in [analyses_by_source_id[source_id]]
    }
    operations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for item in planned_ops:
        if not isinstance(item, dict):
            continue
        page_type = str(item.get("page_type") or "").strip().lower()
        if page_type not in {"source", "concept", "entity", "question"}:
            continue
        raw_title = str(item.get("title") or "").strip()
        if not raw_title:
            continue
        canonical = resolver.canonical_title(raw_title, page_type)
        key = (page_type, canonical)
        if key in seen:
            continue
        seen.add(key)

        candidate = candidate_pool.get(key, {})
        if page_type == "source":
            source_id = source_title_to_id.get(canonical)
            if not source_id:
                continue
            source_ids = [source_id]
            source_titles = [canonical]
        else:
            source_ids = list(candidate.get("source_ids", [])) or _batch_source_ids_for_target(
                canonical,
                page_type,
                analyses_by_source_id,
            )
            if not source_ids:
                continue
            source_titles = [
                str(analyses_by_source_id[source_id].get("title") or packet_map[source_id].title)
                for source_id in source_ids
                if source_id in analyses_by_source_id
            ]

        source_count = len(set(get_source_ids_for_title(store, canonical, page_type) + source_ids))
        status = str(item.get("status") or _status_for_page(page_type, source_count)).strip()
        if page_type in {"concept", "entity", "question"}:
            status = _status_for_page(page_type, source_count)
        key_points = _coerce_analysis_strings(item.get("key_points")) or list(candidate.get("key_points", []))[:8]
        operations.append(
            {
                "action": "update" if canonical in existing_titles else "create",
                "title": canonical,
                "page_type": page_type,
                "path": _default_path_for_operation({"title": canonical, "page_type": page_type}),
                "status": status,
                "reason": str(item.get("reason") or candidate.get("reason") or "").strip(),
                "key_points": key_points[:8],
                "source_ids": source_ids,
                "source_titles": source_titles,
            }
        )
    return operations


def _fallback_batch_operations(
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    store: Any,
    resolver: TitleResolver,
    *,
    existing_titles: set[str],
    candidate_pool: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    packet_map = {packet.source_id: packet for packet in packets}

    for packet in packets:
        analysis = analyses_by_source_id[packet.source_id]
        source_title = resolver.canonical_title(str(analysis.get("title") or packet.title), "source")
        operations.append(
            {
                "action": "update" if source_title in existing_titles else "create",
                "title": source_title,
                "page_type": "source",
                "path": f"sources/{source_title}.md",
                "status": "stable",
                "reason": "Maintain the source page for this raw artifact.",
                "key_points": [],
                "source_ids": [packet.source_id],
                "source_titles": [source_title],
            }
        )

    prioritized = sorted(
        (
            candidate
            for key, candidate in candidate_pool.items()
            if key[0] != "source"
        ),
        key=lambda item: (
            -len(item.get("source_ids", [])),
            -(len(get_source_ids_for_title(store, item["title"], item["page_type"]))),
            item["page_type"],
            item["title"].lower(),
        ),
    )
    for candidate in prioritized:
        title = str(candidate["title"])
        page_type = str(candidate["page_type"])
        source_ids = list(candidate.get("source_ids", []))
        if not source_ids:
            continue
        existing_support = len(get_source_ids_for_title(store, title, page_type))
        if page_type == "entity" and existing_support < 1 and len(source_ids) < 2:
            continue
        if page_type == "question" and existing_support < 1 and len(source_ids) < 2:
            continue
        if page_type == "concept" and existing_support < 1 and len(source_ids) < 2 and len(candidate.get("key_points", []) or []) < 2:
            continue
        source_titles = [
            str(analyses_by_source_id[source_id].get("title") or packet_map[source_id].title)
            for source_id in source_ids
            if source_id in analyses_by_source_id
        ]
        source_count = len(set(get_source_ids_for_title(store, title, page_type) + source_ids))
        operations.append(
            {
                "action": "update" if title in existing_titles else "create",
                "title": title,
                "page_type": page_type,
                "path": _default_path_for_operation({"title": title, "page_type": page_type}),
                "status": _status_for_page(page_type, source_count),
                "reason": str(candidate.get("reason") or "").strip(),
                "key_points": list(candidate.get("key_points", []))[:8],
                "source_ids": source_ids,
                "source_titles": source_titles,
            }
        )
    return operations


def _ensure_mandatory_source_operations(
    operations: list[dict[str, Any]],
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    resolver: TitleResolver,
    *,
    existing_titles: set[str],
) -> list[dict[str, Any]]:
    present = {(str(item.get("page_type")), str(item.get("title"))) for item in operations}
    for packet in packets:
        analysis = analyses_by_source_id[packet.source_id]
        source_title = resolver.canonical_title(str(analysis.get("title") or packet.title), "source")
        key = ("source", source_title)
        if key in present:
            continue
        operations.append(
            {
                "action": "update" if source_title in existing_titles else "create",
                "title": source_title,
                "page_type": "source",
                "path": f"sources/{source_title}.md",
                "status": "stable",
                "reason": "Maintain the source page for this raw artifact.",
                "key_points": [],
                "source_ids": [packet.source_id],
                "source_titles": [source_title],
            }
        )
    return operations


def _augment_batch_operations_with_high_confidence_targets(
    operations: list[dict[str, Any]],
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
    store: Any,
    resolver: TitleResolver,
    *,
    existing_titles: set[str],
    candidate_pool: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    present = {(str(item.get("page_type")), str(item.get("title"))) for item in operations}
    packet_map = {packet.source_id: packet for packet in packets}
    for (page_type, title), candidate in candidate_pool.items():
        if page_type == "source" or (page_type, title) in present:
            continue
        batch_source_count = len(candidate.get("source_ids", []))
        existing_support = len(get_source_ids_for_title(store, title, page_type))
        should_add = batch_source_count >= 2 or (existing_support >= 1 and batch_source_count >= 1)
        if not should_add:
            continue
        source_ids = list(candidate.get("source_ids", []))
        source_titles = [
            str(analyses_by_source_id[source_id].get("title") or packet_map[source_id].title)
            for source_id in source_ids
            if source_id in analyses_by_source_id
        ]
        operations.append(
            {
                "action": "update" if title in existing_titles else "create",
                "title": title,
                "page_type": page_type,
                "path": _default_path_for_operation({"title": title, "page_type": page_type}),
                "status": _status_for_page(page_type, batch_source_count + existing_support),
                "reason": str(candidate.get("reason") or "").strip(),
                "key_points": list(candidate.get("key_points", []))[:8],
                "source_ids": source_ids,
                "source_titles": source_titles,
            }
        )
    return operations


def _prune_batch_operations(
    operations: list[dict[str, Any]],
    store: Any,
    *,
    existing_titles: set[str],
    source_count: int,
) -> list[dict[str, Any]]:
    source_ops = [item for item in operations if str(item.get("page_type")) == "source"]
    knowledge_ops = [item for item in operations if str(item.get("page_type")) != "source"]
    quotas = {
        "concept": max(4, source_count + 2),
        "entity": max(2, (source_count // 2) + 1),
        "question": max(1, (source_count // 2) + 1),
    }
    kept: list[dict[str, Any]] = list(source_ops)
    for page_type in ("concept", "entity", "question"):
        typed_ops = [item for item in knowledge_ops if str(item.get("page_type")) == page_type]
        typed_ops.sort(
            key=lambda item: (
                -_operation_priority_score(item, store, existing_titles=existing_titles),
                str(item.get("title") or "").lower(),
            )
        )
        quota = quotas[page_type]
        must_keep = [
            item
            for item in typed_ops
            if len(item.get("source_ids", []) or []) >= 2
            or len(get_source_ids_for_title(store, str(item.get("title") or ""), page_type)) >= 2
            or str(item.get("title") or "") in existing_titles
        ]
        selected: list[dict[str, Any]] = []
        for item in typed_ops:
            if item in must_keep:
                if item not in selected:
                    selected.append(item)
                continue
            if len(selected) >= quota:
                continue
            if any(
                _operations_look_near_duplicate(item, existing)
                for existing in selected
                if existing not in must_keep
            ):
                continue
            selected.append(item)
        kept.extend(selected)
    return kept


def _operation_priority_score(
    operation: dict[str, Any],
    store: Any,
    *,
    existing_titles: set[str],
) -> float:
    title = str(operation.get("title") or "")
    page_type = str(operation.get("page_type") or "")
    score = 0.0
    if title in existing_titles:
        score += 6.0
    batch_sources = len(operation.get("source_ids", []) or [])
    score += min(batch_sources, 3) * 4.0
    existing_support = len(get_source_ids_for_title(store, title, page_type))
    score += min(existing_support, 3) * 3.0
    score += min(len(operation.get("key_points", []) or []), 4) * 1.0
    score += _anchor_score_from_blobs(
        title,
        [*list(operation.get("source_titles", []) or []), *list(operation.get("key_points", []) or [])],
    )
    if page_type == "entity" and batch_sources < 2 and existing_support < 1:
        score -= 3.0
    if page_type == "question" and batch_sources < 2 and existing_support < 1:
        score -= 2.5
    if page_type == "concept" and batch_sources < 2 and existing_support < 1 and len(operation.get("key_points", []) or []) < 2:
        score -= 2.0
    return score


def _operations_look_near_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if str(left.get("page_type") or "") != str(right.get("page_type") or ""):
        return False
    left_title = str(left.get("title") or "")
    right_title = str(right.get("title") or "")
    if not left_title or not right_title or left_title == right_title:
        return False
    left_tokens = _title_tokens(left_title)
    right_tokens = _title_tokens(right_title)
    if not left_tokens or not right_tokens:
        return _operation_text_similarity(left, right) >= 0.85
    overlap = left_tokens & right_tokens
    left_sources = set(left.get("source_ids", []) or [])
    right_sources = set(right.get("source_ids", []) or [])
    same_sources = left_sources == right_sources
    source_overlap = _source_overlap_ratio(left_sources, right_sources)
    text_similarity = _operation_text_similarity(left, right)
    if len(overlap) >= 2:
        return True
    if len(overlap) >= 1 and same_sources:
        return True
    shorter = min(len(left_tokens), len(right_tokens))
    if bool(overlap) and shorter > 0 and (len(overlap) / shorter) >= 0.6:
        return True
    return source_overlap >= 0.5 and text_similarity >= 0.74


def _title_tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", title.casefold()))


def _content_tokens(text: str) -> set[str]:
    normalized = normalize_alias_key(text)
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) > 2 and token not in CONTENT_STOPWORDS
    }


def _text_similarity(left: str, right: str) -> float:
    left_tokens = _content_tokens(left)
    right_tokens = _content_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(min(len(left_tokens), len(right_tokens)), 1)


def _source_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(min(len(left), len(right)), 1)


def _operation_text_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = " ".join(str(item).strip() for item in left.get("key_points", []) or [] if str(item).strip())
    right_text = " ".join(str(item).strip() for item in right.get("key_points", []) or [] if str(item).strip())
    return _text_similarity(left_text, right_text)


def _anchor_score_from_blobs(title: str, blobs: list[str]) -> float:
    normalized_title = normalize_alias_key(title)
    title_tokens = _content_tokens(title)
    if not normalized_title and not title_tokens:
        return 0.0

    score = 0.0
    for blob in blobs:
        text = str(blob).strip()
        if not text:
            continue
        normalized_blob = normalize_alias_key(text)
        if normalized_title and normalized_title in normalized_blob:
            score += 2.5
        blob_tokens = _content_tokens(text)
        if title_tokens and blob_tokens:
            score += (len(title_tokens & blob_tokens) / len(title_tokens)) * 1.5
    return score


def _batch_source_ids_for_target(
    title: str,
    page_type: str,
    analyses_by_source_id: dict[str, dict[str, Any]],
) -> list[str]:
    matched: list[str] = []
    target = title.casefold()
    for source_id, analysis in analyses_by_source_id.items():
        haystacks: list[str] = []
        if page_type == "concept":
            haystacks.extend(_coerce_analysis_strings(analysis.get("concepts")))
        elif page_type == "entity":
            haystacks.extend(_coerce_analysis_strings(analysis.get("entities")))
        elif page_type == "question":
            haystacks.extend(_coerce_analysis_strings(analysis.get("open_questions")))
        for claim in analysis.get("key_claims", []) or []:
            if isinstance(claim, dict):
                haystacks.extend(_coerce_analysis_strings(claim.get("concepts")))
                haystacks.extend(_coerce_analysis_strings(claim.get("entities")))
                haystacks.append(str(claim.get("text") or ""))
            else:
                haystacks.append(str(claim))
        if any(target == str(item).casefold() or target in str(item).casefold() for item in haystacks if str(item).strip()):
            matched.append(source_id)
    return matched


def _batch_term_supports(
    analyses_by_source_id: dict[str, dict[str, Any]],
    page_type: str,
    resolver: TitleResolver,
) -> dict[str, int]:
    support: dict[str, set[str]] = {}
    field = {
        "concept": "concepts",
        "entity": "entities",
        "question": "open_questions",
    }[page_type]
    for source_id, analysis in analyses_by_source_id.items():
        for item in _coerce_analysis_strings(analysis.get(field)):
            canonical = resolver.canonical_title(
                canonicalize_question(item) if page_type == "question" else item,
                page_type,
            )
            if canonical:
                support.setdefault(canonical, set()).add(source_id)
    return {term: len(source_ids) for term, source_ids in support.items()}


def _group_operations_by_target(
    operations: list[dict[str, Any]],
    packets: list[SourcePacket],
    analyses_by_source_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    packet_map = {packet.source_id: packet for packet in packets}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in operations:
        key = (str(operation["page_type"]), str(operation["title"]))
        bucket = grouped.setdefault(
            key,
            {
                "operation": dict(operation),
                "source_ids": [],
                "source_titles": [],
                "supporting_sources": [],
                "raw_source_path": "",
            },
        )
        source_ids = [str(item).strip() for item in operation.get("source_ids", []) or [] if str(item).strip()]
        source_id = str(operation.get("source_id") or "").strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        for source_id in source_ids:
            if source_id in bucket["source_ids"] or source_id not in analyses_by_source_id:
                continue
            bucket["source_ids"].append(source_id)
            analysis = analyses_by_source_id[source_id]
            source_title = str(analysis.get("title") or packet_map[source_id].title)
            bucket["source_titles"].append(source_title)
            bucket["supporting_sources"].append(_supporting_source_payload(source_id, analysis))
            if bucket["operation"]["page_type"] == "source":
                bucket["raw_source_path"] = packet_map[source_id].raw_path
                bucket["operation"]["key_points"] = list(dict.fromkeys(bucket["operation"].get("key_points", [])))
        bucket["operation"]["action"] = "update" if bucket["operation"]["action"] == "update" or operation.get("action") == "update" else "create"
        merged_points = bucket["operation"].setdefault("key_points", [])
        for item in operation.get("key_points", []) or []:
            text = str(item).strip()
            if text and text not in merged_points:
                merged_points.append(text)

    targets = list(grouped.values())
    targets.sort(key=lambda item: (_page_type_priority(item["operation"]["page_type"]), item["operation"]["title"].lower()))
    return targets


def _compile_page_targets(
    config: Config,
    compiler: Compiler,
    store: Any,
    db: EvidenceDatabase,
    resolver: TitleResolver,
    page_targets: list[dict[str, Any]],
    *,
    max_workers: int = 4,
) -> list[str]:
    touched_paths: list[str] = []
    all_page_titles = sorted({*[_entry["title"] for _entry in db.page_catalog()], *(target["operation"]["title"] for target in page_targets)})
    valid_link_targets = set(all_page_titles)
    valid_link_targets.update(str(raw_file.relative_to(config.workspace_root)).replace("\\", "/") for raw_file in config.raw_dir.rglob("*") if raw_file.is_file())

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(page_targets)))) as executor:
        futures = {
            executor.submit(
                _compile_target_artifact,
                config,
                compiler,
                store,
                db,
                resolver,
                target,
                all_page_titles,
                valid_link_targets,
            ): target
            for target in page_targets
        }
        for future in as_completed(futures):
            compiled = future.result()
            hard_failures = [issue for issue in compiled["issues"] if issue.severity == "high"]
            if hard_failures:
                messages = "; ".join(issue.message for issue in hard_failures[:3])
                raise ValueError(f"Verification failed for {compiled['page_path']}: {messages}")

            full_path = config.wiki_dir / compiled["page_path"]
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(compiled["content"])
            _normalize_written_page(
                config,
                store,
                relative_path=compiled["page_path"],
                title=compiled["title"],
                page_type=compiled["page_type"],
                fallback_sources=compiled["source_titles"],
                fallback_source_ids=compiled["source_ids"],
                db=db,
            )
            touched_paths.append(compiled["page_path"])
            if compiled["title"] not in all_page_titles:
                all_page_titles.append(compiled["title"])
                valid_link_targets.add(compiled["title"])
    return touched_paths


def _compile_target_artifact(
    config: Config,
    compiler: Compiler,
    store: Any,
    db: EvidenceDatabase,
    resolver: TitleResolver,
    target: dict[str, Any],
    all_page_titles: list[str],
    valid_link_targets: set[str],
) -> dict[str, Any]:
    operation = dict(target["operation"])
    page_path = str(operation.get("path") or _default_path_for_operation(operation)).replace("\\", "/").strip("/")
    title = str(operation["title"])
    page_type = str(operation["page_type"])
    source_ids = _supporting_source_ids_for_target(title, page_type, list(dict.fromkeys(target.get("source_ids", []))), store)
    supporting_sources = _supporting_sources_for_target(title, page_type, source_ids, db)
    source_titles = [str(item.get("title") or item.get("source_title") or "").strip() for item in supporting_sources]
    source_titles = [title for title in source_titles if title]
    source_analysis = _page_analysis_payload(title, page_type, supporting_sources)
    source_analysis["source_ids"] = source_ids

    existing_content = read_wiki_page(config, page_path)
    related_titles = [item for item in all_page_titles if item != title]
    page_content = compiler.write_page(
        operation=operation,
        source_analysis=source_analysis,
        existing_content=existing_content,
        related_page_titles=related_titles,
        raw_source_path=str(target.get("raw_source_path") or ""),
    )
    if page_type == "source":
        page_content = _ensure_source_provenance_block(
            page_content,
            raw_source_path=str(target.get("raw_source_path") or ""),
        )
    page_content = _rewrite_wikilinks(page_content, resolver)

    issues = verify_page_content(
        page_type=page_type,
        content=page_content,
        raw_source_path=str(target.get("raw_source_path") or ""),
        source_count=len(source_ids),
        expected_equations=len(source_analysis.get("equations", []) or []),
        expected_metrics=len(source_analysis.get("metrics", []) or []),
        valid_link_targets=valid_link_targets,
    )
    return {
        "page_path": page_path,
        "title": title,
        "page_type": page_type,
        "content": page_content,
        "source_ids": source_ids,
        "source_titles": source_titles,
        "issues": issues,
    }


def _page_analysis_payload(title: str, page_type: str, supporting_sources: list[dict[str, Any]]) -> dict[str, Any]:
    summary_parts = [
        str(item.get("summary") or "").strip()
        for item in supporting_sources
        if str(item.get("summary") or "").strip()
    ]
    claims: list[dict[str, Any]] = []
    concepts: list[str] = []
    entities: list[str] = []
    methods: list[str] = []
    metrics: list[dict[str, Any]] = []
    equations: list[dict[str, Any]] = []
    limitations: list[str] = []
    open_questions: list[str] = []
    tags: list[str] = []
    atoms: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []

    for source in supporting_sources:
        source_title = str(source.get("title") or source.get("source_title") or "").strip()
        profile = source.get("source_profile")
        if isinstance(profile, dict):
            profiles.append(profile)
        for claim in source.get("key_claims", []) or []:
            if isinstance(claim, dict):
                payload = dict(claim)
                payload.setdefault("source_title", source_title)
            else:
                payload = {"text": str(claim).strip(), "source_title": source_title}
            if str(payload.get("text") or "").strip():
                claims.append(payload)
        concepts.extend(_coerce_analysis_strings(source.get("concepts")))
        entities.extend(_coerce_analysis_strings(source.get("entities")))
        methods.extend(_coerce_analysis_strings(source.get("methods")))
        limitations.extend(_coerce_analysis_strings(source.get("limitations")))
        open_questions.extend(_coerce_analysis_strings(source.get("open_questions")))
        tags.extend(_coerce_analysis_strings(source.get("tags")))
        for metric in source.get("metrics", []) or []:
            if isinstance(metric, dict):
                payload = dict(metric)
                payload.setdefault("source_title", source_title)
                metrics.append(payload)
        for equation in source.get("equations", []) or []:
            if isinstance(equation, dict):
                payload = dict(equation)
                payload.setdefault("source_title", source_title)
                equations.append(payload)
        for atom in source.get("evidence_atoms", []) or []:
            if isinstance(atom, dict) and str(atom.get("text") or "").strip():
                payload = dict(atom)
                payload.setdefault("source_title", source_title)
                atoms.append(payload)

    source_profile = profiles[0] if profiles else {}

    return {
        "title": title,
        "page_type": page_type,
        "summary": " ".join(dict.fromkeys(summary_parts[:3])).strip(),
        "key_claims": claims,
        "concepts": list(dict.fromkeys(concepts)),
        "entities": list(dict.fromkeys(entities)),
        "methods": list(dict.fromkeys(methods)),
        "metrics": metrics,
        "equations": equations,
        "limitations": list(dict.fromkeys(limitations)),
        "open_questions": list(dict.fromkeys(open_questions)),
        "tags": list(dict.fromkeys(tags)),
        "source_profile": source_profile,
        "evidence_atoms": atoms[:20],
        "supporting_sources": supporting_sources,
    }


def _extract_target_snippets(title: str, page_type: str, analysis: dict[str, Any]) -> list[str]:
    source_text = str(analysis.get("_source_text") or "").strip()
    if not source_text:
        packet = analysis.get("packet") or {}
        if isinstance(packet, dict):
            source_text = str(packet.get("full_text") or packet.get("analysis_text") or "").strip()
    if not source_text:
        return []

    snippets: list[str] = []
    seen: set[str] = set()
    for raw_fragment in re.split(r"(?<=[.!?])\s+|\n+", source_text):
        fragment = raw_fragment.strip().lstrip("#").strip()
        if len(fragment) < 20:
            continue
        if _is_metadata_fragment(fragment):
            continue
        if not _text_matches_target(fragment, title, page_type=page_type):
            continue
        key = normalize_alias_key(fragment)
        if not key or key in seen:
            continue
        seen.add(key)
        snippets.append(fragment[:320])
        if len(snippets) >= 4:
            break
    return snippets


def _is_metadata_fragment(text: str) -> bool:
    lowered = text.casefold()
    if lowered.startswith("pdf source named "):
        return True
    if "content extraction is deferred to anthropic" in lowered:
        return True
    if "native pdf reader" in lowered:
        return True
    if re.search(r"\bsource_[a-f0-9]{6,}\b", lowered):
        return True
    if re.search(r"\b\S+\.pdf\b", text, flags=re.IGNORECASE):
        return True
    return False


def _text_matches_target(text: str, title: str, *, page_type: str) -> bool:
    normalized_text = normalize_alias_key(text)
    normalized_title = normalize_alias_key(title)
    if not normalized_text or not normalized_title:
        return False
    if normalized_title in normalized_text:
        return True

    title_tokens = _content_tokens(title)
    text_tokens = _content_tokens(text)
    if not title_tokens or not text_tokens:
        return False

    overlap = len(title_tokens & text_tokens)
    if overlap == len(title_tokens):
        return True
    if page_type == "concept" and len(title_tokens) >= 2 and overlap >= max(2, len(title_tokens) - 1):
        return True
    if page_type == "entity" and len(title_tokens) == 1 and overlap >= 1:
        return True
    return False


def _claim_text(claim: dict[str, Any] | str) -> str:
    if isinstance(claim, dict):
        return str(claim.get("text") or "").strip()
    return str(claim).strip()


def _supporting_source_payload(source_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": str(analysis.get("title") or "").strip(),
        "raw_path": str(analysis.get("raw_path") or "").strip(),
        "source_type": str(analysis.get("source_type") or "").strip(),
        "summary": str(analysis.get("summary") or "").strip(),
        "key_claims": list(analysis.get("key_claims", []) or []),
        "concepts": list(analysis.get("concepts", []) or []),
        "entities": list(analysis.get("entities", []) or []),
        "methods": list(analysis.get("methods", []) or []),
        "metrics": list(analysis.get("metrics", []) or []),
        "equations": list(analysis.get("equations", []) or []),
        "limitations": list(analysis.get("limitations", []) or []),
        "open_questions": list(analysis.get("open_questions", []) or []),
        "tags": list(analysis.get("tags", []) or []),
        "source_profile": dict(analysis.get("source_profile") or {}),
        "evidence_atoms": list(analysis.get("evidence_atoms", []) or []),
    }


def _supporting_source_ids_for_target(
    title: str,
    page_type: str,
    target_source_ids: list[str],
    store: Any,
) -> list[str]:
    if page_type == "source":
        return list(dict.fromkeys(target_source_ids))
    source_ids = list(dict.fromkeys(target_source_ids + get_source_ids_for_title(store, title, page_type)))
    return source_ids[:8]


def _supporting_sources_for_target(
    title: str,
    page_type: str,
    source_ids: list[str],
    db: EvidenceDatabase,
) -> list[dict[str, Any]]:
    analyses = db.get_source_analyses(source_ids)
    results: list[dict[str, Any]] = []
    for analysis in analyses:
        filtered = _filter_analysis_for_target(title, page_type, analysis)
        if filtered is not None:
            results.append(filtered)
    if results:
        return results
    return [_supporting_source_payload(str(analysis.get("source_id") or ""), analysis) for analysis in analyses]


def _filter_analysis_for_target(title: str, page_type: str, analysis: dict[str, Any]) -> dict[str, Any] | None:
    if page_type == "source":
        return _supporting_source_payload(str(analysis.get("source_id") or ""), analysis)

    target = title.casefold()
    target_mentions = _extract_target_snippets(title, page_type, analysis)
    filtered_claims: list[dict[str, Any] | str] = []
    for claim in analysis.get("key_claims", []) or []:
        if isinstance(claim, dict):
            text = str(claim.get("text") or "").strip()
            concepts = [str(value).casefold() for value in claim.get("concepts", []) or []]
            entities = [str(value).casefold() for value in claim.get("entities", []) or []]
        else:
            text = str(claim).strip()
            concepts = []
            entities = []
        if not text:
            continue
        if page_type == "concept":
            if target in concepts or target in text.casefold():
                filtered_claims.append(claim)
        elif page_type == "entity":
            if target in text.casefold():
                filtered_claims.append(claim)
        else:
            question_texts = [canonicalize_question(item).casefold() for item in _coerce_analysis_strings(analysis.get("open_questions"))]
            if target in text.casefold() or target in question_texts:
                filtered_claims.append(claim)

    raw_terms = {
        "concept": _coerce_analysis_strings(analysis.get("concepts")),
        "entity": _coerce_analysis_strings(analysis.get("entities")),
        "question": _coerce_analysis_strings(analysis.get("open_questions")),
    }.get(page_type, [])
    mentioned = any(target == str(item).casefold() for item in raw_terms)
    if page_type == "entity":
        if not filtered_claims and not target_mentions:
            return None
    elif not filtered_claims and not mentioned and not target_mentions:
        return None

    payload = _supporting_source_payload(str(analysis.get("source_id") or ""), analysis)
    if not filtered_claims and target_mentions:
        label_key = "entities" if page_type == "entity" else "concepts"
        filtered_claims = [
            {
                "text": mention,
                label_key: [title],
                "source_title": payload.get("title") or payload.get("source_title") or "",
            }
            for mention in target_mentions
        ]
    payload["key_claims"] = filtered_claims[:10]
    payload["target_mentions"] = target_mentions[:4]
    if page_type == "concept":
        payload["concepts"] = [item for item in payload.get("concepts", []) if str(item).casefold() == target]
    elif page_type == "entity":
        payload["entities"] = [item for item in payload.get("entities", []) if str(item).casefold() == target]
        payload["metrics"] = _filter_metrics_for_target(target, payload.get("metrics", []))
    elif page_type == "question":
        payload["open_questions"] = [
            item
            for item in payload.get("open_questions", [])
            if canonicalize_question(str(item)).casefold() == target
        ] or payload.get("open_questions", [])
        payload["metrics"] = _filter_metrics_for_target(target, payload.get("metrics", []))
    targeted_summary = ""
    if target_mentions:
        targeted_summary = target_mentions[0]
    elif filtered_claims:
        targeted_summary = _claim_text(filtered_claims[0])
    if targeted_summary:
        payload["summary"] = targeted_summary
    return payload


def _filter_metrics_for_target(target: str, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for metric in metrics or []:
        if not isinstance(metric, dict):
            continue
        label = str(metric.get("label") or metric.get("metric") or metric.get("name") or "").casefold()
        context = str(metric.get("context") or metric.get("notes") or "").casefold()
        value = str(metric.get("value") or "").casefold()
        if target in label or target in context or target in value:
            filtered.append(metric)
    return filtered


def _key_points_for_title(analysis: dict[str, Any], title: str, page_type: str) -> list[str]:
    title_lower = title.casefold()
    points: list[str] = []
    for claim in analysis.get("key_claims", []) or []:
        text = str(claim.get("text") if isinstance(claim, dict) else claim).strip()
        if not text:
            continue
        if page_type == "concept":
            labels = _coerce_analysis_strings(claim.get("concepts") if isinstance(claim, dict) else [])
        elif page_type == "entity":
            labels = _coerce_analysis_strings(claim.get("entities") if isinstance(claim, dict) else [])
        else:
            labels = []
        if any(label.casefold() == title_lower for label in labels) or title_lower in text.casefold():
            points.append(text)
    for snippet in _extract_target_snippets(title, page_type, analysis):
        if snippet not in points:
            points.append(snippet)
    if not points and analysis.get("summary"):
        points.append(str(analysis["summary"]).strip())
    return points[:8]


def _prioritized_terms(
    analysis: dict[str, Any],
    page_type: str,
    resolver: TitleResolver,
    *,
    store: Any,
    existing_titles: set[str],
    batch_support: dict[str, int],
    limit: int,
) -> list[str]:
    if page_type == "concept":
        candidates = _coerce_analysis_strings(analysis.get("concepts"))
    elif page_type == "entity":
        candidates = _coerce_analysis_strings(analysis.get("entities"))
    else:
        candidates = _coerce_analysis_strings(analysis.get("open_questions"))

    summary = str(analysis.get("summary") or "").casefold()
    title_text = str(analysis.get("title") or "").casefold()
    scored: list[tuple[float, str]] = []
    for candidate in candidates:
        canonical = resolver.canonical_title(candidate, page_type)
        word_count = len(canonical.split())
        lowered = canonical.casefold()
        if not canonical or lowered in GENERIC_KNOWLEDGE_TERMS:
            continue
        if page_type != "question" and word_count > 8:
            continue
        if page_type == "concept" and word_count < 2 and canonical not in existing_titles:
            continue
        if page_type == "entity" and word_count < 2 and canonical[:1].islower():
            continue
        if page_type == "entity" and "et al" in lowered:
            continue
        if page_type == "entity" and canonical.count(".") > 1:
            continue
        if page_type == "question" and (word_count < 4 or word_count > 18 or not canonical.endswith("?")):
            continue

        score = 0.0
        explicit_mentions = _extract_target_snippets(canonical, page_type, analysis)
        explicit_signal = bool(explicit_mentions)
        if " " in canonical:
            score += 1.0
        if canonical.casefold() in summary:
            score += 1.5
            explicit_signal = True
        if canonical.casefold() in title_text:
            score += 0.5
            explicit_signal = True
        for claim in analysis.get("key_claims", []) or []:
            if isinstance(claim, dict):
                text = str(claim.get("text") or "").casefold()
                labels = (
                    _coerce_analysis_strings(claim.get("concepts"))
                    if page_type == "concept"
                    else _coerce_analysis_strings(claim.get("entities"))
                )
                if any(label.casefold() == canonical.casefold() for label in labels):
                    score += 2.0
                    explicit_signal = True
                if canonical.casefold() in text:
                    score += 1.0
                    explicit_signal = True
            elif canonical.casefold() in str(claim).casefold():
                score += 1.0
                explicit_signal = True
        if canonical in existing_titles:
            score += 3.0
        existing_support = get_source_count_for_title(store, canonical, page_type)
        if existing_support:
            score += min(existing_support, 3) * 1.5
        overlap_support = batch_support.get(canonical, 0)
        if overlap_support > 1:
            score += min(overlap_support - 1, 3) * 2.0
        if explicit_mentions:
            score += min(len(explicit_mentions), 2) * 1.0
        if page_type == "question":
            if canonical.endswith("?"):
                score += 1.0
            if canonical.casefold().startswith(("how ", "when ", "what ", "why ")):
                score += 0.5
        if page_type == "entity" and any(char.isdigit() for char in canonical):
            score -= 0.5
        if page_type == "entity" and existing_support < 1 and overlap_support < 2 and not explicit_signal:
            continue
        if score > 0:
            scored.append((score, canonical))

    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1].lower()))
    ordered: list[str] = []
    for _score, canonical in scored:
        if canonical not in ordered:
            ordered.append(canonical)
        if len(ordered) >= limit:
            break
    return ordered


def _rewrite_wikilinks(content: str, resolver: TitleResolver) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        alias = (match.group(2) or "").strip()
        if not target or target.startswith("raw/"):
            return match.group(0)
        resolved = resolver.resolve_wikilink(target)
        if not resolved:
            return alias or target
        return f"[[{resolved}|{alias}]]" if alias else f"[[{resolved}]]"

    return re.sub(r"\[\[([^\]|#]+?)(?:\|([^\]]*?))?\]\]", lambda match: replace(match), content)


def _ensure_source_provenance_block(content: str, *, raw_source_path: str) -> str:
    if not raw_source_path or raw_source_path in content:
        return content
    artifact = f"![[{raw_source_path}]]" if raw_source_path.lower().endswith(".pdf") else f"[[{raw_source_path}]]"
    addition = f"\n\n> [!note] Raw Artifact\n> {artifact}\n"
    marker = "\n# "
    if marker in content:
        front, rest = content.split(marker, 1)
        rest_lines = rest.splitlines()
        heading = rest_lines[0]
        remainder = "\n".join(rest_lines[1:]).lstrip()
        merged = f"{front}{marker}{heading}{addition}"
        if remainder:
            merged += f"\n{remainder}"
        return merged
    return content.rstrip() + addition


def _extract_aliases_from_body(body: str) -> list[str]:
    aliases: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Aliases:"):
            continue
        aliases.extend(part.strip() for part in stripped.removeprefix("Aliases:").split(","))
    return [alias for alias in aliases if alias]


def _coerce_analysis_strings(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _plan_wiki_updates(
    compiler: Compiler,
    analysis: dict[str, Any],
    *,
    index_content: str,
    existing_pages: dict[str, str],
    evidence_context: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        return compiler.plan_wiki_updates(
            analysis,
            index_content,
            existing_pages,
            evidence_context=evidence_context,
        )
    except TypeError:
        return compiler.plan_wiki_updates(analysis, index_content, existing_pages)


def _build_evidence_context(store: Any, analysis: dict[str, Any]) -> dict[str, Any]:
    related_concepts = []
    for concept in analysis.get("concepts", []) or []:
        source_count = get_source_count_for_title(store, concept, "concept")
        related_concepts.append(
            {
                "name": concept,
                "source_count": source_count,
                "source_titles": get_source_titles_for_title(store, concept, "concept"),
            }
        )

    return {
        "analysis_warnings": analysis.get("analysis_warnings", []),
        "concepts_in_this_source": related_concepts,
        "concepts_needing_synthesis": [
            {
                "name": record.name,
                "source_count": len(record.source_ids),
                "wiki_page_path": record.wiki_page_path,
            }
            for record in get_concepts_needing_synthesis(store)[:12]
        ],
    }


def _gather_relevant_pages_via_connector(
    config: Config,
    analysis: dict[str, Any],
    store: Any,
) -> dict[str, str]:
    connector = ObsidianConnector(config.workspace_root)
    report = connector.inspect()
    if report.total_pages <= 4:
        return _gather_relevant_pages_by_filename(config, analysis)

    pages: dict[str, str] = {}
    for path in ("index.md", "overview.md"):
        content = read_wiki_page(config, path)
        if content:
            pages[path] = content

    query_terms: list[str] = []
    if analysis.get("title"):
        query_terms.append(str(analysis["title"]))
    if analysis.get("summary"):
        query_terms.append(str(analysis["summary"])[:160])
    query_terms.extend(str(item) for item in (analysis.get("concepts") or [])[:6])
    query_terms.extend(str(item) for item in (analysis.get("entities") or [])[:4])
    for claim in (analysis.get("key_claims") or [])[:2]:
        if isinstance(claim, dict):
            query_terms.append(str(claim.get("text", ""))[:120])
        else:
            query_terms.append(str(claim)[:120])

    candidate_titles: set[str] = set()
    for query_term in query_terms:
        if not query_term.strip():
            continue
        for hit in connector.search(query_term, limit=5):
            candidate_titles.add(hit.title)
            try:
                neighborhood = connector.get_neighborhood(hit.title)
            except (FileNotFoundError, ValueError):
                continue
            candidate_titles.update(neighborhood.backlinks)
            candidate_titles.update(neighborhood.outbound_pages)
            candidate_titles.update(neighborhood.supporting_source_pages)
            candidate_titles.update(neighborhood.related_pages)
            candidate_titles.update(neighborhood.cited_source_pages)

    for concept in analysis.get("concepts", []) or []:
        for source_title in get_source_titles_for_title(store, concept, "concept"):
            candidate_titles.add(source_title)

    for title in sorted(candidate_titles):
        try:
            page = connector.get_page(title)
        except (FileNotFoundError, ValueError):
            continue
        relative_path = page.relative_path.removeprefix("wiki/")
        content = read_wiki_page(config, relative_path)
        if content:
            pages[relative_path] = content

    return pages


def _gather_relevant_pages_by_filename(config: Config, analysis: dict[str, Any]) -> dict[str, str]:
    pages: dict[str, str] = {}
    overview = read_wiki_page(config, "overview.md")
    if overview:
        pages["overview.md"] = overview

    search_terms = [str(item).lower() for item in (analysis.get("concepts") or []) + (analysis.get("entities") or [])]
    for page_path in list_wiki_pages(config):
        if page_path in ("index.md", "overview.md", "log.md"):
            continue
        stem = Path(page_path).stem.replace("-", " ").lower()
        if any(term in stem or stem in term for term in search_terms):
            content = read_wiki_page(config, page_path)
            if content:
                pages[page_path] = content
    return pages


def _default_path_for_operation(operation: dict[str, Any]) -> str:
    title = str(operation.get("title", "Untitled")).strip() or "Untitled"
    page_type = str(operation.get("page_type", "source")).strip() or "source"
    folder = {
        "source": "sources",
        "concept": "concepts",
        "entity": "entities",
        "question": "questions",
        "dashboard": "dashboards",
        "output": "outputs",
        "comparison": "outputs",
    }.get(page_type, "outputs")
    return f"{folder}/{title}.md"


def _page_type_priority(page_type: str) -> int:
    order = {
        "source": 0,
        "concept": 1,
        "entity": 2,
        "question": 3,
        "comparison": 4,
        "output": 5,
        "dashboard": 6,
    }
    return order.get(str(page_type), 99)


def refresh_navigation_and_dashboards(
    config: Config,
    store: Any,
    *,
    db: EvidenceDatabase | None = None,
) -> list[str]:
    ensure_workspace_schema(config)
    pages_by_type = collect_pages_by_type(config)
    dashboard_paths = write_dashboards(config, _build_dashboard_pages(config, store, pages_by_type, db=db))
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    if db is not None:
        _sync_existing_pages_to_db(config, db)
    return dashboard_paths + ["index.md", "overview.md"]


def _build_dashboard_pages(
    config: Config,
    store: Any,
    pages_by_type: dict[str, list[dict[str, str]]],
    *,
    db: EvidenceDatabase | None = None,
) -> dict[str, str]:
    stable_concepts = []
    provisional_concepts = []
    low_coverage_pages = []
    for page in pages_by_type.get("concepts", []):
        source_count = _source_count_for_page(config, store, page["path"], page["title"], "concept")
        if source_count >= 2:
            stable_concepts.append((page["title"], source_count, page["summary"]))
        else:
            provisional_concepts.append((page["title"], source_count, page["summary"]))
            low_coverage_pages.append((page["title"], "concept", source_count, page["summary"]))
    for page_type, collection in (("entity", pages_by_type.get("entities", [])), ("question", pages_by_type.get("questions", []))):
        for page in collection:
            source_count = _source_count_for_page(config, store, page["path"], page["title"], page_type)
            if source_count <= 1:
                low_coverage_pages.append((page["title"], page_type, source_count, page["summary"]))

    source_rows = []
    if store.sources:
        for source in sorted(store.sources.values(), key=lambda item: item.source_title.lower()):
            concept_count = sum(1 for record in store.concepts.values() if source.source_id in record.source_ids)
            entity_count = sum(1 for record in store.entities.values() if source.source_id in record.source_ids)
            claim_count = sum(1 for claim in store.claims.values() if claim.source_id == source.source_id)
            source_rows.append(
                f"| [[{source.source_title}]] | `{source.raw_path}` | {concept_count} | {entity_count} | {claim_count} |"
            )
    else:
        for page in pages_by_type.get("sources", []):
            frontmatter, _body = _load_markdown_doc(read_wiki_page(config, page["path"]) or "")
            raw_links = _coerce_list(frontmatter.get("sources"))
            source_rows.append(
                f"| [[{page['title']}]] | `{', '.join(raw_links) or 'unknown'}` | ? | ? | ? |"
            )

    missing_note_rows = []
    for source in sorted(store.sources.values(), key=lambda item: item.source_title.lower()):
        if source.source_page_path:
            continue
        missing_note_rows.append(f"| {source.source_title} | `{source.raw_path}` |")

    candidate_merge_rows = []
    seen_merge_pairs: set[tuple[str, str]] = set()
    if db is not None:
        for candidate in _find_materialized_duplicate_pairs(config, store, db=db, page_type="concept", limit=12):
            pair = tuple(sorted((candidate["left_title"], candidate["right_title"])))
            if pair in seen_merge_pairs:
                continue
            seen_merge_pairs.add(pair)
            candidate_merge_rows.append(
                f"| [[{candidate['left_title']}]] | [[{candidate['right_title']}]] | {candidate['shared_sources']} |"
            )
    else:
        materialized_concepts = {page["title"] for page in pages_by_type.get("concepts", [])}
        for left, right, shared_sources in get_overlapping_concepts(store, limit=12):
            if left.name not in materialized_concepts or right.name not in materialized_concepts:
                continue
            pair = tuple(sorted((left.name, right.name)))
            if pair in seen_merge_pairs:
                continue
            seen_merge_pairs.add(pair)
            candidate_merge_rows.append(
                f"| [[{left.name}]] | [[{right.name}]] | {shared_sources} |"
            )

    dashboards = {
        "Needs Synthesis": _dashboard_page(
            title="Needs Synthesis",
            summary="Concepts that are still backed by only one source and should be upgraded.",
            body="\n".join(
                [
                    "# Needs Synthesis",
                    "",
                    "Concepts still backed by only one source stay provisional until the wiki accumulates more evidence.",
                    "",
                    "| Page | Source Count | Summary |",
                    "| --- | ---: | --- |",
                    *[
                        f"| [[{title}]] | {source_count} | {summary or 'No summary yet.'} |"
                        for title, source_count, summary in provisional_concepts
                    ],
                    "",
                ]
            )
            if provisional_concepts
            else _dashboard_page_body("Needs Synthesis", "No provisional concepts are pending synthesis right now."),
        ),
        "Low Coverage": _dashboard_page(
            title="Low Coverage",
            summary="Pages that are still thin, single-source, or otherwise weakly supported.",
            body="\n".join(
                [
                    "# Low Coverage",
                    "",
                    "| Page | Type | Source Count | Summary |",
                    "| --- | --- | ---: | --- |",
                    *[
                        f"| [[{title}]] | {page_type} | {source_count} | {summary or 'No summary yet.'} |"
                        for title, page_type, source_count, summary in low_coverage_pages
                    ],
                    "",
                ]
            )
            if low_coverage_pages
            else _dashboard_page_body("Low Coverage", "No low-coverage knowledge pages are currently flagged."),
        ),
        "Source Coverage": _dashboard_page(
            title="Source Coverage",
            summary="Coverage map of raw sources, concept usage, entity usage, and claim counts.",
            body="\n".join(
                [
                    "# Source Coverage",
                    "",
                    "| Source | Raw Path | Concepts | Entities | Claims |",
                    "| --- | --- | ---: | ---: | ---: |",
                    *source_rows,
                    "",
                ]
            )
            if source_rows
            else _dashboard_page_body("Source Coverage", "No sources have been ingested yet."),
        ),
        "Sources Missing Notes": _dashboard_page(
            title="Sources Missing Notes",
            summary="Raw sources that exist in the evidence layer but do not yet have maintained source pages.",
            body="\n".join(
                [
                    "# Sources Missing Notes",
                    "",
                    "| Source | Raw Path |",
                    "| --- | --- |",
                    *missing_note_rows,
                    "",
                ]
            )
            if missing_note_rows
            else _dashboard_page_body("Sources Missing Notes", "Every tracked source currently has a maintained source note."),
        ),
        "Candidate Merges": _dashboard_page(
            title="Candidate Merges",
            summary="Concept pairs that share substantial supporting source overlap and may warrant consolidation.",
            body="\n".join(
                [
                    "# Candidate Merges",
                    "",
                    "| Left | Right | Shared Sources |",
                    "| --- | --- | ---: |",
                    *candidate_merge_rows,
                    "",
                ]
            )
            if candidate_merge_rows
            else _dashboard_page_body("Candidate Merges", "No strong merge candidates are currently flagged."),
        ),
        "Recent Outputs": _dashboard_page(
            title="Recent Outputs",
            summary="Saved downstream outputs filed back into the wiki.",
            body="\n".join(
                [
                    "# Recent Outputs",
                    "",
                    *[
                        f"- [[{page['title']}]] — {page['summary'] or 'Saved output artifact.'}"
                        for page in pages_by_type.get("outputs", [])[:12]
                    ],
                    "",
                ]
            )
            if pages_by_type.get("outputs")
            else _dashboard_page_body("Recent Outputs", "No durable outputs have been filed back into the wiki yet."),
        ),
        "Map of Content": _dashboard_page(
            title="Map of Content",
            summary="High-level map of dashboards, sources, concepts, entities, questions, and outputs.",
            body="\n".join(
                [
                    "# Map of Content",
                    "",
                    "## Dashboards",
                    "",
                    "- [[Needs Synthesis]]",
                    "- [[Low Coverage]]",
                    "- [[Source Coverage]]",
                    "- [[Sources Missing Notes]]",
                    "- [[Candidate Merges]]",
                    "- [[Recent Outputs]]",
                    "",
                    "## Stable Concepts",
                    "",
                    *[f"- [[{title}]] — {summary or 'No summary yet.'}" for title, _count, summary in stable_concepts],
                    "",
                    "## Provisional Concepts",
                    "",
                    *[f"- [[{title}]] — {summary or 'No summary yet.'}" for title, _count, summary in provisional_concepts],
                    "",
                    "## Entities",
                    "",
                    *[f"- [[{page['title']}]] — {page['summary'] or 'No summary yet.'}" for page in pages_by_type.get("entities", [])],
                    "",
                    "## Questions",
                    "",
                    *[f"- [[{page['title']}]] — {page['summary'] or 'No summary yet.'}" for page in pages_by_type.get("questions", [])],
                    "",
                    "## Outputs",
                    "",
                    *[f"- [[{page['title']}]] — {page['summary'] or 'No summary yet.'}" for page in pages_by_type.get("outputs", [])],
                    "",
                ]
            ),
        ),
    }
    return dashboards


def _merge_duplicate_knowledge_pages(
    config: Config,
    store: Any,
    *,
    db: EvidenceDatabase,
) -> list[str]:
    candidates = _find_materialized_duplicate_pairs(config, store, db=db, page_type="concept", limit=8)
    if not candidates:
        return []

    rename_map: dict[str, str] = {}
    aliases_by_canonical: dict[str, list[str]] = {}
    consumed_titles: set[str] = set()
    entry_lookup = {
        (str(entry.get("page_type") or ""), str(entry.get("title") or "")): entry
        for entry in db.page_catalog()
    }
    for candidate in candidates:
        canonical = str(candidate["canonical_title"])
        duplicate = str(candidate["duplicate_title"])
        if canonical in consumed_titles or duplicate in consumed_titles:
            continue
        if (candidate["page_type"], canonical) not in entry_lookup or (candidate["page_type"], duplicate) not in entry_lookup:
            continue
        rename_map[duplicate] = canonical
        aliases_by_canonical.setdefault(canonical, []).append(duplicate)
        consumed_titles.update({canonical, duplicate})

    if not rename_map:
        return []

    touched_paths: list[str] = []
    for canonical, aliases in aliases_by_canonical.items():
        entry = entry_lookup.get(("concept", canonical))
        if entry is None:
            continue
        page_path = config.wiki_dir / str(entry["path"])
        if not page_path.exists():
            continue
        frontmatter, body = _load_markdown_doc(page_path.read_text())
        existing_aliases = _coerce_list(frontmatter.get("aliases"))
        frontmatter["aliases"] = list(dict.fromkeys([*existing_aliases, *aliases]))
        page_path.write_text(_render_markdown_doc(frontmatter, body))
        touched_paths.append(str(entry["path"]))

    touched_paths.extend(_rewrite_links_for_renamed_titles(config, rename_map))

    for duplicate, canonical in rename_map.items():
        entry = entry_lookup.get(("concept", duplicate))
        if entry is None:
            continue
        duplicate_path = config.wiki_dir / str(entry["path"])
        if duplicate_path.exists():
            duplicate_path.unlink()
            touched_paths.append(str(entry["path"]))
        console.print(f"  Consolidated duplicate concept [[{duplicate}]] into [[{canonical}]].", style="dim")

    _sync_existing_pages_to_db(config, db)
    return list(dict.fromkeys(touched_paths))


def _rewrite_links_for_renamed_titles(config: Config, rename_map: dict[str, str]) -> list[str]:
    if not rename_map:
        return []

    touched_paths: list[str] = []
    pattern = re.compile(r"\[\[([^\]|#]+?)(#[^\]|]+)?(?:\|([^\]]*?))?\]\]")

    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        anchor = match.group(2) or ""
        alias = (match.group(3) or "").strip()
        replacement = rename_map.get(target)
        if not replacement:
            return match.group(0)
        display = alias or target
        return f"[[{replacement}{anchor}|{display}]]"

    for relative_path in list_wiki_pages(config):
        full_path = config.wiki_dir / relative_path
        if not full_path.exists():
            continue
        original = full_path.read_text()
        rewritten = pattern.sub(replace, original)
        if rewritten == original:
            continue
        full_path.write_text(rewritten)
        touched_paths.append(relative_path)
    return touched_paths


def _find_materialized_duplicate_pairs(
    config: Config,
    store: Any,
    *,
    db: EvidenceDatabase,
    page_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in db.page_catalog():
        if str(entry.get("page_type") or "") != page_type:
            continue
        page_path = config.wiki_dir / str(entry.get("path") or "")
        if not page_path.exists():
            continue
        frontmatter, body = _load_markdown_doc(page_path.read_text())
        summary = str(entry.get("summary") or frontmatter.get("summary") or _summarize_body(body)).strip()
        source_ids = set(_coerce_list(frontmatter.get("source_ids")) or list(entry.get("source_ids") or []))
        if not source_ids:
            source_ids = set(get_source_ids_for_title(store, str(entry.get("title") or ""), page_type))
        entries.append(
            {
                "title": str(entry.get("title") or ""),
                "path": str(entry.get("path") or ""),
                "page_type": page_type,
                "status": str(entry.get("status") or ""),
                "summary": summary,
                "source_ids": source_ids,
                "aliases": _coerce_list(frontmatter.get("aliases")) + list(entry.get("aliases") or []),
            }
        )

    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            source_overlap = _source_overlap_ratio(set(left["source_ids"]), set(right["source_ids"]))
            if source_overlap < 0.5:
                continue
            summary_similarity = _text_similarity(str(left["summary"]), str(right["summary"]))
            left_claims = " ".join(_claim_text(item.text) for item in get_claims_for_concept(store, str(left["title"])))
            right_claims = " ".join(_claim_text(item.text) for item in get_claims_for_concept(store, str(right["title"])))
            claim_similarity = _text_similarity(left_claims, right_claims)
            if max(summary_similarity, claim_similarity) < 0.84:
                continue
            canonical_title, duplicate_title = _choose_canonical_duplicate_title(left, right, db=db)
            candidates.append(
                {
                    "page_type": page_type,
                    "left_title": str(left["title"]),
                    "right_title": str(right["title"]),
                    "canonical_title": canonical_title,
                    "duplicate_title": duplicate_title,
                    "shared_sources": len(set(left["source_ids"]) & set(right["source_ids"])),
                    "score": max(summary_similarity, claim_similarity),
                }
            )
    candidates.sort(
        key=lambda item: (
            -float(item["score"]),
            -int(item["shared_sources"]),
            str(item["canonical_title"]).lower(),
            str(item["duplicate_title"]).lower(),
        )
    )
    return candidates[:limit]


def _choose_canonical_duplicate_title(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    db: EvidenceDatabase,
) -> tuple[str, str]:
    left_score = _duplicate_title_anchor_score(left, db=db)
    right_score = _duplicate_title_anchor_score(right, db=db)
    if right_score > left_score:
        return str(right["title"]), str(left["title"])
    if left_score > right_score:
        return str(left["title"]), str(right["title"])

    left_source_count = len(set(left.get("source_ids") or []))
    right_source_count = len(set(right.get("source_ids") or []))
    if right_source_count > left_source_count:
        return str(right["title"]), str(left["title"])
    if left_source_count > right_source_count:
        return str(left["title"]), str(right["title"])

    left_words = len(str(left["title"]).split())
    right_words = len(str(right["title"]).split())
    if right_words > left_words:
        return str(right["title"]), str(left["title"])
    return str(left["title"]), str(right["title"])


def _duplicate_title_anchor_score(entry: dict[str, Any], *, db: EvidenceDatabase) -> float:
    title = str(entry.get("title") or "")
    analyses = db.get_source_analyses(list(entry.get("source_ids") or []))
    blobs: list[str] = [str(entry.get("summary") or "")]
    for analysis in analyses:
        blobs.append(str(analysis.get("title") or ""))
        blobs.append(str(analysis.get("summary") or ""))
        for claim in analysis.get("key_claims", []) or []:
            blobs.append(_claim_text(claim))
        blobs.extend(_extract_target_snippets(title, str(entry.get("page_type") or ""), analysis))
    score = _anchor_score_from_blobs(title, blobs)
    if str(entry.get("status") or "") == "stable":
        score += 1.0
    return score


def _rewrite_weak_entity_pages(
    config: Config,
    compiler: Compiler,
    store: Any,
    *,
    db: EvidenceDatabase,
) -> list[str]:
    catalog = db.page_catalog()
    resolver = TitleResolver.from_catalog(db, catalog)
    all_page_titles = sorted({entry["title"] for entry in catalog})
    valid_link_targets = set(all_page_titles)
    valid_link_targets.update(
        str(raw_file.relative_to(config.workspace_root)).replace("\\", "/")
        for raw_file in config.raw_dir.rglob("*")
        if raw_file.is_file()
    )

    touched_paths: list[str] = []
    for entry in catalog:
        if str(entry.get("page_type") or "") != "entity":
            continue
        title = str(entry.get("title") or "")
        relative_path = str(entry.get("path") or "")
        full_path = config.wiki_dir / relative_path
        if not full_path.exists():
            continue
        existing_content = full_path.read_text()
        frontmatter, _body = _load_markdown_doc(existing_content)
        source_ids = _supporting_source_ids_for_target(
            title,
            "entity",
            list(entry.get("source_ids") or []) or _coerce_list(frontmatter.get("source_ids")),
            store,
        )
        supporting_sources = _supporting_sources_for_target(title, "entity", source_ids, db)
        if not supporting_sources:
            continue
        source_analysis = _page_analysis_payload(title, "entity", supporting_sources)
        source_analysis["source_ids"] = source_ids
        if not _entity_page_needs_rewrite(frontmatter, source_analysis, title=title):
            continue

        operation = {
            "action": "update",
            "title": title,
            "page_type": "entity",
            "status": str(frontmatter.get("status") or _status_for_page("entity", len(source_ids))),
            "reason": "Rewrite weak entity page using entity-specific evidence from supporting sources.",
            "key_points": [str(source_analysis.get("summary") or "").strip()],
        }
        page_content = compiler.write_page(
            operation=operation,
            source_analysis=source_analysis,
            existing_content=None,
            related_page_titles=[page_title for page_title in all_page_titles if page_title != title],
            raw_source_path="",
        )
        page_content = _rewrite_wikilinks(page_content, resolver)
        issues = verify_page_content(
            page_type="entity",
            content=page_content,
            raw_source_path="",
            source_count=len(source_ids),
            expected_equations=len(source_analysis.get("equations", []) or []),
            expected_metrics=len(source_analysis.get("metrics", []) or []),
            valid_link_targets=valid_link_targets,
        )
        hard_failures = [issue for issue in issues if issue.severity == "high"]
        if hard_failures:
            messages = "; ".join(issue.message for issue in hard_failures[:3])
            raise ValueError(f"Verification failed for {relative_path}: {messages}")

        full_path.write_text(page_content)
        _normalize_written_page(
            config,
            store,
            relative_path=relative_path,
            title=title,
            page_type="entity",
            fallback_sources=[item["title"] for item in supporting_sources if str(item.get("title") or "").strip()],
            fallback_source_ids=source_ids,
            db=db,
        )
        touched_paths.append(relative_path)
        console.print(f"  Rewrote weak entity page [[{title}]] with target-specific evidence.", style="dim")

    return touched_paths


def _entity_page_needs_rewrite(frontmatter: dict[str, Any], source_analysis: dict[str, Any], *, title: str) -> bool:
    current_summary = str(frontmatter.get("summary") or "").strip()
    next_summary = str(source_analysis.get("summary") or "").strip()
    if not next_summary:
        return False
    if _text_matches_target(current_summary, title, page_type="entity"):
        return False
    return _text_matches_target(next_summary, title, page_type="entity")


def _dashboard_page(title: str, summary: str, body: str) -> str:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    return (
        "---\n"
        f"title: {title}\n"
        "type: dashboard\n"
        "status: stable\n"
        f"summary: {summary}\n"
        f"created: {now}\n"
        f"updated: {now}\n"
        "tags:\n"
        "  - dashboard\n"
        "cssclasses:\n"
        "  - dashboard\n"
        "  - stable\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _dashboard_page_body(title: str, message: str) -> str:
    return f"# {title}\n\n{message}\n"


def _source_count_for_page(
    config: Config,
    store: Any,
    relative_path: str,
    title: str,
    page_type: str,
) -> int:
    count = get_source_count_for_title(store, title, page_type)
    if count:
        return count
    frontmatter, _body = _load_markdown_doc(read_wiki_page(config, relative_path) or "")
    source_ids = _coerce_list(frontmatter.get("source_ids"))
    if source_ids:
        return len(source_ids)
    return len(_coerce_list(frontmatter.get("sources")))


def _apply_quality_gates(
    config: Config,
    store: Any,
    target_paths: list[str] | None = None,
    *,
    db: EvidenceDatabase | None = None,
) -> list[str]:
    touched_paths: list[str] = []
    candidates = target_paths or [
        path
        for path in list_wiki_pages(config)
        if path not in {"index.md", "overview.md", "log.md"}
    ]
    seen: set[str] = set()
    for relative_path in candidates:
        if relative_path in seen:
            continue
        seen.add(relative_path)
        full_path = config.wiki_dir / relative_path
        if not full_path.exists():
            continue
        frontmatter, body = _load_markdown_doc(full_path.read_text())
        title = str(frontmatter.get("title") or _title_from_body(body) or Path(relative_path).stem).strip()
        page_type = str(frontmatter.get("type") or _page_type_from_path(relative_path)).strip() or "output"
        normalized = _normalize_written_page(
            config,
            store,
            relative_path=relative_path,
            title=title,
            page_type=page_type,
            fallback_sources=_coerce_list(frontmatter.get("sources")),
            fallback_source_ids=_coerce_list(frontmatter.get("source_ids")),
            db=db,
        )
        if normalized:
            touched_paths.append(relative_path)
    return touched_paths


def _normalize_written_page(
    config: Config,
    store: Any,
    *,
    relative_path: str,
    title: str,
    page_type: str,
    fallback_sources: list[str] | None = None,
    fallback_source_ids: list[str] | None = None,
    db: EvidenceDatabase | None = None,
) -> bool:
    full_path = config.wiki_dir / relative_path
    if not full_path.exists():
        return False

    frontmatter, body = _load_markdown_doc(full_path.read_text())
    source_title_map = get_source_title_map(store)
    source_ids = get_source_ids_for_title(store, title, page_type)
    inferred_source_titles = _coerce_list(frontmatter.get("sources")) + list(fallback_sources or [])
    if not source_ids:
        source_ids = [
            source_title_map[source_title]
            for source_title in inferred_source_titles
            if source_title in source_title_map
        ]
    if not source_ids:
        source_ids = list(fallback_source_ids or [])
    source_ids = list(dict.fromkeys(source_ids))

    if page_type == "source" and not source_ids:
        source_ids = [
            source_id
            for source_id, source in store.sources.items()
            if source.source_title == title
        ]

    source_count = len(source_ids) or len(list(dict.fromkeys(inferred_source_titles)))
    status = _status_for_page(page_type, source_count)
    cssclasses = [page_type, status]
    if page_type in {"concept", "entity", "question"} and status != "stable":
        cssclasses.append("provisional")

    source_titles = get_source_titles_for_title(store, title, page_type)
    if page_type == "source" and source_ids:
        source_titles = [store.sources[source_ids[0]].raw_path]
    if not source_titles:
        source_titles = list(fallback_sources or [])

    frontmatter["title"] = title
    frontmatter["type"] = page_type
    frontmatter["status"] = status
    frontmatter["summary"] = str(frontmatter.get("summary") or _summarize_body(body))
    frontmatter["updated"] = datetime.now(UTC).replace(microsecond=0).isoformat()
    frontmatter["created"] = str(frontmatter.get("created") or frontmatter.get("created_at") or frontmatter["updated"])
    if source_titles:
        frontmatter["sources"] = source_titles
    else:
        frontmatter.pop("sources", None)
    if source_ids:
        frontmatter["source_ids"] = source_ids
    else:
        frontmatter.pop("source_ids", None)
    if page_type in {"concept", "entity", "question", "comparison"}:
        citations = [
            {
                "source_id": source_id,
                "source_title": store.sources[source_id].source_title,
            }
            for source_id in source_ids
            if source_id in store.sources
        ]
        if not citations:
            citations = [
                {"source_title": source_title}
                for source_title in list(dict.fromkeys(inferred_source_titles))
                if source_title
            ]
        if citations:
            frontmatter["citations"] = citations
        else:
            frontmatter.pop("citations", None)
    frontmatter["cssclasses"] = list(dict.fromkeys(cssclasses))
    if page_type in {"concept", "entity", "question"}:
        frontmatter["source_count"] = source_count
        frontmatter["claim_count"] = _count_claim_like_items(body)

    rendered_body = body.strip()
    rendered_body = _rewrite_support_callout(rendered_body, page_type=page_type, status=status, source_titles=source_titles)
    rendered_body = _remove_self_links(rendered_body, title=title)
    if not rendered_body.startswith("# "):
        rendered_body = f"# {title}\n\n{rendered_body}"

    full_path.write_text(_render_markdown_doc(frontmatter, rendered_body))
    sync_page_reference(
        store,
        title=title,
        page_type=page_type,
        relative_path=relative_path,
        source_titles=source_titles,
    )
    if db is not None:
        aliases = derive_aliases(title)
        aliases.extend(_coerce_list(frontmatter.get("aliases")))
        db.sync_page(
            path=relative_path,
            title=title,
            page_type=page_type,
            status=status,
            summary=str(frontmatter.get("summary") or ""),
            source_ids=source_ids,
            aliases=list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
        )
    return True


def _load_markdown_doc(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        frontmatter_text, body = text[4:].split("\n---\n", 1)
        try:
            return yaml.safe_load(frontmatter_text) or {}, body.strip()
        except yaml.YAMLError:
            return {}, text.strip()
    return {}, text.strip()


def _render_markdown_doc(frontmatter: dict[str, Any], body: str) -> str:
    frontmatter_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{frontmatter_text}\n---\n\n{body.rstrip()}\n"


def _summarize_body(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<!-- compile:") or stripped == "<!-- /compile:section -->":
            continue
        if stripped.startswith(("- ", "* ")):
            stripped = stripped[2:].strip()
        return stripped[:160]
    return ""


def _rewrite_support_callout(body: str, *, page_type: str, status: str, source_titles: list[str]) -> str:
    if page_type not in {"concept", "entity", "question"}:
        return body
    cleanup_pattern = (
        r"(?:> \[!note\] Supporting Sources\n> .*?(?=\n## |\Z))|"
        r"(?:> \[!warning\] Provisional(?: Synthesis)?\n> .*?(?=\n## |\Z))"
    )
    cleaned = re.sub(cleanup_pattern, "", body, flags=re.DOTALL).strip()
    if not source_titles:
        return cleaned
    refs = ", ".join(f"[[{title}]]" for title in source_titles[:6])
    if status == "stable":
        callout = f"> [!note] Supporting Sources\n> {refs}"
    else:
        label = "source" if len(source_titles) == 1 else "sources"
        callout = f"> [!warning] Provisional Synthesis\n> This page is currently backed by {len(source_titles)} {label}: {refs}."
    if "\n## " in cleaned:
        head, tail = cleaned.split("\n## ", 1)
        merged = f"{head.rstrip()}\n\n{callout}\n\n## {tail.lstrip()}"
        return merged.strip()
    return f"{cleaned}\n\n{callout}".strip()


def _remove_self_links(body: str, *, title: str) -> str:
    pattern = re.compile(r"\[\[([^\]|#]+?)(?:\|([^\]]*?))?\]\]")

    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        alias = (match.group(2) or "").strip()
        if normalize_alias_key(target) != normalize_alias_key(title):
            return match.group(0)
        return alias or target

    return pattern.sub(replace, body)


def _count_claim_like_items(body: str) -> int:
    count = 0
    in_relevant_section = False
    for line in body.splitlines():
        stripped = line.strip()
        section_match = re.match(r"<!-- compile:section id=([a-z0-9_:-]+) -->", stripped)
        if section_match:
            section_id = section_match.group(1)
            in_relevant_section = section_id in {
                "claims_by_source",
                "what_sources_say",
                "evidence",
                "current_evidence",
                "claims",
                "arguments",
            }
            continue
        if stripped == "<!-- /compile:section -->":
            in_relevant_section = False
            continue
        if not in_relevant_section:
            continue
        if stripped.startswith(("- ", "* ", "> - ")):
            count += 1
    return count


def _title_from_body(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _page_type_from_path(relative_path: str) -> str:
    if relative_path.startswith("sources/"):
        return "source"
    if relative_path.startswith("concepts/"):
        return "concept"
    if relative_path.startswith("entities/"):
        return "entity"
    if relative_path.startswith("questions/"):
        return "question"
    if relative_path.startswith("dashboards/"):
        return "dashboard"
    return "output"


def _status_for_page(page_type: str, source_count: int) -> str:
    if page_type in {"source", "dashboard", "output", "comparison", "index", "overview", "log"}:
        return "stable"
    if source_count <= 1:
        return "seed"
    return "stable"


def _coerce_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _all_page_titles(config: Config) -> list[str]:
    titles: list[str] = []
    for page_path in list_wiki_pages(config):
        content = read_wiki_page(config, page_path)
        if not content:
            continue
        frontmatter, body = _load_markdown_doc(content)
        title = str(frontmatter.get("title") or _title_from_body(body)).strip()
        if title:
            titles.append(title)
    return titles


def _check_wikilinks(
    config: Config,
    *,
    resolver: TitleResolver | None = None,
) -> list[tuple[str, str]]:
    import re

    known_titles: set[str] = set()
    for page_path in list_wiki_pages(config):
        known_titles.add(Path(page_path).stem.replace("-", " ").lower())
        content = read_wiki_page(config, page_path)
        if content:
            frontmatter, body = _load_markdown_doc(content)
            title = str(frontmatter.get("title") or _title_from_body(body)).strip()
            if title:
                known_titles.add(title.lower())

    for raw_file in config.raw_dir.rglob("*"):
        if raw_file.is_file():
            relative = str(raw_file.relative_to(config.workspace_root)).replace("\\", "/")
            known_titles.add(relative.lower())
            known_titles.add(raw_file.stem.replace("-", " ").lower())

    broken: list[tuple[str, str]] = []
    for page_path in list_wiki_pages(config):
        content = read_wiki_page(config, page_path)
        if not content:
            continue
        links = re.findall(r"\[\[([^\]|#]+?)(?:\|[^\]]*?)?\]\]", content)
        for link in links:
            link_clean = link.strip()
            if not link_clean:
                continue
            if resolver is not None and resolver.resolve_wikilink(link_clean):
                continue
            if link_clean.lower() not in known_titles:
                broken.append((link_clean, page_path))
    return broken
