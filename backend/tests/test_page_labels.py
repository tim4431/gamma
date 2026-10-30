"""Page labels as pdf.js reads them (gamma/pdf_text.py ``read_page_labels``,
``pdf_text_string``, ``GlyphPages.label``), against PDFs built here with
their /PageLabels written as raw PDF syntax.

Each expected label is what pdf.js gives for that file, by its rules:
``Catalog.#readPageLabels`` in src/core/catalog.js (the dictionary's /Type,
/S, /P and /St checks, the styles, a range counting on, a page before any
range ""; any broken rule: no labels, null), ``toRomanNumerals`` in
src/core/core_utils.js, ``NumberTree.getAll`` in src/core/name_number_tree.js
(breadth first, /Kids before /Nums, a kid twice an error, a later pair over
an earlier one) and ``stringToPDFString`` in src/core/string_utils.js (the
UTF-16 and UTF-8 marks, PDFDocEncoding, language escapes). Zotero's reader
labels its annotations with ``getPageLabels``' result, a page without a
label by its number (zotero_annotations.item_label)."""

import time

import pytest

from gamma import pdf_text
from gamma.pdf_text import GlyphPages, pdf_text_string, read_page_labels


def _pdf(pages: int, labels: str | None, objects=(), *, encrypt: bool = False) -> bytes:
    """A PDF of ``pages`` blank pages whose catalog's /PageLabels is
    ``labels`` (raw PDF syntax; None: none), after ``objects``: PDF syntax
    each, written as new objects and named in ``labels`` (and in each
    other) as {0}, {1}, … for their object numbers."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    xrefs = [doc.get_new_xref() for _ in objects]
    for xref, source in zip(xrefs, objects):
        doc.update_object(xref, source.format(*xrefs))
    if labels is not None:
        doc.xref_set_key(doc.pdf_catalog(), "PageLabels", labels.format(*xrefs))
    if encrypt:
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="")
    return doc.tobytes()


def _labels(data: bytes):
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return read_page_labels(doc)


def _page_labels(data: bytes) -> list[str]:
    with GlyphPages(data) as pages:
        return [pages.label(n) for n in range(1, pages.page_count() + 1)]


# --- text strings: stringToPDFString ---------------------------------------------------------

@pytest.mark.parametrize("raw, text", [
    (b"\xfe\xff\x00\x31", "1"),                       # UTF-16BE after its mark
    (b"\xfe\xff\x00A\x00", "A"),                      # an odd last byte dropped
    (b"\xff\xfeA\x00-\x00", "A-"),                    # UTF-16LE after its mark
    (b"\xef\xbb\xbfcaf\xc3\xa9", "café"),        # UTF-8 after its mark (PDF 2.0)
    (b"\x80\x93\x9f\xa0\xad\xe9", "•ﬁ\x9f€\xad\xe9"),  # PDFDocEncoding; 0x9F and 0xAD as Latin-1
    (b"\x18\x1f", "˘˜"),                    # PDFDocEncoding's accents below the space
    (b"\xfe\xff\xd8\x00", "\xfe\xff\xd8\x00"),        # a lone surrogate: PDFDocEncoding, its mark with it
    (b"\xfe\xff\xd8\x00A", "\xfe\xff\xd8\x00"),       # the same, without the odd last byte pdf.js dropped
    (b"\xef\xbb\xbf\xff", "\xef\xbb\xbf\xff"),        # broken UTF-8: the same
    (b"\xfe\xff\x00\x1b\x00e\x00n\x00\x1b\x00X", "X"),  # a language escape left out
    (b"a\x1bfr\x1bb\x1bc", "ab"),                     # ESC to ESC, or to the end
    (b"", ""),
])
def test_a_text_string_decodes_as_pdfjs_decodes_it(raw, text):
    assert pdf_text_string(raw) == text


# --- prefixes ------------------------------------------------------------------------------

def test_a_hex_utf16_prefix_is_decoded_where_pymupdf_gives_it_raw():
    data = _pdf(3, "<</Nums[0<</P<FEFF0031>>>1<</P<FEFF0032>>>2<</S/D/P<FEFF0041002D>/St 3>>]>>")
    assert _page_labels(data) == ["1", "2", "A-3"]
    with GlyphPages(data) as pages:  # PyMuPDF's own reading keeps the hex as written
        assert pages.mupdf_label(1) == "<FEFF0031>"


def test_a_literal_prefix_with_an_escaped_byte_order_mark_is_decoded():
    data = _pdf(2, r"<</Nums[0<</P(\376\377\000B)>>1<</P(\357\273\277caf\303\251 )/S/D>>]>>")
    assert _page_labels(data) == ["B", "café 1"]


def test_a_pdfdocencoding_prefix_and_a_literal_with_escapes():
    data = _pdf(2, r"<</Nums[0<</P(\200 \(x\) \\ end)>>1<</P(Ann\351e-)/S/D/St 9>>]>>")
    assert _page_labels(data) == ["• (x) \\ end", "Année-9"]


def test_a_prefix_alone_when_there_is_no_style_and_nothing_when_there_is_neither():
    data = _pdf(4, "<</Nums[0<</P(Cover)>>2<<>>]>>")
    assert _page_labels(data) == ["Cover", "Cover", "", ""]


def test_an_empty_style_name_is_no_style():
    # pdf.js refuses an unknown style only when its name is not empty (``if (style)``)
    assert _page_labels(_pdf(2, "<</Nums[0<</S/ /P(x)>>]>>")) == ["x", "x"]


def test_pages_before_the_first_range_have_no_label():
    assert _page_labels(_pdf(3, "<</Nums[2<</S/D>>]>>")) == ["", "", "1"]


# --- styles and /St ------------------------------------------------------------------------

def test_each_style_counts_from_its_start():
    data = _pdf(12, "<</Nums[0<</S/r>>3<</S/R/St 4>>4<</S/R/St 1999>>5<</S/A/St 26>>6<</S/a/St 27>>"
                    "7<</S/A/St 52>>8<</S/A>>9<</S/D/St 7>>]>>")
    assert _page_labels(data) == ["i", "ii", "iii", "IV", "MCMXCIX", "Z", "aa", "ZZ", "A", "7", "8", "9"]


def test_letters_repeat_one_more_time_each_26_pages():
    data = _pdf(3, "<</Nums[0<</S/a/St 52>>]>>")
    assert _page_labels(data) == ["zz", "aaa", "bbb"]  # pdf.js repeats the letter: 53 is AAA, not BA


def test_a_start_written_as_a_real_with_no_fraction_is_an_integer():
    assert _page_labels(_pdf(2, "<</Nums[0<</S/D/St 3.0>>]>>")) == ["3", "4"]


def test_a_long_label_is_cut():
    data = _pdf(1, "<</Nums[0<</S/R/St 900000000>>]>>")  # pdf.js writes 900,000 Ms
    (label,) = _page_labels(data)
    assert label == "M" * pdf_text.LABEL_CHARS


# --- the number tree -----------------------------------------------------------------------

def test_kids_are_read_breadth_first_and_a_node_with_kids_ignores_its_nums():
    data = _pdf(6, "{0} 0 R", [
        "<</Kids[{1} 0 R <</Nums[4<</P(inline-)/S/D>>]>>]/Nums[0<</P(never)>>]>>",
        "<</Kids[{2} 0 R]/Limits[0 3]>>",
        "<</Nums[0<</S/r>> 2 {3} 0 R]>>",
        "<</S/A>>",
    ])
    assert _page_labels(data) == ["i", "ii", "A", "B", "inline-1", "inline-2"]


def test_of_two_leaves_with_the_same_key_the_one_read_later_breadth_first_wins():
    # breadth first: the root, A, B, E, then A's kid C. Key 0 is in B and in C, which is deeper and read
    # later (depth first would read it before B); key 1 is in B and in E, its next sibling, read later (a
    # stack would read it before B)
    data = _pdf(3, "{0} 0 R", [
        "<</Kids[{1} 0 R {2} 0 R {3} 0 R]>>",
        "<</Kids[{4} 0 R]>>",
        "<</Nums[0<</P(shallow-)/S/D>>1<</P(b-)/S/D>>]>>",
        "<</Nums[1<</P(e-)/S/D/St 5>>]>>",
        "<</Nums[0<</P(deep-)/S/D>>]>>",
    ])
    assert _page_labels(data) == ["deep-1", "e-5", "e-6"]


def test_a_later_pair_wins_over_an_earlier_one_of_the_same_key_and_a_key_without_value_is_none():
    assert _page_labels(_pdf(2, "<</Nums[0<</P(a)>>0<</P(b)>>]>>")) == ["b", "b"]
    assert _page_labels(_pdf(2, "<</Nums[0<</P(a)>>1<</P(b)>>1]>>")) == ["a", "a"]


def test_a_label_dictionary_by_reference_and_a_type_of_pagelabel():
    data = _pdf(2, "<</Nums[0 {0} 0 R]>>", ["<</Type/PageLabel/S/D/St 5>>"])
    assert _page_labels(data) == ["5", "6"]


# --- no labels -----------------------------------------------------------------------------

def test_a_file_without_page_labels_has_none():
    data = _pdf(2, None)
    assert _labels(data) is None and _page_labels(data) == ["", ""]


@pytest.mark.parametrize("labels, objects", [
    ("<</Nums[0<</S/X>>]>>", ()),                       # a style pdf.js does not know
    ("<</Nums[0<</S/D/St 0>>]>>", ()),                  # /St below 1
    ("<</Nums[0<</S/D/St 2.5>>]>>", ()),                # /St no integer
    ("<</Nums[0<</S(D)>>]>>", ()),                      # /S no name
    ("<</Nums[0<</S null>>]>>", ()),                    # /S null
    ("<</Nums[0<</P/Name>>]>>", ()),                    # /P no string
    ("<</Nums[0<</Type/Label/S/D>>]>>", ()),            # /Type another name
    ("<</Nums[0 5]>>", ()),                             # a label that is no dictionary
    ("<</Nums[0 99999 0 R]>>", ()),                     # a label the file does not have
    ("{0} 0 R", ["<</Kids[{1} 0 R]>>", "<</Kids[{0} 0 R]>>"]),  # a node twice in the tree
    ("<</Kids[{0} 0 R {0} 0 R]>>", ["<</Nums[0<</S/D>>]>>"]),   # a kid twice
])
def test_a_tree_pdfjs_refuses_gives_no_labels_at_all(labels, objects):
    data = _pdf(3, labels, objects)
    assert _labels(data) is None and _page_labels(data) == ["", "", ""]


def test_a_broken_range_past_the_last_page_never_counts():
    # pdf.js reads only the keys of the file's pages: a broken label for page 9 of 2 never matters
    assert _page_labels(_pdf(2, "<</Nums[0<</S/D>>9<</S/X>>]>>")) == ["1", "2"]


def test_a_tree_that_is_no_dictionary_or_a_dictionary_without_nums():
    assert _labels(_pdf(2, "5")) is None                             # pdf.js: every label ""
    assert _labels(_pdf(2, "<<>>")) == ["", ""]
    assert _labels(_pdf(2, "[0 <</S/D>>]")) == ["", ""]             # an array is no tree node
    assert _labels(_pdf(2, "<</Nums 7>>")) == ["", ""]


# --- the files ------------------------------------------------------------------------------

def test_the_labels_of_an_encrypted_file_are_read_decrypted():
    data = _pdf(2, "<</Nums[0<</P<FEFF0031>>>1<</S/r/P(p. )>>]>>", encrypt=True)
    assert b"/Encrypt" in data and b"FEFF0031" not in data.upper()
    assert _page_labels(data) == ["1", "p. i"]


def test_a_large_document_with_a_deep_tree_reads_quickly():
    pages = 3000
    leaves = [f"<</Nums[{' '.join(f'{p}<</S/D/P({p}:)>>' for p in range(start, start + 100))}]>>"
              for start in range(0, pages, 100)]
    data = _pdf_tree(pages, leaves)
    started = time.monotonic()
    labels = _labels(data)
    assert time.monotonic() - started < 5
    assert len(labels) == pages and labels[0] == "0:1" and labels[1234] == "1234:1" and labels[-1] == "2999:1"


def _pdf_tree(pages: int, leaves) -> bytes:
    """``pages`` blank pages under a four-level label tree: a root, its one
    kid, a node for each ten ``leaves`` under it, the leaves."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    leaf_x = [doc.get_new_xref() for _ in leaves]
    for x, source in zip(leaf_x, leaves):
        doc.update_object(x, source)
    mid_x = []
    for m in range(0, len(leaves), 10):
        x = doc.get_new_xref()
        doc.update_object(x, "<</Kids[" + " ".join(f"{k} 0 R" for k in leaf_x[m:m + 10]) + "]>>")
        mid_x.append(x)
    top = doc.get_new_xref()
    doc.update_object(top, "<</Kids[" + " ".join(f"{k} 0 R" for k in mid_x) + "]>>")
    root = doc.get_new_xref()
    doc.update_object(root, f"<</Kids[{top} 0 R]>>")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"{root} 0 R")
    return doc.tobytes()


def test_a_files_labels_are_read_once_for_all_its_pages(monkeypatch):
    calls = []
    read = pdf_text.read_page_labels
    monkeypatch.setattr(pdf_text, "read_page_labels", lambda pdf: calls.append(1) or read(pdf))
    with GlyphPages(_pdf(3, "<</Nums[0<</S/D>>]>>")) as pages:
        assert [pages.label(n) for n in (1, 2, 3, 2, 4, 0)] == ["1", "2", "3", "2", "", ""]
    assert calls == [1]


def test_a_file_that_does_not_open_has_no_labels():
    with GlyphPages(b"not a pdf") as pages:
        assert pages.label(1) == "" and pages.mupdf_label(1) == ""
