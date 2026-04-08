from __future__ import annotations

from dataclasses import dataclass

from compile.obsidian import ObsidianConnector, SearchHit
from compile.text import ExtractedSource, normalize_text

_EXCLUDED_PAGE_TYPES = {"source", "index", "overview", "log"}
_STRONG_REASONS = {"exact-title", "exact-alias", "title-match", "alias-match"}
DEFAULT_SOURCE_SYNOPSIS = "Minimal source content; no substantive summary available."


@dataclass(frozen=True)
class IngestArtifact:
    title: str
    page_summary: str
    synopsis: str
    key_sections: list[str]
    related_pages: list[SearchHit]
    integration_notes: list[str]
    raw_relative: str
    metadata_only: bool


def build_ingest_artifact(
    *,
    raw_relative: str,
    extracted: ExtractedSource,
    connector: ObsidianConnector,
    title: str | None = None,
) -> IngestArtifact:
    effective_title = title or extracted.title
    synopsis = _build_synopsis(extracted)
    if extracted.metadata_only:
        related_pages: list[SearchHit] = []
    else:
        related_pages = _find_related_pages(
            connector,
            title=effective_title,
            headings=list(extracted.headings),
            synopsis=synopsis,
        )
    return IngestArtifact(
        title=effective_title,
        page_summary=_frontmatter_summary(synopsis),
        synopsis=synopsis,
        key_sections=list(extracted.headings[:6]) if not extracted.metadata_only else [],
        related_pages=related_pages,
        integration_notes=_integration_notes(related_pages),
        raw_relative=raw_relative,
        metadata_only=extracted.metadata_only,
    )


def _find_related_pages(
    connector: ObsidianConnector,
    *,
    title: str,
    headings: list[str],
    synopsis: str,
    limit: int = 5,
) -> list[SearchHit]:
    """Search for related wiki pages using title, headings, and synopsis."""
    queries: list[str] = []
    if title.strip():
        queries.append(title.strip())
    for heading in headings[:3]:
        heading = heading.strip()
        if heading and heading.lower() != title.strip().lower():
            queries.append(heading)
    if synopsis.strip():
        queries.append(synopsis.strip())

    seen: dict[str, SearchHit] = {}
    for query in queries:
        for hit in connector.search(query, limit=limit * 2):
            if hit.page_type in _EXCLUDED_PAGE_TYPES:
                continue
            existing = seen.get(hit.relative_path)
            if existing is None:
                seen[hit.relative_path] = SearchHit(
                    title=hit.title,
                    relative_path=hit.relative_path,
                    page_type=hit.page_type,
                    summary=hit.summary,
                    score=hit.score,
                    reasons=list(hit.reasons),
                    snippet=hit.snippet,
                )
            else:
                existing.score += hit.score
                existing.reasons = sorted(set(existing.reasons) | set(hit.reasons))

    results = sorted(seen.values(), key=lambda h: (-h.score, h.title.lower()))
    return results[:limit]


def render_source_body(artifact: IngestArtifact) -> str:
    lines = [
        "## Synopsis",
        "",
        artifact.synopsis,
    ]

    if artifact.key_sections:
        lines.extend([
            "",
            "## Key Sections",
            "",
            *[f"- {section}" for section in artifact.key_sections],
        ])

    if artifact.related_pages:
        lines.extend([
            "",
            "## Likely Related Pages",
            "",
        ])
        for page in artifact.related_pages:
            description = _truncate_sentence(page.summary or page.snippet, 180)
            bullet = f"- [[{page.title}]]"
            if description:
                bullet += f" — {description}"
            reason_text = _format_reasons(page.reasons)
            if reason_text:
                bullet += f" Reason: {reason_text}."
            lines.append(bullet)

    if artifact.integration_notes:
        lines.extend([
            "",
            "## Integration Notes",
            "",
            *[f"- {note}" for note in artifact.integration_notes],
        ])

    if artifact.metadata_only:
        lines.extend([
            "",
            "## Note",
            "",
            "This is a registration shell. The source content could not be extracted automatically. "
            "Read the raw file directly and replace this note with a proper synopsis, key claims, and limitations.",
        ])

    lines.extend([
        "",
        "## Provenance",
        "",
        f"- Source file: ![[{artifact.raw_relative}]]",
    ])
    return "\n".join(lines)


def _build_synopsis(extracted: ExtractedSource) -> str:
    if extracted.metadata_only:
        return _truncate_sentence(extracted.normalized_text, 600) or DEFAULT_SOURCE_SYNOPSIS

    candidates = [
        paragraph
        for paragraph in extracted.paragraphs
        if _is_substantive_paragraph(paragraph)
    ] or extracted.paragraphs
    if not candidates:
        return DEFAULT_SOURCE_SYNOPSIS

    selected: list[str] = []
    total_length = 0
    for paragraph in candidates:
        if len(selected) >= 2:
            break
        paragraph_length = len(paragraph)
        separator = 2 if selected else 0
        if selected and total_length + separator + paragraph_length > 600:
            break
        selected.append(paragraph)
        total_length += separator + paragraph_length

    synopsis = "\n\n".join(selected or candidates[:1])
    return _truncate_sentence(synopsis, 600) or DEFAULT_SOURCE_SYNOPSIS


def _frontmatter_summary(synopsis: str) -> str:
    return _truncate_sentence(normalize_text(synopsis), 220) or DEFAULT_SOURCE_SYNOPSIS


def _integration_notes(related_pages: list[SearchHit]) -> list[str]:
    strong_matches = [
        page for page in related_pages
        if page.score >= 100 or any(reason in _STRONG_REASONS for reason in page.reasons)
    ]
    medium_matches = [page for page in related_pages if page.score >= 60]

    if len(strong_matches) >= 2:
        first, second = strong_matches[:2]
        return [
            f"This source likely overlaps with [[{first.title}]] and [[{second.title}]]; "
            "review those pages before creating new pages."
        ]
    if len(strong_matches) == 1:
        return [f"Review [[{strong_matches[0].title}]] before creating a new article on this topic."]
    if len(medium_matches) >= 2:
        first, second = medium_matches[:2]
        return [
            f"This source may overlap with [[{first.title}]] and [[{second.title}]]; "
            "review them before creating new pages."
        ]
    return []


def _format_reasons(reasons: list[str]) -> str:
    if not reasons:
        return ""
    return ", ".join(reason.replace("-", " ") for reason in reasons[:3])


def _is_substantive_paragraph(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if len(stripped) < 40:
        return False
    if stripped.lower().startswith(("source file:", "fetched:", "source_url:")):
        return False
    return True


def _truncate_sentence(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned

    window = cleaned[:limit]
    boundary = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
    if boundary >= int(limit * 0.6):
        return window[: boundary + 1].strip()
    return window.rstrip() + "..."
