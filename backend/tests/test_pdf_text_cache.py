"""The page-text cache (gamma/pdf_text.py): a stored PDF's pages, once
extracted, are not extracted again — the AI chat reads a paper on every
turn — and the cache keeps to its bounds and to the file it read."""

import os

import pytest

from gamma import pdf_text
from test_pdfium_lock import _text_pdf


@pytest.fixture(autouse=True)
def _fresh_cache():
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


@pytest.fixture
def opens(monkeypatch):
    """How often the file was opened and how many pages pdfium read."""
    counts = {"open": 0, "pages": 0}
    real_open, real_read = pdf_text._open, pdf_text._read_page

    def counted_open(src):
        counts["open"] += 1
        return real_open(src)

    def counted_read(pdf, i, read):
        counts["pages"] += 1
        return real_read(pdf, i, read)

    monkeypatch.setattr(pdf_text, "_open", counted_open)
    monkeypatch.setattr(pdf_text, "_read_page", counted_read)
    return counts


def test_a_second_read_of_a_file_opens_nothing(tmp_path, opens):
    path = tmp_path / "book.pdf"
    path.write_bytes(_text_pdf(6, "book"))
    first = pdf_text.extract_pages(str(path))
    assert [t.strip() for t in first] == [f"book page {i}" for i in range(1, 7)]
    assert opens == {"open": 1, "pages": 6}
    assert pdf_text.extract_pages(str(path)) == first
    assert pdf_text.extract_text(str(path), 10_000) == "\n\n".join(first)
    assert pdf_text.page_count(str(path)) == 6
    assert opens == {"open": 1, "pages": 6}, "all from the cache"


def test_a_head_read_fills_the_head_and_a_later_window_reads_only_the_rest(tmp_path, opens):
    path = tmp_path / "book.pdf"
    path.write_bytes(_text_pdf(10, "book"))
    text, pages = pdf_text.extract_text_pages(str(path), 20)  # one page's text is 11 chars: two pages
    assert pages == 2 and opens["pages"] == 2
    # The chat's next window starts at page 3: pages 3.. are read, 1 and 2 are not again.
    rest = list(pdf_text.iter_page_texts(str(path), start_page=3))
    assert len(rest) == 8 and opens["pages"] == 10
    assert [t.strip() for t in pdf_text.extract_pages(str(path))] == [f"book page {i}" for i in range(1, 11)]
    assert opens["pages"] == 10


def test_a_file_rewritten_in_place_is_read_again(tmp_path, opens):
    path = tmp_path / "paper.pdf"
    path.write_bytes(_text_pdf(2, "one"))
    assert pdf_text.extract_pages(str(path))[0].strip() == "one page 1"
    path.write_bytes(_text_pdf(3, "two"))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))  # a filesystem with coarse stamps
    assert [t.strip() for t in pdf_text.extract_pages(str(path))] == ["two page 1", "two page 2", "two page 3"]
    assert pdf_text.page_count(str(path)) == 3


def test_bytes_and_cache_false_leave_nothing_behind(tmp_path, opens):
    data = _text_pdf(3, "mem")
    assert len(pdf_text.extract_pages(data)) == 3
    assert len(pdf_text.extract_pages(data)) == 3
    assert opens == {"open": 2, "pages": 6}
    path = tmp_path / "indexed.pdf"
    path.write_bytes(data)
    assert len(pdf_text.extract_pages(str(path), cache=False)) == 3
    assert len(pdf_text.extract_pages(str(path), cache=False)) == 3
    assert opens == {"open": 4, "pages": 12}
    assert pdf_text._text_cache == {}


def test_the_cache_drops_the_least_recently_read_file_past_its_bounds(tmp_path, opens, monkeypatch):
    monkeypatch.setattr(pdf_text, "TEXT_CACHE_CHARS", 40)  # about three pages of "tag page N"
    paths = []
    for tag in ("aa", "bb", "cc"):
        path = tmp_path / f"{tag}.pdf"
        path.write_bytes(_text_pdf(2, tag))
        paths.append(path)
        pdf_text.extract_pages(str(path))
    assert opens["open"] == 3
    keys = [k[0] for k in pdf_text._text_cache]
    assert str(paths[0]) not in keys and str(paths[2]) in keys, "the oldest went, the newest stays"
    pdf_text.extract_pages(str(paths[2]))
    assert opens["open"] == 3, "the newest is still served from the cache"
    pdf_text.extract_pages(str(paths[0]))
    assert opens["open"] == 4, "the evicted one is read again"
