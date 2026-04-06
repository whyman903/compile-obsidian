from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha1
from pathlib import Path
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from compile.evidence import extract_asset_paths, source_id_for_path
from compile.text import normalize_text


CHUNK_TARGET_CHARS = 4000


@dataclass
class SourceChunk:
    chunk_id: str
    index: int
    text: str
    label: str
    page_start: int | None = None
    page_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourcePacket:
    source_id: str
    raw_path: str
    source_type: str
    raw_title: str
    title: str
    analysis_text: str
    full_text: str
    chunks: list[SourceChunk] = field(default_factory=list)
    asset_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [chunk.to_dict() for chunk in self.chunks]
        return data


def extract_source_packet(raw_path: Path, workspace_root: Path) -> SourcePacket:
    suffix = raw_path.suffix.lower()
    source_id = source_id_for_path(raw_path, workspace_root)
    raw_title = _title_from_path(raw_path)
    warnings: list[str] = []
    metadata: dict[str, Any] = {}

    if suffix == ".pdf":
        title, full_text, chunks, metadata = _extract_pdf_packet(raw_path)
    elif suffix in {".md", ".markdown", ".txt"}:
        title, full_text, chunks = _extract_markdown_packet(raw_path)
    elif suffix in {".html", ".htm"}:
        title, full_text, chunks = _extract_html_packet(raw_path)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        title, full_text, chunks, metadata = _extract_image_packet(raw_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    if not full_text.strip():
        warnings.append("No extractable text was found in the source packet.")

    relative_raw = str(raw_path.relative_to(workspace_root)).replace("\\", "/")
    char_count = len(full_text)
    word_count = len(re.findall(r"\b[\w'-]+\b", full_text))
    if char_count > 60000:
        warnings.append(
            "Source packet is long; chunked analysis will be used instead of a single truncated view."
        )

    return SourcePacket(
        source_id=source_id,
        raw_path=relative_raw,
        source_type=suffix.lstrip(".") or "unknown",
        raw_title=raw_title,
        title=title,
        analysis_text=full_text[:60000],
        full_text=full_text,
        chunks=chunks,
        asset_paths=extract_asset_paths(raw_path, workspace_root),
        warnings=warnings,
        word_count=word_count,
        char_count=char_count,
        metadata=metadata,
    )


def save_source_packet(path: Path, packet: SourcePacket) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet.to_dict(), indent=2, sort_keys=True))


def load_source_packet(path: Path) -> SourcePacket:
    payload = json.loads(path.read_text())
    chunks = [SourceChunk(**item) for item in payload.get("chunks", [])]
    payload["chunks"] = chunks
    return SourcePacket(**payload)


def _extract_pdf_packet(raw_path: Path) -> tuple[str, str, list[SourceChunk], dict[str, Any]]:
    title = _title_from_path(raw_path)
    size_bytes = raw_path.stat().st_size
    placeholder = normalize_text(
        f"PDF source named {title}. "
        "Content extraction is deferred to Anthropic's native PDF reader during analysis."
    )
    metadata: dict[str, Any] = {
        "analysis_mode": "anthropic_pdf",
        "pdf_reader": "anthropic_native",
        "file_size_bytes": size_bytes,
    }
    return title, placeholder, [], metadata


def _extract_markdown_packet(raw_path: Path) -> tuple[str, str, list[SourceChunk]]:
    text = raw_path.read_text(errors="ignore")
    title = _title_from_markdown(text) or _title_from_path(raw_path)
    normalized_lines = [line.rstrip() for line in text.splitlines()]
    full_text = normalize_text("\n".join(normalized_lines))
    chunks = _chunk_by_headings(normalized_lines, default_title=title)
    return title, full_text, chunks


def _extract_html_packet(raw_path: Path) -> tuple[str, str, list[SourceChunk]]:
    html = raw_path.read_text(errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else _title_from_path(raw_path))
    body_node = soup.find("article") or soup.body or soup
    body_text = normalize_text(body_node.get_text("\n", strip=True))
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    chunks = _chunk_by_headings(lines, default_title=title)
    return title[:180], body_text, chunks


