"""What a share link's export holds, in every format: never the text, the
title or a file of a page outside the share (routers/export.py
``_ref_resolver``, ``_Builder._can_bundle``). The modes come from ``_BUILDERS``,
so a new format is held to it from the day it exists; each is asked of the
owner too, so that a format this reader cannot see into fails rather than
passes. The owner's export is unchanged: there the same refs still read."""

import io
import zipfile

import fitz
import pytest

from conftest import login, make_folder, make_user
from gamma.auth import ShareScope
from gamma.obsidian_export import anchor_marker
from gamma.routers import export as export_router
from test_ink import _bytes, _ink
from test_pdf_export import _blank_pdf, _position

SECRET = "TOPSECRETWORD"            # a block on the private page
HIDDEN_TITLE = "Hiddentitleword"    # the private page's title
OPEN = "Openword"                   # a block on a page the share reaches
BOX = {"x": 60, "y": 560, "w": 300, "h": 60, "auto": False, "size": 12, "color": "#1f1f1f"}
SHEET = {"width": 595, "height": 842, "pattern": "blank"}

MODES = sorted(export_router._BUILDERS)
# The modes that read a [[ref]] as the text it names: the others write it as
# it is stored, so their owner's export holds no secret either.
READS_REFS = {"annotated-pdf", "notability", "notes-pdf", "notion", "obsidian", "readable", "zotero-rdf"}
# The modes that bundle an embed's image as a file; a notes PDF draws it.
BUNDLES_IMAGES = {"notion", "obsidian", "readable"}


def _png(color: int) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pixmap.clear_with(color)
    return pixmap.tobytes("png")


PRIVATE_PNG, OPEN_PNG = _png(40), _png(200)


def _upload(c, png) -> str:
    up = c.post("/api/upload-image", files={"file": ("p.png", png, "image/png")})
    assert up.status_code == 200, up.text
    return up.json()["url"]


def _page(c, title, properties=None, **body) -> str:
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    if properties:
        assert c.put(f"/api/blocks/{r.json()['id']}", json={"properties": properties}).status_code == 200
    return r.json()["id"]


def _note(text, *children, **properties) -> dict:
    return {"content": text, "properties": properties, "children": list(children)}


def _put(c, page_id, nodes) -> None:
    def numbered(items, at):
        return [{"id": f"{at}-{n}", **item, "children": numbered(item["children"], f"{at}-{n}")}
                for n, item in enumerate(items)]
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": numbered(nodes, page_id)})
    assert r.status_code == 200, r.text


def _share(c, path, **settings) -> str:
    r = c.post(f"/api/share/{path}", json={"audience": "anyone", "role": "view", **settings})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _flat(blob: bytes) -> bytes:
    """All an export says, whatever it is packed in: a zip's member names and
    what each holds, nested zips too (a Notability note), and a PDF's text,
    its annotations' and every object's source beside its raw bytes."""
    if zipfile.is_zipfile(io.BytesIO(blob)):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return b"\n".join(name.encode() + b"\n" + _flat(z.read(name)) for name in z.namelist())
    if not blob.startswith(b"%PDF-"):
        return blob
    with fitz.open(stream=blob, filetype="pdf") as doc:
        words = [page.get_text() for page in doc]
        words += [annot.info["content"] for page in doc for annot in page.annots()]
        words += [doc.xref_object(xref) for xref in range(1, doc.xref_length())]
    return blob + "\n".join(words).encode()


def _drawn_images(blob: bytes) -> int:
    with fitz.open(stream=blob, filetype="pdf") as doc:
        return sum(len(page.get_images()) for page in doc)


