from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
import re
import shutil

from bs4 import BeautifulSoup

from compile.config import _discover_workspace_root
from compile.markdown import parse_markdown_text


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

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
MIN_EXTRACTED_IMAGE_WIDTH = 96
MIN_EXTRACTED_IMAGE_HEIGHT = 96
MIN_EXTRACTED_IMAGE_AREA = 20_000


@dataclass(frozen=True)
class ExtractedAsset:
    relative_path: str
    page_number: int
    width: int
    height: int
    sha1: str


@dataclass(frozen=True)
class ExtractedSource:
    title: str
    normalized_text: str
    paragraphs: tuple[str, ...]
    headings: tuple[str, ...]
    assets: tuple[ExtractedAsset, ...]
    metadata_only: bool


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized or "untitled"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_camel_case(value: str) -> str:
    """Insert spaces into camelCase or PascalCase runs.

    'SingerAllAnimalsAreEqual' -> 'Singer All Animals Are Equal'
    'HTMLParser' -> 'HTML Parser'
    """
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return value


def _title_from_stem(stem: str) -> str:
    """Derive a human-readable title from a filename stem."""
    name = stem.replace("-", " ").replace("_", " ").strip()
    name = _split_camel_case(name)
    return normalize_text(name).title()


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
    extracted = extract_source(path)
    return extracted.title, extracted.normalized_text


def extract_source(path: Path) -> ExtractedSource:
    """Extract structured source details from a file."""
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


def _extract_pdf(path: Path) -> ExtractedSource:
    title = _title_from_stem(path.stem)
    try:
        import fitz
    except ModuleNotFoundError:
        return _pdf_placeholder(title)

    try:
        doc = fitz.open(path)
    except Exception:
        return _pdf_placeholder(title)

    page_texts: list[str] = []
    assets: list[ExtractedAsset] = []
    try:
        assets = _extract_pdf_assets(doc, path)
        for page in doc:
            raw_text = fix_pdf_artifacts(page.get_text("text") or "").strip()
            if raw_text:
                page_texts.append(raw_text)
    finally:
        doc.close()

    full_text = "\n\n".join(page_texts).strip()
    paragraphs = tuple(_paragraphs_from_text(full_text))
    normalized = normalize_text(full_text)
    if not normalized and assets:
        normalized = (
            f"Extracted {len(assets)} figure(s) from the source PDF, "
            "but little usable page text."
        )

    if not normalized and not assets:
        return _pdf_placeholder(title)

    return ExtractedSource(
        title=title,
        normalized_text=normalized,
        paragraphs=paragraphs or ((normalized,) if normalized else ()),
        headings=(),
        assets=tuple(assets),
        metadata_only=False,
    )


def _extract_html(path: Path) -> ExtractedSource:
    html = path.read_text(errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else path.stem)
    article = soup.find("article")
    body_node = article or soup.body or soup
    for tag in body_node.find_all(["nav", "footer", "aside", "script", "style", "noscript"]):
        tag.decompose()

    headings = [
        text
        for text in (
            normalize_text(node.get_text(" ", strip=True))
            for node in body_node.find_all(re.compile("^h[1-6]$"))
        )
        if text
    ]
    paragraphs = [
        text
        for text in (
            normalize_text(node.get_text(" ", strip=True))
            for node in body_node.find_all(["p", "li", "blockquote"])
        )
        if text
    ]
    text = normalize_text(body_node.get_text(" ", strip=True))
    return ExtractedSource(
        title=title[:120],
        normalized_text=text,
        paragraphs=tuple(paragraphs or _paragraphs_from_text(body_node.get_text("\n\n", strip=True))),
        headings=tuple(_dedupe_headings(headings, title[:120])),
        assets=(),
        metadata_only=False,
    )


def _extract_markdown(path: Path) -> ExtractedSource:
    text = path.read_text(errors="ignore")
    cleaned = _strip_comments(text)
    frontmatter, markdown_body = _strip_markdown_frontmatter(cleaned)
    body_without_code = _strip_fenced_code(markdown_body)
    title = _title_from_markdown(markdown_body, path, frontmatter)
    headings = _dedupe_headings(_extract_markdown_headings(body_without_code), title)
    paragraphs = _paragraphs_from_text(body_without_code, strip_headings=True)
    normalized = normalize_text(body_without_code)
    return ExtractedSource(
        title=title,
        normalized_text=normalized,
        paragraphs=tuple(paragraphs),
        headings=tuple(headings),
        assets=(),
        metadata_only=False,
    )


