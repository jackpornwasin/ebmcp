import os
from pathlib import Path

import pytest
from helpers import write_epub

from ebmcp.tools import SearchHit, TocEntry, get_image, open_book, read_chapter, search

# --- TOC ---


def test_toc_flattens_nested_sections_with_levels(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<h1>One</h1><h2 id='a'>A</h2><h3 id='b'>B</h3>", "c2.xhtml": "<h1>Two</h1>"},
        [("One", "c1.xhtml", [("A", "c1.xhtml#a", [("B", "c1.xhtml#b")])]), ("Two", "c2.xhtml")],
    )

    assert open_book(path).toc == [
        TocEntry(index=0, level=0, title="One", href="c1.xhtml"),
        TocEntry(index=1, level=1, title="A", href="c1.xhtml#a"),
        TocEntry(index=2, level=2, title="B", href="c1.xhtml#b"),
        TocEntry(index=3, level=0, title="Two", href="c2.xhtml"),
    ]


def test_toc_section_without_href_uses_first_child(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>intro</p><h2 id='a'>A</h2>"},
        [("Part", "", [("A", "c1.xhtml#a")])],
    )

    assert open_book(path).toc == [
        TocEntry(index=0, level=0, title="Part", href="c1.xhtml#a"),
        TocEntry(index=1, level=1, title="A", href="c1.xhtml#a"),
    ]


def test_toc_drops_section_without_href_or_children(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>one</p>"},
        [("Empty", "", []), ("One", "c1.xhtml")],
    )

    assert [(e.index, e.title) for e in open_book(path).toc] == [(0, "One")]


def test_toc_drops_entries_outside_the_spine_and_reindexes(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>one</p>", "c2.xhtml": "<p>two</p>"},
        [("One", "c1.xhtml"), ("Missing", "nowhere.xhtml"), ("Two", "c2.xhtml")],
    )

    assert [(e.index, e.title) for e in open_book(path).toc] == [(0, "One"), (1, "Two")]


def test_toc_ncx_hrefs_are_normalized(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>one</p>", "c2.xhtml": "<p>two</p>"},
        [("One", "./c1.xhtml"), ("Two", "./c2.xhtml#x")],
        nav=False,
    )

    assert [(e.index, e.title) for e in open_book(path).toc] == [(0, "One"), (1, "Two")]


def test_toc_falls_back_to_spine_when_no_link_resolves(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<h1>One</h1>"}, [("Missing", "nowhere.xhtml")])

    assert open_book(path).toc == [TocEntry(index=0, level=0, title="One", href="c1.xhtml")]


def test_toc_empty_ncx_falls_back_to_spine(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<h1>One</h1>"}, [], nav=False)

    assert open_book(path).toc == [TocEntry(index=0, level=0, title="One", href="c1.xhtml")]


def test_toc_missing_title_becomes_empty_string(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<p>one</p>"}, [(None, "c1.xhtml")], nav=False)

    assert open_book(path).toc == [TocEntry(index=0, level=0, title="", href="c1.xhtml")]


def test_toc_manifest_names_are_normalized(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"./c1.xhtml": "<h1>H</h1>"}, [("One", "c1.xhtml")])

    assert open_book(path).toc == [TocEntry(index=0, level=0, title="One", href="c1.xhtml")]


def test_toc_percent_encoded_href(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"My File.xhtml": "<p>intro</p><h2 id='a b'>A</h2><p>a text</p>"},
        [("A", "My%20File.xhtml#a%20b")],
    )

    assert read_chapter(open_book(path), 0) == "## A\n\na text"


def test_toc_falls_back_to_spine_titled_by_heading_or_file_name(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>x</p><h2>First  Heading</h2>", "c2.xhtml": "<p>no heading</p>"},
        [],
    )

    assert open_book(path).toc == [
        TocEntry(index=0, level=0, title="First Heading", href="c1.xhtml"),
        TocEntry(index=1, level=0, title="c2.xhtml", href="c2.xhtml"),
    ]


def test_metadata(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], title="My Book", authors=("Ann", "Bob"))

    book = open_book(path)

    assert (book.title, book.authors) == ("My Book", ["Ann", "Bob"])


def test_metadata_missing(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], title=None)

    book = open_book(path)

    assert (book.title, book.authors) == ("", [])