@pytest.fixture(scope="module")
def world():
    make_user("sx_owner", "sx-owner-pw")
    owner = login("sx_owner", "sx-owner-pw")
    make_user("sx_stranger", "sx-stranger-pw")

    private = _page(owner, HIDDEN_TITLE)
    private_image = _upload(owner, PRIVATE_PNG)
    _put(owner, private, [_note(f"{SECRET} words"), _note(f"![private]({private_image})"),
                          _note(f"{SECRET} on the sheet")])
    outside = f"{private}-0"

    folder = make_folder(owner, "SX shared")
    doc = owner.post("/api/uploads", files={"file": ("p.pdf", _blank_pdf(), "application/pdf")}).json()["doc_id"]
    paper = _page(owner, "Open paper", {"doc_id": doc, "meta": {"title": "Open paper", "year": 2026}}, folders=[folder])
    other = _page(owner, "Open other", parent_page=paper)
    # One step further: a block of the share naming a block outside it.
    _put(owner, other, [_note(f"{OPEN} with [[{outside}]] in it")])
    _put(owner, paper, [
        _note(f"![[{outside}]]"),
        _note(f"a mention [[{outside}]] in a line"),
        _note(f"![[{private}-1]]"),
        _note(f"[[{private}]]"),
        _note(f"![[{private}]]"),
        _note(f"![[{other}-0]]"),
        _note(f"![seen]({_upload(owner, OPEN_PNG)})"),
        _note(f"![[{outside}]]\n[[{other}-0]]", text_box=BOX, pdf_page=1),
        _note(f"[[{outside}]]", _note(f"![[{outside}]]"),
              quote="a passage", color="#ffee00", pdf_position=_position()),
        _note("", link_page_id=private, quote="", pdf_position=_position(y1=120, y2=140)),
    ])
    r = owner.post(f"/api/pages/{paper}/comments", json={"block_id": f"{paper}-8", "body": f"see [[{outside}]]"})
    assert r.status_code == 201, r.text
    notebook = _page(owner, "Open notebook", parent_page=paper)
    _put(owner, notebook, [_note("", _note(f"![[{private}-2]]", text_box=BOX), sheet=SHEET)])
    return {"owner": owner, "private": private, "private_image": private_image, "paper": paper,
            "notebook": notebook, "folder": folder,
            "page_token": _share(owner, paper), "folder_token": _share(owner, f"folder/{folder}"),
            "users_token": _share(owner, paper, audience="users")}


def _url(world, target, mode) -> tuple[str, dict]:
    """The export of ``target``, a whole share's worth: a page with the pages
    inside it, a folder, or a page opened through a folder share."""
    if target == "folder":
        return f"/api/folders/{world['folder']}/export", {"mode": mode}
    return f"/api/pages/{world['paper']}/export", {"mode": mode, "sub_pages": 1, "comments": 1}


TARGETS = {"page": "page_token", "folder": "folder_token", "page-in-folder": "folder_token"}
_owner_exports = {}


def _owners(world, target, mode) -> bytes:
    key = (target, mode)
    if key not in _owner_exports:
        path, params = _url(world, target, mode)
        r = world["owner"].get(path, params=params)
        assert r.status_code == 200, r.text
        _owner_exports[key] = r.content
    return _owner_exports[key]


def _assert_holds_nothing_outside(blob: bytes) -> None:
    said = _flat(blob)
    assert SECRET.encode() not in said
    assert HIDDEN_TITLE.encode() not in said
    assert PRIVATE_PNG not in said


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("mode", MODES)
def test_an_anonymous_visitor_gets_nothing_outside_the_share(world, anon, target, mode):
    path, params = _url(world, target, mode)
    r = anon.get(path, params={**params, "share": world[TARGETS[target]]})
    assert r.status_code == 200, r.text
    _assert_holds_nothing_outside(r.content)
    assert HIDDEN_TITLE not in r.headers.get("content-disposition", "")

    owner = _flat(_owners(world, target, mode))
    assert (SECRET.encode() in owner) == (mode in READS_REFS)
    assert (PRIVATE_PNG in owner) == (mode in BUNDLES_IMAGES)
    if mode in READS_REFS:                      # the share's own refs still read
        assert OPEN.encode() in _flat(r.content)
    if mode in BUNDLES_IMAGES:                  # and its own files are packed
        assert OPEN_PNG in _flat(r.content)


@pytest.mark.parametrize("mode", MODES)
def test_a_signed_in_visitor_of_a_share_for_any_account_is_held_to_it_too(world, mode):
    stranger = login("sx_stranger", "sx-stranger-pw")
    path, params = _url(world, "page", mode)
    r = stranger.get(path, params={**params, "share": world["users_token"]})
    assert r.status_code == 200, r.text
    _assert_holds_nothing_outside(r.content)


