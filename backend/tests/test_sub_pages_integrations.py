"""Sub-pages in the imports, the connector and the MCP export
(docs/research/sub-pages.md "Integrations and the agent"): a Zotero import
that merges into a sub-page files it nowhere and reports its top's folders,
as a Notability re-import does; the Gamma backup's review lists a sub-page
under its parent and its top's folders, and one imported without its parent
is filed where its top was; the connector's folder picker counts a
sub-page's activity for its top's folders; and the MCP ``export_page`` takes
the pages inside a page with ``sub_pages``."""

import base64
import io
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter

import notability_fixtures as fx
from conftest import doc_props, folder_names, login, make_folder, make_user
from test_import_review import commit, report, review
from test_mcp import call
from test_zotero_import import _custom_zip, _rdf, _send

USER, PASSWORD = "spin_owner", "pw-spin-1"
DONOR, RECEIVER = "spin_donor", "spin_receiver"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _page(c, title, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _props(c, page_id) -> dict:
    return c.get(f"/api/blocks/{page_id}").json()["properties"]


def test_a_zotero_import_merging_into_a_sub_page_files_it_nowhere(owner):
    kept = make_folder(owner, "Spin kept")
    top = _page(owner, "Spin review", folders=[kept])
    url = "https://example.org/spin-paper"
    sub = _page(owner, "Spin paper", parent_page=top, properties={"zotero_key": url})
    rdf = _rdf(f'<bib:Article rdf:about="{url}"><z:itemType>journalArticle</z:itemType>'
               "<dc:title>Spin paper</dc:title></bib:Article>",
               '<z:Collection rdf:about="#collection_1"><dc:title>Spin coll</dc:title>'
               f'<dcterms:hasPart rdf:resource="{url}"/></z:Collection>')
    data = _custom_zip(rdf, {})
    [planned] = _send(owner, data, preview=True).json()["pages"]
    assert (planned["action"], planned["existing_id"]) == ("merge", sub)
    assert planned["folders"] == [["Spin kept"]] and planned["parent"] == top
    r = _send(owner, data)
    assert r.status_code == 200, r.text
    [done] = r.json()["pages"]
    assert done["id"] == sub and done["folders"] == [["Spin kept"]] and done["parent"] == top
    props = _props(owner, sub)
    assert "folders" not in props and props["parent_page"] == top and props["zotero_key"] == url


def test_a_notability_re_import_names_a_sub_page_s_folders(owner):
    data = fx.note_zip("Spin note", session_bytes=fx.session(name="Spin note"),
                       meta=fx.metadata("Spin note", "unsortedNotesKey"))
    r = owner.post("/api/import/notability", files={"file": ("Spin.note", data, "application/zip")})
    assert r.status_code == 200, r.text
    [first] = r.json()["pages"]
    top = _page(owner, "Spin notebook", folders=[make_folder(owner, "Spin notes")])
    assert owner.put(f"/api/blocks/{first['id']}", json={"properties": {"parent_page": top}}).status_code == 200
    for path in ("/api/import/notability/preview", "/api/import/notability"):
        r = owner.post(path, files={"file": ("Spin.note", data, "application/zip")})
        assert r.status_code == 200, r.text
        [again] = r.json()["pages"]
        assert (again["action"], again["id"], again["folders"]) == ("skip", first["id"], [["Spin notes"]])


def test_the_backup_review_nests_sub_pages_and_files_one_left_alone_with_its_top():
    make_user(DONOR, PASSWORD)
    make_user(RECEIVER, PASSWORD)
    donor, receiver = login(DONOR, PASSWORD), login(RECEIVER, PASSWORD)
    top = _page(donor, "Bk top", folders=[make_folder(donor, "Bk lab")])
    child = _page(donor, "Bk child", parent_page=top)
    grandchild = _page(donor, "Bk grandchild", parent_page=child)
    backup = donor.get("/api/export").content
    plan = review(receiver, backup, "gamma")
    pages = {p["id"]: p for p in plan["pages"]}
    assert pages[top]["parent"] == "" and pages[top]["folders"] == [["Bk lab"]] and "folders_alone" not in pages[top]
    for page, parent in ((child, top), (grandchild, child)):
        assert pages[page]["parent"] == parent
        assert pages[page]["folders"] == pages[page]["folders_alone"] == [["Bk lab"]]
    # The child without its parent: a library page in its top's folder, no parent of its own
    done = report(commit(receiver, plan, pages[child]["selection_ids"]))
    [entry] = done["pages"]
    assert entry["id"] == child and entry["parent"] == "" and entry["folders"] == [["Bk lab"]]
    props = _props(receiver, child)
    assert "parent_page" not in props and [folder_names(receiver)[f] for f in props["folders"]] == [["Bk lab"]]
    # The grandchild later, its parent now in the library: inside it, filed nowhere of its own
    plan = review(receiver, backup, "gamma")
    pages = {p["id"]: p for p in plan["pages"]}
    assert pages[child]["action"] == "skip" and pages[child]["parent"] == ""
    assert pages[grandchild]["parent"] == child and pages[grandchild]["folders"] == [["Bk lab"]]
    report(commit(receiver, plan, pages[grandchild]["selection_ids"]))
    props = _props(receiver, grandchild)
    assert props["parent_page"] == child and "folders" not in props


def test_a_sub_page_of_a_page_the_library_has_for_its_pdf_goes_inside_that_page():
    for user in (DONOR + "_pdf", RECEIVER + "_pdf"):
        make_user(user, PASSWORD)
    donor, receiver = login(DONOR + "_pdf", PASSWORD), login(RECEIVER + "_pdf", PASSWORD)
    pdf = _pdf(431)
    top = _page(donor, "Bkr top", folders=[make_folder(donor, "Bkr lab")], properties=_upload(donor, pdf))
    child = _page(donor, "Bkr child", parent_page=top)
    mine = _page(receiver, "Bkr mine", properties=_upload(receiver, pdf))
    backup = donor.get("/api/export").content
    plan = review(receiver, backup, "gamma")
    pages = {p["id"]: p for p in plan["pages"]}
    assert top not in pages and pages[mine]["action"] == "skip"
    assert pages[child]["parent"] == mine and pages[child]["action"] == "create"
    # Its parent left unticked: the review showed it inside the receiver's page, and it lands there
    done = report(commit(receiver, plan, pages[child]["selection_ids"]))
    [entry] = done["pages"]
    assert entry["id"] == child and entry["parent"] == mine
    props = _props(receiver, child)
    assert props["parent_page"] == mine and "folders" not in props


def test_the_connector_s_folders_rank_by_the_pages_inside_pages_too(owner):
    early, late = make_folder(owner, "Spin rank early"), make_folder(owner, "Spin rank late")
    top = _page(owner, "Spin rank top", folders=[early])
    other = _page(owner, "Spin rank other", folders=[late])
    sub = _page(owner, "Spin rank sub", parent_page=top)
    # The top never viewed, the page inside it viewed last
    history = [{"id": other, "at": "2026-09-08T00:00:00.000Z"}, {"id": sub, "at": "2026-09-09T00:00:00.000Z"}]
    assert owner.put("/api/prefs/recent-views", json={"value": history}).status_code == 200
    order = [f["id"] for f in owner.get("/api/library/folders").json()["folders"]]
    assert order.index(early) < order.index(late)


def _pdf(width: int) -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=width, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload(c, pdf: bytes) -> dict:
    up = c.post("/api/uploads", files={"file": ("paper.pdf", pdf, "application/pdf")}).json()
    return doc_props(up["doc_id"], url=up["source_url"])


def _paper(c, title, **body):
    return _page(c, title, properties=_upload(c, _pdf(500 + len(title))), **body)


def test_the_mcp_export_takes_the_pages_inside_a_page(client, owner):
    token = owner.post("/api/integrations/tokens", json={"name": "Codex"}).json()["token"]
    top = _paper(owner, "Spin export top")
    inner = _paper(owner, "Spin export inner", parent_page=top)
    owner.post("/api/blocks", json={"parent_id": inner, "content": "SpinInnerNote"})
    alone = call(client, token, "export_page", {"page_id": top, "format": "markdown"})
    assert "SpinInnerNote" not in alone["content"][0]["text"]
    result = call(client, token, "export_page", {"page_id": top, "format": "markdown", "sub_pages": True})
    text = result["content"][0]["text"]
    assert not result["isError"] and "and the 1 page inside it" in text
    assert text.index("# Spin export top") < text.index("# Spin export inner") < text.index("SpinInnerNote")
    # The annotated PDFs: a zip nested as the pages are, the HTTP export's own address with sub_pages=1
    result = call(client, token, "export_page", {"page_id": top, "format": "pdf", "sub_pages": True})
    assert not result["isError"], result
    resource = result["content"][1]["resource"]
    assert resource["mimeType"] == "application/zip" and "sub_pages=1" in resource["uri"]
    assert resource["uri"].startswith(f"http://localhost/api/pages/{top}/export-pdf?")
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(resource["blob"]))) as z:
        names = sorted(z.namelist())
    assert names == ["Spin export top.pdf", "Spin export top/Spin export inner.pdf"], names
    assert owner.get(resource["uri"].removeprefix("http://localhost")).status_code == 200
    # The notes typeset: one PDF of both pages
    result = call(client, token, "export_page", {"page_id": top, "format": "notes_pdf", "sub_pages": True})
    resource = result["content"][1]["resource"]
    assert resource["mimeType"] == "application/pdf" and "sub_pages=1" in resource["uri"]
    assert len(PdfReader(io.BytesIO(base64.b64decode(resource["blob"]))).pages) >= 2
    # A page with none inside exports as without the switch
    result = call(client, token, "export_page", {"page_id": inner, "format": "pdf", "sub_pages": True})
    assert result["content"][1]["resource"]["mimeType"] == "application/pdf"
    assert "sub_pages" not in result["content"][1]["resource"]["uri"]
