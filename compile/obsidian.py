from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import shutil
from typing import Any

import yaml

from compile.markdown import LINE_RE, WORD_RE, extract_wikilinks, parse_markdown_file
from compile.page_types import ARTICLE_PAGE_TYPES, CONTENT_PAGE_TYPES, NAV_PAGE_TYPES


IGNORED_DIRS = {
    ".compile",
    ".git",
    ".obsidian",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*]+')
STALE_OVERVIEW_MARKERS = (
    "This workspace was just initialized.",
    "_Themes will emerge as sources are compiled._",
    "_Highlights will emerge as the wiki grows._",
    "_Source highlights will appear after the first ingest._",
)
STALE_INDEX_MARKERS = (
    "_No sources ingested yet._",
    "_No source notes yet._",
    "_Articles will appear as the wiki grows._",
    "_Maps of content will appear as the wiki grows._",
)


def _normalize_key(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lower()
    normalized = re.sub(r"\.md$", "", normalized)
    normalized = re.sub(r"[-_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" /")


def _coerce_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _search_terms(value: str) -> list[str]:
    return [token.lower() for token in WORD_RE.findall(value)]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _safe_page_filename(title: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub(" ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Untitled"



def _extract_title(path: Path, body: str, frontmatter: dict[str, Any]) -> str:
    title = str(frontmatter.get("title", "")).strip()
    if title:
        return title
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def _inferred_page_type(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    folder_map = {
        "wiki/articles/": "article",
        "wiki/sources/": "source",
        "wiki/maps/": "map",
        "wiki/outputs/": "output",
        "wiki/concepts/": "concept",
        "wiki/entities/": "entity",
        "wiki/questions/": "question",
        "wiki/dashboards/": "dashboard",
        "pages/": "article",
    }
    for prefix, page_type in folder_map.items():
        if normalized.startswith(prefix):
            return page_type
    return "unknown"


def _parse_markdown(path: Path) -> tuple[dict[str, Any], str, bool]:
    return parse_markdown_file(path)


def discover_vault_root(start: Path) -> Path:
    resolved = start.resolve()
    candidates = [resolved, *resolved.parents]
    for candidate in candidates:
        if (
            (candidate / ".compile" / "config.yaml").exists()
            or (candidate / ".obsidian").is_dir()
            or (candidate / "workspace.json").exists()
            or (candidate / "wiki").is_dir()
            or (candidate / "pages").is_dir()
        ):
            return candidate
    return resolved


@dataclass
class VaultPage:
    title: str
    relative_path: str
    page_type: str
    tags: list[str]
    aliases: list[str]
    has_frontmatter: bool
    word_count: int
    frontmatter: dict[str, Any]
    body: str
    outbound_link_targets: list[str]
    resolved_outbound_links: list[str] = field(default_factory=list)
    resolved_file_links: list[str] = field(default_factory=list)
    unresolved_outbound_links: list[str] = field(default_factory=list)
    inbound_links: list[str] = field(default_factory=list)

    def to_dict(self, include_body: bool = False) -> dict[str, Any]:
        payload = {
            "title": self.title,
            "relative_path": self.relative_path,
            "page_type": self.page_type,
            "tags": self.tags,
            "aliases": self.aliases,
            "has_frontmatter": self.has_frontmatter,
            "word_count": self.word_count,
            "outbound_link_targets": self.outbound_link_targets,
            "resolved_outbound_links": self.resolved_outbound_links,
            "resolved_file_links": self.resolved_file_links,
            "unresolved_outbound_links": self.unresolved_outbound_links,
            "inbound_links": self.inbound_links,
            "frontmatter": _json_safe(self.frontmatter),
        }
        if include_body:
            payload["body"] = self.body
        return payload


@dataclass
class VaultIssue:
    code: str
    severity: str
    message: str
    pages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "pages": self.pages,
        }


@dataclass
class VaultReport:
    root: str
    page_root: str
    layout: str
    obsidian_enabled: bool
    obsidian_files: list[str]
    total_pages: int
    page_type_counts: dict[str, int]
    pages_with_frontmatter: int
    pages_without_frontmatter: int
    pages_with_wikilinks: int
    pages_without_wikilinks: int
    total_outbound_links: int
    resolved_link_count: int
    resolved_file_link_count: int
    unresolved_link_count: int
    orphan_page_count: int
    duplicate_titles: dict[str, list[str]]
    thin_pages: list[str]
    knowledge_page_count: int
    knowledge_pages_with_non_nav_inbound: int
    navigation_bottlenecks: list[str]
    raw_file_count: int
    raw_files_without_source_notes: list[str]
    source_pages_without_raw_links: list[str]
    auxiliary_markdown_files: list[str]
    empty_markdown_files: list[str]
    stale_navigation_pages: list[str]
    issues: list[VaultIssue]
    pages: list[VaultPage]

    def to_dict(self, include_body: bool = False) -> dict[str, Any]:
        return {
            "root": self.root,
            "page_root": self.page_root,
            "layout": self.layout,
            "obsidian_enabled": self.obsidian_enabled,
            "obsidian_files": self.obsidian_files,
            "total_pages": self.total_pages,
            "page_type_counts": self.page_type_counts,
            "pages_with_frontmatter": self.pages_with_frontmatter,
            "pages_without_frontmatter": self.pages_without_frontmatter,
            "pages_with_wikilinks": self.pages_with_wikilinks,
            "pages_without_wikilinks": self.pages_without_wikilinks,
            "total_outbound_links": self.total_outbound_links,
            "resolved_link_count": self.resolved_link_count,
            "resolved_file_link_count": self.resolved_file_link_count,
            "unresolved_link_count": self.unresolved_link_count,
            "orphan_page_count": self.orphan_page_count,
            "duplicate_titles": self.duplicate_titles,
            "thin_pages": self.thin_pages,
            "knowledge_page_count": self.knowledge_page_count,
            "knowledge_pages_with_non_nav_inbound": self.knowledge_pages_with_non_nav_inbound,
            "navigation_bottlenecks": self.navigation_bottlenecks,
            "raw_file_count": self.raw_file_count,
            "raw_files_without_source_notes": self.raw_files_without_source_notes,
            "source_pages_without_raw_links": self.source_pages_without_raw_links,
            "auxiliary_markdown_files": self.auxiliary_markdown_files,
            "empty_markdown_files": self.empty_markdown_files,
            "stale_navigation_pages": self.stale_navigation_pages,
            "issues": [issue.to_dict() for issue in self.issues],
            "pages": [page.to_dict(include_body=include_body) for page in self.pages],
        }


@dataclass
class SearchHit:
    title: str
    relative_path: str
    page_type: str
    score: int
    reasons: list[str]
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "relative_path": self.relative_path,
            "page_type": self.page_type,
            "score": self.score,
            "reasons": self.reasons,
            "snippet": self.snippet,
        }


@dataclass
class PageNeighborhood:
    page: VaultPage
    backlinks: list[str]
    outbound_pages: list[str]
    outbound_files: list[str]
    supporting_source_pages: list[str]
    related_pages: list[str]
    cited_source_pages: list[str]
    unresolved_targets: list[str]

    def to_dict(self, include_body: bool = False) -> dict[str, Any]:
        return {
            "page": self.page.to_dict(include_body=include_body),
            "backlinks": self.backlinks,
            "outbound_pages": self.outbound_pages,
            "outbound_files": self.outbound_files,
            "supporting_source_pages": self.supporting_source_pages,
            "related_pages": self.related_pages,
            "cited_source_pages": self.cited_source_pages,
            "unresolved_targets": self.unresolved_targets,
        }


@dataclass
class GraphNode:
    title: str
    relative_path: str
    page_type: str
    inbound_count: int
    outbound_count: int
    unresolved_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "relative_path": self.relative_path,
            "page_type": self.page_type,
            "inbound_count": self.inbound_count,
            "outbound_count": self.outbound_count,
            "unresolved_count": self.unresolved_count,
        }


@dataclass
class GraphEdge:
    source: str
    target: str
    target_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "target_kind": self.target_kind,
        }


@dataclass
class GraphReport:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


class ObsidianConnector:
    """Scan a markdown vault and surface Obsidian-relevant graph quality signals."""

    def __init__(self, root: Path) -> None:
        self.root = discover_vault_root(root)
        self.page_root, self.layout = self._detect_layout(self.root)
        self.obsidian_dir = self.root / ".obsidian"
        self._pages: list[VaultPage] | None = None
        self._page_by_locator: dict[str, list[VaultPage]] = {}
        self._page_by_id: dict[str, VaultPage] = {}
        self._source_pages_by_source_id: dict[str, list[VaultPage]] = {}
        self._file_by_locator: dict[str, list[str]] = {}

    def inspect(self) -> VaultReport:
        pages = self.scan()
        page_type_counts: dict[str, int] = {}
        duplicate_titles: dict[str, list[str]] = {}
        thin_pages: list[str] = []
        title_groups: dict[str, list[VaultPage]] = {}
        navigation_bottlenecks: list[str] = []
        knowledge_pages_with_non_nav_inbound = 0

        total_outbound_links = 0
        pages_with_frontmatter = 0
        pages_with_wikilinks = 0
        resolved_link_count = 0
        resolved_file_link_count = 0
        unresolved_link_count = 0
        orphan_page_count = 0
        knowledge_pages = [page for page in pages if page.page_type in CONTENT_PAGE_TYPES]

        for page in pages:
            page_type_counts[page.page_type] = page_type_counts.get(page.page_type, 0) + 1
            title_groups.setdefault(_normalize_key(page.title), []).append(page)
            if page.has_frontmatter:
                pages_with_frontmatter += 1
            if page.outbound_link_targets:
                pages_with_wikilinks += 1
            total_outbound_links += len(page.outbound_link_targets)
            resolved_link_count += len(page.resolved_outbound_links)
            resolved_file_link_count += len(page.resolved_file_links)
            unresolved_link_count += len(page.unresolved_outbound_links)
            if not page.inbound_links and not page.resolved_outbound_links:
                orphan_page_count += 1
            if page.page_type in ARTICLE_PAGE_TYPES | {"comparison"} and page.word_count < 120:
                thin_pages.append(page.relative_path)
            if page in knowledge_pages:
                if self._has_non_nav_inbound(page):
                    knowledge_pages_with_non_nav_inbound += 1
                else:
                    navigation_bottlenecks.append(page.relative_path)

        for grouped_pages in title_groups.values():
            if len(grouped_pages) > 1:
                duplicate_titles[grouped_pages[0].title] = [
                    page.relative_path for page in grouped_pages
                ]

        raw_files = self._iter_raw_files()
        raw_file_paths = sorted(str(path.relative_to(self.root)).replace("\\", "/") for path in raw_files)
        linked_raw_files = {
            raw_link
            for page in pages
            if page.page_type == "source"
            for raw_link in page.resolved_file_links
            if raw_link.startswith("raw/")
        }
        raw_files_without_source_notes = sorted(
            path for path in raw_file_paths if path not in linked_raw_files
        )
        source_pages_without_raw_links = sorted(
            page.relative_path
            for page in pages
            if page.page_type == "source"
            and not any(target.startswith("raw/") for target in page.resolved_file_links)
        )
        auxiliary_markdown_files = self._iter_auxiliary_markdown_files()
        empty_markdown_files = sorted(
            relative_path
            for relative_path in auxiliary_markdown_files
            if (self.root / relative_path).stat().st_size == 0
        )
        stale_navigation_pages = self._find_stale_navigation_pages(
            pages,
            has_material=any(page.page_type not in NAV_PAGE_TYPES for page in pages),
        )

        issues = self._build_issues(
            pages=pages,
            duplicate_titles=duplicate_titles,
            thin_pages=thin_pages,
            total_outbound_links=total_outbound_links,
            unresolved_link_count=unresolved_link_count,
            orphan_page_count=orphan_page_count,
            knowledge_pages=knowledge_pages,
            navigation_bottlenecks=navigation_bottlenecks,
            raw_files_without_source_notes=raw_files_without_source_notes,
            source_pages_without_raw_links=source_pages_without_raw_links,
            empty_markdown_files=empty_markdown_files,
            stale_navigation_pages=stale_navigation_pages,
        )

        obsidian_files = []
        if self.obsidian_dir.is_dir():
            obsidian_files = sorted(
                entry.name for entry in self.obsidian_dir.iterdir() if entry.is_file()
            )

        return VaultReport(
            root=str(self.root),
            page_root=str(self.page_root),
            layout=self.layout,
            obsidian_enabled=self.obsidian_dir.is_dir(),
            obsidian_files=obsidian_files,
            total_pages=len(pages),
            page_type_counts=dict(sorted(page_type_counts.items())),
            pages_with_frontmatter=pages_with_frontmatter,
            pages_without_frontmatter=len(pages) - pages_with_frontmatter,
            pages_with_wikilinks=pages_with_wikilinks,
            pages_without_wikilinks=len(pages) - pages_with_wikilinks,
            total_outbound_links=total_outbound_links,
            resolved_link_count=resolved_link_count,
            resolved_file_link_count=resolved_file_link_count,
            unresolved_link_count=unresolved_link_count,
            orphan_page_count=orphan_page_count,
            duplicate_titles=duplicate_titles,
            thin_pages=thin_pages,
            knowledge_page_count=len(knowledge_pages),
            knowledge_pages_with_non_nav_inbound=knowledge_pages_with_non_nav_inbound,
            navigation_bottlenecks=navigation_bottlenecks,
            raw_file_count=len(raw_file_paths),
            raw_files_without_source_notes=raw_files_without_source_notes,
            source_pages_without_raw_links=source_pages_without_raw_links,
            auxiliary_markdown_files=auxiliary_markdown_files,
            empty_markdown_files=empty_markdown_files,
            stale_navigation_pages=stale_navigation_pages,
            issues=issues,
            pages=pages,
        )

    def get_page(self, locator: str) -> VaultPage:
        pages = self.scan()
        locator_path = Path(locator)
        if locator_path.suffix.lower() == ".md":
            direct_candidates = [
                page for page in pages
                if page.relative_path == locator.replace("\\", "/")
                or page.relative_path.endswith(locator.replace("\\", "/"))
            ]
            if len(direct_candidates) == 1:
                return direct_candidates[0]

        lookup_key = _normalize_key(locator)
        candidates = self._page_by_locator.get(lookup_key, [])
        if not candidates:
            return self._resolve_fuzzy_page(locator, pages)
        if len(candidates) > 1:
            matches = ", ".join(page.relative_path for page in candidates[:4])
            raise ValueError(f"Ambiguous page locator '{locator}'. Matches: {matches}")
        return candidates[0]

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        page_type: str | None = None,
    ) -> list[SearchHit]:
        pages = self.scan()
        query = query.strip()
        query_key = _normalize_key(query)
        query_terms = _search_terms(query)
        hits: list[SearchHit] = []

        for page in pages:
            if page_type and page.page_type != page_type:
                continue
            score, reasons = self._score_page(page, query_key, query_terms)
            if query and score <= 0:
                continue
            hits.append(
                SearchHit(
                    title=page.title,
                    relative_path=page.relative_path,
                    page_type=page.page_type,
                    score=score,
                    reasons=reasons,
                    snippet=self._snippet_for_page(page, query_terms),
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.title.lower(), hit.relative_path))
        return hits[: max(limit, 1)]

    def get_neighborhood(self, locator: str) -> PageNeighborhood:
        page = self.get_page(locator)
        supporting_source_pages = self._resolve_supporting_source_pages(page)
        related_pages = self._resolve_related_pages(page)
        cited_source_pages = self._resolve_cited_source_pages(page)
        return PageNeighborhood(
            page=page,
            backlinks=page.inbound_links,
            outbound_pages=page.resolved_outbound_links,
            outbound_files=page.resolved_file_links,
            supporting_source_pages=supporting_source_pages,
            related_pages=related_pages,
            cited_source_pages=cited_source_pages,
            unresolved_targets=page.unresolved_outbound_links,
        )

    def graph(self) -> GraphReport:
        pages = self.scan()
        nodes = [
            GraphNode(
                title=page.title,
                relative_path=page.relative_path,
                page_type=page.page_type,
                inbound_count=len(page.inbound_links),
                outbound_count=len(page.resolved_outbound_links) + len(page.resolved_file_links),
                unresolved_count=len(page.unresolved_outbound_links),
            )
            for page in pages
        ]
        edges: list[GraphEdge] = []
        for page in pages:
            for target in page.resolved_outbound_links:
                edges.append(GraphEdge(source=page.title, target=target, target_kind="page"))
            for target in page.resolved_file_links:
                edges.append(GraphEdge(source=page.title, target=target, target_kind="file"))
        edges.sort(key=lambda edge: (edge.source.lower(), edge.target_kind, edge.target.lower()))
        return GraphReport(nodes=nodes, edges=edges)

    def upsert_page(
        self,
        *,
        title: str,
        body: str,
        page_type: str,
        tags: list[str] | None = None,
        sources: list[str] | None = None,
        aliases: list[str] | None = None,
        summary: str | None = None,
        relative_path: str | None = None,
    ) -> VaultPage:
        self.scan()

        existing_page: VaultPage | None = None
        if relative_path:
            try:
                existing_page = self.get_page(relative_path)
            except FileNotFoundError:
                existing_page = None
        else:
            lookup = self._page_by_locator.get(_normalize_key(title), [])
            if len(lookup) == 1:
                existing_page = lookup[0]
            elif len(lookup) > 1:
                matches = ", ".join(page.relative_path for page in lookup[:4])
                raise ValueError(f"Ambiguous page title '{title}'. Matches: {matches}")

        target_path = self._resolve_target_path(
            title=title,
            page_type=page_type,
            relative_path=relative_path,
            existing_page=existing_page,
        )
        frontmatter: dict[str, Any] = {}
        if existing_page is not None:
            frontmatter = dict(existing_page.frontmatter)

        now = datetime.now().astimezone().replace(microsecond=0).isoformat()
        created = str(frontmatter.get("created") or frontmatter.get("created_at") or now)
        effective_summary = (summary or str(frontmatter.get("summary", "")).strip() or self._summarize_body(body)).strip()

        normalized_tags = sorted({item.strip() for item in (tags or _coerce_list(frontmatter.get("tags"))) if item.strip()})
        normalized_sources = [item.strip() for item in (sources or _coerce_list(frontmatter.get("sources"))) if item.strip()]
        normalized_aliases = [item.strip() for item in (aliases or _coerce_list(frontmatter.get("aliases"))) if item.strip()]

        frontmatter["title"] = title
        frontmatter["type"] = page_type
        frontmatter["status"] = str(frontmatter.get("status") or self._default_status_for_type(page_type))
        frontmatter["updated"] = now
        if "created" in frontmatter or "created_at" not in frontmatter:
            frontmatter["created"] = created
        if effective_summary:
            frontmatter["summary"] = effective_summary
        if normalized_tags:
            frontmatter["tags"] = normalized_tags
        elif "tags" in frontmatter:
            frontmatter.pop("tags")
        if normalized_sources:
            frontmatter["sources"] = normalized_sources
        elif "sources" in frontmatter:
            frontmatter.pop("sources")
        if normalized_aliases:
            frontmatter["aliases"] = normalized_aliases
        elif "aliases" in frontmatter:
            frontmatter.pop("aliases")
        cssclasses = [item.strip() for item in _coerce_list(frontmatter.get("cssclasses")) if item.strip()]
        for item in (page_type, str(frontmatter["status"])):
            if item not in cssclasses:
                cssclasses.append(item)
        if cssclasses:
            frontmatter["cssclasses"] = cssclasses

        rendered_body = body.strip()
        if not rendered_body.startswith("# "):
            rendered_body = f"# {title}\n\n{rendered_body}"

        frontmatter_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(f"---\n{frontmatter_text}\n---\n\n{rendered_body.rstrip()}\n")
        self._invalidate_cache()
        return self.get_page(str(target_path.relative_to(self.root)).replace("\\", "/"))

    def scan(self) -> list[VaultPage]:
        if self._pages is not None:
            return self._pages

        pages = [self._parse_page(path) for path in self._iter_markdown_files()]
        title_lookup: dict[str, list[VaultPage]] = {}
        locator_lookup: dict[str, list[VaultPage]] = {}
        page_by_id: dict[str, VaultPage] = {}
        source_pages_by_source_id: dict[str, list[VaultPage]] = {}
        file_lookup: dict[str, list[str]] = {}

        for path in self._iter_all_files():
            relative = str(path.relative_to(self.root)).replace("\\", "/")
            keys = {
                _normalize_key(relative),
                _normalize_key(path.name),
                _normalize_key(path.stem),
            }
            for key in keys:
                if key:
                    file_lookup.setdefault(key, []).append(relative)

        for page in pages:
            for key in self._locator_keys_for_page(page):
                locator_lookup.setdefault(key, []).append(page)
            for key in self._title_keys_for_page(page):
                title_lookup.setdefault(key, []).append(page)
            page_id = str(page.frontmatter.get("id", "")).strip()
            if page_id:
                page_by_id[page_id] = page
            if page.page_type == "source":
                for source_id in _coerce_list(page.frontmatter.get("source_ids")):
                    source_pages_by_source_id.setdefault(source_id, []).append(page)

        for page in pages:
            resolved_titles: set[str] = set()
            resolved_files: set[str] = set()
            unresolved_titles: set[str] = set()
            inbound_targets: list[VaultPage] = []
            for raw_target in page.outbound_link_targets:
                target_key = _normalize_key(raw_target)
                matches = locator_lookup.get(target_key, [])
                if not matches:
                    file_matches = file_lookup.get(target_key, [])
                    if file_matches:
                        resolved_files.update(file_matches)
                    else:
                        unresolved_titles.add(raw_target)
                    continue
                resolved_titles.update(match.title for match in matches)
                inbound_targets.extend(matches)
            page.resolved_outbound_links = sorted(resolved_titles)
            page.resolved_file_links = sorted(resolved_files)
            page.unresolved_outbound_links = sorted(unresolved_titles)
            for target in inbound_targets:
                if page.title not in target.inbound_links:
                    target.inbound_links.append(page.title)

        for page in pages:
            page.inbound_links.sort()

        self._pages = sorted(pages, key=lambda page: page.relative_path)
        self._page_by_locator = locator_lookup
        self._page_by_id = page_by_id
        self._source_pages_by_source_id = source_pages_by_source_id
        self._file_by_locator = file_lookup
        return self._pages

    def _detect_layout(self, root: Path) -> tuple[Path, str]:
        if (root / ".compile" / "config.yaml").exists() and (root / "wiki").is_dir():
            return root / "wiki", "compile_workspace"
        if (root / "workspace.json").exists() and (root / "pages").is_dir():
            return root / "pages", "backend_workspace"
        if (root / "wiki").is_dir():
            return root / "wiki", "compile_like_workspace"
        if (root / "pages").is_dir():
            return root / "pages", "backend_like_workspace"
        return root, "generic_vault"

    def _resolve_target_path(
        self,
        *,
        title: str,
        page_type: str,
        relative_path: str | None,
        existing_page: VaultPage | None,
    ) -> Path:
        if existing_page is not None:
            return self.root / existing_page.relative_path
        if relative_path:
            return self.root / relative_path

        filename = f"{_safe_page_filename(title)}.md"
        if self.layout == "compile_workspace":
            folder_map = {
                "article": "wiki/articles",
                "source": "wiki/sources",
                "note": "wiki/articles",
                "concept": "wiki/articles",
                "entity": "wiki/articles",
                "question": "wiki/articles",
                "person": "wiki/articles",
                "place": "wiki/articles",
                "timeline": "wiki/articles",
                "map": "wiki/maps",
                "dashboard": "wiki/maps",
                "output": "wiki/outputs",
                "comparison": "wiki/outputs",
                "overview": "wiki",
                "index": "wiki",
                "log": "wiki",
            }
            base_dir = folder_map.get(page_type, "wiki")
            return self.root / base_dir / filename
        return self.page_root / filename

    def _resolve_fuzzy_page(self, locator: str, pages: list[VaultPage]) -> VaultPage:
        hits = self.search(locator, limit=5)
        if not hits:
            raise FileNotFoundError(locator)

        top_hit = hits[0]
        if not self._is_high_confidence_locator_hit(top_hit):
            raise FileNotFoundError(locator)

        if len(hits) > 1 and self._is_competing_locator_hit(top_hit, hits[1]):
            matches = ", ".join(hit.relative_path for hit in hits[:4])
            raise ValueError(f"Ambiguous page locator '{locator}'. Matches: {matches}")

        for page in pages:
            if page.relative_path == top_hit.relative_path:
                return page
        raise FileNotFoundError(locator)

    def _summarize_body(self, body: str) -> str:
        for line in LINE_RE.split(body):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("<!-- compile:") and stripped != "<!-- /compile:section -->":
                return stripped[:160]
        return ""

    def _invalidate_cache(self) -> None:
        self._pages = None
        self._page_by_locator = {}
        self._page_by_id = {}
        self._source_pages_by_source_id = {}
        self._file_by_locator = {}

    def _default_status_for_type(self, page_type: str) -> str:
        if page_type in {"source", "map", "dashboard", "output", "comparison", "overview", "index", "log"}:
            return "stable"
        return "seed"

    def _iter_markdown_files(self) -> list[Path]:
        paths: list[Path] = []
        for current_root, dirs, files in os.walk(self.page_root):
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
            for filename in files:
                if filename.startswith(".") or not filename.lower().endswith(".md"):
                    continue
                paths.append(Path(current_root) / filename)
        return sorted(paths)

    def _iter_all_files(self) -> list[Path]:
        paths: list[Path] = []
        for current_root, dirs, files in os.walk(self.root):
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
            for filename in files:
                if filename.startswith("."):
                    continue
                paths.append(Path(current_root) / filename)
        return sorted(paths)

    def _parse_page(self, path: Path) -> VaultPage:
        frontmatter, body, has_frontmatter = _parse_markdown(path)
        title = _extract_title(path, body, frontmatter)
        relative_path = str(path.relative_to(self.root)).replace("\\", "/")
        page_type = str(
            frontmatter.get("type") or frontmatter.get("page_type") or "unknown"
        ).strip() or "unknown"
        if page_type == "unknown":
            page_type = _inferred_page_type(relative_path)
        tags = _coerce_list(frontmatter.get("tags"))
        aliases = _coerce_list(frontmatter.get("aliases"))
        outbound_link_targets = extract_wikilinks(body)
        word_count = len(WORD_RE.findall(body))
        return VaultPage(
            title=title,
            relative_path=relative_path,
            page_type=page_type,
            tags=tags,
            aliases=aliases,
            has_frontmatter=has_frontmatter,
            word_count=word_count,
            frontmatter=frontmatter,
            body=body.strip(),
            outbound_link_targets=outbound_link_targets,
        )

    def _locator_keys_for_page(self, page: VaultPage) -> set[str]:
        keys = {
            _normalize_key(page.title),
            _normalize_key(page.relative_path),
            _normalize_key(Path(page.relative_path).name),
            _normalize_key(Path(page.relative_path).stem),
        }
        for alias in page.aliases:
            keys.add(_normalize_key(alias))
        return {key for key in keys if key}

    def _title_keys_for_page(self, page: VaultPage) -> set[str]:
        keys = {_normalize_key(page.title), _normalize_key(Path(page.relative_path).stem)}
        for alias in page.aliases:
            keys.add(_normalize_key(alias))
        return {key for key in keys if key}

    def _is_high_confidence_locator_hit(self, hit: SearchHit) -> bool:
        if hit.score < 70:
            return False
        strong_reasons = {"exact-title", "exact-alias", "title-match", "alias-match", "path-match"}
        return any(reason in strong_reasons for reason in hit.reasons)

    def _is_competing_locator_hit(self, top_hit: SearchHit, next_hit: SearchHit) -> bool:
        if next_hit.score <= 0:
            return False
        return next_hit.score >= top_hit.score - 25

    def _score_page(
        self,
        page: VaultPage,
        query_key: str,
        query_terms: list[str],
    ) -> tuple[int, list[str]]:
        if not query_key and not query_terms:
            return (1, ["all-pages"])

        score = 0
        reasons: list[str] = []
        title_key = _normalize_key(page.title)
        path_key = _normalize_key(page.relative_path)
        body_lower = page.body.lower()
        tags_lower = [tag.lower() for tag in page.tags]
        aliases_lower = [_normalize_key(alias) for alias in page.aliases]

        if query_key and title_key == query_key:
            score += 120
            reasons.append("exact-title")
        elif query_key and query_key in title_key:
            score += 80
            reasons.append("title-match")

        if query_key and any(alias == query_key for alias in aliases_lower):
            score += 70
            reasons.append("exact-alias")
        elif query_key and any(query_key in alias for alias in aliases_lower):
            score += 40
            reasons.append("alias-match")

        if query_key and query_key in path_key:
            score += 45
            reasons.append("path-match")

        if query_terms:
            title_terms = set(_search_terms(page.title))
            alias_terms = set(_search_terms(" ".join(page.aliases)))
            tag_terms = set(_search_terms(" ".join(page.tags)))
            body_terms = _search_terms(page.body)
            body_term_set = set(body_terms)
            overlap = set(query_terms) & title_terms
            if overlap:
                score += 16 * len(overlap)
                reasons.append("title-terms")
            alias_overlap = set(query_terms) & alias_terms
            if alias_overlap:
                score += 10 * len(alias_overlap)
                reasons.append("alias-terms")
            tag_overlap = set(query_terms) & tag_terms
            if tag_overlap:
                score += 8 * len(tag_overlap)
                reasons.append("tag-match")
            body_overlap = set(query_terms) & body_term_set
            if body_overlap:
                score += 4 * len(body_overlap)
                reasons.append("body-match")
            if all(term in body_lower for term in query_terms):
                score += 12
                reasons.append("all-terms")

        # Reward pages with a meaningful graph footprint when scores tie.
        score += min(len(page.resolved_outbound_links) + len(page.inbound_links), 6)
        return score, reasons

    def _snippet_for_page(self, page: VaultPage, query_terms: list[str]) -> str:
        summary = str(page.frontmatter.get("summary", "")).strip()
        candidates = [summary, *LINE_RE.split(page.body)]
        for line in candidates:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not query_terms or any(term in stripped.lower() for term in query_terms):
                return stripped[:180]
        return summary or page.body[:180].strip()

    def _resolve_supporting_source_pages(self, page: VaultPage) -> list[str]:
        titles: set[str] = set()
        for source_id in _coerce_list(page.frontmatter.get("source_ids")):
            for source_page in self._source_pages_by_source_id.get(source_id, []):
                titles.add(source_page.title)
        for source_title in _coerce_list(page.frontmatter.get("sources")):
            matches = self._page_by_locator.get(_normalize_key(source_title), [])
            for match in matches:
                if match.page_type == "source":
                    titles.add(match.title)
        if page.page_type == "source":
            titles.discard(page.title)
        return sorted(titles)

    def _resolve_related_pages(self, page: VaultPage) -> list[str]:
        titles: set[str] = set()
        for page_id in _coerce_list(page.frontmatter.get("related_page_ids")):
            related_page = self._page_by_id.get(page_id)
            if related_page and related_page.title != page.title:
                titles.add(related_page.title)
        return sorted(titles)

    def _resolve_cited_source_pages(self, page: VaultPage) -> list[str]:
        titles: set[str] = set()
        citations = page.frontmatter.get("citations")
        if isinstance(citations, list):
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                for source_id in _coerce_list(citation.get("source_id")):
                    for source_page in self._source_pages_by_source_id.get(source_id, []):
                        titles.add(source_page.title)
                for source_title in _coerce_list(citation.get("source_title")):
                    matches = self._page_by_locator.get(_normalize_key(source_title), [])
                    for match in matches:
                        if match.page_type == "source":
                            titles.add(match.title)
        if page.page_type == "source":
            titles.discard(page.title)
        return sorted(titles)

    def _has_non_nav_inbound(self, page: VaultPage) -> bool:
        for inbound_title in page.inbound_links:
            for inbound_page in self._page_by_locator.get(_normalize_key(inbound_title), []):
                if inbound_page.page_type not in NAV_PAGE_TYPES:
                    return True
        return False

    def _iter_raw_files(self) -> list[Path]:
        raw_root = self.root / "raw"
        if not raw_root.is_dir():
            return []

        files: list[Path] = []
        for current_root, dirs, filenames in os.walk(raw_root):
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                files.append(Path(current_root) / filename)
        return sorted(files)

    def _iter_auxiliary_markdown_files(self) -> list[str]:
        if self.page_root == self.root:
            return []

        page_root_prefix = f"{self.page_root.relative_to(self.root).as_posix().strip('/')}/"
        auxiliary_files: list[str] = []
        for path in self._iter_all_files():
            relative = str(path.relative_to(self.root)).replace("\\", "/")
            if not relative.lower().endswith(".md"):
                continue
            if relative.startswith(page_root_prefix) or relative.startswith("raw/"):
                continue
            auxiliary_files.append(relative)
        return sorted(auxiliary_files)

    def list_auxiliary_markdown_files(self, *, empty_only: bool = False) -> list[str]:
        auxiliary_files = self._iter_auxiliary_markdown_files()
        if not empty_only:
            return auxiliary_files
        return [
            relative_path
            for relative_path in auxiliary_files
            if (self.root / relative_path).stat().st_size == 0
        ]

    def cleanup_empty_auxiliary_markdown_files(self) -> list[str]:
        moved_paths: list[str] = []
        quarantine_root = self.root / ".compile" / "quarantine"
        for relative_path in self.list_auxiliary_markdown_files(empty_only=True):
            source = self.root / relative_path
            destination = quarantine_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved_paths.append(relative_path)
        if moved_paths:
            self._invalidate_cache()
        return moved_paths

    def _find_stale_navigation_pages(
        self,
        pages: list[VaultPage],
        *,
        has_material: bool,
    ) -> list[str]:
        if not has_material:
            return []

        stale_pages: list[str] = []
        for page in pages:
            body = page.body
            if page.page_type == "overview" and any(marker in body for marker in STALE_OVERVIEW_MARKERS):
                stale_pages.append(page.relative_path)
            if page.page_type == "index" and any(marker in body for marker in STALE_INDEX_MARKERS):
                stale_pages.append(page.relative_path)
        return sorted(stale_pages)

    def _build_issues(
        self,
        *,
        pages: list[VaultPage],
        duplicate_titles: dict[str, list[str]],
        thin_pages: list[str],
        total_outbound_links: int,
        unresolved_link_count: int,
        orphan_page_count: int,
        knowledge_pages: list[VaultPage],
        navigation_bottlenecks: list[str],
        raw_files_without_source_notes: list[str],
        source_pages_without_raw_links: list[str],
        empty_markdown_files: list[str],
        stale_navigation_pages: list[str],
    ) -> list[VaultIssue]:
        issues: list[VaultIssue] = []

        if not pages:
            issues.append(
                VaultIssue(
                    code="empty_vault",
                    severity="high",
                    message="No markdown pages were found in the selected vault root.",
                )
            )
            return issues

        if not self.obsidian_dir.is_dir():
            severity = "high" if self.layout.startswith("backend") else "medium"
            issues.append(
                VaultIssue(
                    code="missing_obsidian_config",
                    severity=severity,
                    message=(
                        "No .obsidian directory was found, so this workspace is not a ready-to-open "
                        "Obsidian vault."
                    ),
                )
            )

        if total_outbound_links == 0:
            issues.append(
                VaultIssue(
                    code="no_wikilinks",
                    severity="high",
                    message=(
                        "No double-bracket wikilinks were found in page bodies. Obsidian graph, backlinks, "
                        "and page-to-page traversal will be effectively empty."
                    ),
                    pages=[page.relative_path for page in pages[:12]],
                )
            )
        elif total_outbound_links / max(len(pages), 1) < 0.75:
            issues.append(
                VaultIssue(
                    code="sparse_graph",
                    severity="medium",
                    message=(
                        "The vault graph is sparse relative to page count. Cross-links are present "
                        "but not dense enough to make navigation efficient."
                    ),
                )
            )

        if unresolved_link_count:
            affected_pages = [
                page.relative_path for page in pages if page.unresolved_outbound_links
            ]
            issues.append(
                VaultIssue(
                    code="unresolved_links",
                    severity="medium",
                    message=(
                        f"{unresolved_link_count} wikilink target(s) do not resolve to known pages."
                    ),
                    pages=affected_pages[:12],
                )
            )

        if duplicate_titles:
            duplicate_pages: list[str] = []
            for paths in duplicate_titles.values():
                duplicate_pages.extend(paths)
            issues.append(
                VaultIssue(
                    code="duplicate_titles",
                    severity="medium",
                    message="Duplicate titles or aliases make wikilinks ambiguous.",
                    pages=duplicate_pages[:12],
                )
            )

        if orphan_page_count and len(pages) >= 5:
            issues.append(
                VaultIssue(
                    code="orphan_pages",
                    severity="medium",
                    message=(
                        f"{orphan_page_count} page(s) have no inbound or outbound graph connection."
                    ),
                    pages=[
                        page.relative_path
                        for page in pages
                        if not page.inbound_links and not page.resolved_outbound_links
                    ][:12],
                )
            )

        if thin_pages:
            issues.append(
                VaultIssue(
                    code="thin_synthesis_pages",
                    severity="low",
                    message=(
                        "Some article-like pages are very short and may not add much beyond a stub."
                    ),
                    pages=thin_pages[:12],
                )
            )

        if stale_navigation_pages:
            issues.append(
                VaultIssue(
                    code="stale_navigation_pages",
                    severity="medium",
                    message=(
                        "Navigation pages still contain initialization boilerplate even though the vault has material."
                    ),
                    pages=stale_navigation_pages[:12],
                )
            )

        if raw_files_without_source_notes:
            issues.append(
                VaultIssue(
                    code="raw_files_without_source_notes",
                    severity="medium",
                    message=(
                        f"{len(raw_files_without_source_notes)} raw file(s) are not linked from any source page, "
                        "so provenance is incomplete."
                    ),
                    pages=raw_files_without_source_notes[:12],
                )
            )

        if source_pages_without_raw_links:
            issues.append(
                VaultIssue(
                    code="source_pages_without_raw_links",
                    severity="medium",
                    message=(
                        "Some source pages do not link back to a concrete raw artifact."
                    ),
                    pages=source_pages_without_raw_links[:12],
                )
            )

        if knowledge_pages and len(navigation_bottlenecks) >= max(2, len(knowledge_pages) // 3):
            issues.append(
                VaultIssue(
                    code="navigation_bottlenecks",
                    severity="medium",
                    message=(
                        "Several content pages rely on Index/Overview/Log as their only backlinks, which weakens "
                        "Obsidian navigation and graph value."
                    ),
                    pages=navigation_bottlenecks[:12],
                )
            )

        if empty_markdown_files:
            issues.append(
                VaultIssue(
                    code="empty_markdown_files",
                    severity="low",
                    message="Some markdown files outside the maintained wiki are empty and may confuse vault navigation.",
                    pages=empty_markdown_files[:12],
                )
            )

        return issues