def test_a_notes_pdf_draws_no_image_of_a_page_outside_the_share(world, anon):
    path, params = _url(world, "page", "notes-pdf")
    owner = world["owner"].get(path, params=params)
    shared = anon.get(path, params={**params, "share": world["page_token"]})
    assert _drawn_images(owner.content) == 2 and _drawn_images(shared.content) == 1


@pytest.mark.parametrize("params", [{"notes": 1, "comments": 1}, {"notes": 1, "notes_at": "page"},
                                    {"notes": 1, "sub_pages": 1}, {"highlights": 0, "notes": 1}])
def test_the_annotated_pdf_route_stays_inside_the_share(world, anon, params):
    path = f"/api/pages/{world['paper']}/export-pdf"
    shared = anon.get(path, params={**params, "share": world["page_token"]})
    assert shared.status_code == 200, shared.text
    _assert_holds_nothing_outside(shared.content)
    assert SECRET.encode() in _flat(world["owner"].get(path, params=params).content)


def test_a_notebook_s_pdf_stays_inside_the_share(world, anon):
    path = f"/api/pages/{world['notebook']}/export-pdf"
    shared = anon.get(path, params={"share": world["page_token"]})
    assert shared.status_code == 200, shared.text
    _assert_holds_nothing_outside(shared.content)
    assert SECRET.encode() in _flat(world["owner"].get(path).content)


@pytest.mark.parametrize("mode", ["readable", "obsidian", "notes-pdf", "notion"])
def test_a_ref_out_of_the_share_reads_as_the_id_the_share_view_shows(world, anon, mode):
    """Readable as it is typed, or in a PDF as its bare id, the chip the share
    view itself draws for a block it cannot read."""
    path, params = _url(world, "page", mode)
    r = anon.get(path, params={**params, "share": world["page_token"]})
    assert f"{world['private']}-0".encode() in _flat(r.content).replace(b"\\", b"")


def test_a_sub_page_s_share_does_not_reach_the_page_around_it(world, anon):
    owner = world["owner"]
    around = _page(owner, "Around page")
    _put(owner, around, [_note(f"{SECRET} around")])
    inner = _page(owner, "Inner page", parent_page=around)
    _put(owner, inner, [_note(f"![[{around}-0]]"), _note(f"[[{around}]]")])
    token = _share(owner, inner)
    for mode in MODES:
        r = anon.get(f"/api/pages/{inner}/export", params={"mode": mode, "share": token})
        if r.status_code != 400:                # no PDF, no paper metadata
            assert r.status_code == 200, r.text
            said = _flat(r.content)
            assert SECRET.encode() not in said and b"Around page" not in said, mode


@pytest.mark.parametrize("mode", sorted(BUNDLES_IMAGES))
def test_a_zip_goes_by_what_the_share_can_read_not_by_what_a_ref_says(world, anon, monkeypatch, mode):
    """The file stays out on its own rule: were the resolver ever to let a
    private block through, ``_can_bundle`` still would not pack its image."""
    monkeypatch.setattr(export_router, "_ref_resolver", lambda conn, reach=None: export_router.block_ref_resolver(conn))
    path, params = _url(world, "page", mode)
    r = anon.get(path, params={**params, "share": world["page_token"]})
    assert r.status_code == 200, r.text
    said = _flat(r.content)
    assert PRIVATE_PNG not in said and OPEN_PNG in said


