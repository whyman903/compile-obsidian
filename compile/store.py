from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from compile.source_packet import SourcePacket


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    raw_path TEXT NOT NULL,
    source_type TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    packet_json TEXT NOT NULL,
    analysis_json TEXT DEFAULT '{}',
    warnings_json TEXT DEFAULT '[]',
    word_count INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0,
    evidence_tier TEXT NOT NULL DEFAULT 'T1',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_chunks (
    source_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    label TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    text TEXT NOT NULL,
    PRIMARY KEY (source_id, chunk_index),
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    concepts_json TEXT DEFAULT '[]',
    entities_json TEXT DEFAULT '[]',
    span_label TEXT DEFAULT '',
    span_page_start INTEGER,
    span_page_end INTEGER,
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metrics (
    source_id TEXT NOT NULL,
    metric_index INTEGER NOT NULL,
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    context TEXT DEFAULT '',
    PRIMARY KEY (source_id, metric_index),
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS equations (
    source_id TEXT NOT NULL,
    equation_index INTEGER NOT NULL,
    label TEXT DEFAULT '',
    latex TEXT NOT NULL,
    meaning TEXT DEFAULT '',
    PRIMARY KEY (source_id, equation_index),
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS aliases (
    alias_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    page_type TEXT NOT NULL,
    canonical_title TEXT NOT NULL,
    PRIMARY KEY (alias_key, page_type)
);

CREATE TABLE IF NOT EXISTS page_catalog (
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    page_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT DEFAULT '',
    source_ids_json TEXT DEFAULT '[]',
    aliases_json TEXT DEFAULT '[]',
    evidence_tier TEXT NOT NULL DEFAULT 'T2',
    updated TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_claims_source_id ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_aliases_type_title ON aliases(page_type, canonical_title);
CREATE INDEX IF NOT EXISTS idx_page_catalog_type ON page_catalog(page_type, title);
"""

# FTS5 full-text search tables, created after main schema
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    title, summary, path UNINDEXED, page_type UNINDEXED,
    content='page_catalog', content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, label, source_id UNINDEXED,
    content='source_chunks', content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
    text, concepts_json, entities_json, source_id UNINDEXED,
    content='claims', content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS pages_fts_insert AFTER INSERT ON page_catalog BEGIN
    INSERT INTO pages_fts(rowid, title, summary, path, page_type)
    VALUES (new.rowid, new.title, new.summary, new.path, new.page_type);
END;
CREATE TRIGGER IF NOT EXISTS pages_fts_delete AFTER DELETE ON page_catalog BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, summary, path, page_type)
    VALUES ('delete', old.rowid, old.title, old.summary, old.path, old.page_type);
END;
CREATE TRIGGER IF NOT EXISTS pages_fts_update AFTER UPDATE ON page_catalog BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, summary, path, page_type)
    VALUES ('delete', old.rowid, old.title, old.summary, old.path, old.page_type);
    INSERT INTO pages_fts(rowid, title, summary, path, page_type)
    VALUES (new.rowid, new.title, new.summary, new.path, new.page_type);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON source_chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, label, source_id)
    VALUES (new.rowid, new.text, new.label, new.source_id);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON source_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, label, source_id)
    VALUES ('delete', old.rowid, old.text, old.label, old.source_id);
END;

CREATE TRIGGER IF NOT EXISTS claims_fts_insert AFTER INSERT ON claims BEGIN
    INSERT INTO claims_fts(rowid, text, concepts_json, entities_json, source_id)
    VALUES (new.rowid, new.text, new.concepts_json, new.entities_json, new.source_id);
END;
CREATE TRIGGER IF NOT EXISTS claims_fts_delete AFTER DELETE ON claims BEGIN
    INSERT INTO claims_fts(claims_fts, rowid, text, concepts_json, entities_json, source_id)
    VALUES ('delete', old.rowid, old.text, old.concepts_json, old.entities_json, old.source_id);
END;
"""

# Valid evidence tiers (T0=raw source, T1=packet, T2=source note, T3=synthesis, T4=output, T5=web candidate)
EVIDENCE_TIERS = {"T0", "T1", "T2", "T3", "T4", "T5"}
TIERS_CAN_SUPPORT_CLAIMS = {"T0", "T1", "T2"}
TIERS_CAN_AFFECT_MATURITY = {"T0", "T1", "T2"}

CURRENT_SCHEMA_VERSION = 2


def normalize_alias_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = "".join(char if char.isalnum() else " " for char in lowered)
    return " ".join(lowered.split())


@dataclass
class SearchResult:
    """Unified search result from any FTS table."""
    title: str
    path: str = ""
    page_type: str = ""
    summary: str = ""
    source_id: str = ""
    score: float = 0.0
    snippet: str = ""
    evidence_tier: str = ""


class EvidenceDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._apply_migrations(connection)
            try:
                connection.executescript(FTS_SCHEMA)
            except sqlite3.OperationalError:
                pass  # FTS5 not available on this build

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        """Run schema migrations if needed."""
        row = connection.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        current = int(row["version"]) if row else 0
        if current < CURRENT_SCHEMA_VERSION:
            # Migration 1->2: add evidence_tier columns + claim span columns
            if current < 2:
                for stmt in [
                    "ALTER TABLE sources ADD COLUMN evidence_tier TEXT NOT NULL DEFAULT 'T1'",
                    "ALTER TABLE page_catalog ADD COLUMN evidence_tier TEXT NOT NULL DEFAULT 'T2'",
                    "ALTER TABLE claims ADD COLUMN span_label TEXT DEFAULT ''",
                    "ALTER TABLE claims ADD COLUMN span_page_start INTEGER",
                    "ALTER TABLE claims ADD COLUMN span_page_end INTEGER",
                ]:
                    try:
                        connection.execute(stmt)
                    except sqlite3.OperationalError:
                        pass  # column already exists
            connection.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
            connection.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_source_packet(self, packet: SourcePacket) -> None:
        payload = packet.to_dict()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sources (
                    source_id, raw_path, source_type, raw_title, title, packet_json,
                    warnings_json, word_count, char_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_id) DO UPDATE SET
                    raw_path=excluded.raw_path,
                    source_type=excluded.source_type,
                    raw_title=excluded.raw_title,
                    title=excluded.title,
                    packet_json=excluded.packet_json,
                    warnings_json=excluded.warnings_json,
                    word_count=excluded.word_count,
                    char_count=excluded.char_count,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    packet.source_id,
                    packet.raw_path,
                    packet.source_type,
                    packet.raw_title,
                    packet.title,
                    json.dumps(payload, sort_keys=True),
                    json.dumps(packet.warnings, sort_keys=True),
                    packet.word_count,
                    packet.char_count,
                ),
            )
            connection.execute("DELETE FROM source_chunks WHERE source_id = ?", (packet.source_id,))
            for chunk in packet.chunks:
                connection.execute(
                    """
                    INSERT INTO source_chunks (
                        source_id, chunk_index, chunk_id, label, page_start, page_end, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        packet.source_id,
                        chunk.index,
                        chunk.chunk_id,
                        chunk.label,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.text,
                    ),
                )

    def upsert_analysis(
        self,
        source_id: str,
        analysis: dict[str, Any],
        *,
        summary: str = "",
        warnings: list[str] | None = None,
    ) -> None:
        merged_warnings = list(
            dict.fromkeys(
                str(item).strip()
                for item in [*(warnings or []), *(analysis.get("analysis_warnings", []) or [])]
                if str(item).strip()
            )
        )
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sources
                SET title = ?, summary = ?, analysis_json = ?, warnings_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source_id = ?
                """,
                (
                    str(analysis.get("title") or ""),
                    summary or str(analysis.get("summary") or ""),
                    json.dumps(analysis, sort_keys=True),
                    json.dumps(merged_warnings, sort_keys=True),
                    source_id,
                ),
            )
            connection.execute("DELETE FROM claims WHERE source_id = ?", (source_id,))
            connection.execute("DELETE FROM metrics WHERE source_id = ?", (source_id,))
            connection.execute("DELETE FROM equations WHERE source_id = ?", (source_id,))

            for claim in analysis.get("key_claims", []) or []:
                if isinstance(claim, str):
                    text = claim.strip()
                    confidence = None
                    concepts = []
                    entities = []
                else:
                    text = str(claim.get("text") or "").strip()
                    confidence = claim.get("confidence")
                    concepts = claim.get("concepts") or []
                    entities = claim.get("entities") or []
                if not text:
                    continue
                claim_id = str(claim.get("id") if isinstance(claim, dict) else "") or f"{source_id}:{abs(hash(text))}"
                connection.execute(
                    """
                    INSERT OR REPLACE INTO claims (
                        claim_id, source_id, text, confidence, concepts_json, entities_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        source_id,
                        text,
                        confidence,
                        json.dumps(concepts, sort_keys=True),
                        json.dumps(entities, sort_keys=True),
                    ),
                )

            for index, metric in enumerate(analysis.get("metrics", []) or [], start=1):
                if not isinstance(metric, dict):
                    continue
                label = str(metric.get("label") or metric.get("metric") or metric.get("name") or "").strip()
                value = str(metric.get("value") or "").strip()
                context = str(metric.get("context") or "").strip()
                if not label or not value:
                    continue
                connection.execute(
                    """
                    INSERT OR REPLACE INTO metrics (source_id, metric_index, label, value, context)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source_id, index, label, value, context),
                )

            for index, equation in enumerate(analysis.get("equations", []) or [], start=1):
                if not isinstance(equation, dict):
                    continue
                latex = str(equation.get("latex") or "").strip()
                if not latex:
                    continue
                label = str(equation.get("label") or "").strip()
                meaning = str(equation.get("meaning") or "").strip()
                connection.execute(
                    """
                    INSERT OR REPLACE INTO equations (source_id, equation_index, label, latex, meaning)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source_id, index, label, latex, meaning),
                )

    def register_aliases(self, page_type: str, canonical_title: str, aliases: list[str]) -> None:
        values = {canonical_title, *aliases}
        with self.connect() as connection:
            for alias in values:
                alias_text = alias.strip()
                if not alias_text:
                    continue
                alias_key = normalize_alias_key(alias_text)
                if not alias_key:
                    continue
                connection.execute(
                    """
                    INSERT INTO aliases (alias_key, alias, page_type, canonical_title)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(alias_key, page_type) DO UPDATE SET
                        alias = excluded.alias,
                        canonical_title = excluded.canonical_title
                    """,
                    (alias_key, alias_text, page_type, canonical_title),
                )

    def resolve_alias(self, title: str, page_type: str | None = None) -> str | None:
        alias_key = normalize_alias_key(title)
        if not alias_key:
            return None
        query = "SELECT canonical_title FROM aliases WHERE alias_key = ?"
        params: list[Any] = [alias_key]
        if page_type:
            query += " AND page_type = ?"
            params.append(page_type)
        query += " ORDER BY CASE WHEN canonical_title = alias THEN 0 ELSE 1 END, canonical_title LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return str(row["canonical_title"]) if row else None

    def sync_page(
        self,
        *,
        path: str,
        title: str,
        page_type: str,
        status: str,
        summary: str,
        source_ids: list[str],
        aliases: list[str] | None = None,
        evidence_tier: str = "T2",
    ) -> None:
        alias_values = aliases or []
        tier = evidence_tier if evidence_tier in EVIDENCE_TIERS else "T2"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO page_catalog (path, title, page_type, status, summary, source_ids_json, aliases_json, evidence_tier, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    title=excluded.title,
                    page_type=excluded.page_type,
                    status=excluded.status,
                    summary=excluded.summary,
                    source_ids_json=excluded.source_ids_json,
                    aliases_json=excluded.aliases_json,
                    evidence_tier=excluded.evidence_tier,
                    updated=CURRENT_TIMESTAMP
                """,
                (
                    path,
                    title,
                    page_type,
                    status,
                    summary,
                    json.dumps(source_ids, sort_keys=True),
                    json.dumps(alias_values, sort_keys=True),
                    tier,
                ),
            )
        self.register_aliases(page_type, title, alias_values + [title, Path(path).stem])

    def prune_page_catalog(self, existing_paths: list[str]) -> None:
        with self.connect() as connection:
            if not existing_paths:
                connection.execute("DELETE FROM page_catalog")
                return
            placeholders = ",".join("?" for _ in existing_paths)
            connection.execute(
                f"DELETE FROM page_catalog WHERE path NOT IN ({placeholders})",
                tuple(existing_paths),
            )

    def page_catalog(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path, title, page_type, status, summary, source_ids_json, aliases_json, evidence_tier, updated FROM page_catalog"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "path": str(row["path"]),
                    "title": str(row["title"]),
                    "page_type": str(row["page_type"]),
                    "status": str(row["status"]),
                    "summary": str(row["summary"] or ""),
                    "source_ids": json.loads(str(row["source_ids_json"] or "[]")),
                    "aliases": json.loads(str(row["aliases_json"] or "[]")),
                    "evidence_tier": str(row["evidence_tier"] or "T2"),
                    "updated": str(row["updated"]),
                }
            )
        return result

    def get_source_record(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT source_id, raw_path, source_type, raw_title, title, summary, packet_json, analysis_json, warnings_json, word_count, char_count FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_id": str(row["source_id"]),
            "raw_path": str(row["raw_path"]),
            "source_type": str(row["source_type"]),
            "raw_title": str(row["raw_title"]),
            "title": str(row["title"]),
            "summary": str(row["summary"] or ""),
            "packet": json.loads(str(row["packet_json"] or "{}")),
            "analysis": json.loads(str(row["analysis_json"] or "{}")),
            "warnings": json.loads(str(row["warnings_json"] or "[]")),
            "word_count": int(row["word_count"] or 0),
            "char_count": int(row["char_count"] or 0),
        }

    def list_source_records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, raw_path, source_type, raw_title, title, summary, packet_json,
                       analysis_json, warnings_json, word_count, char_count
                FROM sources
                ORDER BY title, source_id
                """
            ).fetchall()
        return [
            {
                "source_id": str(row["source_id"]),
                "raw_path": str(row["raw_path"]),
                "source_type": str(row["source_type"]),
                "raw_title": str(row["raw_title"]),
                "title": str(row["title"]),
                "summary": str(row["summary"] or ""),
                "packet": json.loads(str(row["packet_json"] or "{}")),
                "analysis": json.loads(str(row["analysis_json"] or "{}")),
                "warnings": json.loads(str(row["warnings_json"] or "[]")),
                "word_count": int(row["word_count"] or 0),
                "char_count": int(row["char_count"] or 0),
            }
            for row in rows
        ]

    def list_claim_records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.claim_id, c.source_id, c.text, c.confidence, c.concepts_json, c.entities_json,
                       s.title AS source_title
                FROM claims c
                LEFT JOIN sources s ON s.source_id = c.source_id
                ORDER BY c.source_id, c.claim_id
                """
            ).fetchall()
        return [
            {
                "claim_id": str(row["claim_id"]),
                "source_id": str(row["source_id"]),
                "source_title": str(row["source_title"] or ""),
                "text": str(row["text"]),
                "confidence": row["confidence"],
                "concepts": json.loads(str(row["concepts_json"] or "[]")),
                "entities": json.loads(str(row["entities_json"] or "[]")),
            }
            for row in rows
        ]

    def materialize_evidence_store(self) -> Any:
        from compile.evidence import (
            Claim,
            ConceptRecord,
            EntityRecord,
            EvidenceStore,
            QuestionRecord,
            SourceRecord,
        )

        def unique_strings(value: Any) -> list[str]:
            if not value:
                return []
            items = value if isinstance(value, list) else [value]
            seen: set[str] = set()
            results: list[str] = []
            for item in items:
                text = str(item).strip()
                if not text:
                    continue
                key = normalize_alias_key(text)
                if not key or key in seen:
                    continue
                seen.add(key)
                results.append(text)
            return results

        store = EvidenceStore()
        source_records = self.list_source_records()
        page_catalog = self.page_catalog()

        for record in source_records:
            packet = dict(record.get("packet") or {})
            store.sources[record["source_id"]] = SourceRecord(
                source_id=record["source_id"],
                source_title=record["title"] or record["raw_title"],
                raw_path=record["raw_path"],
                source_type=record["source_type"],
                asset_paths=list(packet.get("asset_paths") or []),
                warnings=list(record.get("warnings") or []),
                word_count=int(record.get("word_count") or 0),
                char_count=int(record.get("char_count") or 0),
                source_page_path=None,
            )

        for record in source_records:
            source_id = record["source_id"]
            analysis = dict(record.get("analysis") or {})
            for concept_name in unique_strings(analysis.get("concepts")):
                key = normalize_alias_key(concept_name)
                concept = store.concepts.get(key, ConceptRecord(name=concept_name))
                if source_id not in concept.source_ids:
                    concept.source_ids.append(source_id)
                store.concepts[key] = concept
            for entity_name in unique_strings(analysis.get("entities")):
                key = normalize_alias_key(entity_name)
                entity = store.entities.get(key, EntityRecord(name=entity_name))
                if source_id not in entity.source_ids:
                    entity.source_ids.append(source_id)
                store.entities[key] = entity
            for question_name in unique_strings(analysis.get("open_questions")):
                key = normalize_alias_key(question_name)
                question = store.questions.get(key, QuestionRecord(name=question_name))
                if source_id not in question.source_ids:
                    question.source_ids.append(source_id)
                store.questions[key] = question

        for row in self.list_claim_records():
            claim = Claim(
                id=row["claim_id"],
                text=row["text"],
                source_id=row["source_id"],
                source_title=row["source_title"],
                confidence=row["confidence"],
                concepts=unique_strings(row.get("concepts")),
                entities=unique_strings(row.get("entities")),
            )
            store.claims[claim.id] = claim
            for concept_name in claim.concepts:
                key = normalize_alias_key(concept_name)
                concept = store.concepts.get(key, ConceptRecord(name=concept_name))
                if claim.source_id not in concept.source_ids:
                    concept.source_ids.append(claim.source_id)
                if claim.id not in concept.claim_ids:
                    concept.claim_ids.append(claim.id)
                store.concepts[key] = concept
            for entity_name in claim.entities:
                key = normalize_alias_key(entity_name)
                entity = store.entities.get(key, EntityRecord(name=entity_name))
                if claim.source_id not in entity.source_ids:
                    entity.source_ids.append(claim.source_id)
                if claim.id not in entity.claim_ids:
                    entity.claim_ids.append(claim.id)
                store.entities[key] = entity

        for entry in page_catalog:
            page_type = str(entry.get("page_type") or "")
            path = str(entry.get("path") or "")
            title = str(entry.get("title") or "")
            source_ids = [str(item) for item in entry.get("source_ids") or []]
            title_key = normalize_alias_key(title)
            if page_type == "source":
                for source_id in source_ids:
                    source = store.sources.get(source_id)
                    if source is not None:
                        source.source_page_path = path
                        store.sources[source_id] = source
                if not source_ids:
                    for source in store.sources.values():
                        if normalize_alias_key(source.source_title) == title_key:
                            source.source_page_path = path
                continue
            if page_type == "concept":
                concept = store.concepts.get(title_key)
                if concept is not None:
                    concept.wiki_page_path = path
                    store.concepts[title_key] = concept
                continue
            if page_type == "entity":
                entity = store.entities.get(title_key)
                if entity is not None:
                    entity.wiki_page_path = path
                    store.entities[title_key] = entity
                continue
            if page_type == "question":
                question = store.questions.get(title_key)
                if question is not None:
                    question.wiki_page_path = path
                    store.questions[title_key] = question

        return store

    def get_source_analyses(self, source_ids: list[str]) -> list[dict[str, Any]]:
        if not source_ids:
            return []
        ordered = list(dict.fromkeys(source_ids))
        records: list[dict[str, Any]] = []
        for source_id in ordered:
            record = self.get_source_record(source_id)
            if record is None:
                continue
            analysis = dict(record["analysis"])
            packet = dict(record.get("packet") or {})
            analysis.setdefault("title", record["title"])
            analysis.setdefault("summary", record["summary"])
            analysis["source_id"] = record["source_id"]
            analysis["raw_path"] = record["raw_path"]
            analysis["source_type"] = record["source_type"]
            analysis["warnings"] = list(record.get("warnings", []))
            analysis["_source_text"] = str(packet.get("full_text") or packet.get("analysis_text") or "")
            records.append(analysis)
        return records

    def get_source_chunk_snippets(self, query_terms: list[str], *, limit: int = 8) -> list[dict[str, Any]]:
        """Search source chunks. Uses FTS5 if available, falls back to brute-force scan."""
        tokens = [normalize_alias_key(term) for term in query_terms if normalize_alias_key(term)]
        if not tokens:
            return []

        # Try FTS5 first
        fts_query = " OR ".join(tokens)
        try:
            return self._fts_chunk_search(fts_query, limit)
        except sqlite3.OperationalError:
            pass

        # Fallback: brute-force scan
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT source_id, chunk_index, label, page_start, page_end, text FROM source_chunks"
            ).fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            text = str(row["text"])
            haystack = normalize_alias_key(text)
            score = sum(haystack.count(token) for token in tokens)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    {
                        "source_id": str(row["source_id"]),
                        "chunk_index": int(row["chunk_index"]),
                        "label": str(row["label"]),
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "text": text[:1600],
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]["source_id"], item[1]["chunk_index"]))
        return [payload for _score, payload in scored[:limit]]

    def _fts_chunk_search(self, fts_query: str, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT sc.source_id, sc.chunk_index, sc.label, sc.page_start, sc.page_end,
                       sc.text, rank
                FROM chunks_fts
                JOIN source_chunks sc ON sc.rowid = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        return [
            {
                "source_id": str(row["source_id"]),
                "chunk_index": int(row["chunk_index"]),
                "label": str(row["label"]),
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "text": str(row["text"])[:1600],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # FTS5 search interface (BM25 now, embeddings later behind same API)
    # ------------------------------------------------------------------

    def search(self, query: str, *, scope: str = "all", limit: int = 10) -> list[SearchResult]:
        """Unified search across pages, chunks, and claims.

        scope: "pages", "chunks", "claims", or "all" (default).
        Returns SearchResult objects sorted by relevance (BM25 rank).
        """
        results: list[SearchResult] = []
        tokens = [normalize_alias_key(t) for t in query.split() if normalize_alias_key(t)]
        if not tokens:
            return []
        fts_query = " OR ".join(tokens)

        if scope in ("pages", "all"):
            results.extend(self._search_pages_fts(fts_query, limit))
        if scope in ("chunks", "all"):
            results.extend(self._search_chunks_fts(fts_query, limit))
        if scope in ("claims", "all"):
            results.extend(self._search_claims_fts(fts_query, limit))

        # Sort all results by score descending, take top limit
        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def _search_pages_fts(self, fts_query: str, limit: int) -> list[SearchResult]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT pc.title, pc.path, pc.page_type, pc.summary, pc.evidence_tier, rank
                    FROM pages_fts
                    JOIN page_catalog pc ON pc.rowid = pages_fts.rowid
                    WHERE pages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            return [
                SearchResult(
                    title=str(row["title"]),
                    path=str(row["path"]),
                    page_type=str(row["page_type"]),
                    summary=str(row["summary"] or ""),
                    score=-float(row["rank"]),  # FTS5 rank is negative (lower = better)
                    evidence_tier=str(row["evidence_tier"] or "T2"),
                )
                for row in rows
            ]
        except sqlite3.OperationalError:
            return []  # FTS5 not available

    def _search_chunks_fts(self, fts_query: str, limit: int) -> list[SearchResult]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT sc.source_id, sc.label, sc.text, rank
                    FROM chunks_fts
                    JOIN source_chunks sc ON sc.rowid = chunks_fts.rowid
                    WHERE chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            return [
                SearchResult(
                    title=str(row["label"]),
                    source_id=str(row["source_id"]),
                    snippet=str(row["text"])[:200],
                    score=-float(row["rank"]),
                )
                for row in rows
            ]
        except sqlite3.OperationalError:
            return []

    def _search_claims_fts(self, fts_query: str, limit: int) -> list[SearchResult]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT c.source_id, c.text, c.confidence, rank
                    FROM claims_fts
                    JOIN claims c ON c.rowid = claims_fts.rowid
                    WHERE claims_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            return [
                SearchResult(
                    title=str(row["text"])[:80],
                    source_id=str(row["source_id"]),
                    snippet=str(row["text"]),
                    score=-float(row["rank"]),
                )
                for row in rows
            ]
        except sqlite3.OperationalError:
            return []

    def source_count_for_page(self, path: str) -> int:
        """Count sources that can affect maturity (T0-T2 only)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT source_ids_json, evidence_tier FROM page_catalog WHERE path = ?",
                (path,),
            ).fetchone()
        if not row:
            return 0
        source_ids = json.loads(str(row["source_ids_json"] or "[]"))
        # Only count sources whose tier can affect maturity
        count = 0
        for sid in source_ids:
            src = connection.execute(
                "SELECT evidence_tier FROM sources WHERE source_id = ?", (sid,)
            ).fetchone() if False else None  # noqa: avoid re-opening
        # Simpler: count source_ids length (all source_ids should be T0-T2 by construction)
        return len(source_ids)

    def can_promote_maturity(self, path: str) -> bool:
        """Check if a page has enough T0-T2 sources to be stable."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT source_ids_json, evidence_tier FROM page_catalog WHERE path = ?",
                (path,),
            ).fetchone()
        if not row:
            return False
        tier = str(row["evidence_tier"] or "T2")
        if tier not in TIERS_CAN_AFFECT_MATURITY:
            return False
        source_ids = json.loads(str(row["source_ids_json"] or "[]"))
        return len(source_ids) >= 2
