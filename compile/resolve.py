from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from compile.store import EvidenceDatabase, normalize_alias_key


TOKEN_RE = re.compile(r"[a-z0-9]+")
SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "into",
    "nor",
    "of",
    "on",
    "or",
    "over",
    "per",
    "the",
    "to",
    "under",
    "vs",
    "via",
    "with",
}
KNOWN_ACRONYMS = {
    "ai",
    "api",
    "apis",
    "ast",
    "bm25",
    "cli",
    "cot",
    "gpu",
    "gpt",
    "html",
    "ide",
    "json",
    "llm",
    "llms",
    "mcp",
    "ocr",
    "pdf",
    "oss",
    "qwen",
    "qwen2",
    "rag",
    "rl",
    "sql",
    "swe",
    "vla",
    "yaml",
}


def derive_aliases(title: str) -> list[str]:
    text = title.strip()
    if not text:
        return []
    aliases = {
        text,
        text.replace(":", ""),
        text.replace("-", " "),
        text.replace("—", " "),
        re.sub(r"\s+", " ", text).strip(),
    }
    collapsed = re.sub(r"[^A-Za-z0-9 ]+", " ", text)
    aliases.add(re.sub(r"\s+", " ", collapsed).strip())
    return sorted(alias for alias in aliases if alias.strip())


def canonicalize_question(text: str) -> str:
    stripped = text.strip().rstrip(".")
    return stripped if stripped.endswith("?") else f"{stripped}?"


def _smart_title_case(text: str) -> str:
    words = [word for word in text.split() if word]
    if not words:
        return ""

    rendered: list[str] = []
    for index, word in enumerate(words):
        rendered.append(_smart_title_token(word, is_edge=index in {0, len(words) - 1}))
    return " ".join(rendered)


def _smart_title_token(token: str, *, is_edge: bool) -> str:
    parts = re.split(r"([-/])", token)
    return "".join(_smart_title_atom(part, is_edge=is_edge) if part not in {"-", "/"} else part for part in parts)


def _smart_title_atom(atom: str, *, is_edge: bool) -> str:
    if not atom:
        return atom
    lowered = atom.casefold()
    if lowered in KNOWN_ACRONYMS:
        return atom.upper()
    if atom.isupper() and len(atom) <= 8:
        return atom
    if re.search(r"[A-Z].*[A-Z]", atom):
        return atom
    if any(char.isdigit() for char in atom):
        return atom.upper() if lowered in KNOWN_ACRONYMS or len(atom) <= 5 else atom
    if not is_edge and lowered in SMALL_WORDS:
        return lowered
    return atom[:1].upper() + atom[1:].lower()


@dataclass
class TitleResolver:
    page_index: dict[str, dict[str, str]]
    db: EvidenceDatabase

    @classmethod
    def from_catalog(cls, db: EvidenceDatabase, catalog: Iterable[dict[str, str]]) -> TitleResolver:
        page_index: dict[str, dict[str, str]] = {}
        for entry in catalog:
            page_type = str(entry.get("page_type") or entry.get("type") or "").strip()
            title = str(entry.get("title") or "").strip()
            if not page_type or not title:
                continue
            page_index.setdefault(page_type, {})[normalize_alias_key(title)] = title
            for alias in entry.get("aliases", []) or []:
                alias_text = str(alias).strip()
                if alias_text:
                    page_index.setdefault(page_type, {})[normalize_alias_key(alias_text)] = title
        return cls(page_index=page_index, db=db)

    def resolve(self, title: str, page_type: str) -> str | None:
        normalized = normalize_alias_key(title)
        if not normalized:
            return None
        direct = self.page_index.get(page_type, {}).get(normalized)
        if direct:
            return direct
        alias = self.db.resolve_alias(title, page_type=page_type)
        if alias:
            return alias
        return self._fuzzy_resolve(title, page_type)

    def canonical_title(self, title: str, page_type: str) -> str:
        if page_type == "question":
            title = canonicalize_question(title)
        return self.resolve(title, page_type) or self._normalize_fresh_title(title, page_type)

    def register(self, title: str, page_type: str, aliases: list[str] | None = None) -> str:
        canonical = self.canonical_title(title, page_type)
        combined_aliases = derive_aliases(canonical)
        if aliases:
            combined_aliases.extend(aliases)
        self.db.register_aliases(page_type, canonical, combined_aliases)
        normalized_map = self.page_index.setdefault(page_type, {})
        for alias in combined_aliases:
            normalized_map[normalize_alias_key(alias)] = canonical
        return canonical

    def resolve_wikilink(self, link_target: str) -> str | None:
        stripped = link_target.strip()
        if not stripped or stripped.startswith("raw/"):
            return stripped
        for page_type in ("source", "concept", "entity", "question", "dashboard", "output", "comparison", "overview", "index", "log"):
            match = self.resolve(stripped, page_type)
            if match:
                return match
        return None

    def _fuzzy_resolve(self, title: str, page_type: str) -> str | None:
        target_tokens = set(TOKEN_RE.findall(normalize_alias_key(title)))
        if not target_tokens:
            return None
        candidates = self.page_index.get(page_type, {})
        best_title: str | None = None
        best_score = 0.0
        for normalized, candidate in candidates.items():
            candidate_tokens = set(TOKEN_RE.findall(normalized))
            if not candidate_tokens:
                continue
            overlap = len(target_tokens & candidate_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(target_tokens), len(candidate_tokens))
            if score > best_score:
                best_title = candidate
                best_score = score
        return best_title if best_score >= 0.8 else None

    def _normalize_fresh_title(self, title: str, page_type: str) -> str:
        stripped = Path(" ".join(title.strip().split())).stem.replace("_", " ").strip()
        if not stripped:
            return "Untitled"
        if page_type == "question":
            return canonicalize_question(stripped)
        normalized = re.sub(r"\s+", " ", stripped).strip(" -_/:;,.!?")
        normalized = re.sub(r"\b(\d{3})[-_]+", "", normalized).strip()
        return _smart_title_case(normalized) or "Untitled"
