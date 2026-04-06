"""Benchmark evaluation for Compile wiki quality.

Scores a workspace across six dimensions:
  - citation_density: ratio of claim sentences with [[wikilinks]] to total claim sentences
  - equation_preservation: ratio of equations surviving from source packets into wiki pages
  - table_preservation: ratio of tables surviving from source packets into wiki pages
  - duplicate_concept_rate: fraction of concept pages that are near-duplicates
  - maturity_accuracy: fraction of stable pages that actually have >=2 raw sources
  - filler_density: fraction of body paragraphs containing filler language

Usage:
  compile eval                          # score the current workspace
  compile eval --rebuild                # init fresh workspace, copy benchmark PDFs, ingest, score
  compile eval --rebuild --limit 5      # rebuild with only first 5 corpus PDFs
  compile eval --json-output            # machine-readable output
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


from compile.verify import FILLER_PHRASES

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*?)?\]\]")
EQUATION_BLOCK_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)
EQUATION_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
TABLE_ROW_RE = re.compile(r"^\|.+\|$", re.MULTILINE)


@dataclass
class PageScore:
    path: str
    page_type: str
    title: str
    citation_density: float = 0.0
    equation_count: int = 0
    table_row_count: int = 0
    filler_count: int = 0
    paragraph_count: int = 0
    has_provisional_notice: bool = False
    status: str = ""
    source_count: int = 0
    word_count: int = 0


@dataclass
class EvalReport:
    workspace_root: str = ""
    page_count: int = 0
    source_page_count: int = 0
    concept_page_count: int = 0
    entity_page_count: int = 0
    question_page_count: int = 0
    output_page_count: int = 0

    # Aggregate scores (0.0 - 1.0 where higher is better, except filler/duplicate which are lower-is-better)
    citation_density: float = 0.0
    equation_preservation: float = 0.0
    table_preservation: float = 0.0
    duplicate_concept_rate: float = 0.0
    maturity_accuracy: float = 1.0
    filler_density: float = 0.0

    # Raw counts
    total_equations_in_sources: int = 0
    total_equations_in_wiki: int = 0
    total_tables_in_sources: int = 0
    total_tables_in_wiki: int = 0
    total_claim_sentences: int = 0
    total_cited_sentences: int = 0
    total_paragraphs: int = 0
    total_filler_hits: int = 0
    stable_pages: int = 0
    stable_with_enough_sources: int = 0
    duplicate_concept_pairs: list[tuple[str, str]] = field(default_factory=list)

    # Per-page breakdown
    page_scores: list[PageScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["page_scores"] = [asdict(ps) for ps in self.page_scores]
        return result

    def summary_lines(self) -> list[str]:
        lines = [
            f"Pages: {self.page_count} (src={self.source_page_count} con={self.concept_page_count} ent={self.entity_page_count} q={self.question_page_count} out={self.output_page_count})",
            f"Citation density:      {self.citation_density:.2%}  ({self.total_cited_sentences}/{self.total_claim_sentences} sentences)",
            f"Equation preservation: {self.equation_preservation:.2%}  ({self.total_equations_in_wiki}/{self.total_equations_in_sources} equations)",
            f"Table preservation:    {self.table_preservation:.2%}  ({self.total_tables_in_wiki}/{self.total_tables_in_sources} tables)",
            f"Duplicate concept rate: {self.duplicate_concept_rate:.2%}  ({len(self.duplicate_concept_pairs)} pairs)",
            f"Maturity accuracy:     {self.maturity_accuracy:.2%}  ({self.stable_with_enough_sources}/{self.stable_pages} stable pages)",
            f"Filler density:        {self.filler_density:.2%}  ({self.total_filler_hits}/{self.total_paragraphs} paragraphs)",
        ]
        return lines

    def grade(self) -> str:
        """Single-letter grade based on aggregate scores."""
        score = 0.0
        score += min(self.citation_density, 1.0) * 25  # 25 points
        score += min(self.equation_preservation, 1.0) * 15  # 15 points
        score += min(self.table_preservation, 1.0) * 10  # 10 points
        score += (1.0 - min(self.duplicate_concept_rate, 1.0)) * 15  # 15 points
        score += min(self.maturity_accuracy, 1.0) * 20  # 20 points
        score += (1.0 - min(self.filler_density, 1.0)) * 15  # 15 points

        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        fm_text, body = text[4:].split("\n---\n", 1)
        try:
            return yaml.safe_load(fm_text) or {}, body
        except yaml.YAMLError:
            return {}, text
    return {}, text


def _page_type_from_path(rel_path: str) -> str:
    """Infer page type from wiki subdirectory."""
    for prefix in ("sources/", "concepts/", "entities/", "questions/", "outputs/", "dashboards/"):
        if rel_path.startswith(prefix):
            return prefix.rstrip("/")
    return "other"


def _count_equations(text: str) -> int:
    blocks = len(EQUATION_BLOCK_RE.findall(text))
    inlines = len(EQUATION_INLINE_RE.findall(text))
    return blocks + inlines


def _count_table_rows(text: str) -> int:
    """Count data rows in markdown tables (excludes separator rows)."""
    rows = TABLE_ROW_RE.findall(text)
    # Exclude separator rows like |---|---| or |:---|---:|
    data_rows = [r for r in rows if not re.match(r"^\|[\s\-:|]+\|$", r)]
    return len(data_rows)


def _extract_paragraphs(body: str) -> list[str]:
    """Extract non-empty paragraphs from markdown body (skip headings, lists, code blocks)."""
    paragraphs: list[str] = []
    in_code_block = False
    current: list[str] = []

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        # Skip headings, list items, blockquotes, frontmatter markers, HTML comments
        if stripped.startswith(("#", "- ", "* ", "> ", "---", "<!--", "|")):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(stripped)

    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _is_claim_sentence(sentence: str) -> bool:
    """Heuristic: a claim sentence makes a factual assertion (not a heading, link list, or label)."""
    s = sentence.strip()
    if len(s) < 20:
        return False
    if s.startswith(("#", "-", "*", ">", "|", "!")):
        return False
    # Must contain a verb-like word (very rough heuristic)
    return bool(re.search(r"\b(is|are|was|were|has|have|had|can|could|should|will|would|use[sd]?|show[sd]?|found|demonstrate[sd]?|achieve[sd]?|reduce[sd]?|improve[sd]?|increase[sd]?|decrease[sd]?|produce[sd]?|require[sd]?|enable[sd]?|support[sd]?|provide[sd]?|introduce[sd]?|propose[sd]?|present[sd]?|report[sd]?|perform[sd]?|outperform[sd]?|indicate[sd]?|suggest[sd]?|allow[sd]?|address|combine[sd]?|extend[sd]?|implement[sd]?|apply|relies?|depend[sd]?|operate[sd]?|process|train[sd]?|learn[sd]?|generate[sd]?|compute[sd]?|optimize[sd]?|minimize[sd]?|maximize[sd]?)\b", s, re.IGNORECASE))


def _citation_density_for_page(body: str) -> tuple[int, int]:
    """Return (cited_sentences, total_claim_sentences) for knowledge pages."""
    paragraphs = _extract_paragraphs(body)
    total_claims = 0
    cited_claims = 0
    for paragraph in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        for sentence in sentences:
            if _is_claim_sentence(sentence):
                total_claims += 1
                if WIKILINK_RE.search(sentence):
                    cited_claims += 1
    return cited_claims, total_claims


def _count_filler(body: str) -> int:
    lowered = body.casefold()
    return sum(1 for phrase in FILLER_PHRASES if phrase in lowered)


def _normalize_title_tokens(title: str) -> set[str]:
    lowered = title.strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s]", "", lowered)
    return set(cleaned.split())


def _find_duplicate_concepts(concept_pages: list[PageScore], threshold: float = 0.75) -> list[tuple[str, str]]:
    """Find concept pages with high token overlap in titles."""
    pairs: list[tuple[str, str]] = []
    token_sets = [(ps.title, _normalize_title_tokens(ps.title)) for ps in concept_pages]
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            title_a, tokens_a = token_sets[i]
            title_b, tokens_b = token_sets[j]
            if not tokens_a or not tokens_b:
                continue
            intersection = tokens_a & tokens_b
            union = tokens_a | tokens_b
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= threshold:
                pairs.append((title_a, title_b))
    return pairs


# ---------------------------------------------------------------------------
# Source packet equation/table counting
# ---------------------------------------------------------------------------


def _count_source_equations(source_packets_dir: Path) -> int:
    """Count equations across all source packets."""
    total = 0
    if not source_packets_dir.exists():
        return 0
    import json
    for packet_path in source_packets_dir.glob("*.json"):
        try:
            data = json.loads(packet_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # Count from analysis text
        analysis_text = data.get("analysis_text", "") or data.get("full_text", "")
        total += _count_equations(analysis_text)
    return total


def _count_source_tables(source_packets_dir: Path) -> int:
    """Count table rows across all source packets."""
    total = 0
    if not source_packets_dir.exists():
        return 0
    import json
    for packet_path in source_packets_dir.glob("*.json"):
        try:
            data = json.loads(packet_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        analysis_text = data.get("analysis_text", "") or data.get("full_text", "")
        total += _count_table_rows(analysis_text)
    return total


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def evaluate_workspace(workspace_root: Path) -> EvalReport:
    """Score an entire Compile workspace."""
    wiki_dir = workspace_root / "wiki"
    source_packets_dir = workspace_root / ".compile" / "source-packets"

    report = EvalReport(workspace_root=str(workspace_root))

    if not wiki_dir.exists():
        return report

    # Collect all wiki pages
    page_scores: list[PageScore] = []
    for md_path in sorted(wiki_dir.rglob("*.md")):
        rel = str(md_path.relative_to(wiki_dir))
        # Skip navigation pages
        if rel in ("index.md", "overview.md", "log.md"):
            continue

        content = md_path.read_text()
        fm, body = _parse_frontmatter(content)

        page_type = _page_type_from_path(rel)
        title = str(fm.get("title") or md_path.stem.replace("-", " ").title())
        status = str(fm.get("status") or "")

        # Source count from frontmatter
        sources_field = fm.get("sources") or fm.get("source_ids") or []
        if isinstance(sources_field, list):
            source_count = len(sources_field)
        else:
            source_count = 1 if sources_field else 0

        # Citation density (for concept, entity, question pages)
        cited, total_claims = (0, 0)
        if page_type in ("concepts", "entities", "questions"):
            cited, total_claims = _citation_density_for_page(body)

        paragraphs = _extract_paragraphs(body)
        filler = _count_filler(body)
        equations = _count_equations(body)
        tables = _count_table_rows(body)
        word_count = len(re.findall(r"\b[\w'-]+\b", body))

        ps = PageScore(
            path=rel,
            page_type=page_type,
            title=title,
            citation_density=cited / total_claims if total_claims else 0.0,
            equation_count=equations,
            table_row_count=tables,
            filler_count=filler,
            paragraph_count=len(paragraphs),
            has_provisional_notice="Provisional" in content,
            status=status,
            source_count=source_count,
            word_count=word_count,
        )
        page_scores.append(ps)

    report.page_scores = page_scores
    report.page_count = len(page_scores)

    # Count by type
    for ps in page_scores:
        if ps.page_type == "sources":
            report.source_page_count += 1
        elif ps.page_type == "concepts":
            report.concept_page_count += 1
        elif ps.page_type == "entities":
            report.entity_page_count += 1
        elif ps.page_type == "questions":
            report.question_page_count += 1
        elif ps.page_type == "outputs":
            report.output_page_count += 1

    # Aggregate citation density (knowledge pages only)
    knowledge_pages = [ps for ps in page_scores if ps.page_type in ("concepts", "entities", "questions")]
    for ps in knowledge_pages:
        cited, total = _citation_density_for_page(
            _parse_frontmatter((workspace_root / "wiki" / ps.path).read_text())[1]
        )
        report.total_cited_sentences += cited
        report.total_claim_sentences += total
    report.citation_density = (
        report.total_cited_sentences / report.total_claim_sentences
        if report.total_claim_sentences
        else 0.0
    )

    # Equation preservation
    report.total_equations_in_sources = _count_source_equations(source_packets_dir)
    report.total_equations_in_wiki = sum(ps.equation_count for ps in page_scores)
    report.equation_preservation = (
        report.total_equations_in_wiki / report.total_equations_in_sources
        if report.total_equations_in_sources
        else 1.0  # No equations expected = perfect score
    )

    # Table preservation
    report.total_tables_in_sources = _count_source_tables(source_packets_dir)
    report.total_tables_in_wiki = sum(ps.table_row_count for ps in page_scores)
    report.table_preservation = (
        report.total_tables_in_wiki / report.total_tables_in_sources
        if report.total_tables_in_sources
        else 1.0
    )

    # Duplicate concepts
    concept_scores = [ps for ps in page_scores if ps.page_type == "concepts"]
    report.duplicate_concept_pairs = _find_duplicate_concepts(concept_scores)
    report.duplicate_concept_rate = (
        len(report.duplicate_concept_pairs) / len(concept_scores)
        if concept_scores
        else 0.0
    )

    # Maturity accuracy
    for ps in page_scores:
        if ps.status == "stable" and ps.page_type in ("concepts", "entities", "questions"):
            report.stable_pages += 1
            if ps.source_count >= 2:
                report.stable_with_enough_sources += 1
    report.maturity_accuracy = (
        report.stable_with_enough_sources / report.stable_pages
        if report.stable_pages
        else 1.0
    )

    # Filler density
    report.total_paragraphs = sum(ps.paragraph_count for ps in page_scores)
    report.total_filler_hits = sum(ps.filler_count for ps in page_scores)
    report.filler_density = (
        report.total_filler_hits / report.total_paragraphs
        if report.total_paragraphs
        else 0.0
    )

    return report


# ---------------------------------------------------------------------------
# Benchmark corpus
# ---------------------------------------------------------------------------

DEFAULT_CORPUS_FILE = Path(__file__).resolve().parent.parent / "data" / "benchmarks" / "corpus.txt"
DEFAULT_PDF_DIR = Path(__file__).resolve().parent.parent / "data" / "arxiv" / "ai-research-corpus" / "pdfs"


def load_benchmark_corpus(
    corpus_file: Path = DEFAULT_CORPUS_FILE,
    pdf_dir: Path = DEFAULT_PDF_DIR,
) -> list[Path]:
    """Read corpus.txt and resolve each filename to a real PDF path."""
    if not corpus_file.exists():
        raise FileNotFoundError(f"Corpus file not found: {corpus_file}")

    pdfs: list[Path] = []
    for line in corpus_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pdf_path = pdf_dir / line
        if not pdf_path.exists():
            raise FileNotFoundError(f"Corpus PDF not found: {pdf_path}")
        pdfs.append(pdf_path)
    return pdfs


def run_benchmark(
    *,
    corpus_file: Path = DEFAULT_CORPUS_FILE,
    pdf_dir: Path = DEFAULT_PDF_DIR,
    workspace_dir: Path | None = None,
    limit: int = 0,
    parallelism: int = 4,
) -> EvalReport:
    """Full benchmark: init workspace, copy PDFs, ingest, score.

    If workspace_dir is None, uses a temporary directory that is NOT cleaned up
    (so you can inspect the output).
    """
    from compile.config import load_config
    from compile.workspace import init_workspace

    pdfs = load_benchmark_corpus(corpus_file, pdf_dir)
    if limit > 0:
        pdfs = pdfs[:limit]

    # Create workspace
    if workspace_dir is None:
        workspace_dir = Path(tempfile.mkdtemp(prefix="compile-bench-"))

    topic = "Benchmark Evaluation Corpus"
    description = f"Auto-generated benchmark workspace with {len(pdfs)} ArXiv PDFs."
    config = init_workspace(workspace_dir, topic, description)

    # Copy .env into the workspace so API key is found on config reload
    project_root = Path(__file__).resolve().parent.parent
    for env_candidate in [project_root / ".env", Path.home() / ".env"]:
        if env_candidate.exists():
            shutil.copy2(env_candidate, workspace_dir / ".env")
            break

    # Reload config so it picks up the API key from the .env we just copied
    config = load_config(workspace_dir)

    # Copy PDFs into raw/
    for pdf in pdfs:
        shutil.copy2(pdf, config.raw_dir / pdf.name)

    # Run ingest
    from compile.compiler import Compiler
    from compile.ingest import ingest_sources, run_synthesis_pass
    from compile.workspace import get_unprocessed

    compiler = Compiler(config)
    unprocessed = get_unprocessed(config)
    if unprocessed:
        ingest_sources(config, compiler, unprocessed, max_workers=parallelism)
        if len(unprocessed) >= 2:
            run_synthesis_pass(config, compiler)

    # Score
    report = evaluate_workspace(workspace_dir)
    report.workspace_root = str(workspace_dir)
    return report
