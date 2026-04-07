"""Fetch a URL and save it as a local raw source."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from compile.text import slugify

_USER_AGENT = "compile-wiki/0.2 (personal knowledge base builder)"
_TIMEOUT = 30


def fetch_url(
    url: str,
    raw_dir: Path,
    *,
    download_images: bool = False,
) -> tuple[Path, str]:
    """Fetch *url*, convert to markdown, save into *raw_dir*.

    Returns ``(saved_path, title)``.
    """
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = _extract_title(soup, url)

    # Prefer <article> or <main> over the full <body>
    content_node = soup.find("article") or soup.find("main") or soup.body or soup

    if download_images:
        _download_images(content_node, url, raw_dir)

    # Strip nav, footer, aside, script, style
    for tag in content_node.find_all(["nav", "footer", "aside", "script", "style", "noscript"]):
        tag.decompose()

    markdown_body = md(str(content_node), heading_style="ATX", strip=["img"] if not download_images else [])
    markdown_body = _clean_markdown(markdown_body)

    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    provenance = f"<!-- source_url: {url} -->\n<!-- fetched: {now} -->\n\n"
    full_content = provenance + f"# {title}\n\n" + markdown_body

    slug = slugify(title) or slugify(urlparse(url).netloc + "-" + urlparse(url).path)
    dest = raw_dir / f"{slug}.md"

    # Avoid overwriting — append a counter if needed
    counter = 1
    while dest.exists():
        dest = raw_dir / f"{slug}-{counter}.md"
        counter += 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(full_content)
    return dest, title


def _extract_title(soup: BeautifulSoup, url: str) -> str:
    """Pull a page title from OG tags, <title>, or <h1>."""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()[:120]
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:120]
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:120]
    return urlparse(url).netloc


def _clean_markdown(text: str) -> str:
    """Remove excessive blank lines and trailing whitespace."""
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def _download_images(
    node: BeautifulSoup,
    base_url: str,
    raw_dir: Path,
) -> None:
    """Download <img> sources into raw/assets/ and rewrite src attributes."""
    from urllib.parse import urljoin

    assets_dir = raw_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for img in node.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        abs_url = urljoin(base_url, src)
        try:
            resp = httpx.get(abs_url, follow_redirects=True, timeout=15, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL):
            continue

        # Determine filename from URL path
        url_path = urlparse(abs_url).path
        filename = slugify(Path(url_path).stem) or "image"
        suffix = Path(url_path).suffix or _guess_extension(resp.headers.get("content-type", ""))
        if not suffix:
            suffix = ".png"
        dest = assets_dir / f"{filename}{suffix}"
        counter = 1
        while dest.exists():
            dest = assets_dir / f"{filename}-{counter}{suffix}"
            counter += 1
        dest.write_bytes(resp.content)
        # Rewrite the img src to local relative path
        img["src"] = f"raw/assets/{dest.name}"


def _guess_extension(content_type: str) -> str:
    """Map a Content-Type to a file extension."""
    ct = content_type.lower().split(";")[0].strip()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }.get(ct, "")
