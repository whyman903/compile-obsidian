from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*?)?\]\]")
WORD_RE = re.compile(r"\b[\w'-]+\b")
LINE_RE = re.compile(r"\r?\n")


def parse_markdown_text(text: str) -> tuple[dict[str, Any], str, bool]:
    if not text.startswith("---\n"):
        return {}, text.strip(), False

    marker = "\n---\n"
    if marker not in text[4:]:
        return {}, text.strip(), False

    frontmatter_text, body = text[4:].split(marker, 1)
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return {}, text.strip(), False
    return frontmatter, body.strip(), True


def parse_markdown_file(path: Path) -> tuple[dict[str, Any], str, bool]:
    return parse_markdown_text(path.read_text(errors="ignore"))


def extract_wikilinks(body: str) -> list[str]:
    return [match.group(1).strip() for match in WIKILINK_RE.finditer(body) if match.group(1).strip()]


def count_content_paragraphs(body: str) -> int:
    count = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "#-*>|" or stripped.startswith("<!--") or stripped.startswith("$$"):
            continue
        if len(stripped) > 30:
            count += 1
    return count