# --- read_chapter ---


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, "# One\n\nintro\n\n## A\n\na text\n\n## B\n\nb text"),  # top level includes subsections
        (1, "## A\n\na text"),  # subsection stops at next sibling
        (2, "## B\n\nb text"),  # last subsection stops at next top-level entry
        (3, "# Two\n\ntwo text\n\nmore in next file"),  # last entry runs to the end, across files
    ],
)
def test_read_chapter_spans(tmp_path: Path, index: int, expected: str) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {
            "c1.xhtml": "<h1>One</h1><p>intro</p><h2 id='a'>A</h2><p>a text</p><h2 id='b'>B</h2><p>b text</p>",
            "c2.xhtml": "<h1>Two</h1><p>two text</p>",
            "c3.xhtml": "<p>more in next file</p>",
        },
        [("One", "c1.xhtml", [("A", "c1.xhtml#a"), ("B", "c1.xhtml#b")]), ("Two", "c2.xhtml")],
    )

    assert read_chapter(open_book(path), index) == expected


def test_read_chapter_spans_multiple_files_until_next_entry(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>part one</p>", "c2.xhtml": "<p>part two</p>", "c3.xhtml": "<p>next</p>"},
        [("One", "c1.xhtml"), ("Next", "c3.xhtml")],
    )

    assert read_chapter(open_book(path), 0) == "part one\n\npart two"


def test_read_chapter_anchor_inside_heading_keeps_heading_markup(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>before</p><h2><a id='a'></a>Title</h2><p>body</p>"},
        [("A", "c1.xhtml#a")],
    )

    assert read_chapter(open_book(path), 0) == "## Title\n\nbody"


