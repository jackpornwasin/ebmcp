import posixpath
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import ebooklib  # type: ignore[import-untyped]
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from bs4.element import NavigableString
from ebooklib import epub
from markdownify import MarkdownConverter

# XHTML is parsed as HTML on purpose: the HTML parser tolerates the malformed markup common in real EPUBs.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Private-use code points: valid in XML text and never present in real book content.
_MARKER = "{}"
_MARKER_RE = re.compile("(\\d+)")
_SNIPPET_RADIUS = 150
# Line-anchored so inline code showing backticks isn't mistaken for a fence; info strings can't contain "`".
# Leading whitespace allowed because markdownify indents code blocks inside list items.
_FENCE_RE = re.compile(r"(^[ \t]*```[^`\n]*\n.*?^[ \t]*```[ \t]*$)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class TocEntry:
    """One table-of-contents entry; `index` is the id the tools expose."""

    index: int
    level: int
    title: str
    href: str


@dataclass(frozen=True)
class SearchHit:
    """A search match and the deepest TOC entry containing it (None if before the first entry)."""

    entry: TocEntry | None
    snippet: str


@dataclass(frozen=True)
class Book:
    """Parsed EPUB, built once per path+mtime and reused by every tool call."""

    title: str
    authors: list[str]
    toc: list[TocEntry]
    markdown: str
    starts: list[int]
    images: dict[str, tuple[bytes, str]]


def open_book(path: str | Path) -> Book:
    """Load an EPUB, reusing the parsed result until the file changes."""
    resolved = Path(path).expanduser().resolve()
    return _load(str(resolved), resolved.stat().st_mtime_ns)


def read_chapter(book: Book, index: int) -> str:
    """Return the Markdown of a TOC entry, including its subsections."""
    if not 0 <= index < len(book.toc):
        raise IndexError(f"index {index} is out of range; the TOC has entries 0-{len(book.toc) - 1}")
    start, level = book.starts[index], book.toc[index].level
    end = next(
        (
            book.starts[j]
            for j in range(index + 1, len(book.toc))
            if book.toc[j].level <= level and book.starts[j] > start
        ),
        len(book.markdown),
    )
    # Odd parts are fenced code blocks, whose blank lines are content.
    parts = _FENCE_RE.split(book.markdown[start:end])
    return "".join(p if i % 2 else re.sub(r"\n{3,}", "\n\n", p) for i, p in enumerate(parts)).strip()


def search(book: Book, query: str, offset: int, max_results: int) -> tuple[list[SearchHit], int]:
    """Find a phrase, ignoring case and punctuation between words; return one page of hits and the total."""
    words = re.findall(r"\w+", query)
    if not words:
        raise ValueError("query must contain at least one word")
    pattern = re.compile(r"\W+".join(map(re.escape, words)), re.IGNORECASE)
    matches = list(pattern.finditer(book.markdown))
    hits = [
        SearchHit(entry=_entry_at(book, m.start()), snippet=_snippet(book.markdown, m.start(), m.end()))
        for m in matches[offset : offset + max_results]
    ]
    return hits, len(matches)


def get_image(book: Book, href: str) -> tuple[bytes, str]:
    """Return an image's bytes and media type by its book-relative href."""
    return book.images[posixpath.normpath(href).lstrip("/")]


def _entry_at(book: Book, pos: int) -> TocEntry | None:
    """Return the deepest entry starting at or before `pos`."""
    candidates = [i for i, start in enumerate(book.starts) if start <= pos]
    if not candidates:
        return None
    return book.toc[max(candidates, key=lambda i: (book.starts[i], i))]


def _snippet(text: str, start: int, end: int) -> str:
    """Return the match with surrounding context, whitespace collapsed."""
    lo, hi = max(0, start - _SNIPPET_RADIUS), min(len(text), end + _SNIPPET_RADIUS)
    snippet = " ".join(text[lo:hi].split())
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int) -> Book:
    """Parse an EPUB; `mtime_ns` is part of the cache key so edited files reload."""
    book = epub.read_epub(path)
    documents = [
        item
        for item_id, _linear in book.spine
        if (item := book.get_item_with_id(item_id)) is not None and item.get_type() == ebooklib.ITEM_DOCUMENT
    ]
    names = {_doc_name(doc) for doc in documents}
    # ebooklib returns a bare Link instead of a list for an empty NCX navMap.
    nodes = book.toc if isinstance(book.toc, list) else []
    # Entries pointing outside the spine can't be located; with none left, fall back to the spine.
    raw_toc = [e for e in _flatten_toc(nodes, 0) if _split_href(e[2])[0] in names] or _spine_toc(documents)

    targets: dict[str, list[tuple[int, str]]] = {}
    for i, (_level, _title, href) in enumerate(raw_toc):
        name, fragment = _split_href(href)
        targets.setdefault(name, []).append((i, fragment))
    markdown = "\n\n".join(_to_markdown(doc, targets.get(_doc_name(doc), [])) for doc in documents)

    text, positions = _strip_markers(markdown)
    toc = [TocEntry(i, *entry) for i, entry in enumerate(raw_toc)]
    titles = book.get_metadata("DC", "title")
    return Book(
        title=titles[0][0] if titles else "",
        authors=[creator for creator, _attrs in book.get_metadata("DC", "creator")],
        toc=toc,
        markdown=text,
        starts=[positions[i] for i in range(len(toc))],
        # By media type, not ITEM_IMAGE: ebooklib reads the EPUB3 cover-image as ITEM_COVER.
        images={
            _doc_name(item): (item.get_content(), item.media_type)
            for item in book.get_items()
            if (item.media_type or "").startswith("image/")
        },
    )


