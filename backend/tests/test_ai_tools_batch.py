"""The page tools with a list of pages — rename_page, move_page, label_page,
restore_page: every page through the one-page planner, the ones that cannot
change left out and named while the rest change in one transaction, the
card that lists every page and binds the run (its digest), the message's
change budget, the argument shapes a saved chat replays, and the revert of
a rename, move or label change (POST /api/ai/revert, kind "pages")."""

import json

from conftest import folder_names, make_folder, make_label
from gamma import ai_tools, ops
from gamma.ai_tools import BATCH_CHANGED, CHANGE_LIMIT_PREFIX, approval_preview, run_agent_tool
from gamma.blocks_store import label_by_name
from gamma.db import connect_pages_db
from gamma.ops import OpError, apply_ops

from ai_fixtures import folder, notes, org, props  # noqa: F401  (fixtures)


def _page(c, title, **properties):
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": properties})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _filed(c, page_id, tree):
    return props(c, page_id)["properties"].get(tree)


def _title(c, page_id):
    return props(c, page_id)["content"]


def _retitle(c, page_id, title):
    """The user's own rename, as their tab sends it."""
    assert c.put(f"/api/blocks/{page_id}", json={"content": title}).status_code == 200


def _refile(c, page_id, folders):
    assert c.put(f"/api/blocks/{page_id}", json={"properties": {"folders": folders}}).status_code == 200


def _revert(c, action, redo=False):
    return c.post("/api/ai/revert", json={"kind": "pages", "block_id": "", "revert": action["revert"],
                                          "redo": redo})


def _scope(folder_id="", **extra):
    """What approval_preview is given: a folder chat that may write."""
    return {**folder(folder_id), "can_write": True, **extra}


def _renames(*pairs):
    return {"renames": [{"page_id": p, "title": t} for p, t in pairs]}


def _newest_op(ws, page_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT client FROM page_ops WHERE page_id = ? ORDER BY seq DESC LIMIT 1",
                            (page_id,)).fetchone()


def _op_rows(ws, page_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0]


def _parents(ws, page_ids):
    """Where each page is: "root" (the library) or "trash" (Recently deleted)."""
    with connect_pages_db(ws) as conn:
        return [conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (p,)).fetchone()[0]
                for p in page_ids]


# --- rename_page --------------------------------------------------------------------

def test_rename_page_renames_several_pages_in_one_step(org):
    c, ids = org
    shelf = make_folder(c, "batchR")
    a, b, d = (_page(c, t, folders=[shelf]) for t in ("A", "B", "C"))
    stamps = [props(c, p)["updated_at"] for p in (a, b, d)]
    text, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames((a, "A2"), (b, "B2"), (d, "C2")))
    assert text.startswith("ok — renamed 3 pages."), text
    assert text.splitlines()[-1] == f"Changed: {a}, {b}, {d}"
    assert (action["kind"], action["changes"], action["summary"]) == ("rename", 3, "Renamed 3 pages")
    assert [(i["page_id"], i["from"], i["to"]) for i in action["items"]] == [(a, "A", "A2"), (b, "B", "B2"),
                                                                             (d, "C", "C2")]
    assert action["revert"]["pages"] == [[a, "title", "A", "A2"], [b, "title", "B", "B2"], [d, "title", "C", "C2"]]
    assert [_title(c, p) for p in (a, b, d)] == ["A2", "B2", "C2"]
    assert all(_newest_op(ids["ws"], p) == ("ai",) for p in (a, b, d))
    assert all(props(c, p)["updated_at"] != stamp for p, stamp in zip((a, b, d), stamps))


def test_a_batch_changes_the_pages_it_can_and_names_the_rest(org):
    c, ids = org
    shelf = make_folder(c, "batchR2")
    good, same, twice = (_page(c, t, folders=[shelf]) for t in ("good", "same", "twice"))
    outside = _page(c, "outside")
    text, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames(
        (good, "good2"), ("nopage12345", "x"), (outside, "y"), (same, "same"), (twice, "t1"), (twice, "t2")))
    assert [_title(c, p) for p in (good, same, twice, outside)] == ["good2", "same", "twice", "outside"]
    assert "renamed 1 of 6 pages" in text, text
    assert f"Already so, not changed: {same}" in text
    assert "- no such page — use exact page ids: nopage12345" in text
    assert f"- page is outside this chat's scope: {outside}" in text
    assert f"- named twice with different titles — send it once: {twice}, {twice}" in text
    assert text.index("Left out — ") < text.index("Changed:")
    assert action["changes"] == 1 and [i["page_id"] for i in action["items"]] == [good]