def _extract_image_packet(raw_path: Path) -> tuple[str, str, list[SourceChunk], dict[str, Any]]:
    title = _title_from_path(raw_path)
    size = raw_path.stat().st_size
    suffix = raw_path.suffix.lower().lstrip(".")
    text = normalize_text(
        f"Image asset named {title}. Format: {suffix}. File size: {size} bytes. "
        "Pending vision analysis -- this placeholder will be replaced after image analysis completes."
    )
    chunk = SourceChunk(chunk_id=f"{raw_path.stem}-1", index=1, text=text, label="Asset metadata")
    metadata: dict[str, Any] = {
        "requires_vision": True,
        "image_path": str(raw_path),
        "image_format": suffix,
        "image_size_bytes": size,
    }
    return title, text, [chunk], metadata


def _extract_url_packet(url: str, workspace_root: Path) -> SourcePacket:
    """Fetch a URL and extract text content into a SourcePacket."""
    import httpx

    try:
        response = httpx.get(url, follow_redirects=True, timeout=30.0, headers={
            "User-Agent": "Compile-Wiki/1.0 (research aggregator)",
        })
        response.raise_for_status()
        html = response.text
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise ValueError(f"Failed to fetch URL {url}: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")

    # Extract title
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not title:
        # Fall back to URL path segment
        from urllib.parse import urlparse
        parsed = urlparse(url)
        title = parsed.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title() or parsed.netloc

    # Extract body text -- prefer <article>, then <main>, then <body>
    body_node = soup.find("article") or soup.find("main") or soup.body or soup
    body_text = normalize_text(body_node.get_text("\n", strip=True))

    # Chunk by headings
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    chunks = _chunk_by_headings(lines, default_title=title)

    # Build source ID from URL
    source_id = f"source_{sha1(url.encode('utf-8')).hexdigest()[:10]}"

    char_count = len(body_text)
    word_count = len(re.findall(r"\b[\w'-]+\b", body_text))
    warnings: list[str] = []
    if not body_text.strip():
        warnings.append("No extractable text was found at the URL.")
    if char_count > 60000:
        warnings.append(
            "URL content is long; chunked analysis will be used instead of a single truncated view."
        )

    metadata: dict[str, Any] = {
        "url": url,
        "fetched_title": title[:200],
    }

    return SourcePacket(
        source_id=source_id,
        raw_path=url,
        source_type="url",
        raw_title=title[:200],
        title=title[:200],
        analysis_text=body_text[:60000],
        full_text=body_text,
        chunks=chunks,
        asset_paths=[],
        warnings=warnings,
        word_count=word_count,
        char_count=char_count,
        metadata=metadata,
    )


def extract_url_packet(url: str, workspace_root: Path) -> SourcePacket:
    """Public entry point: fetch a URL and return a SourcePacket."""
    return _extract_url_packet(url, workspace_root)


