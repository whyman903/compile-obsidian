from __future__ import annotations

from compile.page_ir import PageDraft, PagePatch, PageSection, SectionPatch, apply_page_patch, parse_managed_page, render_page_draft


def test_render_page_draft_inserts_managed_sections_and_pdf_provenance() -> None:
    draft = PageDraft(
        title="Planner-Executor Loops",
        page_type="source",
        status="stable",
        summary="Compressed source note.",
        tags=["debugging"],
        sources=["raw/planner-executor-loops.pdf"],
        cssclasses=["source", "stable"],
        sections=[
            PageSection("core_contribution", "Core Contribution", "- Separates diagnosis from action."),
            PageSection("claims", "Claims", "| Claim | Evidence |\n| --- | --- |\n| Reliability improves | Explicit in source |"),
        ],
    )

    text = render_page_draft(draft, raw_source_path="raw/planner-executor-loops.pdf")

    assert '<div class="compile-lede">Compressed source note.</div>' in text
    assert "> [!note] Raw Artifact" in text
    assert "<!-- compile:section id=core_contribution -->" in text
    assert "<!-- compile:section id=claims -->" in text
    assert "<!-- compile:section id=provenance -->" in text
    assert "![[raw/planner-executor-loops.pdf]]" in text


def test_apply_page_patch_replaces_only_targeted_section() -> None:
    existing = render_page_draft(
        PageDraft(
            title="Tool-First Architecture",
            page_type="concept",
            status="seed",
            summary="Initial concept note.",
            tags=["debugging"],
            sources=["Source A"],
            cssclasses=["concept", "seed", "provisional"],
            sections=[
                PageSection("definition", "Definition", "Original definition."),
                PageSection("claims_by_source", "Claims by Source", "- Source A: initial claim"),
                PageSection("tensions", "Tensions", "- No tensions recorded."),
            ],
        )
    )

    patched = apply_page_patch(
        existing,
        PagePatch(
            frontmatter_updates={"summary": "Updated concept note.", "sources": ["Source A", "Source B"]},
            section_patches=[
                SectionPatch(
                    section_id="claims_by_source",
                    mode="replace",
                    heading="Claims by Source",
                    body="- Source A: initial claim\n- Source B: new evidence",
                ),
                SectionPatch(
                    section_id="agreements",
                    mode="append",
                    heading="Agreements",
                    body="- Both sources favor tool execution before explanation.",
                    after_section_id="claims_by_source",
                ),
            ],
        ),
    )

    _frontmatter, _title, sections = parse_managed_page(patched)
    section_map = {section.section_id: section for section in sections}

    assert section_map["definition"].body == "Original definition."
    assert "Source B: new evidence" in section_map["claims_by_source"].body
    assert "Both sources favor tool execution" in section_map["agreements"].body
    assert "No tensions recorded." in section_map["tensions"].body
    assert "Updated concept note." in patched
    assert "> [!warning] Provisional Synthesis" in patched