def test_a_batch_that_changes_no_page_says_why(org):
    c, ids = org
    shelf = make_folder(c, "batchN")
    a, b = _page(c, "none a", folders=[shelf]), _page(c, "none b", folders=[shelf])
    text, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames((a, "none a"), (b, "none b")))
    assert text == "ok — all 2 pages already are so; nothing changed", text
    assert action["noop"] is True and not action.get("error")
    text, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames(("nopage1", "x"), (a, " ")))
    assert text.splitlines() == ["error: none of these pages can be renamed:",
                                 "Left out — fix these and send only them again:",
                                 "- no such page — use exact page ids: nopage1", f"- empty title: {a}"], text
    assert action["error"] is True and _title(c, a) == "none a"


def test_a_one_page_list_is_the_one_page_tool(org):
    c, ids = org
    shelf = make_folder(c, "batchOne")
    page = _page(c, "lone", folders=[shelf])
    for args, title in ((_renames((page, "x")), "x"), ({"page_id": page, "title": "y"}, "y")):
        text, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", args)
        assert text == f'ok — renamed to "{title}"', text
        assert {"page_id", "title", "from", "to", "summary", "revert"} <= set(action)
        assert "items" not in action and "changes" not in action
    text, action = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_id": page, "folder": "sub"})
    assert text == 'ok — moved to "batchOne / sub" (a new folder)', text
    assert action["page_id"] == page and "revert" in action and "items" not in action
    text, action = run_agent_tool(ids["ws"], folder(shelf), "label_page", {"page_id": page, "add": ["lone-l"]})
    assert text.startswith('ok — "y" now carries "lone-l"'), text
    assert action["page_id"] == page and "revert" in action and "items" not in action
    preview, _ = approval_preview(ids["ws"], _scope(shelf), "rename_page", _renames((page, "z")))
    assert preview["page_id"] == page and "count" not in preview and "digest" not in preview


# --- move_page ----------------------------------------------------------------------

def test_move_page_makes_the_folder_once_and_files_every_page(org, monkeypatch):
    c, ids = org
    top, sub, keep = make_folder(c, "batchM"), make_folder(c, "batchM/sub"), make_folder(c, "batchKeep")
    m1, m2, m3 = _page(c, "M1", folders=[top, keep]), _page(c, "M2", folders=[sub]), _page(c, "M3", folders=[top])
    rows = _op_rows(ids["ws"], "folders")
    checks = []
    monkeypatch.setattr(ops.collab, "revalidate_shares", lambda ws: checks.append(ws))
    text, action = run_agent_tool(ids["ws"], folder(top), "move_page",
                                  {"page_ids": [m1, m2, m3], "folder": "sub / new"})
    assert text.startswith('ok — moved 3 pages to "batchM / sub / new" (a new folder).'), text
    assert checks == [ids["ws"]], "the folder shares are checked once for the call"
    assert _op_rows(ids["ws"], "folders") == rows + 1 and _newest_op(ids["ws"], "folders") == ("ai",)
    new = make_folder(c, "batchM/sub/new")
    assert (_filed(c, m1, "folders"), _filed(c, m2, "folders"), _filed(c, m3, "folders")) == ([keep, new], [new], [new])
    assert (action["to"], action["changes"]) == ("batchM / sub / new", 3)
    assert [i["from"] for i in action["items"]] == ["batchM, batchKeep", "batchM / sub", "batchM"]


