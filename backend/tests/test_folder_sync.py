"""A folder of the workspace as a folder on disk: the server's manifest and
notes reads (gamma/folder_sync.py, routers/sync.py ``/sync/folders*``) and
the gamma-sync client (gamma/gamma_sync.py, run over HTTP) that writes them —
the layout, the vault pages with the page's id, and the client's rounds:
adds, updates, renames, removals, and the files it must leave alone."""

import base64
import io
import json

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_label, make_page, make_user, workspace_of
from gamma.integrations import create_token
from test_pdf_export import _blank_pdf, _position

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
SERVER = "http://testserver"


@pytest.fixture(scope="module")
def owner(client):
    make_user("fs_owner", "pw")
    return login("fs_owner", "pw")


@pytest.fixture(scope="module")
def token(owner):
    return create_token(account_of("fs_owner"), workspace_of("fs_owner"), "folder sync", 90)["token"]


def _pdf_page(c, title, folders, **props):
    data = _blank_pdf(pages=1) + title.encode()
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
                                "folders": [make_folder(c, f) for f in folders], **props})
    return page, up.json()["doc_id"], data


def _children(c, page_id, blocks):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": blocks})
    assert r.status_code == 200, r.text


def _note(block_id, content, children=()):
    return {"id": block_id, "content": content, "properties": {}, "children": list(children)}