def _title_from_path(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def _title_from_markdown(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _is_heading_line(line: str, next_line: str | None) -> bool:
    """Detect heading-like lines: ALL CAPS or short lines followed by longer text."""
    stripped = line.strip()
    if not stripped or len(stripped) < 3:
        return False
    # ALL CAPS lines (at least 3 alpha chars, all uppercase)
    alpha_chars = re.sub(r"[^a-zA-Z]", "", stripped)
    if len(alpha_chars) >= 3 and stripped == stripped.upper():
        return True
    # Short line followed by a longer line (section title pattern)
    if next_line and len(stripped) < 60 and len(next_line.strip()) > len(stripped) * 2:
        return True
    return False


_TABLE_LINE_RE = re.compile(r".*\|.*\|")
_TABLE_SEP_RE = re.compile(r"(?:\t|  {2,})")
_MATH_CHARS = set("{}=<>~^")


def _is_table_line(line: str) -> bool:
    """Return True if a line looks like a table row."""
    if _TABLE_LINE_RE.match(line):
        return True
    # Lines with 2+ tab or multi-space separators
    if len(_TABLE_SEP_RE.findall(line)) >= 2:
        return True
    return False


def _is_equation_line(line: str) -> bool:
    """Return True if a line is likely part of an equation block."""
    stripped = line.strip()
    if not stripped:
        return False
    math_symbols = sum(1 for ch in stripped if ch in _MATH_CHARS or ord(ch) > 0x2200)
    return math_symbols >= 3


def _detect_table_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Identify contiguous runs of table-like lines. Returns (start, end) index pairs."""
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]):
            start = i
            while i < len(lines) and _is_table_line(lines[i]):
                i += 1
            blocks.append((start, i - 1))
        else:
            i += 1
    return blocks


def _in_protected_block(line_idx: int, table_blocks: list[tuple[int, int]], lines: list[str]) -> bool:
    """Return True if splitting here would break a table or equation block."""
    for start, end in table_blocks:
        if start <= line_idx <= end:
            return True
    if _is_equation_line(lines[line_idx]):
        return True
    return False


def _chunk_pdf_pages(page_texts: list[tuple[int, str]]) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    current_texts: list[str] = []
    current_page_start: int | None = None
    current_page_end: int | None = None
    current_size = 0
    chunk_index = 1

    for page_number, page_text in page_texts:
        page_block = f"[Page {page_number}] {page_text}"

        # Check if we should start a new chunk
        if current_texts and current_size + len(page_block) > CHUNK_TARGET_CHARS:
            # Before splitting, check if the last page's text ends inside a table
            # or equation block. If so, allow slight overflow to keep it together.
            last_text = current_texts[-1] if current_texts else ""
            last_lines = last_text.splitlines()
            table_blocks = _detect_table_blocks(last_lines)
            in_protected = last_lines and _in_protected_block(
                len(last_lines) - 1, table_blocks, last_lines,
            )

            if not in_protected:
                chunks.append(
                    SourceChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        index=chunk_index,
                        text="\n\n".join(current_texts),
                        label=f"Pages {current_page_start}-{current_page_end}",
                        page_start=current_page_start,
                        page_end=current_page_end,
                    )
                )
                chunk_index += 1
                current_texts = []
                current_page_start = None
                current_page_end = None
                current_size = 0

        # Detect if this page starts with a heading-like line (section boundary)
        lines = page_text.splitlines()
        if (
            current_texts
            and current_size > CHUNK_TARGET_CHARS // 2
            and lines
            and _is_heading_line(lines[0], lines[1] if len(lines) > 1 else None)
        ):
            # Start a new chunk at the section boundary
            chunks.append(
                SourceChunk(
                    chunk_id=f"chunk-{chunk_index}",
                    index=chunk_index,
                    text="\n\n".join(current_texts),
                    label=f"Pages {current_page_start}-{current_page_end}",
                    page_start=current_page_start,
                    page_end=current_page_end,
                )
            )
            chunk_index += 1
            current_texts = []
            current_page_start = None
            current_page_end = None
            current_size = 0

        current_texts.append(page_block)
        current_page_start = page_number if current_page_start is None else current_page_start
        current_page_end = page_number
        current_size += len(page_block)

    if current_texts:
        chunks.append(
            SourceChunk(
                chunk_id=f"chunk-{chunk_index}",
                index=chunk_index,
                text="\n\n".join(current_texts),
                label=f"Pages {current_page_start}-{current_page_end}",
                page_start=current_page_start,
                page_end=current_page_end,
            )
        )

    return chunks


def _chunk_by_headings(lines: list[str], *, default_title: str) -> list[SourceChunk]:
    sections: list[tuple[str, list[str]]] = []
    heading = default_title
    current: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            if current:
                sections.append((heading, current))
            heading = stripped.lstrip("#").strip() or default_title
            current = []
            continue
        current.append(stripped)
    if current:
        sections.append((heading, current))

    if not sections:
        sections = [(default_title, [line for line in lines if line.strip()])]

    chunks: list[SourceChunk] = []
    chunk_index = 1
    for heading, section_lines in sections:
        text = normalize_text("\n".join(section_lines))
        if not text:
            continue
        if len(text) <= CHUNK_TARGET_CHARS:
            chunks.append(
                SourceChunk(
                    chunk_id=f"chunk-{chunk_index}",
                    index=chunk_index,
                    text=text,
                    label=heading[:120],
                )
            )
            chunk_index += 1
            continue

        sentence_parts = re.split(r"(?<=[.!?])\s+", text)
        current_parts: list[str] = []
        current_size = 0
        part_index = 1
        for sentence in sentence_parts:
            if current_parts and current_size + len(sentence) > CHUNK_TARGET_CHARS:
                chunks.append(
                    SourceChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        index=chunk_index,
                        text=" ".join(current_parts).strip(),
                        label=f"{heading[:100]} ({part_index})",
                    )
                )
                chunk_index += 1
                part_index += 1
                current_parts = []
                current_size = 0
            current_parts.append(sentence)
            current_size += len(sentence)
        if current_parts:
            chunks.append(
                SourceChunk(
                    chunk_id=f"chunk-{chunk_index}",
                    index=chunk_index,
                    text=" ".join(current_parts).strip(),
                    label=f"{heading[:100]} ({part_index})",
                )
            )
            chunk_index += 1
    return chunks