def test_read_chapter_legacy_name_anchor(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>intro</p><h2><a name='x'></a>X</h2><p>x text</p><h2><a name='y'></a>Y</h2><p>y text</p>"},
        [("X", "c1.xhtml#x"), ("Y", "c1.xhtml#y")],
    )

    assert read_chapter(open_book(path), 0) == "## X\n\nx text"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<pre><code><span>print</span><span>(</span><span>x</span><span>)</span></code></pre>", "```\nprint(x)\n```"),
        ("<p><b>Bold</b><span>text</span></p>", "**Bold**text"),
        ("Lead text<p>para</p>", "Lead text\n\npara"),
    ],
)
def test_read_chapter_uses_markup_as_stored_in_the_file(tmp_path: Path, body: str, expected: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": body}, [("A", "c1.xhtml")])

    assert read_chapter(open_book(path), 0) == expected


def test_read_chapter_fragment_on_body_starts_at_file_start(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<html><head><title>t</title></head><body id='top'><p>x</p></body></html>"},
        [("A", "c1.xhtml#top")],
    )

    assert read_chapter(open_book(path), 0) == "x"


@pytest.mark.parametrize(
    "doc",
    [
        "<html><head><meta name='x' content='y'/></head>"
        "<body><p>intro</p><h2><a name='x'></a>X</h2><p>x text</p></body></html>",
        "<html><body><p><input name='x'/>intro</p><h2><a name='x'></a>X</h2><p>x text</p></body></html>",
    ],
)
def test_read_chapter_name_anchor_ignores_other_named_elements(tmp_path: Path, doc: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": doc}, [("X", "c1.xhtml#x")])

    assert read_chapter(open_book(path), 0) == "## X\n\nx text"


def test_read_chapter_id_takes_precedence_over_name(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<a name='x'></a><p>first</p><h2 id='x'>X</h2><p>body</p>"},
        [("X", "c1.xhtml#x")],
    )

    assert read_chapter(open_book(path), 0) == "## X\n\nbody"


def test_read_chapter_inline_backticks_do_not_break_code_blocks(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>use <code>```</code> to fence</p><pre>a\n\n\n\nb</pre><p>x</p><p></p><p></p><p>y</p>"},
        [("A", "c1.xhtml")],
    )

    chapter = read_chapter(open_book(path), 0)

    assert "a\n\n\n\nb" in chapter
    assert "\n\n\n" not in chapter.split("```\n")[-1]


def test_read_chapter_keeps_blank_lines_in_code_block_inside_list_item(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<ul><li><p>item</p><pre>a\n\n\n\nb</pre></li></ul><p>after</p>"},
        [("A", "c1.xhtml")],
    )

    assert read_chapter(open_book(path), 0) == "- item\n\n  ```\n  a\n\n\n\n  b\n  ```\n\nafter"


def test_read_chapter_head_only_document_has_no_text(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<html><head><title>Cover Title</title></head></html>", "c2.xhtml": "<p>next</p>"},
        [("Cover", "c1.xhtml"), ("Next", "c2.xhtml")],
    )

    assert read_chapter(open_book(path), 0) == ""


def test_read_chapter_keeps_blank_lines_inside_code_blocks(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<p>a</p><pre>x\n\n\n\ny</pre>"}, [("A", "c1.xhtml")])

    assert read_chapter(open_book(path), 0) == "a\n\n```\nx\n\n\n\ny\n```"


def test_read_chapter_unknown_fragment_starts_at_file_start(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c1.xhtml": "<p>start</p><p>end</p>"}, [("A", "c1.xhtml#missing")])

    assert read_chapter(open_book(path), 0) == "start\n\nend"


def test_read_chapter_entry_sharing_start_with_sibling_is_not_empty(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<p>shared</p>", "c2.xhtml": "<p>two</p>"},
        [("A", "c1.xhtml"), ("A again", "c1.xhtml"), ("B", "c2.xhtml")],
    )

    assert read_chapter(open_book(path), 0) == "shared"


@pytest.mark.parametrize("index", [-1, 2])
def test_read_chapter_out_of_range(tmp_path: Path, index: int) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c1.xhtml": "<p>a</p>", "c2.xhtml": "<p>b</p>"}, [("A", "c1.xhtml"), ("B", "c2.xhtml")]
    )

    with pytest.raises(IndexError, match="entries 0-1"):
        read_chapter(open_book(path), index)


def test_read_chapter_last_valid_index(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c1.xhtml": "<p>a</p>", "c2.xhtml": "<p>b</p>"}, [("A", "c1.xhtml"), ("B", "c2.xhtml")]
    )

    assert read_chapter(open_book(path), 1) == "b"


def test_markdown_conversion_snapshot(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {
            "Text/c.xhtml": (
                "<h1>Title</h1><p>Some <b>bold</b> and <i>italic</i>.</p>"
                "<ul><li>one</li><li>two</li></ul>"
                "<p><a href='other.xhtml#x'>internal</a> and <a href='https://example.com'>external</a></p>"
                "<p><img src='../Images/x.png' alt='fig'/></p>"
                "<p>call my_func</p>"
                "<script>bad()</script><style>p {}</style>"
            )
        },
        [("Title", "Text/c.xhtml")],
    )

    assert read_chapter(open_book(path), 0) == (
        "# Title\n\nSome **bold** and *italic*.\n\n- one\n- two\n\n"
        "internal and [external](https://example.com)\n\n![fig](Images/x.png)\n\ncall my_func"
    )


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<table><tr><th>h</th></tr><tr><td><img src='i.png' alt='A'/></td></tr></table>", "![A](Text/i.png)"),
        ("<h2><img src='i.png' alt='B'/></h2>", "## ![B](Text/i.png)"),
        ("<table><tr><th>h</th></tr><tr><td><p><img src='i.png' alt='F'/></p></td></tr></table>", "![F](Text/i.png)"),
        ("<h2><span><img src='i.png' alt='G'/></span> Title</h2>", "## ![G](Text/i.png) Title"),
        (
            "<table><tr><th>h</th></tr>"
            "<tr><td><a href='https://x.org'><img src='i.png' alt='H'/></a></td></tr></table>",
            "[![H](Text/i.png)](https://x.org)",
        ),
        ("<p><img src='../My%20Images/a.png' alt='C'/></p>", "![C](My Images/a.png)"),
        ("<p><img src='https://example.com/a.png' alt='D'/></p>", "![D](https://example.com/a.png)"),
        ("<p>see <img src='data:image/png;base64,AAAA' alt='E'/></p>", "see E"),
    ],
)
def test_image_references(tmp_path: Path, html: str, expected: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"Text/c.xhtml": html}, [("C", "Text/c.xhtml")])

    assert expected in read_chapter(open_book(path), 0)


def test_data_uri_is_not_kept(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p><img src='data:image/png;base64,AAAA' alt='E'/></p>"}, [("C", "c.xhtml")]
    )

    assert "base64" not in read_chapter(open_book(path), 0)


# --- search ---


@pytest.mark.parametrize(
    "text", ["risk/reward ratio", "risk-reward ratio", "Risk  Reward\nratio", "RISK **reward** ratio"]
)
def test_search_ignores_case_and_punctuation_between_words(tmp_path: Path, text: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": f"<p>{text}</p>"}, [("C", "c.xhtml")])

    _hits, total = search(open_book(path), "risk reward ratio", 0, 10)

    assert total == 1


@pytest.mark.parametrize(
    ("text", "query"), [("reward/risk ratio", "risk reward ratio"), ("riskreward", "risk reward"), ("aXb", "a.b")]
)
def test_search_does_not_match(tmp_path: Path, text: str, query: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": f"<p>{text}</p>"}, [("C", "c.xhtml")])

    assert search(open_book(path), query, 0, 10) == ([], 0)


@pytest.mark.parametrize("query", ["", "  ", "/-!"])
def test_search_query_without_words(tmp_path: Path, query: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [("C", "c.xhtml")])

    with pytest.raises(ValueError, match="at least one word"):
        search(open_book(path), query, 0, 10)


def test_search_maps_hits_to_deepest_entry_or_none_before_first(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c0.xhtml": "<p>vwap cover</p>", "c1.xhtml": "<h1>One</h1><p>vwap intro</p><h2 id='a'>A</h2><p>vwap deep</p>"},
        [("One", "c1.xhtml", [("A", "c1.xhtml#a")])],
    )

    hits, total = search(open_book(path), "VWAP", 0, 10)

    assert total == 3
    assert [h.entry.index if h.entry else None for h in hits] == [None, 0, 1]


@pytest.mark.parametrize("query", ["my_func", "MY_FUNC"])
def test_search_finds_words_with_underscores(tmp_path: Path, query: str) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>call my_func now</p>"}, [("C", "c.xhtml")])

    _hits, total = search(open_book(path), query, 0, 10)

    assert total == 1


def test_search_hit_at_shared_start_maps_to_deepest_entry(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"c1.xhtml": "<h2 id='a'>A</h2><p>needle</p>"},
        [("Part", "", [("A", "c1.xhtml#a")])],
    )

    hits, _total = search(open_book(path), "needle", 0, 10)

    assert [h.entry.index if h.entry else None for h in hits] == [1]


def test_search_snippet_snapshot(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>Use the   VWAP\nline.</p>"}, [("C", "c.xhtml")])

    hits, _total = search(open_book(path), "vwap", 0, 10)

    assert hits == [SearchHit(entry=TocEntry(0, 0, "C", "c.xhtml"), snippet="Use the VWAP line.")]


def test_search_snippet_is_truncated_with_ellipses(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": f"<p>{'a' * 300} needle {'b' * 300}</p>"}, [("C", "c.xhtml")])

    hits, _total = search(open_book(path), "needle", 0, 10)

    assert hits[0].snippet == "…" + "a" * 149 + " needle " + "b" * 149 + "…"


@pytest.mark.parametrize(
    ("offset", "max_results", "expected"),
    [
        (0, 2, ["C0", "C1"]),
        (4, 2, ["C4"]),
        (5, 2, []),
        (6, 2, []),
        (0, 4, ["C0", "C1", "C2", "C3"]),
        (0, 5, ["C0", "C1", "C2", "C3", "C4"]),
        (0, 6, ["C0", "C1", "C2", "C3", "C4"]),
    ],
)
def test_search_pagination(tmp_path: Path, offset: int, max_results: int, expected: list[str]) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {f"c{i}.xhtml": f"<p>w{i} needle</p>" for i in range(5)},
        [(f"C{i}", f"c{i}.xhtml") for i in range(5)],
    )

    hits, total = search(open_book(path), "needle", offset, max_results)

    assert total == 5
    assert [h.entry.title if h.entry else None for h in hits] == expected


# --- get_image ---


def test_get_image(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], images={"Images/x.png": (b"\x89PNG", "image/png")}
    )

    assert get_image(open_book(path), "Images/x.png") == (b"\x89PNG", "image/png")


def test_get_image_normalizes_href(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], images={"Images/x.png": (b"\x89PNG", "image/png")}
    )

    assert get_image(open_book(path), "/Text/../Images/x.png") == (b"\x89PNG", "image/png")


def test_get_image_manifest_name_is_normalized(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], images={"./Images/x.png": (b"\x89PNG", "image/png")}
    )

    assert get_image(open_book(path), "Images/x.png") == (b"\x89PNG", "image/png")


def test_get_image_epub3_cover(tmp_path: Path) -> None:
    path = write_epub(
        tmp_path / "b.epub",
        {"cover.xhtml": "<img src='Images/cover.png' alt='Cover'/>"},
        [],
        cover=("Images/cover.png", b"\x89PNG"),
    )

    assert get_image(open_book(path), "Images/cover.png") == (b"\x89PNG", "image/png")


def test_get_image_missing(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [])

    with pytest.raises(KeyError):
        get_image(open_book(path), "Images/none.png")


# --- open_book ---


def test_open_book_caches_until_file_changes(tmp_path: Path) -> None:
    path = write_epub(tmp_path / "b.epub", {"c.xhtml": "<p>x</p>"}, [], title="Old")
    first = open_book(path)

    assert open_book(path) is first

    write_epub(path, {"c.xhtml": "<p>x</p>"}, [], title="New")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert open_book(path).title == "New"


def test_open_book_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_book(tmp_path / "missing.epub")


def test_open_book_not_an_epub(tmp_path: Path) -> None:
    path = tmp_path / "b.epub"
    path.write_text("not a zip")

    with pytest.raises(Exception, match="(?i)zip"):
        open_book(path)
