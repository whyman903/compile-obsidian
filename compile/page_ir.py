from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path
import re
from typing import Any

import yaml


SECTION_START_RE = re.compile(
    r"^## (?P<heading>[^\n]+)\n<!-- compile:section id=(?P<section_id>[a-z0-9_:-]+) -->\n(?P<body>.*?)(?:\n)?<!-- /compile:section -->\n?",
    re.MULTILINE | re.DOTALL,
)


@dataclass
class PageSection:
    section_id: str
    heading: str
    body: str


@dataclass
class PageDraft:
    title: str
    page_type: str
    status: str
    summary: str
    tags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    cssclasses: list[str] = field(default_factory=list)
    sections: list[PageSection] = field(default_factory=list)


@dataclass
class SectionPatch:
    section_id: str
    mode: str
    heading: str | None = None
    body: str | None = None
    after_section_id: str | None = None


@dataclass
class PagePatch:
    frontmatter_updates: dict[str, Any] = field(default_factory=dict)
    section_patches: list[SectionPatch] = field(default_factory=list)


def has_managed_sections(text: str) -> bool:
    return bool(SECTION_START_RE.search(text))


def coerce_page_artifact(payload: dict[str, Any], *, fallback_title: str, fallback_page_type: str, fallback_status: str) -> PageDraft | PagePatch:
    if "section_patches" in payload or "frontmatter_updates" in payload:
        return PagePatch(
            frontmatter_updates=dict(payload.get("frontmatter_updates") or {}),
            section_patches=[
                SectionPatch(
                    section_id=str(item.get("section_id", "")).strip(),
                    mode=str(item.get("mode", "replace")).strip() or "replace",
                    heading=_optional_string(item.get("heading")),
                    body=_optional_string(item.get("body")),
                    after_section_id=_optional_string(item.get("after_section_id")),
                )
                for item in payload.get("section_patches", []) or []
                if str(item.get("section_id", "")).strip()
            ],
        )

    raw_sections = payload.get("sections", []) or []
    sections: list[PageSection] = []
    for index, item in enumerate(raw_sections, start=1):
        parsed = _coerce_section(item, index=index)
        if parsed is not None:
            sections.append(parsed)
    return PageDraft(
        title=str(payload.get("title") or fallback_title).strip() or fallback_title,
        page_type=str(payload.get("page_type") or payload.get("type") or fallback_page_type).strip() or fallback_page_type,
        status=str(payload.get("status") or fallback_status).strip() or fallback_status,
        summary=str(payload.get("summary", "")).strip(),
        tags=_coerce_list(payload.get("tags")),
        sources=_coerce_list(payload.get("sources")),
        source_ids=_coerce_list(payload.get("source_ids")),
        cssclasses=_coerce_list(payload.get("cssclasses")),
        sections=sections,
    )


def render_page_draft(
    draft: PageDraft,
    *,
    existing_content: str | None = None,
    raw_source_path: str = "",
) -> str:
    existing_frontmatter, _existing_body = _load_markdown_doc(existing_content or "")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    created = str(existing_frontmatter.get("created") or existing_frontmatter.get("created_at") or now)

    frontmatter: dict[str, Any] = {}
    frontmatter["title"] = draft.title
    frontmatter["type"] = draft.page_type
    frontmatter["status"] = draft.status
    if draft.summary:
        frontmatter["summary"] = draft.summary
    if draft.sources:
        frontmatter["sources"] = list(dict.fromkeys(draft.sources))
    if draft.source_ids:
        frontmatter["source_ids"] = list(dict.fromkeys(draft.source_ids))
    frontmatter["created"] = created
    frontmatter["updated"] = now
    # Ensure tags is always a list in frontmatter for Dataview TABLE queries
    frontmatter["tags"] = list(dict.fromkeys(draft.tags)) if draft.tags else []

    # Dataview-friendly fields per page type
    if draft.page_type in {"concept", "entity", "question"}:
        frontmatter["source_count"] = len(draft.sources)
        claim_count = sum(
            1
            for section in draft.sections
            if "claim" in section.section_id.lower() or "claim" in section.heading.lower()
        )
        frontmatter["claim_count"] = claim_count
    if draft.page_type == "source":
        body_text = "\n".join(section.body for section in draft.sections)
        frontmatter["word_count"] = len(body_text.split())
        # Derive source_type from the raw_source_path if available
        if raw_source_path:
            suffix = Path(raw_source_path).suffix.lower().lstrip(".")
            frontmatter["source_type"] = suffix if suffix else "unknown"

    cssclasses = list(dict.fromkeys(draft.cssclasses or [draft.page_type, draft.status]))
    if draft.page_type in {"concept", "entity", "question"} and draft.status != "stable" and "provisional" not in cssclasses:
        cssclasses.append("provisional")
    if cssclasses:
        frontmatter["cssclasses"] = cssclasses

    sections = list(draft.sections)
    if draft.page_type == "source" and raw_source_path and not any(section.section_id == "provenance" for section in sections):
        sections.append(_provenance_section(raw_source_path))

    body_parts = [f"# {draft.title}", ""]
    lead = _render_lede(draft.summary)
    if lead:
        body_parts.extend([lead, ""])
    context_box = _render_context_box(draft, raw_source_path)
    if context_box:
        body_parts.extend([context_box, ""])
    for section in sections:
        body_parts.extend(
            [
                f"## {section.heading}",
                f"<!-- compile:section id={section.section_id} -->",
                section.body.strip(),
                "<!-- /compile:section -->",
                "",
            ]
        )

    frontmatter_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{frontmatter_text}\n---\n\n" + "\n".join(body_parts).rstrip() + "\n"