def _extract_image_stub(path: Path) -> ExtractedSource:
    title = _title_from_stem(path.stem)
    size = path.stat().st_size
    suffix = path.suffix.lower().lstrip(".")
    text = (
        "Image asset registered. "
        f"Format: {suffix}. "
        f"File size: {size} bytes. "
        "Visual analysis is not available in this pipeline yet, so this ingest is metadata-only. "
        "Use the resulting source page to anchor provenance and link the image into related wiki pages."
    )
    normalized = normalize_text(text)
    return ExtractedSource(
        title=title,
        normalized_text=normalized,
        paragraphs=(normalized,),
        headings=(),
        assets=(),
        metadata_only=True,
    )


def _pdf_placeholder(title: str) -> ExtractedSource:
    text = (
        "PDF source registered. "
        "Content extraction is deferred to Anthropic's native PDF reader during analysis."
    )
    normalized = normalize_text(text)
    return ExtractedSource(
        title=title,
        normalized_text=normalized,
        paragraphs=(normalized,),
        headings=(),
        assets=(),
        metadata_only=True,
    )


def _extract_pdf_assets(doc: object, pdf_path: Path) -> list[ExtractedAsset]:
    assets: list[ExtractedAsset] = []
    seen: set[str] = set()
    assets_root, relative_root = _asset_storage_root(pdf_path)
    if assets_root.exists():
        shutil.rmtree(assets_root)
    image_counter = 1

    for page_index, page in enumerate(doc):
        page_number = page_index + 1
        for image in page.get_images(full=True):
            xref = image[0]
            try:
                payload = doc.extract_image(xref)
            except Exception:
                continue
            image_bytes = payload.get("image")
            width = int(payload.get("width") or 0)
            height = int(payload.get("height") or 0)
            if not image_bytes:
                continue
            if width < MIN_EXTRACTED_IMAGE_WIDTH or height < MIN_EXTRACTED_IMAGE_HEIGHT:
                continue
            if width * height < MIN_EXTRACTED_IMAGE_AREA:
                continue

            digest = sha1(image_bytes).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)

            ext = str(payload.get("ext") or "png").lower().strip(".")
            assets_root.mkdir(parents=True, exist_ok=True)
            asset_path = assets_root / f"page-{page_number:03d}-image-{image_counter:02d}.{ext}"
            image_counter += 1
            asset_path.write_bytes(image_bytes)
            assets.append(
                ExtractedAsset(
                    relative_path=_relative_asset_path(asset_path, relative_root),
                    page_number=page_number,
                    width=width,
                    height=height,
                    sha1=digest,
                )
            )

    return assets


def _find_raw_dir(path: Path) -> Path | None:
    for parent in path.parents:
        if parent.name == "raw":
            return parent
    return None


def _asset_storage_root(pdf_path: Path) -> tuple[Path, Path | None]:
    workspace_root = _discover_workspace_root(pdf_path.parent)
    raw_dir = _find_raw_dir(pdf_path)
    if workspace_root is not None and raw_dir == workspace_root / "raw":
        source_key = str(pdf_path.relative_to(workspace_root)).replace("\\", "/")
        relative_source = pdf_path.relative_to(raw_dir).with_suffix("")
        return workspace_root / "raw" / "assets" / _slugified_path(relative_source, source_key=source_key), workspace_root
    if workspace_root is not None:
        source_key = str(pdf_path.relative_to(workspace_root)).replace("\\", "/")
        relative_source = pdf_path.relative_to(workspace_root).with_suffix("")
        return workspace_root / "raw" / "assets" / _slugified_path(relative_source, source_key=source_key), workspace_root
    if raw_dir is not None:
        source_key = str(pdf_path.relative_to(raw_dir.parent)).replace("\\", "/")
        relative_source = pdf_path.relative_to(raw_dir).with_suffix("")
        return raw_dir / "assets" / _slugified_path(relative_source, source_key=source_key), raw_dir.parent
    source_key = str(pdf_path).replace("\\", "/")
    stem_slug = slugify(pdf_path.stem) or "untitled"
    suffix = sha1(source_key.encode()).hexdigest()[:6]
    return pdf_path.parent / "assets" / f"{stem_slug}-{suffix}", pdf_path.parent