@pytest.fixture(scope="module")
def lab(owner):
    """Folder "FS lab": a paper with a highlight, a label and a mention of
    the paper in "Sub"; a notes page; a page named like a Windows device;
    in "Sub" a paper whose note shows a picture, two pages called Twin, and
    a page filed in Sub and in the lab; an empty subfolder. One paper
    elsewhere."""
    folder = make_folder(owner, "FS lab")
    sub, empty = make_folder(owner, "FS lab/Sub"), make_folder(owner, "FS lab/Empty")
    tag = make_label(owner, "fs-tag")
    up = owner.post("/api/upload-image", files={"file": ("p.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    png_name = up.json()["url"].rsplit("/", 1)[-1]

    deep, deep_doc, _ = _pdf_page(owner, "Deep paper", ["FS lab/Sub"])
    _children(owner, deep["id"], [_note("fs-deep-1", f"a figure ![pic](/api/uploads/{png_name})")])
    outside, _, _ = _pdf_page(owner, "Outside paper", ["FS elsewhere"])
    top, top_doc, top_pdf = _pdf_page(owner, "Top paper", ["FS lab"], labels=[tag])
    _children(owner, top["id"], [
        {"id": "fs-top-hl", "content": "a note on the quote", "children": [],
         "properties": {"quote": "the quoted words", "color": "rgba(255, 226, 143, 0.65)", "pdf_position": _position()}},
        _note("fs-top-2", f"see [[{deep['id']}]] and [[{outside['id']}]]"),
    ])
    notes = make_page(owner, "Just notes", {"folders": [folder]})
    _children(owner, notes["id"], [_note("fs-notes-1", "hello")])
    con = make_page(owner, "CON", {"folders": [folder]})
    twin1 = make_page(owner, "Twin", {"folders": [sub]})
    twin2 = make_page(owner, "Twin", {"folders": [sub]})
    both = make_page(owner, "Both", {"folders": [sub, folder]})
    return {"folder": folder, "sub": sub, "empty": empty, "png": png_name, "top": top, "top_doc": top_doc,
            "top_pdf": top_pdf, "deep": deep, "deep_doc": deep_doc, "outside": outside, "notes": notes, "con": con,
            "twin1": twin1, "twin2": twin2, "both": both}


def _manifest(c, folder, **kw):
    r = c.get(f"/api/sync/folders/{folder}", **kw)
    assert r.status_code == 200, r.text
    return r.json()


# --- the server's reads ------------------------------------------------------

def test_manifest_lays_the_folder_out_as_a_directory_tree(owner, lab):
    m = _manifest(owner, lab["folder"])
    assert m["folder"] == {"id": lab["folder"], "path": ["FS lab"]}
    assert sorted(d["path"] for d in m["dirs"]) == ["Empty", "Sub"]   # an empty folder is a directory too
    assert m["cursor"].isdigit()
    by_id = {p["id"]: p for p in m["pages"]}
    assert sorted(p["stem"] for p in m["pages"]) == [
        "CON_", "Just notes", "Sub/Both", "Sub/Deep paper", "Sub/Twin", "Sub/Twin 2", "Top paper"]
    top = by_id[lab["top"]["id"]]
    assert top["pdf"] == "Top paper.pdf" and top["notes"] == "Top paper.md"
    assert top["doc_id"] == lab["top_doc"] and top["pdf_size"] == len(lab["top_pdf"])
    notes = by_id[lab["notes"]["id"]]
    assert notes["pdf"] is None and notes["doc_id"] == "" and notes["notes"] == "Just notes.md"
    assert by_id[lab["both"]["id"]]["stem"] == "Sub/Both"            # its first folder below the lab
    assert by_id[lab["twin1"]["id"]]["stem"] == "Sub/Twin" and by_id[lab["twin2"]["id"]]["stem"] == "Sub/Twin 2"
    assert lab["outside"]["id"] not in by_id
    assert all(":" in p["version"] for p in m["pages"])


def test_root_is_the_whole_library_and_a_page_is_no_folder(owner, lab):
    m = _manifest(owner, "root")
    by_id = {p["id"]: p for p in m["pages"]}
    assert by_id[lab["outside"]["id"]]["stem"] == "FS elsewhere/Outside paper"
    assert by_id[lab["deep"]["id"]]["stem"] == "FS lab/Sub/Deep paper"
    assert {"FS lab", "FS lab/Sub", "FS lab/Empty", "FS elsewhere"} <= {d["path"] for d in m["dirs"]}
    assert owner.get(f"/api/sync/folders/{lab['top']['id']}").status_code == 404
    assert owner.get(f"/api/sync/folders/{lab['top']['id']}/notes", params={"pages": lab["top"]["id"]}).status_code == 404


def test_notes_are_vault_pages_of_the_folder_with_the_page_id(owner, lab):
    ids = [lab["top"]["id"], lab["deep"]["id"], lab["outside"]["id"]]
    r = owner.get(f"/api/sync/folders/{lab['folder']}/notes", params={"pages": ",".join(ids)})
    assert r.status_code == 200, r.text
    pages = r.json()["pages"]
    assert set(pages) == {lab["top"]["id"], lab["deep"]["id"]}          # a page outside the folder is left out
    md = pages[lab["top"]["id"]]["markdown"]
    assert md.startswith(f"---\ngamma_id: {lab['top']['id']}\n")
    assert "tags:\n  - fs-tag" in md
    assert 'source: "[[Top paper.pdf]]"' in md                           # the PDF beside the note
    assert "> [!quote] [[Top paper.pdf#page=" in md and "> the quoted words" in md
    assert "[[Deep paper]]" in md                                        # a sibling of the folder, by title
    assert "Outside paper" in md and "[[Outside paper]]" not in md       # outside the folder: its title as text
    assert pages[lab["top"]["id"]]["attachments"] == []
    deep = pages[lab["deep"]["id"]]
    assert f"![pic](../attachments/{lab['png']})" in deep["markdown"]   # relative to Sub/
    assert deep["attachments"] == [lab["png"]]
    manifest = {p["id"]: p for p in _manifest(owner, lab["folder"])["pages"]}
    assert deep["version"] == manifest[lab["deep"]["id"]]["version"]


def test_who_may_read(owner, lab, token):
    anon = fresh_client()
    assert anon.get(f"/api/sync/folders/{lab['folder']}").status_code == 401
    with_token = anon.get(f"/api/sync/folders/{lab['folder']}", headers={"Authorization": f"Bearer {token}"})
    assert with_token.status_code == 200 and with_token.json()["folder"]["id"] == lab["folder"]
    folders = anon.get("/api/sync/folders", headers={"Authorization": f"Bearer {token}"}).json()["folders"]
    assert {tuple(f["path"]) for f in folders} >= {("FS lab",), ("FS lab", "Sub"), ("FS elsewhere",)}
    too_many = ",".join(f"p{i}" for i in range(201))
    assert owner.get(f"/api/sync/folders/{lab['folder']}/notes", params={"pages": too_many}).status_code == 400


# --- the client --------------------------------------------------------------

def _tool(monkeypatch):
    from gamma import gamma_sync as mod
    http = fresh_client()

    def open_(method, path, headers):
        r = http.request(method, path, headers=headers)
        return r.status_code, r.headers, io.BytesIO(r.content)

    monkeypatch.setattr(mod, "default_open", open_)
    return mod


def _round(mod, dest, token, **kw):
    link = mod.Link.load(dest)
    return mod.Round(link, mod.Server(SERVER, token).source(link.state["folder"]), **kw).run()


def _state(dest):
    return json.loads((dest / ".gamma-sync.json").read_text(encoding="utf-8"))


def test_client_mirrors_the_folder_and_follows_changes(owner, lab, token, tmp_path, monkeypatch):
    mod = _tool(monkeypatch)
    dest = tmp_path / "quantum"
    assert mod.main(["init", str(dest), "--server", SERVER, "--folder", "fs lab", "--token", token, "--save-token"]) == 0
    assert mod.main(["init", str(dest), "--server", SERVER, "--folder", "FS lab", "--token", token]) == 1  # linked already
    state = _state(dest)
    assert state["folder"] == lab["folder"] and state["token"] == token and state["folder_path"] == ["FS lab"]

    assert mod.main(["sync", str(dest)]) == 0
    assert (dest / "Top paper.pdf").read_bytes() == lab["top_pdf"]
    assert (dest / "Top paper.md").read_text(encoding="utf-8").startswith(f"---\ngamma_id: {lab['top']['id']}\n")
    assert (dest / "Sub" / "Deep paper.pdf").is_file() and (dest / "Sub" / "Twin 2.md").is_file()
    assert (dest / "CON_.md").is_file() and (dest / "Empty").is_dir()
    assert (dest / "attachments" / lab["png"]).read_bytes() == PNG
    assert not (dest / "FS elsewhere").exists() and not (dest / "Outside paper.pdf").exists()
    files = _state(dest)["files"]
    assert files["Top paper.pdf"]["doc"] == lab["top_doc"] and files["Sub/Deep paper.md"]["attachments"] == [lab["png"]]
    again = _round(mod, dest, token)   # nine page files unchanged; the attachment is simply still there
    assert again["added"] == again["updated"] == again["renamed"] == again["removed"] == 0 and again["unchanged"] == 9

    # a title change renames both files; the note is written again (its version moved)
    r = owner.put(f"/api/blocks/{lab['deep']['id']}", json={"content": "Deeper paper"})
    assert r.status_code == 200, r.text
    counts = _round(mod, dest, token)
    assert counts["renamed"] == 2 and counts["updated"] == 1 and counts["added"] == 0
    assert (dest / "Sub" / "Deeper paper.pdf").is_file() and not (dest / "Sub" / "Deep paper.pdf").exists()
    assert 'source: "[[Deeper paper.pdf]]"' in (dest / "Sub" / "Deeper paper.md").read_text(encoding="utf-8")

    # an edit reaches the note
    _children(owner, lab["notes"]["id"], [_note("fs-notes-1", "hello"), _note("fs-notes-2", "hello again")])
    counts = _round(mod, dest, token)
    assert counts["updated"] == 1 and "hello again" in (dest / "Just notes.md").read_text(encoding="utf-8")

    # a note changed on disk is kept, until --force
    (dest / "Just notes.md").write_text("mine\n", encoding="utf-8")
    _children(owner, lab["notes"]["id"], [_note("fs-notes-1", "hello"), _note("fs-notes-3", "third")])
    counts = _round(mod, dest, token)
    assert counts["kept"] == 1 and counts["updated"] == 0
    assert (dest / "Just notes.md").read_text(encoding="utf-8") == "mine\n"
    counts = _round(mod, dest, token, force=True)
    assert counts["updated"] == 1 and "third" in (dest / "Just notes.md").read_text(encoding="utf-8")

    # a file the client did not write is never overwritten
    (dest / "Fresh.md").write_text("mine\n", encoding="utf-8")
    fresh = make_page(owner, "Fresh", {"folders": [lab["folder"]]})
    counts = _round(mod, dest, token)
    assert counts["kept"] == 1 and (dest / "Fresh.md").read_text(encoding="utf-8") == "mine\n"
    (dest / "Fresh.md").unlink()
    counts = _round(mod, dest, token)
    assert counts["added"] == 1 and f"gamma_id: {fresh['id']}" in (dest / "Fresh.md").read_text(encoding="utf-8")

    # a folder rename moves the directory's files; the old directory goes
    r = owner.post("/api/pages/folders/ops", json={"ops": [{"op": "set", "id": lab["sub"], "content": "Sub2"}]})
    assert r.status_code == 200, r.text
    counts = _round(mod, dest, token)   # the paper's two files, Twin, Twin 2, Both
    assert counts["renamed"] == 5 and counts["updated"] == 0 and counts["added"] == 0
    assert (dest / "Sub2" / "Deeper paper.pdf").is_file() and (dest / "Sub2" / "Both.md").is_file()
    assert not (dest / "Sub").exists() and _state(dest)["dirs"] == ["Empty", "Sub2"]

    # a page deleted in Gamma leaves the disk; a dry run changes nothing
    owner.delete(f"/api/blocks/{lab['twin2']['id']}").raise_for_status()
    dry = make_page(owner, "Dry", {"folders": [lab["folder"]]})
    counts = _round(mod, dest, token, dry_run=True)
    assert counts["removed"] == 1 and counts["added"] == 1
    assert (dest / "Sub2" / "Twin 2.md").is_file() and not (dest / "Dry.md").exists()
    assert "Dry.md" not in _state(dest)["files"] and "Sub2/Twin 2.md" in _state(dest)["files"]
    counts = _round(mod, dest, token)
    assert counts["removed"] == 1 and counts["added"] == 1
    assert not (dest / "Sub2" / "Twin 2.md").exists() and (dest / "Dry.md").is_file()
    assert dry["id"] == _state(dest)["files"]["Dry.md"]["page"]

    # an attachment no note shows any more goes too
    _children(owner, lab["deep"]["id"], [_note("fs-deep-1", "no figure")])
    counts = _round(mod, dest, token)
    assert counts["removed"] == 1 and not (dest / "attachments" / lab["png"]).exists()

    assert mod.main(["status", str(dest)]) == 0


def test_client_can_take_the_pdfs_alone_and_lists_folders(owner, lab, token, tmp_path, capsys, monkeypatch):
    mod = _tool(monkeypatch)
    dest = tmp_path / "papers"
    assert mod.main(["init", str(dest), "--server", SERVER, "--folder", lab["folder"], "--token", token, "--no-notes"]) == 0
    assert mod.main(["sync", str(dest), "--token", token]) == 0
    assert (dest / "Top paper.pdf").is_file() and not (dest / "Top paper.md").exists()
    assert not (dest / "Just notes.md").exists() and not (dest / "attachments").exists()
    assert mod.main(["sync", str(dest)]) == 1                           # no token anywhere
    capsys.readouterr()
    assert mod.main(["folders", "--server", SERVER, "--token", token]) == 0
    out = capsys.readouterr().out
    assert "root  (the whole library)" in out and f"FS lab / Sub2  ({lab['sub']})" in out
    assert mod.main(["init", str(tmp_path / "x"), "--server", SERVER, "--folder", "no such folder", "--token", token]) == 1