def test_a_failing_write_changes_nothing(org, monkeypatch):
    c, ids = org
    shelf = make_folder(c, "batchFail")
    pages = [_page(c, f"F{n}", folders=[shelf]) for n in range(2)]
    real, calls = ai_tools.apply_batches, []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise OpError(413, "boom")
        return real(*args, **kw)

    monkeypatch.setattr(ai_tools, "apply_batches", flaky)
    text, _ = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_ids": pages, "folder": "never made"})
    assert text.startswith("error"), text
    assert len(calls) == 2, "the folder's batch ran, then the pages' failed"
    assert ["batchFail", "never made"] not in folder_names(c).values(), "the folder went with the transaction"
    assert all(_filed(c, p, "folders") == [shelf] for p in pages)


# --- label_page ---------------------------------------------------------------------

def test_label_page_puts_and_takes_labels_on_many_pages(org):
    c, ids = org
    keep, gone = make_label(c, "keepB"), make_label(c, "goneB")
    p1, p2, p3 = _page(c, "P1", labels=[gone]), _page(c, "P2", labels=[keep]), _page(c, "P3")
    rows = _op_rows(ids["ws"], "labels")
    args = {"page_ids": [p1, p2, p3], "add": ["Fresh B", "keepb"], "remove": ["goneB"]}
    text, action = run_agent_tool(ids["ws"], folder(""), "label_page", args)
    assert text.startswith("ok — changed the labels of 3 pages: +keepB, +Fresh B, −goneB (1 new label)."), text
    with connect_pages_db(ids["ws"]) as conn:
        fresh = label_by_name(conn, "Fresh B")
    assert len(fresh) == 1 and _op_rows(ids["ws"], "labels") == rows + 1
    assert [_filed(c, p, "labels") for p in (p1, p2, p3)] == [[keep, fresh[0]]] * 3
    assert (action["added"], action["removed"]) == (["keepB", "Fresh B"], ["goneB"])
    assert action["items"][0]["removed"] == ["goneB"] and action["items"][1]["added"] == ["Fresh B"]
    # What the call shares is refused whole, before any page.
    for bad, said in [({"remove": ["no such label B"]}, "there is no label"),
                      ({"add": ["SameB"], "remove": ["sameb"]}, "added and removed in one call"),
                      ({}, "`add` or to `remove`")]:
        text, _ = run_agent_tool(ids["ws"], folder(""), "label_page", {"page_ids": [p1, p2], **bad})
        assert text.startswith("error") and said in text, (bad, text)
    assert [_filed(c, p, "labels") for p in (p1, p2)] == [[keep, fresh[0]]] * 2


# --- restore_page -------------------------------------------------------------------

def _trashed(c, shelf, n, prefix):
    pages = [_page(c, f"{prefix} {k}", folders=[shelf]) for k in range(n)]
    for page in pages:
        assert c.delete(f"/api/blocks/{page}").status_code == 200
    return pages


def test_restore_page_brings_several_back(org):
    c, ids = org
    shelf, other = make_folder(c, "batchT"), make_folder(c, "batchTo")
    t1, t2, t3 = _trashed(c, shelf, 3, "T")
    elsewhere, = _trashed(c, other, 1, "elsewhere")
    live = _page(c, "live", folders=[shelf])
    text, action = run_agent_tool(ids["ws"], folder(shelf), "restore_page",
                                  {"page_ids": [t1, t2, t3, elsewhere, live]})
    assert text.startswith("ok — restored 3 of 5 pages."), text
    assert f"Already so, not changed: {live}" in text
    assert f"- that page was not filed in this chat's folder — restore it from the library root: {elsewhere}" in text
    assert f"Changed: {t1}, {t2}, {t3}" in text
    assert all(f"](/?page={p})" in text for p in (t1, t2, t3))
    assert action["changes"] == 3 and "revert" not in action
    assert _parents(ids["ws"], [t1, t2, t3, elsewhere]) == ["root", "root", "root", "trash"]
    many = _trashed(c, shelf, 25, "many")
    text, action = run_agent_tool(ids["ws"], folder(shelf), "restore_page", {"page_ids": many})
    assert text.count("](/?page=") == 20 and "(and 5 more)" in text and len(text) < 4000, text
    assert action["changes"] == 25