def _slugified_path(path: Path, *, source_key: str | None = None) -> Path:
    parts = [slugify(part) for part in path.parts if part not in {"", "."}]
    if not parts:
        parts = ["untitled"]
    if source_key:
        suffix = sha1(source_key.encode()).hexdigest()[:6]
        parts[-1] = f"{parts[-1]}-{suffix}"
    return Path(*parts)


def _relative_asset_path(asset_path: Path, relative_root: Path | None) -> str:
    if relative_root is not None:
        try:
            return str(asset_path.relative_to(relative_root)).replace("\\", "/")
        except ValueError:
            pass
    return str(asset_path).replace("\\", "/")


def _strip_comments(text: str) -> str:
    return HTML_COMMENT_RE.sub("", text)


def _strip_markdown_frontmatter(text: str) -> tuple[dict[str, object], str]:
    frontmatter, body, has_frontmatter = parse_markdown_text(text)
    if has_frontmatter:
        return frontmatter, body
    return {}, text


def _title_from_markdown(text: str, path: Path, frontmatter: dict[str, object] | None = None) -> str:
    if frontmatter:
        frontmatter_title = normalize_text(str(frontmatter.get("title", "")))
        if frontmatter_title:
            return frontmatter_title
    title = _title_from_stem(path.stem)
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match and line.lstrip().startswith("# "):
            title = normalize_text(match.group(1))
            break
    return title


def _extract_markdown_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in _markdown_lines_without_fences(text):
        match = HEADING_RE.match(line)
        if not match:
            continue
        heading = normalize_text(match.group(1))
        if heading:
            headings.append(heading)
    return headings


def _paragraphs_from_text(text: str, *, strip_headings: bool = False) -> list[str]:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        cleaned_lines: list[str] = []
        for line in _markdown_lines_without_fences(block):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<!--"):
                continue
            if strip_headings and HEADING_RE.match(stripped):
                continue
            stripped = re.sub(r"^[-*+]\s+", "", stripped)
            stripped = re.sub(r"^>\s*", "", stripped)
            cleaned_lines.append(stripped)
        paragraph = normalize_text(" ".join(cleaned_lines))
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def _strip_fenced_code(text: str) -> str:
    return "\n".join(_markdown_lines_without_fences(text))


def _markdown_lines_without_fences(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        stripped = line.strip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0]
            elif stripped.startswith(fence_marker * 3):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        lines.append(line)
    return lines


def _dedupe_headings(headings: list[str], title: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    title_key = normalize_text(title).lower()
    for heading in headings:
        heading_key = normalize_text(heading).lower()
        if not heading_key or heading_key == title_key or heading_key in seen:
            continue
        seen.add(heading_key)
        results.append(heading)
    return results


def is_url(path_or_url: str) -> bool:
    """Return True if the string looks like an HTTP(S) URL."""
    return path_or_url.startswith("http://") or path_or_url.startswith("https://")


def is_supported(path_or_url: str | Path) -> bool:
    """Return True if the path is a supported file type or an HTTP(S) URL."""
    if isinstance(path_or_url, str) and is_url(path_or_url):
        return True
    path = Path(path_or_url) if isinstance(path_or_url, str) else path_or_url
    return path.suffix.lower() in SUPPORTED_EXTENSIONS and not path.name.startswith(".")


def is_generated_raw_asset(path_or_url: str | Path) -> bool:
    """Return True for generated attachments stored under ``raw/assets/``."""
    if isinstance(path_or_url, str) and is_url(path_or_url):
        return False
    path = Path(path_or_url) if isinstance(path_or_url, str) else path_or_url
    parts = [part.lower() for part in path.parts]
    return any(parts[index] == "raw" and parts[index + 1] == "assets" for index in range(len(parts) - 1))
