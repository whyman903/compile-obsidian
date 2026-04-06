from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha1
import json
from pathlib import Path
import re
from typing import Any


NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = NORMALIZE_RE.sub(" ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _claim_id(source_id: str, text: str) -> str:
    return sha1(f"{source_id}:{text.strip()}".encode("utf-8")).hexdigest()[:12]


def source_id_for_path(raw_path: Path, workspace_root: Path) -> str:
    relative = str(raw_path.relative_to(workspace_root)).replace("\\", "/")
    return f"source_{sha1(relative.encode('utf-8')).hexdigest()[:10]}"


@dataclass
class Claim:
    id: str
    text: str
    source_id: str
    source_title: str
    confidence: float | None = None
    concepts: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


@dataclass
class ConceptRecord:
    name: str
    source_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    wiki_page_path: str | None = None


@dataclass
class EntityRecord:
    name: str
    source_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    wiki_page_path: str | None = None


@dataclass
class QuestionRecord:
    name: str
    source_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    wiki_page_path: str | None = None


@dataclass
class SourceRecord:
    source_id: str
    source_title: str
    raw_path: str
    source_type: str
    asset_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0
    source_page_path: str | None = None


@dataclass
class EvidenceStore:
    claims: dict[str, Claim] = field(default_factory=dict)
    concepts: dict[str, ConceptRecord] = field(default_factory=dict)
    entities: dict[str, EntityRecord] = field(default_factory=dict)
    questions: dict[str, QuestionRecord] = field(default_factory=dict)
    sources: dict[str, SourceRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": {key: asdict(value) for key, value in self.claims.items()},
            "concepts": {key: asdict(value) for key, value in self.concepts.items()},
            "entities": {key: asdict(value) for key, value in self.entities.items()},
            "questions": {key: asdict(value) for key, value in self.questions.items()},
            "sources": {key: asdict(value) for key, value in self.sources.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceStore:
        claims = {
            key: Claim(**value)
            for key, value in (payload.get("claims") or {}).items()
        }
        concepts = {
            key: ConceptRecord(**value)
            for key, value in (payload.get("concepts") or {}).items()
        }
        entities = {
            key: EntityRecord(**value)
            for key, value in (payload.get("entities") or {}).items()
        }
        questions = {
            key: QuestionRecord(**value)
            for key, value in (payload.get("questions") or {}).items()
        }
        sources = {
            key: SourceRecord(**value)
            for key, value in (payload.get("sources") or {}).items()
        }
        return cls(claims=claims, concepts=concepts, entities=entities, questions=questions, sources=sources)


def load_evidence(path: Path) -> EvidenceStore:
    if not path.exists():
        return EvidenceStore()
    payload = json.loads(path.read_text())
    return EvidenceStore.from_dict(payload)


def save_evidence(path: Path, store: EvidenceStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.to_dict(), indent=2, sort_keys=True))


def extract_asset_paths(raw_path: Path, workspace_root: Path) -> list[str]:
    relative_root = raw_path.parent
    if raw_path.suffix.lower() not in {".md", ".markdown", ".html", ".htm"}:
        return []

    text = raw_path.read_text(errors="ignore")
    candidates: set[str] = set()
    markdown_matches = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    html_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    for raw_target in markdown_matches + html_matches:
        cleaned = raw_target.strip()
        if not cleaned or "://" in cleaned or cleaned.startswith("data:") or cleaned.startswith("#"):
            continue
        path = (relative_root / cleaned).resolve()
        if path.exists() and path.is_file():
            try:
                candidates.add(str(path.relative_to(workspace_root)).replace("\\", "/"))
            except ValueError:
                continue
    return sorted(candidates)


def merge_source_evidence(
    store: EvidenceStore,
    analysis: dict[str, Any],
    *,
    source_id: str,
    source_title: str,
    raw_path: Path,
    workspace_root: Path,
    warnings: list[str] | None = None,
) -> EvidenceStore:
    relative_raw = str(raw_path.relative_to(workspace_root)).replace("\\", "/")
    source_record = SourceRecord(
        source_id=source_id,
        source_title=source_title,
        raw_path=relative_raw,
        source_type=raw_path.suffix.lower().lstrip(".") or "unknown",
        asset_paths=extract_asset_paths(raw_path, workspace_root),
        warnings=list(warnings or []),
        word_count=len(re.findall(r"\b[\w'-]+\b", analysis.get("_source_text", ""))),
        char_count=len(analysis.get("_source_text", "")),
        source_page_path=store.sources.get(source_id, SourceRecord(source_id, source_title, relative_raw, raw_path.suffix.lower().lstrip(".") or "unknown")).source_page_path,
    )
    store.sources[source_id] = source_record

    concept_names = _unique_strings(analysis.get("concepts"))
    entity_names = _unique_strings(analysis.get("entities"))
    question_names = _unique_strings(analysis.get("open_questions"))

    for concept_name in concept_names:
        record = store.concepts.get(_normalize_key(concept_name), ConceptRecord(name=concept_name))
        if source_id not in record.source_ids:
            record.source_ids.append(source_id)
        store.concepts[_normalize_key(concept_name)] = record

    for entity_name in entity_names:
        record = store.entities.get(_normalize_key(entity_name), EntityRecord(name=entity_name))
        if source_id not in record.source_ids:
            record.source_ids.append(source_id)
        store.entities[_normalize_key(entity_name)] = record

    for question_name in question_names:
        record = store.questions.get(_normalize_key(question_name), QuestionRecord(name=question_name))
        if source_id not in record.source_ids:
            record.source_ids.append(source_id)
        store.questions[_normalize_key(question_name)] = record

    for raw_claim in analysis.get("key_claims", []) or []:
        claim_payload = _normalize_claim(raw_claim, concept_names, entity_names)
        if not claim_payload["text"]:
            continue
        claim_id = _claim_id(source_id, claim_payload["text"])
        claim = Claim(
            id=claim_id,
            text=claim_payload["text"],
            source_id=source_id,
            source_title=source_title,
            confidence=claim_payload["confidence"],
            concepts=claim_payload["concepts"],
            entities=claim_payload["entities"],
        )
        store.claims[claim_id] = claim
        for concept_name in claim.concepts:
            key = _normalize_key(concept_name)
            record = store.concepts.get(key, ConceptRecord(name=concept_name))
            if source_id not in record.source_ids:
                record.source_ids.append(source_id)
            if claim_id not in record.claim_ids:
                record.claim_ids.append(claim_id)
            store.concepts[key] = record
        for entity_name in claim.entities:
            key = _normalize_key(entity_name)
            record = store.entities.get(key, EntityRecord(name=entity_name))
            if source_id not in record.source_ids:
                record.source_ids.append(source_id)
            if claim_id not in record.claim_ids:
                record.claim_ids.append(claim_id)
            store.entities[key] = record

    return store


def sync_page_reference(
    store: EvidenceStore,
    *,
    title: str,
    page_type: str,
    relative_path: str,
    source_titles: list[str] | None = None,
) -> None:
    if page_type == "source":
        for source_id, source in store.sources.items():
            if source.source_title == title or title in (source_titles or []):
                source.source_page_path = relative_path
                store.sources[source_id] = source
        return

    key = _normalize_key(title)
    if page_type == "entity":
        record = store.entities.get(key)
        if record:
            record.wiki_page_path = relative_path
            store.entities[key] = record
        return
    if page_type == "question":
        record = store.questions.get(key)
        if record:
            record.wiki_page_path = relative_path
            store.questions[key] = record
        return

    record = store.concepts.get(key)
    if record:
        record.wiki_page_path = relative_path
        store.concepts[key] = record


def get_concepts_needing_synthesis(store: EvidenceStore, *, min_sources: int = 2) -> list[ConceptRecord]:
    records = [
        record
        for record in store.concepts.values()
        if len(record.source_ids) >= min_sources
    ]
    return sorted(records, key=lambda item: (-len(item.source_ids), item.name.lower()))


def get_claims_for_concept(store: EvidenceStore, concept_name: str) -> list[Claim]:
    record = store.concepts.get(_normalize_key(concept_name))
    if not record:
        return []
    claims = [store.claims[claim_id] for claim_id in record.claim_ids if claim_id in store.claims]
    return sorted(claims, key=lambda claim: (claim.source_title.lower(), claim.text.lower()))


def get_overlapping_concepts(store: EvidenceStore, *, limit: int = 10) -> list[tuple[ConceptRecord, ConceptRecord, int]]:
    records = list(store.concepts.values())
    overlaps: list[tuple[ConceptRecord, ConceptRecord, int, int, float]] = []
    for index, left in enumerate(records):
        left_sources = set(left.source_ids)
        left_claims = set(left.claim_ids)
        left_tokens = _term_tokens(left.name)
        for right in records[index + 1:]:
            shared = left_sources & set(right.source_ids)
            right_claims = set(right.claim_ids)
            shared_claims = left_claims & right_claims
            similarity = _token_similarity(left_tokens, _term_tokens(right.name))
            if len(shared) < 2 and not (
                len(shared) >= 1 and (len(shared_claims) >= 1 or similarity >= 0.34)
            ):
                continue
            overlaps.append((left, right, len(shared), len(shared_claims), similarity))
    overlaps.sort(key=lambda item: (-item[2], -item[3], -item[4], item[0].name.lower(), item[1].name.lower()))
    return [(left, right, shared_sources) for left, right, shared_sources, _shared_claims, _similarity in overlaps[:limit]]


def get_source_titles_for_title(store: EvidenceStore, title: str, page_type: str) -> list[str]:
    source_ids = get_source_ids_for_title(store, title, page_type)
    return [
        store.sources[source_id].source_title
        for source_id in source_ids
        if source_id in store.sources
    ]


def get_source_ids_for_title(store: EvidenceStore, title: str, page_type: str) -> list[str]:
    if page_type == "source":
        return [
            source_id
            for source_id, source in store.sources.items()
            if source.source_title == title
        ]
    if page_type == "entity":
        record = store.entities.get(_normalize_key(title))
        return sorted(record.source_ids) if record else []
    if page_type == "question":
        record = store.questions.get(_normalize_key(title))
        return sorted(record.source_ids) if record else []
    record = store.concepts.get(_normalize_key(title))
    return sorted(record.source_ids) if record else []


def get_source_count_for_title(store: EvidenceStore, title: str, page_type: str) -> int:
    return len(get_source_ids_for_title(store, title, page_type))


def get_source_title_map(store: EvidenceStore) -> dict[str, str]:
    return {source.source_title: source_id for source_id, source in store.sources.items()}


def _unique_strings(value: Any) -> list[str]:
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = _normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _term_tokens(text: str) -> set[str]:
    return set(_normalize_key(text).split())


def _token_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalize_claim(raw_claim: Any, concepts: list[str], entities: list[str]) -> dict[str, Any]:
    if isinstance(raw_claim, str):
        return {
            "text": raw_claim.strip(),
            "confidence": None,
            "concepts": concepts,
            "entities": entities,
        }
    if not isinstance(raw_claim, dict):
        return {"text": str(raw_claim).strip(), "confidence": None, "concepts": concepts, "entities": entities}

    text = str(raw_claim.get("text", "")).strip()
    confidence = raw_claim.get("confidence")
    if isinstance(confidence, str):
        try:
            confidence = float(confidence)
        except ValueError:
            confidence = None
    elif not isinstance(confidence, (int, float)):
        confidence = None

    claim_concepts = _unique_strings(raw_claim.get("concepts")) or concepts
    claim_entities = _unique_strings(raw_claim.get("entities")) or entities
    return {
        "text": text,
        "confidence": float(confidence) if confidence is not None else None,
        "concepts": claim_concepts,
        "entities": claim_entities,
    }