def test_a_restore_that_fails_on_one_page_reports_the_rest(org, monkeypatch):
    c, ids = org
    shelf = make_folder(c, "batchT2")
    pages = _trashed(c, shelf, 3, "R")
    real = ops.restore_page

    def busy(ws, conn, page_id, **kw):
        if page_id == pages[1]:
            raise RuntimeError("busy")
        return real(ws, conn, page_id, **kw)

    monkeypatch.setattr(ops, "restore_page", busy)
    text, action = run_agent_tool(ids["ws"], folder(shelf), "restore_page", {"page_ids": pages})
    assert text.startswith("ok — restored 2 of 3 pages."), text
    assert f"- busy: {pages[1]}" in text and action["changes"] == 2
    assert _parents(ids["ws"], pages) == ["root", "trash", "root"]
    monkeypatch.setattr(ops, "restore_page", real)
    # A restore that committed and then failed to fan out came back: it counts.
    again = _trashed(c, shelf, 3, "S")
    reload = ops.collab.publish_reload

    def deaf(ws, page_id):
        if page_id == again[0]:
            raise RuntimeError("no room")
        return reload(ws, page_id)

    monkeypatch.setattr(ops.collab, "publish_reload", deaf)
    text, action = run_agent_tool(ids["ws"], folder(shelf), "restore_page", {"page_ids": again})
    assert text.startswith("ok — restored 3 pages."), text
    assert action["changes"] == 3 and "Left out" not in text
    # None came back: an error naming each.
    stuck = _trashed(c, shelf, 2, "U")

    def refused(ws, conn, page_id, **kw):
        raise OpError(409, "the page is busy")

    monkeypatch.setattr(ops, "restore_page", refused)
    text, action = run_agent_tool(ids["ws"], folder(shelf), "restore_page", {"page_ids": stuck})
    assert text.splitlines() == ["error: none of these pages could be restored:",
                                 "Left out — fix these and send only them again:",
                                 f"- the page is busy: {stuck[0]}, {stuck[1]}"], text
    assert action["error"] is True and _parents(ids["ws"], stuck) == ["trash", "trash"]


# --- argument shapes and caps -------------------------------------------------------

def test_caps_and_argument_shapes(org):
    c, ids = org
    shelf = make_folder(c, "batchS")
    a, b = _page(c, "shape a", folders=[shelf]), _page(c, "shape b", folders=[shelf])
    ws, scope = ids["ws"], folder(shelf)
    text, _ = run_agent_tool(ws, scope, "rename_page", _renames(*[(a, f"t{n}") for n in range(51)]))
    assert text.startswith("error: 51 renames — at most 50 in one call"), text
    text, _ = run_agent_tool(ws, scope, "move_page", {"page_ids": [a] + [f"id{n:04d}" for n in range(100)],
                                                      "folder": "x"})
    assert text.startswith("error: 101 pages — at most 100 in one call"), text
    assert (_title(c, a), _filed(c, a, "folders")) == ("shape a", [shelf])
    # A list's whole JSON text, or one bare id, names its pages.
    text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": json.dumps([a, b]), "add": ["shape-1"]})
    assert text.startswith("ok — changed the labels of 2 pages"), text
    text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": a, "add": ["shape-2"]})
    assert text.startswith('ok — "shape a" now carries'), text
    # Text that is neither is refused, never read in part.
    for raw in (f"{a}, {b}", json.dumps([a, b])[:30] + "…", "the papers in folder Physics"):
        text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": raw, "add": ["shape-3"]})
        assert text.startswith("error") and "the list itself, not its text" in text, (raw, text)
    with connect_pages_db(ws) as conn:
        assert not label_by_name(conn, "shape-3")
    # A long bogus id is answered without a lookup, and shown cut.
    text, _ = run_agent_tool(ws, scope, "rename_page", _renames((a, "shape a2"), ("x" * 300, "y")))
    assert f"- no such page — use exact page ids: {'x' * 64}…" in text and "x" * 65 not in text, text
    renames = json.dumps([{"page_id": a, "title": "shape a3"}, {"page_id": b, "title": "shape b3"}])
    text, _ = run_agent_tool(ws, scope, "rename_page", {"renames": renames})
    assert text.startswith("ok — renamed 2 pages"), text
    text, _ = run_agent_tool(ws, scope, "rename_page", {"renames": renames[:40] + "…"})
    assert text.startswith("error") and "the list itself, not its text" in text, text
    assert (_title(c, a), _title(c, b)) == ("shape a3", "shape b3")
    # A replayed list of names is that list, not one name — an empty one
    # too — and one cut off is refused.
    run_agent_tool(ws, scope, "label_page", {"page_ids": [a], "add": '["to-read"]'})
    with connect_pages_db(ws) as conn:
        assert label_by_name(conn, "to-read") and not label_by_name(conn, '["to-read"]')
    text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": [a, b], "add": "[]", "remove": '["to-read"]'})
    assert text.startswith("ok — changed the labels of 1 of 2 pages: −to-read."), text
    text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": [a], "add": '["shape-4"]', "remove": "[]"})
    assert text.startswith('ok — "shape a3" now carries'), text
    text, _ = run_agent_tool(ws, scope, "label_page", {"page_ids": [a], "add": '["shape-5", "shape-…'})
    assert text == "error: `add` must be a list of label names — the list itself, not its text", text
    with connect_pages_db(ws) as conn:
        assert not label_by_name(conn, "[]") and not label_by_name(conn, '["shape-5", "shape-…')


