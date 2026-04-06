from __future__ import annotations

from pathlib import Path

from compile.evidence import (
    extract_asset_paths,
    get_claims_for_concept,
    get_concepts_needing_synthesis,
    get_overlapping_concepts,
    load_evidence,
    merge_source_evidence,
    save_evidence,
    source_id_for_path,
)


def test_merge_source_evidence_tracks_claims_and_multi_source_concepts(tmp_path: Path) -> None:
    workspace_root = tmp_path
    raw_a = workspace_root / "raw" / "source-a.md"
    raw_b = workspace_root / "raw" / "source-b.md"
    raw_a.parent.mkdir(parents=True, exist_ok=True)
    raw_a.write_text("# Source A\n\nPlanner executor loops improve recovery.")
    raw_b.write_text("# Source B\n\nPlanner executor loops improve reliability.")

    store = load_evidence(workspace_root / ".compile" / "evidence.json")
    merge_source_evidence(
        store,
        {
            "title": "Source A",
            "_source_text": raw_a.read_text(),
            "concepts": ["Planner-Executor Loops"],
            "entities": ["Execution Agent"],
            "key_claims": [
                {
                    "text": "Planner executor loops improve recovery.",
                    "confidence": 0.8,
                    "concepts": ["Planner-Executor Loops"],
                    "entities": ["Execution Agent"],
                }
            ],
        },
        source_id=source_id_for_path(raw_a, workspace_root),
        source_title="Source A",
        raw_path=raw_a,
        workspace_root=workspace_root,
    )
    merge_source_evidence(
        store,
        {
            "title": "Source B",
            "_source_text": raw_b.read_text(),
            "concepts": ["Planner-Executor Loops"],
            "entities": [],
            "key_claims": [
                {
                    "text": "Planner executor loops improve reliability.",
                    "confidence": 0.7,
                    "concepts": ["Planner-Executor Loops"],
                    "entities": [],
                }
            ],
        },
        source_id=source_id_for_path(raw_b, workspace_root),
        source_title="Source B",
        raw_path=raw_b,
        workspace_root=workspace_root,
    )

    evidence_path = workspace_root / ".compile" / "evidence.json"
    save_evidence(evidence_path, store)
    reloaded = load_evidence(evidence_path)

    concepts = get_concepts_needing_synthesis(reloaded)
    assert len(concepts) == 1
    assert concepts[0].name == "Planner-Executor Loops"
    assert len(concepts[0].source_ids) == 2

    claims = get_claims_for_concept(reloaded, "Planner-Executor Loops")
    assert len(claims) == 2
    assert claims[0].source_title == "Source A"
    assert claims[1].source_title == "Source B"


def test_extract_asset_paths_finds_local_markdown_and_html_images(tmp_path: Path) -> None:
    workspace_root = tmp_path
    assets_dir = workspace_root / "raw" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "chart.png").write_bytes(b"png-bytes")
    (assets_dir / "diagram.webp").write_bytes(b"webp-bytes")

    markdown = workspace_root / "raw" / "note.md"
    markdown.write_text("![Chart](assets/chart.png)\n\n<img src=\"assets/diagram.webp\" />")

    asset_paths = extract_asset_paths(markdown, workspace_root)
    assert asset_paths == ["raw/assets/chart.png", "raw/assets/diagram.webp"]


def test_get_overlapping_concepts_flags_same_source_near_duplicates(tmp_path: Path) -> None:
    workspace_root = tmp_path
    raw_a = workspace_root / "raw" / "source-a.md"
    raw_a.parent.mkdir(parents=True, exist_ok=True)
    raw_a.write_text("# Source A\n\nTool-first agents reproduce bugs concretely.")

    store = load_evidence(workspace_root / ".compile" / "evidence.json")
    source_id = source_id_for_path(raw_a, workspace_root)
    merge_source_evidence(
        store,
        {
            "title": "Source A",
            "_source_text": raw_a.read_text(),
            "concepts": ["Bug Reproduction", "Concrete Reproduction"],
            "entities": [],
            "key_claims": [
                {
                    "text": "Tool-first agents prioritize concrete bug reproduction.",
                    "confidence": 0.9,
                    "concepts": ["Bug Reproduction", "Concrete Reproduction"],
                    "entities": [],
                }
            ],
        },
        source_id=source_id,
        source_title="Source A",
        raw_path=raw_a,
        workspace_root=workspace_root,
    )

    overlaps = get_overlapping_concepts(store, limit=5)

    assert overlaps
    pair = {(overlaps[0][0].name, overlaps[0][1].name), (overlaps[0][1].name, overlaps[0][0].name)}
    assert ("Bug Reproduction", "Concrete Reproduction") in pair
