"""Content for the fake Zotero (zotero_fake.py): a small paper PDF with a
real text layer, and ``standard_library``, a library a Zotero link's
engine can be tested against, one of each kind of thing it meets.

``pdf_bytes()`` is a two-page US Letter paper (612 × 792) typeset with
PyMuPDF in Helvetica: ``PAGES`` lists each line as ``(x, baseline from the
top, size, text)``. Like a file Zotero stores, it carries no embedded
annotation: Zotero keeps its reader's annotations as items. The ``*_RECT``
constants are boxes over its text in PDF user space (bottom-left origin),
as an ``annotationPosition``'s rects are, and the ``*_TEXT`` what lies
under them (test_zotero_web checks it). The annotations copy the shapes
Zotero's reader writes (compact JSON, the keys in its order, no
``annotationText`` but on a highlight or underline), and their sort
indexes are the reader's for the fixture PDF (test_zotero_web computes
them by the reader's rule). The notes are the note editor's HTML; one
shows a pasted picture as the editor keeps one, an ``embedded_image``
attachment under the note that ``data-attachment-key`` names.
"""

import base64
import functools
import json

PAGE_SIZE = (612, 792)
CAROL = 456  # the group library's other member
PAGES = [
    [(72, 80, 18, "Attention Is All You Need"),
     (72, 120, 11, "The dominant sequence transduction models are based on recurrent networks."),
     (72, 136, 11, "We propose the Transformer, based solely on attention mechanisms."),
     (72, 152, 11, "Experiments on two machine translation tasks show superior quality.")],
    [(72, 80, 11, "Self-attention relates different positions of a single sequence"),
     (72, 96, 11, "in order to compute a representation of the sequence.")],
]
TITLE_RECT = [72.0, 706.618, 272.106, 731.35]
TITLE_TEXT = "Attention Is All You Need"
HIGHLIGHT_RECT = [94.011, 668.711, 291.494, 683.825]
HIGHLIGHT_TEXT = "dominant sequence transduction models"
UNDERLINE_RECT = [218.718, 652.711, 405.19, 667.825]
UNDERLINE_TEXT = "based solely on attention mechanisms"
# A passage over two lines of page 2: one rect per line, as the reader writes it.
SPANNING_RECTS = [[72.0, 708.711, 366.096, 723.825], [72.0, 692.711, 249.309, 707.825]]
SPANNING_TEXT = "Self-attention relates different positions of a single sequence in order to compute a representation"


@functools.lru_cache(maxsize=32)
def pdf_bytes(text: str = "") -> bytes:
    """The standard paper; with ``text``, a one-page PDF of that one line
    instead (an upload's own file). The same bytes every time."""
    import pymupdf  # local: only the tests that store files need it

    pages = [[(72, 72, 12, text)]] if text else PAGES
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
        for x, y, size, line in lines:
            page.insert_text((x, y), line, fontname="helv", fontsize=size)
    return doc.tobytes(no_new_id=True)


def _person(first: str, last: str, role: str = "author") -> dict:
    return {"creatorType": role, "firstName": first, "lastName": last}


def _position(page: int, **shape) -> str:
    """An ``annotationPosition`` as the reader writes it: compact JSON,
    ``pageIndex`` first."""
    return json.dumps({"pageIndex": page, **shape}, separators=(",", ":"))


def _annotation(parent: str, kind: str, color: str, sort: str, position: str, *, label: str = "1",
                comment: str = "", **fields) -> dict:
    """An annotation item's data, its keys in the reader's order."""
    return {"itemType": "annotation", "parentItem": parent, "annotationType": kind, **fields,
            "annotationComment": comment, "annotationColor": color, "annotationPageLabel": label,
            "annotationSortIndex": sort, "annotationPosition": position}


def _note(html: str) -> str:
    """A note as Zotero's note editor saves it."""
    return f'<div data-schema-version="9">{html}</div>'


# A 1×1 PNG: the picture pasted into a note.
PICTURE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
                           "hKmMIQAAAABJRU5ErkJggg==")
# The key of that picture's attachment, which the note's HTML names.
PICTURE_KEY = "PICT2345"