def test_a_zip_holds_what_the_share_s_file_route_serves(world, anon):
    """``_can_bundle`` reads in one go what ``GET /api/uploads/…?share=`` asks
    file by file: the same yes for a picture however its address ends, a
    chip's file in a block's properties, handwriting and the page's PDF, and
    the same no for a file only a private page names."""
    owner = world["owner"]
    picture = _upload(owner, _png(90))
    held = _upload(owner, _png(120))
    ink = owner.post("/api/upload-file", files={"file": ("x.ink", _bytes(_ink()), "application/octet-stream")})
    assert ink.status_code == 200, ink.text
    ink = ink.json()["url"]
    doc = owner.post("/api/uploads", files={"file": ("q.pdf", _blank_pdf(pages=2), "application/pdf")}).json()["doc_id"]
    page = _page(owner, "Files page", {"doc_id": doc})
    _put(owner, page, [_note(f"![a]({picture}?ws=other)"), _note("recorded", segments=[{"url": held}]),
                       _note("ink", ink_url=ink)])
    private = world["private_image"]
    token = _share(owner, page)

    def route(url):
        return anon.get(url, params={"share": token}).status_code

    names = zipfile.ZipFile(io.BytesIO(anon.get(f"/api/pages/{page}/export",
                                                params={"mode": "gamma", "share": token}).content)).namelist()
    packed = {name.rsplit("/", 1)[-1] for name in names}
    for url in (picture, held, ink, f"/api/uploads/{doc}.pdf"):
        assert route(url) == 200 and url.rsplit("/", 1)[-1] in packed, url
    assert route(private) == 403 and private.rsplit("/", 1)[-1] not in packed

    markdown = anon.get(f"/api/pages/{page}/export", params={"mode": "readable", "share": token})
    written = zipfile.ZipFile(io.BytesIO(markdown.content)).namelist()
    assert f"assets/{ink.rsplit('/', 1)[-1][:-4]}.svg" in written


def test_a_share_s_export_reads_its_files_once_however_many_they_are(world, anon, monkeypatch):
    """Asked of each file, the share's whole reach would be read and its
    blocks scanned once per file: a large folder's zip took minutes."""
    owner = world["owner"]
    reads = []
    pages = ShareScope.pages
    monkeypatch.setattr(ShareScope, "pages", lambda scope, conn: (reads.append(1), pages(scope, conn))[1])

    def exported(images):
        page = _page(owner, f"Pictures {images}")
        urls = [_upload(owner, OPEN_PNG + bytes([images, n])) for n in range(images)]
        _put(owner, page, [_note(f"![p]({url})") for url in urls])
        token = _share(owner, page)
        reads.clear()
        r = anon.get(f"/api/pages/{page}/export", params={"mode": "readable", "share": token})
        assert r.status_code == 200, r.text
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert sum(name.endswith(".png") for name in names) == images
        return len(reads)

    assert exported(2) == exported(6)


def test_the_vault_s_anchors_come_from_the_share_s_own_pages(world, anon):
    """An anchor says something links to the block: a link from a private
    page does not say so through a share, as it does to the owner."""
    owner = world["owner"]
    open_page = _page(owner, "Anchored page")
    _put(owner, open_page, [_note("anchored words")])
    linking = _page(owner, "Linking page")
    _put(owner, linking, [_note(f"[[{open_page}-0]]")])
    anchor = f"^{anchor_marker(f'{open_page}-0')}".encode()
    path = f"/api/pages/{open_page}/export"
    assert anchor in _flat(owner.get(path, params={"mode": "obsidian"}).content)
    shared = anon.get(path, params={"mode": "obsidian", "share": _share(owner, open_page)})
    assert anchor not in _flat(shared.content)


def test_a_bibliography_s_review_holds_only_the_share_s_pages(world, anon):
    r = anon.get("/api/bibliography", params={"folder": world["folder"], "share": world["folder_token"]})
    assert r.status_code == 200, r.text
    assert HIDDEN_TITLE not in r.text and [e["title"] for e in r.json()["entries"]] == ["Open paper"]


def test_an_upload_is_told_by_its_name_wherever_its_file_lies(tmp_path):
    """The entry carries the name: a stored file read from outside the
    uploads directory is filtered like any other, and only a file of the
    export's own, which has no name, passes."""
    builder = export_router._Builder("ws", "base", {"share": object()})
    builder.readable = {"open.pdf"}
    path = tmp_path / "A paper.pdf"
    builder.files = [("open.pdf", path, "open.pdf"), ("private.pdf", path, "private.pdf"), ("own.pdf", path, "")]
    assert builder._bundled()[1] == [("open.pdf", path), ("own.pdf", path)]


def test_the_export_job_is_not_open_to_a_share(world, anon):
    """It works in the signed-in account's own workspace: a link visitor has
    none, and a stranger's names no page of the share."""
    body = {"page_id": world["paper"], "mode": "readable"}
    params = {"share": world["page_token"]}
    assert anon.post("/api/jobs/export", json=body, params=params).status_code == 401
    assert login("sx_stranger", "sx-stranger-pw").post("/api/jobs/export", json=body, params=params).status_code == 404
