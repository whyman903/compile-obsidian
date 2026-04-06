from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

import yaml


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*?)?\]\]")

FILLER_PHRASES = [
    "significant advancement",
    "novel approach",
    "robust framework",
    "comprehensive overview",
    "this concept is important because",
    "groundbreaking",
    "state-of-the-art",
    "cutting-edge",
    "paradigm shift",
    "revolutionize",
    "game-changing",
    "exciting development",
    "promising results",
    "important contribution",
    "noteworthy",
    "it is worth noting",
    "it should be noted",
    "importantly",
    "interestingly",
    "in recent years",
    "has gained significant attention",
    "has attracted considerable interest",
    "plays a crucial role",
    "paves the way",
    "opens the door",
]

# Verbs that signal a factual claim in a sentence.
_CLAIM_VERBS_RE = re.compile(
    r"\b(?:achieves?|shows?|demonstrates?|improves?|reduces?|increases?|"
    r"outperforms?|enables?|requires?|produces?|provides?|reaches?|"
    r"exceeds?|supports?|confirms?|introduces?|measures?|reports?|"
    r"finds?|suggests?|indicates?|reveals?|yields?)\b",
    re.IGNORECASE,
)

METADATA_LEAK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsource_[a-f0-9]{6,}\b", re.IGNORECASE), "raw source id"),
    (re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b"), "arXiv id"),
    (re.compile(r"\b\S+\.pdf\b", re.IGNORECASE), "PDF filename"),
]


@dataclass
class VerificationIssue:
    severity: str
    code: str
    message: str


def verify_page_content(
    *,
    page_type: str,
    content: str,
    raw_source_path: str = "",
    source_count: int = 0,
    expected_equations: int = 0,
    expected_metrics: int = 0,
    valid_link_targets: Iterable[str] = (),
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    frontmatter, body = _load_markdown_doc(content)

    for required in ("title", "type", "status", "updated"):
        if required not in frontmatter:
            issues.append(
                VerificationIssue("medium", "missing_frontmatter", f"Missing required frontmatter field: {required}")
            )

    if page_type == "source":
        if raw_source_path and raw_source_path not in content:
            issues.append(
                VerificationIssue("high", "missing_provenance", "Source page does not link or embed the raw artifact.")
            )
        if expected_equations and "$$" not in body:
            issues.append(
                VerificationIssue("high", "dropped_equations", "Source page dropped equations extracted from the source.")
            )
        if expected_metrics and "| Metric | Value | Context |" not in body:
            issues.append(
                VerificationIssue("medium", "dropped_metrics", "Source page dropped structured metrics extracted from the source.")
            )

    if page_type in {"concept", "entity", "question"}:
        status = str(frontmatter.get("status") or "").strip()
        if status == "stable" and source_count < 2:
            issues.append(
                VerificationIssue("high", "overstated_maturity", "Stable knowledge page has fewer than two supporting sources.")
            )
        if source_count <= 1 and "Provisional Synthesis" not in content:
            issues.append(
                VerificationIssue("medium", "missing_provisional_note", "Single-source synthesis page should visibly declare provisional support.")
            )
        for pattern, label in METADATA_LEAK_PATTERNS:
            if pattern.search(body):
                issues.append(
                    VerificationIssue("high", "metadata_leak", f"Knowledge page leaked {label} into visible content.")
                )
                break

    valid_targets = {target.casefold() for target in valid_link_targets}
    for match in WIKILINK_RE.finditer(content):
        link_target = match.group(1).strip()
        if not link_target or link_target.startswith("raw/"):
            continue
        if valid_targets and link_target.casefold() not in valid_targets:
            issues.append(
                VerificationIssue("medium", "unresolved_wikilink", f"Page links to unresolved target [[{link_target}]].")
            )

    lowered = body.casefold()
    for phrase in FILLER_PHRASES:
        if phrase in lowered:
            issues.append(
                VerificationIssue("low", "filler_language", f"Page still contains filler phrase: {phrase}")
            )

    # --- Citation check for knowledge pages ---
    if page_type in {"concept", "entity", "question"}:
        sentences = _extract_claim_sentences(body)
        if sentences:
            uncited = sum(1 for s in sentences if "[[" not in s)
            if uncited > len(sentences) * 0.5:
                issues.append(
                    VerificationIssue(
                        "medium",
                        "missing_citation",
                        f"{uncited}/{len(sentences)} factual-claim sentences lack a [[wikilink]] citation.",
                    )
                )

    # --- Content density check for knowledge pages ---
    if page_type in {"concept", "entity", "question"}:
        para_count = _count_content_paragraphs(body)
        if para_count < 3:
            issues.append(
                VerificationIssue(
                    "medium",
                    "thin_content",
                    f"Knowledge page has only {para_count} content paragraph(s); expected at least 3.",
                )
            )

    return issues


def _load_markdown_doc(text: str) -> tuple[dict, str]:
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        frontmatter_text, body = text[4:].split("\n---\n", 1)
        try:
            return yaml.safe_load(frontmatter_text) or {}, body.strip()
        except yaml.YAMLError:
            return {}, text.strip()
    return {}, text.strip()


def _extract_claim_sentences(body: str) -> list[str]:
    """Return sentences that look like factual claims (contain a claim verb and are >20 chars)."""
    claims: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        # Skip headings, list markers, and callout lines
        if not stripped or stripped.startswith("#") or stripped.startswith(">"):
            continue
        # Strip leading list marker for sentence analysis but keep the whole line for citation check
        text = re.sub(r"^[-*]\s+", "", stripped)
        # Split on sentence-ending punctuation
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip()
            if len(sentence) > 20 and _CLAIM_VERBS_RE.search(sentence):
                claims.append(stripped)  # use original line so [[...]] detection works
                break  # one claim per line is enough
    return claims


def _count_content_paragraphs(body: str) -> int:
    """Count paragraphs of actual prose content, excluding headings, lists, frontmatter, and blank lines."""
    count = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip headings, list items, callouts, tables, HTML comments, section markers
        if (
            stripped.startswith("#")
            or stripped.startswith("-")
            or stripped.startswith("*")
            or stripped.startswith(">")
            or stripped.startswith("|")
            or stripped.startswith("<!--")
            or stripped.startswith("$$")
        ):
            continue
        # Must have meaningful length to count as a content paragraph
        if len(stripped) > 30:
            count += 1
    return count
