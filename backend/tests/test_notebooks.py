"""Notebook data survives ordinary ops and exports with stable sheet anchors."""

import io
import json
from pathlib import Path

import pytest
from PyPDF2 import PdfReader

from conftest import make_page
from gamma import ink
from gamma.notebooks import Paper


def _ops(client, page, ops):
    return client.post(f"/api/pages/{page}/ops", json={"client": "notebook-test", "ops": ops})


def _sheet(sid, parent, position="a0", **paper):
    return {"op": "insert", "id": sid, "parent": parent, "position": position, "content": sid,
            "props": {"type": "notebook-sheet", "paper": Paper(**paper).model_dump()}}


def test_notebook_appends_keep_both_sheets_and_export_reorders_without_moving_ink(guest):
    root = make_page(guest, "Notebook", {"notebook": {"version": 1, "default_paper": Paper().model_dump()}})["id"]
    # Both clients started from the same last order key. IDs preserve both.
    assert _ops(guest, root, [_sheet("sheetA", root, pattern="ruled")]).status_code == 200
    assert _ops(guest, root, [_sheet("sheetB", root, width=400, height=600, pattern="dots")]).status_code == 200
    data = {"format": "gamma-ink", "version": 2,
            "space": {"kind": "notebook-page", "sheet_id": "sheetB", "width": 400, "height": 600},
            "strokes": [{"id": "fragment", "source_id": "original", "ch": "xypt", "pts": [1000, 2000, 500, 12, 1000, 0, 600, 8]}]}
    upload = guest.post("/api/upload-ink", json=data)
    assert upload.status_code == 200, upload.text
    assert upload.json()["pdf_position"] is None and upload.json()["sheet_id"] == "sheetB"
    group = {"op": "insert", "id": "sheetInk", "parent": "sheetB", "content": "caption",
             "props": {"ink_url": upload.json()["url"], "sheet_id": "sheetB", "ink_strokes": 1}}
    assert _ops(guest, root, [group]).status_code == 200
    # Changing paper size preserves the point geometry; moving sheets only
    # changes the derived PDF ordinal, never stored ink or sheet IDs.
    assert _ops(guest, root, [{"op": "set", "id": "sheetB", "props": {"paper": Paper(width=500, height=700).model_dump()}},
                              {"op": "move", "id": "sheetA", "parent": root, "position": "a9"}]).status_code == 200
    response = guest.get(f"/api/pages/{root}/export-pdf")
    assert response.status_code == 200, response.text[:200]
    pdf = PdfReader(io.BytesIO(response.content))
    assert len(pdf.pages) == 2
    assert float(pdf.pages[0].mediabox.width) == 500
    annotation = pdf.pages[0]["/Annots"][0].get_object()
    assert [float(n) for n in annotation["/InkList"][0]][:2] == pytest.approx([10, 680])
    embedded = ink.parse_ink(str(annotation["/GammaInk"]))
    assert embedded.version == 2 and embedded.strokes[0].source_id == "original"
    assert embedded.space.kind == "pdf-page" and embedded.space.page == 1
    # Existing PDF importer retains timing/lineage, converts to its PDF frame.
    from gamma.routers.imports import _extract_pdf_annotations
    imported = _extract_pdf_annotations(pdf)
    assert imported[0]["ink"].strokes[0].pts == data["strokes"][0]["pts"]


def test_notebook_contract_rejects_invalid_paper_and_cross_page_sheets(guest):
    bad = guest.post("/api/pages", json={"properties": {"notebook": {"version": 1, "default_paper": {"width": -1}}}})
    assert bad.status_code == 400
    plain = make_page(guest)["id"]
    assert _ops(guest, plain, [_sheet("notASheet", plain)]).status_code == 400
    root = make_page(guest, "Notebook", {"notebook": {"version": 1}})["id"]
    assert _ops(guest, root, [_sheet("validSheet", root)]).status_code == 200
    op = {"op": "insert", "id": "crossInk", "parent": plain, "props": {"ink_url": "", "sheet_id": "validSheet"}}
    assert _ops(guest, plain, [op]).status_code == 403
    op["props"]["sheet_id"] = []
    assert _ops(guest, plain, [op]).status_code == 400


def test_shared_ink_contract_fixture():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/shared/ink-v2.json").read_text())
    for case in fixture["valid"]:
        parsed = ink.parse_ink(case["ink"])
        assert json.loads(ink.dumps(parsed))["space"] == case["ink"]["space"]
        decoded = ink.decode_stroke(parsed.strokes[0])
        assert [{key: point[key] for key in expected} for point, expected in zip(decoded, case["decoded"])] == case["decoded"]
    for case in fixture["invalid"]:
        with pytest.raises(ink.InkError):
            ink.parse_ink(case)


def test_notebook_share_uses_existing_page_permissions():
    from conftest import login, make_user
    from fastapi.testclient import TestClient
    from gamma.app import app
    make_user("notebook_permission_owner", "pw")
    owner = login("notebook_permission_owner", "pw")
    page = make_page(owner, "Shared notebook", {"notebook": {"version": 1}})["id"]
    token = owner.post(f"/api/share/{page}").json()["token"]
    assert owner.put(f"/api/share-settings/{page}", json={"audience": "anyone", "role": "edit"}).status_code == 200
    visitor = TestClient(app)
    endpoint = f"/api/pages/{page}/ops?share={token}"
    response = visitor.post(endpoint, json={"ops": [_sheet("sharedSheet", page)]})
    assert response.status_code == 200, response.text
    assert visitor.post(endpoint, json={"ops": [{"op": "set", "id": page,
                          "props": {"notebook": {"version": 1}}}]}).status_code == 403
    assert visitor.get(f"/api/pages/{page}/export-pdf?share={token}").status_code == 200
    assert owner.put(f"/api/share-settings/{page}", json={"audience": "anyone", "role": "view"}).status_code == 200
    assert visitor.post(endpoint, json={"ops": [_sheet("forbiddenSheet", page)]}).status_code == 403