# --- the card -------------------------------------------------------------------------

def test_the_batch_card_lists_every_page_and_changes_nothing(notes):
    c, ids = notes
    ws = ids["ws"]
    shelf, out = make_folder(c, "batchC"), make_folder(c, "batchOut")
    inner, old = make_folder(c, "batchC/inner"), make_folder(c, "batchC/inner/old")
    a, b = _page(c, "card a", folders=[shelf]), _page(c, "card b", folders=[shelf])
    preview, _ = approval_preview(ws, _scope(shelf), "rename_page",
                                  _renames((a, "card a2"), (b, "card b2"), ("nopage99", "z")))
    assert (preview["count"], preview["left_out_count"], preview["left_out"]) == (2, 1, ["nopage99"])
    assert preview["diff"].count(["ctx", "\n"]) == 1 and len(preview["digest"]) == 16
    # A note's id is left out by its id, never its text; a long title is cut.
    long_title = _page(c, "L" * 300)
    preview, _ = approval_preview(ws, _scope(shelf), "move_page",
                                  {"page_ids": [a, b, ids["top"], long_title], "folder": "made later"})
    assert preview["left_out"][0] == ids["top"] and "top-level idea" not in json.dumps(preview)
    assert preview["left_out"][1] == f"“{'L' * 79}…”"
    assert (preview["to"], preview["new_folder"]) == ("batchC / made later", True)
    assert ["batchC", "made later"] not in folder_names(c).values(), "a preview makes no folder"
    preview, _ = approval_preview(ws, _scope(shelf), "label_page", {"page_ids": [a, b], "add": ["card new"]})
    assert preview["new_labels"] == ["card new"]
    # A move strikes only the folders a page leaves: one out of reach above the
    # target gives way, one out of reach elsewhere stays and reads as context.
    p, q = _page(c, "card p", folders=[shelf, out, old]), _page(c, "card q", folders=[old])
    preview, _ = approval_preview(ws, _scope(inner), "move_page", {"page_ids": [p, q], "folder": ""})
    first = preview["diff"][:preview["diff"].index(["ctx", "\n"])]
    assert first == [["ctx", "card p"], ["ctx", " · "], ["del", "batchC, batchC / inner / old"],
                     ["ctx", ", batchOut"]]
    assert preview["to"] == "batchC / inner" and "new_folder" not in preview
    assert (_title(c, a), _filed(c, p, "folders"), _filed(c, a, "labels")) == ("card a", [shelf, out, old], None)