def standard_library(fake) -> dict:
    """Fill ``fake``'s My Library and a group "Lab" (``groups/9``, with a
    second member, carol, ``CAROL``); ``{"library", "group", "keys": {name:
    key}}``.

    Collections: "ML" > "Transformers" > "Efficient", and "Reading". Items:

    - ``attention``: journalArticle "Attention is all you need" (two
      authors and an editor, a full date, a DOI, its citationKey, tags
      "transformers" and an automatic "attention"), in Transformers. Its
      children: ``pdf``, the primary PDF (imported_file, the oldest), with
      the annotations ``highlight`` (``HIGHLIGHT_RECT``, a comment),
      ``underline`` (a comment with markup), ``note_annotation``,
      ``image_annotation``, ``ink_annotation``, ``text_annotation`` on page
      1 and ``spanning`` (two lines of page 2, page label "2");
      ``supplement``, a second PDF added later; ``note`` and ``image_note``
      (a pasted picture, ``image``: its ``embedded_image`` attachment,
      ``PICTURE_KEY``, holding ``PICTURE``), child notes; ``snapshot``, a
      zipped web page (imported_url, text/html); ``link``, a linked URL;
    - ``ppo``: preprint (arXiv:1707.06347) in ML, no file;
    - ``deep_learning``: book (ISBN, MIT Press), unfiled, tag "textbook";
    - ``thesis``: a PhD thesis (university), in Reading;
    - ``conference``: conferencePaper whose cite key is only an Extra line,
      in Efficient and Reading, its PDF ``linked_pdf`` a linked file (on
      its desktop only: Zotero's storage has none);
    - ``standalone_pdf``: a top-level PDF attachment with its file, in ML;
    - ``standalone_note``: a top-level note, in Reading;
    - ``trashed``: a journalArticle in the trash;
    - in the group: ``group_item`` with ``group_pdf``, holding ``mine`` (a
      highlight of the user's) and ``carols`` (one of carol's).
    """
    lib = fake.library
    keys = {}
    keys["ml"] = fake.add_collection(lib, "ML")
    keys["transformers"] = fake.add_collection(lib, "Transformers", parent=keys["ml"])
    keys["efficient"] = fake.add_collection(lib, "Efficient", parent=keys["transformers"])
    keys["reading"] = fake.add_collection(lib, "Reading")

    keys["attention"] = article = fake.add_item(lib, {
        "itemType": "journalArticle", "title": "Attention is all you need",
        "creators": [_person("Ashish", "Vaswani"), _person("Noam", "Shazeer"), _person("Jane", "Editor", "editor")],
        "date": "2017-06-12", "publicationTitle": "Nature", "volume": "647", "pages": "1-10",
        "DOI": "10.1038/s41586-000-00000-0", "citationKey": "vaswani2017attention",
        "url": "https://www.nature.com/articles/s41586-000-00000-0",
        "tags": [{"tag": "transformers"}, {"tag": "attention", "type": 1}],
        "collections": [keys["transformers"]]})
    keys["pdf"] = pdf = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": article, "linkMode": "imported_file", "title": "Full Text PDF",
        "contentType": "application/pdf", "filename": "Vaswani - 2017 - Attention is all you need.pdf",
        "dateAdded": "2024-01-10T09:00:00Z", "dateModified": "2024-01-10T09:00:00Z"})
    fake.add_file(lib, pdf, pdf_bytes())
    keys["supplement"] = supplement = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": article, "linkMode": "imported_file", "title": "Supplement",
        "contentType": "application/pdf", "filename": "supplement.pdf",
        "dateAdded": "2024-02-01T09:00:00Z", "dateModified": "2024-02-01T09:00:00Z"})
    fake.add_file(lib, supplement, pdf_bytes("Supplementary material."))
    keys["note"] = fake.add_item(lib, {"itemType": "note", "parentItem": article, "note": _note(
        "<h1>Key points</h1><p>Read this <strong>twice</strong>, then <em>once more</em>.</p>"
        "<ul><li>Self-attention</li><li>Positional encoding</li></ul>")})
    keys["image_note"] = image_note = fake.add_item(lib, {"itemType": "note", "parentItem": article, "note": _note(
        f'<p>The architecture:</p>\n<img alt="architecture" data-attachment-key="{PICTURE_KEY}" width="320" '
        'height="200">\n')})
    keys["image"] = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": image_note, "linkMode": "embedded_image",
        "contentType": "image/png", "filename": "image.png"}, key=PICTURE_KEY)
    fake.add_file(lib, PICTURE_KEY, PICTURE)
    keys["snapshot"] = snapshot = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": article, "linkMode": "imported_url", "title": "Nature Snapshot",
        "url": "https://www.nature.com/articles/s41586-000-00000-0", "accessDate": "2024-01-10T09:00:00Z",
        "contentType": "text/html", "charset": "utf-8", "filename": "s41586-000-00000-0.html"})
    fake.add_file(lib, snapshot, b"<html><body>Attention is all you need</body></html>", zipped=True)
    keys["link"] = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": article, "linkMode": "linked_url", "title": "Project page",
        "url": "https://example.org/attention", "contentType": "text/html"})

    keys["highlight"] = fake.add_item(lib, _annotation(
        pdf, "highlight", "#ffd400", "00000|000024|00108", _position(0, rects=[HIGHLIGHT_RECT]),
        comment="great point", annotationText=HIGHLIGHT_TEXT))
    keys["underline"] = fake.add_item(lib, _annotation(
        pdf, "underline", "#2ea8e5", "00000|000110|00124", _position(0, rects=[UNDERLINE_RECT]),
        comment="see <i>Section 3</i>", annotationText=UNDERLINE_TEXT))
    keys["note_annotation"] = fake.add_item(lib, _annotation(
        pdf, "note", "#5fb236", "00000|000085|00118", _position(0, rects=[[540, 652, 562, 674]]),
        comment="Check the appendix"))
    keys["image_annotation"] = fake.add_item(lib, _annotation(
        pdf, "image", "#a28ae5", "00000|000143|00180", _position(0, rects=[[72, 420, 300, 612]]),
        comment="Figure 1"))
    keys["ink_annotation"] = fake.add_item(lib, _annotation(
        pdf, "ink", "#ff6666", "00000|000147|00157", _position(0, width=2, paths=[
            [100, 630, 103.5, 633.2, 110, 634.8, 118.25, 632.1, 125, 630.4], [140, 628, 160.75, 629.5]])))
    keys["text_annotation"] = fake.add_item(lib, _annotation(
        pdf, "text", "#2ea8e5", "00000|000189|00300", _position(0, fontSize=14, rotation=0,
                                                                rects=[[350, 470, 500, 492]]),
        comment="A text box"))
    keys["spanning"] = fake.add_item(lib, _annotation(
        pdf, "highlight", "#5fb236", "00001|000000|00068", _position(1, rects=SPANNING_RECTS), label="2",
        annotationText=SPANNING_TEXT))

    keys["ppo"] = fake.add_item(lib, {
        "itemType": "preprint", "title": "Proximal Policy Optimization Algorithms",
        "creators": [_person("John", "Schulman"), _person("Filip", "Wolski")], "date": "2017-07-20",
        "archiveID": "arXiv:1707.06347", "repository": "arXiv", "url": "https://arxiv.org/abs/1707.06347",
        "collections": [keys["ml"]]})
    keys["deep_learning"] = fake.add_item(lib, {
        "itemType": "book", "title": "Deep Learning",
        "creators": [_person("Ian", "Goodfellow"), _person("Yoshua", "Bengio"), _person("Aaron", "Courville")],
        "date": "2016", "ISBN": "9780262035613", "publisher": "MIT Press", "place": "Cambridge, MA",
        "tags": [{"tag": "textbook"}]})
    keys["thesis"] = fake.add_item(lib, {
        "itemType": "thesis", "title": "Quantum error correction with bosonic codes",
        "creators": [_person("Alice", "Example")], "date": "2019", "thesisType": "PhD thesis",
        "university": "Yale University", "collections": [keys["reading"]]})
    keys["conference"] = fake.add_item(lib, {
        "itemType": "conferencePaper", "title": "Retrieval-augmented generation for knowledge-intensive tasks",
        "creators": [_person("Patrick", "Lewis"), _person("Ethan", "Perez")], "date": "2020",
        "proceedingsTitle": "Advances in Neural Information Processing Systems", "pages": "9459-9474",
        "extra": "Citation Key: lewis2020", "collections": [keys["efficient"], keys["reading"]]})
    keys["linked_pdf"] = fake.add_item(lib, {
        "itemType": "attachment", "parentItem": keys["conference"], "linkMode": "linked_file",
        "title": "Lewis - 2020 - Retrieval-augmented generation.pdf", "contentType": "application/pdf",
        "path": "/Users/tim/Papers/lewis2020.pdf"})
    keys["standalone_pdf"] = standalone = fake.add_item(lib, {
        "itemType": "attachment", "linkMode": "imported_file", "title": "Scanned handout",
        "contentType": "application/pdf", "filename": "handout.pdf", "collections": [keys["ml"]]})
    fake.add_file(lib, standalone, pdf_bytes("A standalone handout without metadata."))
    keys["standalone_note"] = fake.add_item(lib, {"itemType": "note", "collections": [keys["reading"]], "note": _note(
        "<h1>Ideas for the next project</h1><p>Try sparse attention on long documents.</p>")})
    keys["trashed"] = fake.add_item(lib, {
        "itemType": "journalArticle", "title": "Retracted results", "creators": [_person("Bob", "Gone")],
        "date": "2015"})
    fake.trash(lib, keys["trashed"])

    group = fake.add_group(9, "Lab", members={CAROL: "carol"})
    keys["group_item"] = group_item = fake.add_item(group, {
        "itemType": "journalArticle", "title": "Group reading", "creators": [_person("Carol", "Member")],
        "date": "2021", "publicationTitle": "Physical Review Letters"})
    keys["group_pdf"] = group_pdf = fake.add_item(group, {
        "itemType": "attachment", "parentItem": group_item, "linkMode": "imported_file", "title": "PDF",
        "contentType": "application/pdf", "filename": "group-reading.pdf"})
    fake.add_file(group, group_pdf, pdf_bytes())
    keys["mine"] = fake.add_item(group, _annotation(
        group_pdf, "highlight", "#ffd400", "00000|000000|00060", _position(0, rects=[TITLE_RECT]),
        annotationText=TITLE_TEXT))
    with fake.acting_as(CAROL):
        keys["carols"] = fake.add_item(group, _annotation(
            group_pdf, "highlight", "#ff6666", "00000|000024|00108", _position(0, rects=[HIGHLIGHT_RECT]),
            comment="carol's reading", annotationText=HIGHLIGHT_TEXT))
    return {"library": lib, "group": group, "keys": keys}