def apply_page_patch(
    existing_content: str,
    patch: PagePatch,
    *,
    raw_source_path: str = "",
) -> str:
    frontmatter, title, sections = parse_managed_page(existing_content)
    section_map: dict[str, PageSection] = {section.section_id: section for section in sections}
    section_order = [section.section_id for section in sections]

    for section_patch in patch.section_patches:
        mode = section_patch.mode
        if mode == "delete":
            if section_patch.section_id in section_map:
                section_map.pop(section_patch.section_id, None)
                section_order = [item for item in section_order if item != section_patch.section_id]
            continue

        heading = section_patch.heading or section_map.get(section_patch.section_id, PageSection(section_patch.section_id, section_patch.section_id.replace("_", " ").title(), "")).heading
        body = (section_patch.body or "").strip()
        section_map[section_patch.section_id] = PageSection(section_patch.section_id, heading, body)
        if section_patch.section_id not in section_order:
            if section_patch.after_section_id and section_patch.after_section_id in section_order:
                insert_at = section_order.index(section_patch.after_section_id) + 1
                section_order.insert(insert_at, section_patch.section_id)
            else:
                section_order.append(section_patch.section_id)

    updated_frontmatter = dict(frontmatter)
    for key, value in patch.frontmatter_updates.items():
        if value in (None, "", [], {}):
            updated_frontmatter.pop(key, None)
        else:
            updated_frontmatter[key] = value

    page_type = str(updated_frontmatter.get("type", "output")).strip() or "output"
    status = str(updated_frontmatter.get("status", "stable")).strip() or "stable"
    draft = PageDraft(
        title=str(updated_frontmatter.get("title") or title).strip() or title,
        page_type=page_type,
        status=status,
        summary=str(updated_frontmatter.get("summary", "")).strip(),
        tags=_coerce_list(updated_frontmatter.get("tags")),
        sources=_coerce_list(updated_frontmatter.get("sources")),
        source_ids=_coerce_list(updated_frontmatter.get("source_ids")),
        cssclasses=_coerce_list(updated_frontmatter.get("cssclasses")),
        sections=[section_map[section_id] for section_id in section_order if section_id in section_map],
    )
    return render_page_draft(draft, existing_content=existing_content, raw_source_path=raw_source_path)


def parse_managed_page(text: str) -> tuple[dict[str, Any], str, list[PageSection]]:
    frontmatter, body = _load_markdown_doc(text)
    title = str(frontmatter.get("title") or _title_from_body(body) or "Untitled").strip() or "Untitled"
    sections = [
        PageSection(
            section_id=match.group("section_id"),
            heading=match.group("heading").strip(),
            body=match.group("body").strip(),
        )
        for match in SECTION_START_RE.finditer(body)
    ]
    return frontmatter, title, sections


def _provenance_section(raw_source_path: str) -> PageSection:
    suffix = Path(raw_source_path).suffix.lower()
    if suffix == ".pdf":
        body = f"- Source file: ![[{raw_source_path}]]\n- Source type: PDF document"
    else:
        source_type = suffix.lstrip(".").upper() or "UNKNOWN"
        body = f"- Source file: [[{raw_source_path}]]\n- Source type: {source_type} document"
    return PageSection(section_id="provenance", heading="Provenance", body=body)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _render_lede(summary: str) -> str:
    summary = summary.strip()
    if not summary:
        return ""
    return f'<div class="compile-lede">{escape(summary)}</div>'


def _render_context_box(draft: PageDraft, raw_source_path: str) -> str:
    if draft.page_type == "source" and raw_source_path:
        artifact = _render_source_locator(raw_source_path)
        return "\n".join(
            [
                "> [!note] Raw Artifact",
                f"> {artifact}",
            ]
        )
    if draft.page_type in {"concept", "entity", "question"} and draft.sources:
        refs = ", ".join(_render_source_locator(item) for item in draft.sources[:6])
        if draft.status == "stable":
            return "\n".join(
                [
                    "> [!note] Supporting Sources",
                    f"> {refs}",
                ]
            )
        source_count = len(draft.sources)
        label = "source" if source_count == 1 else "sources"
        return "\n".join(
            [
                "> [!warning] Provisional Synthesis",
                f"> This page is currently backed by {source_count} {label}: {refs}.",
            ]
        )
    return ""


def _render_source_locator(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text.startswith("raw/"):
        suffix = Path(text).suffix.lower()
        return f"![[{text}]]" if suffix == ".pdf" else f"[[{text}]]"
    if text.startswith("[[") and text.endswith("]]"):
        return text
    return f"[[{text}]]"


def _coerce_section(value: Any, *, index: int) -> PageSection | None:
    if isinstance(value, dict):
        section_id = str(value.get("id") or value.get("section_id") or "").strip()
        heading = str(value.get("heading", "")).strip()
        body = str(value.get("body", "")).strip()
        if section_id and heading:
            return PageSection(section_id=section_id, heading=heading, body=body)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        first = lines[0]
        if " | " in first:
            left, right = first.split(" | ", 1)
            section_id = _slug_section_id(left) or f"section_{index}"
            heading = right.strip() or left.strip().title()
            body = "\n".join(lines[1:]).strip() or "-"
            return PageSection(section_id=section_id, heading=heading, body=body)
        heading = first.lstrip("# ").strip()[:80] or f"Section {index}"
        section_id = _slug_section_id(heading) or f"section_{index}"
        body = "\n".join(lines[1:]).strip() or "-"
        return PageSection(section_id=section_id, heading=heading, body=body)
    return None


def _coerce_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _slug_section_id(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")


def _load_markdown_doc(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        frontmatter_text, body = text[4:].split("\n---\n", 1)
        try:
            return yaml.safe_load(frontmatter_text) or {}, body.strip()
        except yaml.YAMLError:
            return {}, text.strip()
    return {}, text.strip()


def _title_from_body(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""