def test_a_batch_changes_at_most_what_the_message_may_still_change(org):
    c, ids = org
    shelf = make_folder(c, "batchB")
    pages = [_page(c, f"budget {n}", folders=[shelf]) for n in range(3)]

    def call(left, title):
        args = _renames(*[(p, f"{title} {n}") for n, p in enumerate(pages)])
        return (approval_preview(ids["ws"], _scope(shelf, changes_left=left), "rename_page", args),
                run_agent_tool(ids["ws"], {**folder(shelf), "changes_left": left}, "rename_page", args)[0])

    (preview, answer), text = call(2, "two")
    assert preview is None and answer.startswith(CHANGE_LIMIT_PREFIX) and "may make only 2 more changes" in answer
    assert text.startswith(CHANGE_LIMIT_PREFIX) and _title(c, pages[0]) == "budget 0"
    for left in (3, None):
        (preview, _), text = call(left, f"left {left}")
        assert preview["count"] == 3 and text.startswith("ok — renamed 3 pages"), text
        assert _title(c, pages[2]) == f"left {left} 2"


def test_an_allowed_card_runs_only_as_it_showed(org):
    c, ids = org
    ws = ids["ws"]
    shelf, target = make_folder(c, "batchA"), make_folder(c, "batchA/there")
    pages = [_page(c, f"allowed {n}", folders=[shelf]) for n in range(3)]
    args = {"page_ids": pages, "folder": "there"}
    preview, _ = approval_preview(ws, _scope(shelf), "move_page", args)

    def rename_folder(folder_id, name):
        with connect_pages_db(ws) as conn:
            apply_ops(conn, "folders", [{"op": "set", "id": folder_id, "content": name}], actor="")

    # The path now names no folder: the run would make one, which the card did not show.
    rename_folder(target, "elsewhere")
    text, _ = run_agent_tool(ws, folder(shelf), "move_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED and all(_filed(c, p, "folders") == [shelf] for p in pages)
    rename_folder(target, "there")
    text, _ = run_agent_tool(ws, folder(shelf), "move_page", args, approved=preview["digest"])
    assert text.startswith("ok — moved 3 pages"), text
    # A folder to make under a folder renamed meanwhile: the same path, made elsewhere.
    parent = make_folder(c, "batchP")
    args = {"page_ids": pages, "folder": "batchP / new"}
    preview, _ = approval_preview(ws, _scope(), "move_page", args)
    rename_folder(parent, "batchP2")
    text, _ = run_agent_tool(ws, folder(), "move_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED
    assert not any(path[:1] == ["batchP"] for path in folder_names(c).values()), "no folder was made"
    # A page the card left out that came into reach meanwhile.
    outsider = _page(c, "allowed outsider")
    args = {"page_ids": [*pages, outsider], "folder": ""}
    preview, _ = approval_preview(ws, _scope(shelf), "move_page", args)
    assert preview["left_out_count"] == 1
    _refile(c, outsider, [target])
    text, _ = run_agent_tool(ws, folder(shelf), "move_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED and _filed(c, pages[0], "folders") == [target]


def test_a_page_changed_while_the_card_waited_voids_the_call(org):
    """Even where the page would end as the card showed: the user's own
    change made meanwhile is never written over."""
    c, ids = org
    ws = ids["ws"]
    shelf = make_folder(c, "batchW")
    x, y, t = (make_folder(c, f"batchW/{n}") for n in ("x", "y", "t"))
    a, b = _page(c, "wait a", folders=[x]), _page(c, "wait b", folders=[x])
    args = _renames((a, "wait a2"), (b, "wait b2"))
    preview, _ = approval_preview(ws, _scope(shelf), "rename_page", args)
    _retitle(c, a, "by hand")
    text, _ = run_agent_tool(ws, folder(shelf), "rename_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED and [_title(c, p) for p in (a, b)] == ["by hand", "wait b"]
    # Refiled within the chat's folder: the card struck x, not y.
    args = {"page_ids": [a, b], "folder": "t"}
    preview, _ = approval_preview(ws, _scope(shelf), "move_page", args)
    _refile(c, a, [y])
    text, _ = run_agent_tool(ws, folder(shelf), "move_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED and (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([y], [x])
    # A label taken off by hand that the call takes off too.
    gone = make_label(c, "wait gone")
    for page in (a, b):
        assert c.put(f"/api/blocks/{page}", json={"properties": {"labels": [gone]}}).status_code == 200
    args = {"page_ids": [a, b], "add": ["wait new"], "remove": ["wait gone"]}
    preview, _ = approval_preview(ws, _scope(shelf), "label_page", args)
    assert c.put(f"/api/blocks/{a}", json={"properties": {"labels": None}}).status_code == 200
    text, _ = run_agent_tool(ws, folder(shelf), "label_page", args, approved=preview["digest"])
    assert text == BATCH_CHANGED and (_filed(c, a, "labels"), _filed(c, b, "labels")) == (None, [gone])
    # Unchanged, the same card runs.
    preview, _ = approval_preview(ws, _scope(shelf), "move_page", args := {"page_ids": [a, b], "folder": "t"})
    text, _ = run_agent_tool(ws, folder(shelf), "move_page", args, approved=preview["digest"])
    assert text.startswith("ok — moved 2 pages"), text
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([t], [t])


def test_a_lone_surrogate_the_model_sent_is_stored_as_a_replacement(org):
    c, ids = org
    shelf = make_folder(c, "batchU")
    a, b = _page(c, "lone a", folders=[shelf]), _page(c, "lone b", folders=[shelf])
    lone = json.loads('"Smile \\ud83d"')
    text, _ = run_agent_tool(ids["ws"], folder(shelf), "rename_page", {"page_id": a, "title": lone})
    assert text.startswith("ok — renamed"), text
    assert _title(c, a) == "Smile �"
    preview, _ = approval_preview(ids["ws"], _scope(shelf), "rename_page", _renames((a, lone + "!"), (b, lone)))
    assert preview["count"] == 2 and len(preview["digest"]) == 16
    text, _ = run_agent_tool(ids["ws"], folder(shelf), "label_page", {"page_ids": [a, b], "add": [lone]})
    assert text.startswith("ok — changed the labels of 2 pages"), text


# --- reverting ------------------------------------------------------------------------

def test_reverting_a_rename_batch_keeps_a_title_renamed_since(org):
    c, ids = org
    shelf = make_folder(c, "batchRv")
    a, b, d = (_page(c, t, folders=[shelf]) for t in ("rv a", "rv b", "rv c"))
    _, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page",
                               _renames((a, "rv a2"), (b, "rv b2"), (d, "rv c2")))
    _retitle(c, a, "by hand")
    r = _revert(c, action)
    assert r.status_code == 200 and r.json()["kept"] == 1 and r.json()["noop"] is False, r.text
    assert [_title(c, p) for p in (a, b, d)] == ["by hand", "rv b", "rv c"]
    r = _revert(c, action)
    assert (r.json()["noop"], r.json()["kept"]) == (True, 1), r.text
    r = _revert(c, action, redo=True)
    assert (r.json()["noop"], r.json()["kept"]) == (False, 1), r.text
    assert [_title(c, p) for p in (a, b, d)] == ["by hand", "rv b2", "rv c2"]
    # A one-page rename renamed again by hand: nothing to put back, and it says so.
    _, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames((b, "rv b3")))
    _retitle(c, b, "by hand too")
    r = _revert(c, action)
    assert r.status_code == 409 and (r.json()["conflict"], r.json()["preview"]) == ("kept", {"kept": 1}), r.text
    assert _title(c, b) == "by hand too"


def test_reverting_a_move_keeps_a_filing_made_since(org):
    c, ids = org
    shelf, other = make_folder(c, "batchV"), make_folder(c, "batchOther")
    a, b = _page(c, "mv a", folders=[shelf]), _page(c, "mv b", folders=[shelf])
    _, action = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_ids": [a, b], "folder": "dest"})
    dest = make_folder(c, "batchV/dest")
    assert action["revert"]["pages"] == [[a, "folders", [shelf], [dest]], [b, "folders", [shelf], [dest]]]
    _refile(c, a, [dest, other])
    r = _revert(c, action)
    assert r.status_code == 200 and "kept" not in r.json(), r.text
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([other, shelf], [shelf])
    assert ["batchV", "dest"] in folder_names(c).values(), "a folder the call made stays"
    r = _revert(c, action, redo=True)
    assert r.status_code == 200, r.text
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([other, dest], [dest])


def test_a_move_revert_leaves_a_page_whose_folders_were_deleted(org):
    """Never filed in no folder, which neither the call nor its revert
    left it in: the page stays as it is and is counted."""
    c, ids = org
    shelf, other = make_folder(c, "batchG"), make_folder(c, "batchGOther")
    x = make_folder(c, "batchG/x")
    a, b = _page(c, "gone a", folders=[x]), _page(c, "gone b", folders=[x])
    _, action = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_ids": [a, b], "folder": "newone"})
    new = make_folder(c, "batchG/newone")
    assert _revert(c, action).status_code == 200
    _refile(c, b, [x, other])
    assert c.delete(f"/api/folders/{new}").status_code == 200
    r = _revert(c, action, redo=True)
    assert r.status_code == 200 and r.json()["kept"] == 1, r.text
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([x], [other])
    # The folder it came from, deleted: the revert leaves it where the call put it.
    _, action = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_ids": [a], "folder": ""})
    assert c.delete(f"/api/folders/{x}").status_code == 200
    r = _revert(c, action)
    assert r.status_code == 409 and r.json()["preview"] == {"kept": 1}, r.text
    assert _filed(c, a, "folders") == [shelf]


def test_reverting_labels_keeps_a_label_added_since(org):
    c, ids = org
    a, b = _page(c, "lb a"), _page(c, "lb b")
    _, action = run_agent_tool(ids["ws"], folder(""), "label_page", {"page_ids": [a, b], "add": ["L1 batch"]})
    l1, l2 = make_label(c, "L1 batch"), make_label(c, "L2 batch")
    assert c.put(f"/api/blocks/{a}", json={"properties": {"labels": [l1, l2]}}).status_code == 200
    r = _revert(c, action)
    assert r.status_code == 200, r.text
    assert (_filed(c, a, "labels"), _filed(c, b, "labels")) == ([l2], None)


def test_a_revert_passes_by_pages_deleted_since_and_refuses_a_bad_record(org):
    c, ids = org
    shelf = make_folder(c, "batchD")
    a, b = _page(c, "del a", folders=[shelf]), _page(c, "del b", folders=[shelf])
    _, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", _renames((a, "del a2"), (b, "del b2")))
    assert c.delete(f"/api/blocks/{a}").status_code == 200
    r = _revert(c, action)
    assert r.status_code == 200 and r.json()["kept"] == 1, r.text
    assert _title(c, b) == "del b"
    assert c.delete(f"/api/blocks/{b}").status_code == 200
    r = _revert(c, action, redo=True)
    assert r.status_code == 409 and (r.json()["conflict"], r.json()["preview"]) == ("kept", {"kept": 2}), r.text
    for pages in ([[a, "title", "x", "y", "z"]], [[a, "colour", "x", "y"]], [[a, ["title"], "x", "y"]],
                  [[a, {"title": 1}, "x", "y"]], [[a, "folders", "x", ["y"]]], [[a, "title", "x", "y"]] * 101, []):
        r = c.post("/api/ai/revert", json={"kind": "pages", "block_id": "", "revert": {"pages": pages}})
        assert r.status_code == 400, (pages[:1], r.text)
    r = c.post("/api/ai/revert", json={"kind": "edit", "block_id": "", "revert": {"before": "a", "after": "b"}})
    assert r.status_code == 400, r.text


def test_a_one_page_rename_move_or_label_reverts_too(org):
    c, ids = org
    shelf = make_folder(c, "batchOne2")
    page = _page(c, "soon untitled", folders=[shelf])
    _retitle(c, page, "")  # reads "Untitled", stores ""
    _, action = run_agent_tool(ids["ws"], folder(shelf), "rename_page", {"page_id": page, "title": "named"})
    assert action["revert"]["pages"] == [[page, "title", "", "named"]]
    assert _revert(c, action).status_code == 200 and _title(c, page) == "", "untitled again, not 'Untitled'"
    _, action = run_agent_tool(ids["ws"], folder(shelf), "move_page", {"page_id": page, "folder": "one sub"})
    assert _revert(c, action).status_code == 200 and _filed(c, page, "folders") == [shelf]
    _, action = run_agent_tool(ids["ws"], folder(shelf), "label_page", {"page_id": page, "add": ["one-l"]})
    assert _filed(c, page, "labels")
    assert _revert(c, action).status_code == 200 and _filed(c, page, "labels") is None
