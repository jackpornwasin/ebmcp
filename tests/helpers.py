import posixpath
import zipfile
from pathlib import Path
from typing import Any

from ebooklib import epub  # type: ignore[import-untyped]

Toc = list[tuple[Any, ...]]


def write_epub(
    path: Path,
    docs: dict[str, str],
    toc: Toc,
    *,
    title: str | None = "T",
    authors: tuple[str, ...] = (),
    images: dict[str, tuple[bytes, str]] | None = None,
    nav: bool = True,
    cover: tuple[str, bytes] | None = None,
) -> Path:
    """Write an EPUB whose TOC nodes are (title, href) leaves or (title, href, children) sections.

    Each doc is a body fragment, or a whole document if it starts with "<html". It is stored byte-for-byte,
    because ebooklib's writer re-indents markup, which would hide bugs in how real files are read.
    With `nav=False` only the EPUB2 NCX is written, whose hrefs ebooklib returns unnormalized.
    `cover` is a (name, data) image marked `properties="cover-image"`, which ebooklib reads as a cover, not an image.
    """
    book = epub.EpubBook()
    book.set_identifier("id")
    if title is not None:
        book.set_title(title)
    for author in authors:
        book.add_author(author)
    items = []
    for name in docs:
        item = epub.EpubHtml(file_name=name, content="<html><body><p>placeholder</p></body></html>")
        book.add_item(item)
        items.append(item)
    for name, (data, media_type) in (images or {}).items():
        book.add_item(epub.EpubItem(file_name=name, media_type=media_type, content=data))

    if cover:
        book.set_cover(cover[0], cover[1], create_page=False)

    def build(nodes: Toc) -> list[Any]:
        built: list[Any] = []
        for n, node in enumerate(nodes):
            if len(node) == 3:
                built.append((epub.Section(node[0], node[1]), build(node[2])))
            else:
                built.append(epub.Link(node[1], node[0], f"{node[0]}-{n}"))
        return built

    book.toc = build(toc)
    book.add_item(epub.EpubNcx())
    if nav:
        book.add_item(epub.EpubNav())
    book.spine = items
    epub.write_epub(str(path), book)

    raw = {
        posixpath.normpath(f"EPUB/{name}"): doc if doc.startswith("<html") else f"<html><body>{doc}</body></html>"
        for name, doc in docs.items()
    }
    with zipfile.ZipFile(path) as zf:
        entries = {posixpath.normpath(info.filename): zf.read(info) for info in zf.infolist()}
    assert raw.keys() <= entries.keys(), f"docs not found in zip: {raw.keys() - entries.keys()}"
    # Real EPUBs store normalized zip paths; ebooklib's writer keeps "./" segments verbatim.
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", entries.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
        for name, data in entries.items():
            zf.writestr(name, raw.get(name, data), compress_type=zipfile.ZIP_DEFLATED)
    return path