def _flatten_toc(nodes: list[Any], level: int) -> list[tuple[int, str, str]]:
    """Flatten ebooklib's nested TOC into (level, title, href) in reading order."""
    entries: list[tuple[int, str, str]] = []
    for node in nodes:
        if isinstance(node, tuple):
            section, children = node
            child_entries = _flatten_toc(children, level + 1)
            # A heading without a link starts where its first child does.
            href = section.href or (child_entries[0][2] if child_entries else "")
            if href:
                entries.append((level, section.title or "", href))
            entries.extend(child_entries)
        else:
            entries.append((level, node.title or "", node.href))
    return entries


def _split_href(href: str) -> tuple[str, str]:
    """Split a TOC href into a normalized file name and fragment."""
    name, _, fragment = href.partition("#")
    return posixpath.normpath(unquote(name)), unquote(fragment)


def _doc_name(item: Any) -> str:
    """Return an item's manifest name, normalized like TOC hrefs (ebooklib keeps "./" segments)."""
    return posixpath.normpath(str(item.get_name()))


def _parse(doc: Any) -> BeautifulSoup:
    """Parse a document's stored bytes; ebooklib's get_content() rebuilds and re-indents the markup."""
    return BeautifulSoup(doc.content, "lxml")


def _spine_toc(documents: list[Any]) -> list[tuple[int, str, str]]:
    """Build a flat TOC from spine documents, titled by first heading or file name."""
    entries = []
    for doc in documents:
        heading = _parse(doc).find(re.compile(r"^h[1-6]$"))
        title = " ".join(heading.get_text(" ").split()) if heading else ""
        entries.append((0, title or _doc_name(doc), _doc_name(doc)))
    return entries


def _to_markdown(doc: Any, targets: list[tuple[int, str]]) -> str:
    """Convert one spine document to Markdown, inserting a marker where each TOC entry starts."""
    soup = _parse(doc)
    if soup.head:
        soup.head.decompose()
    body = soup.body or soup
    for i, fragment in targets:
        # Search inside <body> only: a marker in <head> or on <body> itself would be lost from the output.
        anchor = (body.find(id=fragment) or body.find("a", attrs={"name": fragment})) if fragment else None
        if isinstance(anchor, Tag):
            anchor.insert_before(NavigableString(_MARKER.format(i)))
        else:
            body.insert(0, NavigableString(_MARKER.format(i)))
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    base = posixpath.dirname(_doc_name(doc))
    for img in soup.find_all("img", src=True):
        scheme = urlparse(str(img["src"])).scheme
        if scheme == "data":
            # Embedded images have no href for get_image and would flood the text with base64.
            img.replace_with(str(img.get("alt", "")))
        elif not scheme:
            img["src"] = posixpath.normpath(posixpath.join(base, unquote(str(img["src"]))))
    # Links between book files are meaningless outside the reader; keep only their text.
    for link in soup.find_all("a"):
        if not urlparse(str(link.get("href", ""))).scheme:
            link.unwrap()
    return _Converter(heading_style="ATX", bullets="-", escape_underscores=False).convert_soup(body)


class _Converter(MarkdownConverter):
    """Markdown converter that keeps image references inside headings and table cells."""

    def convert_img(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Convert an image as if it were not inline, so get_image can still reach it."""
        # markdownify's stub omits convert_img, which exists at runtime.
        return str(super().convert_img(el, text, parent_tags - {"_inline"}))  # type: ignore[misc]


def _strip_markers(markdown: str) -> tuple[str, dict[int, int]]:
    """Remove markers, returning the text and each entry's start offset snapped to its line start."""
    pieces: list[str] = []
    raw_positions: dict[int, int] = {}
    length = last = 0
    for m in _MARKER_RE.finditer(markdown):
        pieces.append(markdown[last : m.start()])
        length += m.start() - last
        raw_positions.setdefault(int(m.group(1)), length)
        last = m.end()
    pieces.append(markdown[last:])
    text = "".join(pieces)
    return text, {i: text.rfind("\n", 0, pos) + 1 for i, pos in raw_positions.items()}
