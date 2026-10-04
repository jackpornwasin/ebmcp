from typing import Annotated

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ebmcp import tools

INSTRUCTIONS = """Read EPUB books by file path. Choose a route by question type:
- About a chapter, section or named topic: get_toc, match the title, then read_chapter with its [n].
- A specific term, fact, definition or quote: search_book with a short key phrase, then read_chapter on the most \
relevant hit for full context.
- Broad (summarize, overview): get_toc, then read_chapter on top-level chapters.
- If search_book finds nothing: retry with fewer or different words, then fall back to get_toc and read likely sections.
Ground answers in the text you read and name the chapter it came from."""

_RASTER_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

server = MCPServer("ebmcp", instructions=INSTRUCTIONS)

BookPath = Annotated[str, Field(description="Absolute path to an .epub file")]


@server.tool()
def get_toc(path: BookPath) -> str:
    """List the book's table of contents. Each entry is prefixed with [n]; pass n to read_chapter as `index`."""
    book = _open(path)
    lines = [f"# {book.title or 'Untitled'}"]
    if book.authors:
        lines.append(f"*{', '.join(book.authors)}*")
    lines.append("")
    lines += [f"{'  ' * entry.level}- [{entry.index}] {entry.title}" for entry in book.toc]
    return "\n".join(lines)


@server.tool()
def read_chapter(
    path: BookPath,
    index: Annotated[int, Field(description="Entry number [n] from get_toc or search_book")],
) -> str:
    """Read one TOC entry as Markdown. A chapter includes its subsections; read a subsection's [n] for a smaller piece.
    Images appear as ![alt](href); pass href to get_image to view one."""
    try:
        return tools.read_chapter(_open(path), index)
    except IndexError as exc:
        raise ToolError(str(exc)) from exc


@server.tool()
def search_book(
    path: BookPath,
    query: Annotated[str, Field(description="Short key phrase, e.g. 'risk reward ratio'")],
    offset: Annotated[int, Field(description="Number of matches to skip, for paging")] = 0,
    max_results: Annotated[int, Field(description="Maximum matches to return")] = 20,
) -> str:
    """Find where a word or phrase appears; use short key phrases. Punctuation between words is ignored.
    Each hit shows the [n] of the section containing it, for read_chapter."""
    if offset < 0 or max_results < 1:
        raise ToolError("offset must be >= 0 and max_results >= 1")
    book = _open(path)
    try:
        hits, total = tools.search(book, query, offset, max_results)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if total == 0:
        return f"No matches for {query!r}. Try fewer or different words."
    if not hits:
        return f"No more matches ({total} total)."
    lines = [f"- {_breadcrumb(book, hit.entry)} — {hit.snippet}" for hit in hits]
    end = offset + len(hits)
    footer = f"Showing {offset + 1}–{end} of {total} matches."
    if end < total:
        footer += f" Call again with offset={end} for more."
    return "\n".join([*lines, "", footer])


@server.tool()
def get_image(
    path: BookPath,
    image_path: Annotated[str, Field(description="Image href from read_chapter output, e.g. 'Images/fig1.png'")],
) -> Image:
    """Return an image from the book so it can be viewed."""
    try:
        data, media_type = tools.get_image(_open(path), image_path)
    except KeyError as exc:
        raise ToolError(f"No image {image_path!r} in this book") from exc
    if media_type not in _RASTER_TYPES:
        raise ToolError(f"Image {image_path!r} is {media_type}, which can't be displayed")
    return Image(data=data, format=media_type.removeprefix("image/"))


def _open(path: str) -> tools.Book:
    """Open a book, turning load failures into tool errors."""
    try:
        return tools.open_book(path)
    except FileNotFoundError as exc:
        raise ToolError(f"File not found: {path}") from exc
    except Exception as exc:
        raise ToolError(f"Cannot read {path} as an EPUB: {exc}") from exc


def _breadcrumb(book: tools.Book, entry: tools.TocEntry | None) -> str:
    """Format an entry with its ancestors, e.g. "[53] Chapter 7 › Risk Management"."""
    if entry is None:
        return "(before the first TOC entry)"
    titles = [entry.title]
    level = entry.level
    for candidate in reversed(book.toc[: entry.index]):
        if candidate.level < level:
            titles.append(candidate.title)
            level = candidate.level
    return f"[{entry.index}] {' › '.join(reversed(titles))}"


def main() -> None:
    """Run the server over stdio."""
    server.run()
