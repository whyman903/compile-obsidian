from __future__ import annotations

from pathlib import Path
import re

from bs4 import BeautifulSoup


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized or "untitled"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fix_pdf_artifacts(text: str) -> str:
    """Normalize common PDF extraction artifacts.

    - Fix hyphenated line breaks: "word-\\nbreak" -> "wordbreak"
    - Fix ligature issues: fi, fl, ff ligatures
    - Collapse multiple blank lines to max 2
    """
    # Fix hyphenated line breaks (word- followed by newline then lowercase continuation)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # Fix common ligature characters
    text = text.replace("\ufb01", "fi")   # ﬁ -> fi
    text = text.replace("\ufb02", "fl")   # ﬂ -> fl
    text = text.replace("\ufb00", "ff")   # ﬀ -> ff
    text = text.replace("\ufb03", "ffi")  # ﬃ -> ffi
    text = text.replace("\ufb04", "ffl")  # ﬄ -> ffl

    # Collapse multiple blank lines to max 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


def is_equation_heavy(text: str) -> bool:
    """Return True if the text contains more than 3 equation patterns.

    Detects $$...$$ display equations, $...$ inline equations,
    and lines with heavy math-symbol usage.
    """
    # Count display math blocks $$...$$
    display_eqs = len(re.findall(r"\$\$.+?\$\$", text, re.DOTALL))
    # Count inline math $...$ (not preceded/followed by another $)
    inline_eqs = len(re.findall(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", text))
    return (display_eqs + inline_eqs) > 3


def extract_text(path: Path) -> tuple[str, str]:
    """Extract text from a file. Returns (title, text)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in {".html", ".htm"}:
        return _extract_html(path)
    if suffix in {".md", ".markdown", ".txt"}:
        return _extract_markdown(path)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return _extract_image_stub(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: Path) -> tuple[str, str]:
    title = path.stem.replace("-", " ").replace("_", " ").strip().title()
    text = (
        f"PDF source named {title}. "
        "Content extraction is deferred to Anthropic's native PDF reader during analysis."
    )
    return title, normalize_text(text)


def _extract_html(path: Path) -> tuple[str, str]:
    html = path.read_text(errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else path.stem)
    article = soup.find("article")
    body_node = article or soup.body or soup
    text = normalize_text(body_node.get_text(" ", strip=True))
    return title[:120], text


def _extract_markdown(path: Path) -> tuple[str, str]:
    text = path.read_text(errors="ignore")
    # Try to extract title from first heading
    title = path.stem.replace("-", " ").replace("_", " ").strip().title()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break
    return title, normalize_text(text)


def _extract_image_stub(path: Path) -> tuple[str, str]:
    title = path.stem.replace("-", " ").replace("_", " ").strip().title()
    size = path.stat().st_size
    suffix = path.suffix.lower().lstrip(".")
    text = (
        f"Image asset named {title}. "
        f"Format: {suffix}. "
        f"File size: {size} bytes. "
        "Visual analysis is not available in this pipeline yet, so this ingest is metadata-only. "
        "Use the resulting source page to anchor provenance and link the image into related wiki pages."
    )
    return title, normalize_text(text)


def is_url(path_or_url: str) -> bool:
    """Return True if the string looks like an HTTP(S) URL."""
    return path_or_url.startswith("http://") or path_or_url.startswith("https://")


def is_supported(path_or_url: str | Path) -> bool:
    """Return True if the path is a supported file type or an HTTP(S) URL."""
    if isinstance(path_or_url, str) and is_url(path_or_url):
        return True
    path = Path(path_or_url) if isinstance(path_or_url, str) else path_or_url
    return path.suffix.lower() in SUPPORTED_EXTENSIONS and not path.name.startswith(".")
