"""pdf_text.title_guess: the first page's largest-type lines, which the
metadata lookup asks Crossref with before the text head (whose reading
order often starts with a running header or a banner)."""

import pymupdf

from gamma.pdf_text import title_guess

TITLE_1 = "Deterministic generation of multiparticle"
TITLE_2 = "entanglement by quantum Zeno dynamics"


def _pdf(draw, pages=1) -> bytes:
    """A PDF whose first page ``draw(page)`` sets; later pages carry a big
    heading that must not count."""
    doc = pymupdf.open()
    draw(doc.new_page(width=612, height=792))
    for _ in range(pages - 1):
        doc.new_page(width=612, height=792).insert_text((72, 100), "A much larger heading on page two", fontsize=40)
    data = doc.tobytes()
    doc.close()
    return data


def _words(page, x, y, words, size):
    """Each word a span of its own, no space character between them, as
    many PDFs set text."""
    for word in words.split():
        page.insert_text((x, y), word, fontsize=size)
        x += pymupdf.get_text_length(word, fontsize=size) + size * 0.3
    return x


def test_the_title_is_the_largest_horizontal_type_with_words():
    def draw(page):
        page.insert_text((72, 30), "Downloaded from https://journal.example on 1 May 2024", fontsize=7)
        page.insert_text((20, 700), "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", fontsize=24, rotate=90)
        page.insert_text((72, 70), "ARTICLE", fontsize=22)                  # a banner: bigger, one word
        page.insert_text((72, 100), "T", fontsize=44)                       # a drop cap: no size of its own
        page.insert_text((72, 128), TITLE_1, fontsize=18)
        x = _words(page, 72, 150, "entanglement by quantum", 18)
        page.insert_text((x, 150), "Zeno dynamics", fontsize=16)            # italic, a size smaller, same line
        page.insert_text((72, 175), "PHYSICAL REVIEW X 12, 021028 (2022)", fontsize=17)
        page.insert_text((72, 200), "Giovanni Barontini, Jakob Reichel", fontsize=11)
        page.insert_text((72, 230), "Abstract. We generate entanglement among atoms in a cavity.", fontsize=9)
    assert title_guess(_pdf(draw, pages=2)) == f"{TITLE_1} {TITLE_2}"


def test_the_guess_is_capped_at_whole_lines():
    def draw(page):
        page.insert_text((72, 128), TITLE_1, fontsize=18)
        page.insert_text((72, 150), TITLE_2, fontsize=18)
    assert title_guess(_pdf(draw), max_chars=len(TITLE_1) + 5) == TITLE_1


def test_no_text_and_no_pdf_guess_nothing():
    assert title_guess(_pdf(lambda page: None)) == ""
    assert title_guess(b"not a pdf at all") == ""
