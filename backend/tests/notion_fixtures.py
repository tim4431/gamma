"""Notion exports for tests, from the folder of this name.

``roundtrip_html`` and ``roundtrip_md`` are the two exports of one synthetic
page, made in Notion in November 2026: every file as it came, but for the
stylesheet and the favicon, which the HTML pages no longer carry, the
``data-notion-space-id`` on their links, and the workspace member in the Person
column, who is ``Ada Example`` now. ``older`` is a page written by hand in the
markup of exports from 2021 to 2024, with no text from any real one: what the
two exports above have too little of, the eras' other shapes and colour (the
Notion page has no coloured text). Its files are read and zipped byte for byte
(``.gitattributes`` there keeps Git's hands off the line ends).

``page`` puts a body in a page, as the export writes one.
"""

import functools
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent / "notion_fixtures"
PAGE_ID = "40f85cc7-97e7-8099-b2d9-fe6f9e045640"


def page(body: str = "", header: str = "") -> str:
    """A page of the export around ``body``, its markup; ``header`` goes
    after the title."""
    return (f'<html><head><title>A page</title></head><body><article id="{PAGE_ID}" class="page sans">'
            f'<header><h1 class="page-title" dir="auto">A page</h1>{header}</header>'
            f'<div class="page-body">{body}</div></article></body></html>')


@functools.lru_cache
def files(name: str) -> dict:
    """A fixture's files: {path in the export: bytes}, in sorted order."""
    folder = ROOT / name
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def export_zip(name: str) -> bytes:
    """A fixture as the zip Notion gives."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files(name).items():
            zf.writestr(path, data)
    return out.getvalue()


def html_page(name: str, start: str) -> str:
    """The text of the fixture's page whose file name starts ``start``."""
    return next(data for path, data in files(name).items()
                if path.rpartition("/")[2].startswith(start)).decode("utf-8")
