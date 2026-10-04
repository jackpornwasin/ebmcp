import base64
from pathlib import Path
from typing import Any

import pytest
from helpers import write_epub
from mcp import Client
from mcp.types import CallToolResult, ImageContent, TextContent

from ebmcp.main import server


async def _call(name: str, args: dict[str, Any]) -> CallToolResult:
    async with Client(server) as client:
        return await client.call_tool(name, args)


def _text(result: CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


async def test_lists_the_four_tools() -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert sorted(t.name for t in tools) == ["get_image", "get_toc", "read_chapter", "search_book"]


async def test_get_toc(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<h1>One</h1><h2 id='a'>A</h2>", "c2.xhtml": "<h1>Two</h1>"},
        [("One", "c1.xhtml", [("A", "c1.xhtml#a")]), ("Two", "c2.xhtml")],
        title="My Book",
        authors=("Ann", "Bob"),
    )

    result = await _call("get_toc", {"path": str(path)})

    assert _text(result) == "# My Book\n*Ann, Bob*\n\n- [0] One\n  - [1] A\n- [2] Two"


async def test_get_toc_untitled_without_authors(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<h1>One</h1>"}, [("One", "c1.xhtml")], title=None)

    result = await _call("get_toc", {"path": str(path)})

    assert _text(result) == "# Untitled\n\n- [0] One"


async def test_read_chapter(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<h1>One</h1><p>text</p>"}, [("One", "c1.xhtml")])

    result = await _call("read_chapter", {"path": str(path), "index": 0})

    assert (result.is_error, _text(result)) == (False, "# One\n\ntext")


async def test_read_chapter_bad_index(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<p>x</p>"}, [("One", "c1.xhtml")])

    result = await _call("read_chapter", {"path": str(path), "index": 5})

    assert result.is_error
    assert _text(result) == "Error executing tool read_chapter: index 5 is out of range; the TOC has entries 0-0"


async def test_missing_file(tmp_path: Path) -> None:
    result = await _call("get_toc", {"path": str(tmp_path / "none.epub")})

    assert result.is_error
    assert _text(result) == f"Error executing tool get_toc: File not found: {tmp_path / 'none.epub'}"


async def test_not_an_epub(tmp_path: Path) -> None:
    path = tmp_path / "b.epub"
    path.write_text("not a zip")

    result = await _call("get_toc", {"path": str(path)})

    assert result.is_error
    assert _text(result).startswith(f"Error executing tool get_toc: Cannot read {path} as an EPUB:")


async def test_search_book_breadcrumbs(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c0.xhtml": "<p>needle cover</p>", "c1.xhtml": "<h1>One</h1><h2 id='a'>A</h2><h3 id='b'>B</h3><p>needle</p>"},
        [("One", "c1.xhtml", [("A", "c1.xhtml#a", [("B", "c1.xhtml#b")])])],
    )

    result = await _call("search_book", {"path": str(path), "query": "needle"})

    lines = _text(result).splitlines()
    assert lines[0].startswith("- (before the first TOC entry) — ")
    assert lines[1].startswith("- [2] One › A › B — ")


@pytest.mark.parametrize(
    ("offset", "max_results", "footer"),
    [
        (0, 2, "Showing 1–2 of 3 matches. Call again with offset=2 for more."),
        (1, 1, "Showing 2–2 of 3 matches. Call again with offset=2 for more."),
        (2, 2, "Showing 3–3 of 3 matches."),
        (0, 3, "Showing 1–3 of 3 matches."),
    ],
)
async def test_search_book_footer(tmp_path: Path, offset: int, max_results: int, footer: str) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {f"c{i}.xhtml": f"<p>needle {i}</p>" for i in range(3)},
        [(f"C{i}", f"c{i}.xhtml") for i in range(3)],
    )

    result = await _call(
        "search_book", {"path": str(path), "query": "needle", "offset": offset, "max_results": max_results}
    )

    assert _text(result).splitlines()[-1] == footer


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"query": "absent"}, "No matches for 'absent'. Try fewer or different words."),
        ({"query": "needle", "offset": 1}, "No more matches (1 total)."),
    ],
)
async def test_search_book_empty_results(tmp_path: Path, args: dict[str, Any], expected: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>needle</p>"}, [("C", "c.xhtml")])

    result = await _call("search_book", {"path": str(path), **args})

    assert (result.is_error, _text(result)) == (False, expected)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"query": "needle", "offset": -1}, "offset must be >= 0 and max_results >= 1"),
        ({"query": "needle", "max_results": 0}, "offset must be >= 0 and max_results >= 1"),
        ({"query": "--"}, "query must contain at least one word"),
    ],
)
async def test_search_book_invalid_arguments(tmp_path: Path, args: dict[str, Any], expected: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>needle</p>"}, [("C", "c.xhtml")])

    result = await _call("search_book", {"path": str(path), **args})

    assert (result.is_error, _text(result)) == (True, f"Error executing tool search_book: {expected}")


@pytest.mark.parametrize(("media_type", "mime_type"), [("image/jpeg", "image/jpeg"), ("image/png", "image/png")])
async def test_get_image(tmp_path: Path, media_type: str, mime_type: str) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], images={"Images/x.img": (b"\xff\xd8data", media_type)}
    )

    result = await _call("get_image", {"path": str(path), "image_path": "Images/x.img"})

    content = result.content[0]
    assert isinstance(content, ImageContent)
    assert (content.mime_type, base64.b64decode(content.data)) == (mime_type, b"\xff\xd8data")


@pytest.mark.parametrize(
    ("image_path", "expected"),
    [
        ("Images/x.svg", "Image 'Images/x.svg' is image/svg+xml, which can't be displayed"),
        ("Images/none.png", "No image 'Images/none.png' in this book"),
    ],
)
async def test_get_image_errors(tmp_path: Path, image_path: str, expected: str) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], images={"Images/x.svg": (b"<svg/>", "image/svg+xml")}
    )

    result = await _call("get_image", {"path": str(path), "image_path": image_path})

    assert (result.is_error, _text(result)) == (True, f"Error executing tool get_image: {expected}")