TAG_POOL = [f"topic {n}" for n in range(40)]


def generated_library(fake, items: int, *, pdfs: int = 0, annotations: int = 0, notes: int = 0,
                      collections: int = 0) -> dict:
    """A made-up library of any size in ``fake``'s My Library, for the scale
    tests: ``collections`` collections (every fourth under the one before
    it), then ``items`` journal articles "Paper <n>" (a DOI each, filed in
    one collection, two of ``TAG_POOL``'s manual tags and an automatic one);
    the first ``pdfs`` of them with a one-page PDF of their own
    (``pdf_bytes("Paper <n>")``) holding ``annotations`` highlights each, and
    the first ``notes`` with a child note. ``{"items", "pdfs",
    "annotations", "notes", "collections"}``: the keys made, by kind."""
    lib = fake.library
    made = {"items": [], "pdfs": [], "annotations": [], "notes": [], "collections": []}
    for n in range(collections):
        parent = made["collections"][-1] if n % 4 == 3 else None
        made["collections"].append(fake.add_collection(lib, f"Shelf {n}", parent=parent))
    for n in range(items):
        tags = [{"tag": TAG_POOL[n % len(TAG_POOL)]}, {"tag": TAG_POOL[(n * 7 + 3) % len(TAG_POOL)]},
                {"tag": f"auto {n % 13}", "type": 1}]
        key = fake.add_item(lib, {
            "itemType": "journalArticle", "title": f"Paper {n}", "date": str(1990 + n % 35),
            "creators": [_person("Ada", f"Author{n % 97}")], "publicationTitle": f"Journal {n % 11}",
            "DOI": f"10.5555/paper.{n}", "tags": tags,
            "collections": [made["collections"][n % collections]] if collections else []})
        made["items"].append(key)
        if n < pdfs:
            att = fake.add_item(lib, {
                "itemType": "attachment", "parentItem": key, "linkMode": "imported_file", "title": "Full Text PDF",
                "contentType": "application/pdf", "filename": f"paper-{n}.pdf"})
            fake.add_file(lib, att, pdf_bytes(f"Paper {n}"))
            made["pdfs"].append(att)
            for a in range(annotations):
                top = 700.0 - 40 * a
                made["annotations"].append(fake.add_item(lib, _annotation(
                    att, "highlight", "#ffd400", f"00000|{a:06d}|{int(792 - top - 14):05d}",
                    _position(0, rects=[[72.0, top, 160.0, top + 14]]), comment=f"note {a} on paper {n}",
                    annotationText=f"Paper {n}")))
        if n < notes:
            made["notes"].append(fake.add_item(lib, {"itemType": "note", "parentItem": key, "note": _note(
                f"<h1>Reading paper {n}</h1><p>First thought on paper {n}.</p><p>Second thought.</p>")}))
    return made
