from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import httpx


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
DEFAULT_QUERY = "(cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.RO OR cat:stat.ML)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a recent AI-focused corpus from arXiv."
    )
    parser.add_argument("--count", type=int, default=200, help="Number of papers to fetch.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of metadata records to request per arXiv API call.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/arxiv/ai-research-corpus",
        help="Target directory for metadata and PDFs.",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="arXiv API search query.",
    )
    parser.add_argument(
        "--api-delay",
        type=float,
        default=3.5,
        help="Delay in seconds between arXiv API metadata requests.",
    )
    parser.add_argument(
        "--pdf-delay",
        type=float,
        default=0.4,
        help="Delay in seconds between PDF downloads.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slugify(value: str, max_length: int = 120) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    normalized = normalized[:max_length].strip("-")
    return normalized or "untitled"


def text_or_empty(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return re.sub(r"\s+", " ", node.text).strip()


def parse_entry(entry: ET.Element) -> dict[str, object]:
    article_url = text_or_empty(entry.find("atom:id", ATOM_NS))
    arxiv_id = article_url.rstrip("/").split("/")[-1]
    title = text_or_empty(entry.find("atom:title", ATOM_NS))
    summary = text_or_empty(entry.find("atom:summary", ATOM_NS))
    authors = [
        text_or_empty(author.find("atom:name", ATOM_NS))
        for author in entry.findall("atom:author", ATOM_NS)
        if text_or_empty(author.find("atom:name", ATOM_NS))
    ]
    categories = [
        category.attrib.get("term", "")
        for category in entry.findall("atom:category", ATOM_NS)
        if category.attrib.get("term")
    ]
    primary_category = ""
    primary = entry.find("arxiv:primary_category", ATOM_NS)
    if primary is not None:
        primary_category = primary.attrib.get("term", "")

    pdf_url = ""
    for link in entry.findall("atom:link", ATOM_NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and article_url:
        pdf_url = article_url.replace("/abs/", "/pdf/")
    if pdf_url.startswith("http://"):
        pdf_url = "https://" + pdf_url[len("http://") :]
    if pdf_url and not pdf_url.endswith(".pdf"):
        pdf_url = f"{pdf_url}.pdf"

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "summary": summary,
        "authors": authors,
        "categories": categories,
        "primary_category": primary_category,
        "published": text_or_empty(entry.find("atom:published", ATOM_NS)),
        "updated": text_or_empty(entry.find("atom:updated", ATOM_NS)),
        "article_url": article_url,
        "pdf_url": pdf_url,
    }


def fetch_entries(
    client: httpx.Client,
    query: str,
    count: int,
    batch_size: int,
    api_delay: float,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for start in range(0, count, batch_size):
        current_batch = min(batch_size, count - start)
        response = client.get(
            ARXIV_API_URL,
            params={
                "search_query": query,
                "start": start,
                "max_results": current_batch,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        batch_entries = [parse_entry(entry) for entry in root.findall("atom:entry", ATOM_NS)]
        entries.extend(batch_entries)
        print(f"Fetched metadata batch starting at {start}: {len(batch_entries)} records")
        if start + current_batch < count:
            time.sleep(api_delay)
    return entries[:count]


def download_pdfs(
    client: httpx.Client,
    entries: list[dict[str, object]],
    output_dir: Path,
    pdf_delay: float,
) -> list[dict[str, object]]:
    pdf_dir = output_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    completed: list[dict[str, object]] = []
    for index, entry in enumerate(entries, start=1):
        pdf_url = str(entry.get("pdf_url", ""))
        if not pdf_url:
            print(f"[{index}/{len(entries)}] skipped {entry['arxiv_id']} (no pdf url)")
            continue

        filename = f"{index:03d}-{slugify(str(entry['title']))}-{str(entry['arxiv_id']).replace('/', '_')}.pdf"
        pdf_path = pdf_dir / filename
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            with client.stream("GET", pdf_url) as response:
                response.raise_for_status()
                with pdf_path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            time.sleep(pdf_delay)
        entry["local_pdf"] = str(pdf_path)
        completed.append(entry)
        print(f"[{index}/{len(entries)}] saved {pdf_path.name}")
    return completed


def write_metadata(entries: list[dict[str, object]], output_dir: Path, query: str) -> None:
    metadata_path = output_dir / "metadata.jsonl"
    manifest_path = output_dir / "manifest.json"

    with metadata_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")

    manifest = {
        "generated_at": utc_now(),
        "query": query,
        "count": len(entries),
        "pdf_dir": str(output_dir / "pdfs"),
        "metadata_path": str(metadata_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "CompileResearchCollector/0.1 (local dataset builder)",
    }

    with httpx.Client(headers=headers, follow_redirects=True, timeout=60.0) as client:
        entries = fetch_entries(
            client=client,
            query=args.query,
            count=args.count,
            batch_size=args.batch_size,
            api_delay=args.api_delay,
        )
        entries = download_pdfs(
            client=client,
            entries=entries,
            output_dir=output_dir,
            pdf_delay=args.pdf_delay,
        )

    write_metadata(entries, output_dir, args.query)
    print(f"Downloaded {len(entries)} arXiv papers into {output_dir}")


if __name__ == "__main__":
    main()
