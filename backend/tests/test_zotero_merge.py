"""The Zotero link's pure decisions (gamma/zotero_merge.py): projections in
Zotero's terms, the field-by-field merge against two bases and the bases a
round records, the tags a PATCH sends, note regions (what a note renders,
what Gamma keeps, the block edits a pulled note makes), the note checks,
primary-PDF selection, first-link matching, and the page rules and
conflict lines. Plain data throughout: no database, no network."""

import json
import random

import pytest

from gamma import zotero_merge as zm
from gamma.zotero_merge import (Merge, annotation_line, bases_after, colour_name, fetchable, field_line,
                                first_link_base, folder_projection, collection_projection, gamma_notes_tree,
                                is_paper, item_projection, match, match_paths, merge, note_edits, note_lossy,
                                note_removal, page_projection, paths, primary, represented, standalone_gets_parent,
                                tag_names, tags_for_push, wants_item)
from gamma.zotero_notes import note_html, note_tree
from gamma.zotero_web import item_uri

LIB = "users/123"

ATTENTION = {
    "key": "AAAA2222", "version": 7, "itemType": "journalArticle", "title": "Attention Is All You Need",
    "creators": [{"creatorType": "author", "firstName": "Ashish", "lastName": "Vaswani"},
                 {"creatorType": "author", "firstName": "Noam", "lastName": "Shazeer"},
                 {"creatorType": "editor", "firstName": "Ed", "lastName": "Itor"}],
    "date": "2017-06-12", "publicationTitle": "NeurIPS", "DOI": "10.5555/3295222.3295349",
    "extra": "Citation Key: vaswani2017", "url": "https://arxiv.org/abs/1706.03762",
    "tags": [{"tag": "Transformers"}, {"tag": "cs.CL", "type": 1}, {"tag": "transformers", "type": 1},
             {"tag": "Reading"}],
    "collections": ["COLL2222", "GONE2222"], "dateAdded": "2023-01-01T00:00:00Z",
}


# --- projections ------------------------------------------------------------------------

def test_an_item_projects_its_record_manual_tags_and_live_collections():
    p = item_projection(ATTENTION, live_collections={"COLL2222"})
    assert p["type"] == "journalArticle"
    assert p["fields"]["title"] == "Attention Is All You Need"
    assert p["fields"]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert p["fields"]["year"] == "2017" and p["fields"]["cite_key"] == "vaswani2017"
    assert list(p["fields"]) == list(zm.zotero_fields.COMPARE_FIELDS)
    assert p["tags"] == ["reading", "transformers"]  # the automatic cs.CL left in Zotero
    assert p["collections"] == ["COLL2222"]           # a trashed or unknown collection takes no part


def test_automatic_tags_take_part_when_the_link_brings_them():
    p = item_projection(ATTENTION, live_collections=None, auto_tags=True)
    assert p["tags"] == ["cs.cl", "reading", "transformers"]
    assert p["collections"] == ["COLL2222", "GONE2222"]


def test_tag_names_keep_zoteros_first_spelling():
    assert tag_names(ATTENTION, auto_tags=False) == {"transformers": "Transformers", "reading": "Reading"}
    assert tag_names(ATTENTION, auto_tags=True)["cs.cl"] == "cs.CL"
    assert tag_names({"tags": [{"tag": "  "}, "junk", {"tag": "x", "type": "1"}]}, auto_tags=False) == {}


def test_a_page_built_from_an_item_projects_as_the_item():
    meta = zm.zotero_fields.meta_from_item(ATTENTION)
    page = page_projection(meta, "vaswani2017", "Attention Is All You Need", "journalArticle",
                           ["Transformers", "reading "], ["COLL2222"])
    item = item_projection(ATTENTION, live_collections={"COLL2222"})
    assert page == item


def test_a_page_keeps_the_items_own_type_while_it_reads_as_its_kind():
    blog = {"key": "BBBB2222", "itemType": "blogPost", "title": "A post", "websiteTitle": "Blog"}
    meta = zm.zotero_fields.meta_from_item(blog)
    assert meta["kind"] == "document"
    assert page_projection(meta, "", "A post", "blogPost")["type"] == "blogPost"
    assert page_projection({**meta, "kind": "report"}, "", "A post", "blogPost")["type"] == "report"
    assert page_projection({"title": "New"}, item_type="")["type"] == "journalArticle"  # inferred
    assert page_projection({}, title="Slides.pdf")["type"] == "document"
    assert page_projection({}, title="Slides.pdf")["fields"]["title"] == "Slides.pdf"


def test_collections_and_folders_project_as_name_and_parent():
    assert collection_projection({"key": "C", "name": " Reading ", "parentCollection": False}) == \
        {"name": "Reading", "parent": ""}
    sub = {"name": "Sub", "parentCollection": "PARENT22"}
    assert collection_projection(sub) == {"name": "Sub", "parent": "PARENT22"}
    assert folder_projection(" Reading ") == {"name": "Reading", "parent": ""}
    assert folder_projection("Sub", "PARENT22") == {"name": "Sub", "parent": "PARENT22"}


def test_tags_for_push_keeps_zoteros_objects_and_automatic_tags():
    tags = [{"tag": "Transformers"}, {"tag": "cs.CL", "type": 1}, {"tag": "old"}, {"tag": "Keep", "type": 0}]
    out = tags_for_push(tags, ["transformers", "keep", "New label", "new label"])
    assert out == [{"tag": "Transformers"}, {"tag": "cs.CL", "type": 1}, {"tag": "Keep", "type": 0},
                   {"tag": "New label"}]


def test_tags_for_push_removes_an_automatic_tag_only_when_the_link_brings_them():
    tags = [{"tag": "cs.CL", "type": 1}, {"tag": "quant-ph", "type": 1}]
    assert tags_for_push(tags, ["quant-ph"], auto_tags=True) == [{"tag": "quant-ph", "type": 1}]
    assert tags_for_push(tags, [], auto_tags=False) == tags
    assert tags_for_push([], []) == []


def test_a_standalone_pdfs_page_and_attachment_project_alike():
    data = {"itemType": "attachment", "title": "Scanned   handout", "filename": "handout.pdf",
            "tags": [{"tag": "Foo"}, {"tag": "bar"}, {"tag": "auto", "type": 1}],
            "collections": ["COLL0001", "COLL0002", "GONE0003"]}
    zotero = zm.standalone_projection(data, live_collections=["COLL0001", "COLL0002"], auto_tags=False)
    gamma = zm.page_standalone_projection("  Scanned handout ", ["  Foo ", "bar"], ["COLL0002", "COLL0001"])
    assert zotero == gamma == {"title": "Scanned handout", "tags": ["bar", "foo"],
                               "collections": ["COLL0001", "COLL0002"]}
    note = zm.standalone_projection(data, live_collections=["COLL0001"], title=False)
    assert note == zm.page_standalone_projection("x", ["foo", "BAR"], ["COLL0001"], with_title=False)
    assert zm.fold_tag("  Foo ") == "foo"


# --- the merge ---------------------------------------------------------------------------

def item(**fields):
    base = {"type": "journalArticle", "fields": {"title": "T", "year": "2017", "venue": "V"},
            "tags": ["a"], "collections": ["C1"]}
    for k, v in fields.items():
        if k in ("type", "tags", "collections"):
            base[k] = v
        else:
            base["fields"] = {**base["fields"], k: v}
    return base


def test_nothing_changed_and_a_version_bump_give_nothing():
    b = item()
    m = merge(b, b, b, b)
    assert m == Merge({}, {}, [], {}, {"tags": {"gamma": ["a"], "zotero": ["a"]},
                                        "collections": {"gamma": ["C1"], "zotero": ["C1"]}})
    assert not m.apply and not m.push and not m.conflicts


def test_one_sides_change_goes_to_the_other():
    b = item()
    m = merge(b, b, item(year="2018"), item(venue="NeurIPS"))
    assert m.apply == {"year": "2018"}
    assert m.push == {"venue": "NeurIPS"}
    assert m.conflicts == []


def test_both_changed_alike_is_a_no_op():
    b = item()
    m = merge(b, b, item(year="2018"), item(year="2018"))
    assert not m.apply and not m.push and not m.conflicts


def test_both_changed_apart_gammas_wins_and_is_reported():
    b = item()
    m = merge(b, b, item(year="2018"), item(year="2019"))
    assert m.push == {"year": "2019"} and m.apply == {}
    assert m.conflicts == [("year", "2019", "2018")]
    theirs = merge(b, b, item(year="2018"), item(year="2019"), prefer="zotero")
    assert theirs.apply == {"year": "2018"} and theirs.push == {}
    assert theirs.conflicts == [("year", "2019", "2018")]
    with pytest.raises(ValueError):
        merge(b, b, b, b, prefer="mine")


def test_a_lossy_mapping_never_reads_as_a_change_of_the_other_side():
    # Zotero holds a 2,400-character quote and a subscript; Gamma's copy is
    # shorter. Only the colour changes in Gamma: only the colour is sent.
    zotero = {"type": "highlight", "color": "#ffd400", "text": "x" * 2400, "comment": "H<sub>2</sub>O"}
    gamma = {"type": "highlight", "color": "#ffd400", "text": "x" * 1000, "comment": "H2O"}
    m = merge(zotero, gamma, zotero, {**gamma, "color": "#ff6666"})
    assert m.push == {"color": "#ff6666"} and m.apply == {} and m.conflicts == []
    # A Zotero-side tag on the annotation bumps its version, nothing projected changed
    assert merge(zotero, gamma, dict(zotero), dict(gamma)).apply == {}


def test_a_text_both_edited_apart_merges_when_the_edits_do_not_overlap():
    zb = gb = {"comment": "The cat sat."}
    m = merge(zb, gb, {"comment": "The cat sat down."}, {"comment": "The black cat sat."}, texts=("comment",))
    assert m.merged_text == {"comment": "The black cat sat down."}
    assert not m.apply and not m.push and not m.conflicts


def test_a_text_merge_that_lands_on_one_sides_text_sends_only_that_side():
    zb = gb = {"comment": "abc"}
    m = merge(zb, gb, {"comment": "abc def"}, {"comment": "abc def ghi"}, texts=("comment",))
    assert m.push == {"comment": "abc def ghi"} and not m.merged_text  # Gamma's already holds Zotero's edit


def test_overlapping_text_edits_are_a_conflict_gamma_wins():
    zb = gb = {"comment": "abc"}
    m = merge(zb, gb, {"comment": "abY"}, {"comment": "abX"}, texts=("comment",))
    assert m.push == {"comment": "abX"} and m.conflicts == [("comment", "abX", "abY")]
    assert not m.merged_text


def test_a_text_merges_in_gammas_terms():
    # Zotero's comment holds tags Gamma reads as marks: merged after conversion
    to_gamma = {"comment": lambda v: v.replace("<b>", "**").replace("</b>", "**")}
    zb = {"comment": "one two"}
    m = merge(zb, {"comment": "one two"}, {"comment": "one two <b>three</b>"}, {"comment": "zero one two"},
              texts=to_gamma)
    assert m.merged_text == {"comment": "zero one two **three**"}


def test_no_text_merge_without_a_base():
    m = merge({}, {}, {"comment": "z"}, {"comment": "g"}, texts=("comment",))
    assert m.push == {"comment": "g"} and m.conflicts == [("comment", "g", "z")]


def test_sets_merge_member_by_member():
    b = item(tags=["a", "b"])
    m = merge(b, b, item(tags=["a", "b", "z"]), item(tags=["b", "g"]))
    assert m.apply == {"tags": ["b", "g", "z"]}
    assert m.push == {"tags": ["b", "g", "z"]}
    assert m.sets["tags"] == {"gamma": ["b", "g", "z"], "zotero": ["b", "g", "z"]}
    assert m.conflicts == []


def test_a_set_change_one_side_already_holds_sends_nothing():
    b = item(tags=["a"])
    m = merge(b, b, item(tags=["a", "x"]), item(tags=["a", "x"]))
    assert "tags" not in m.apply and "tags" not in m.push


def test_a_trashed_collection_left_out_of_both_sides_is_no_change():
    # The collection is trashed in Zotero: its key leaves both projections
    # (only live keys take part), so Gamma's refiling sends no removal.
    data = {**ATTENTION, "collections": ["COLL2222", "GONE2222"]}
    before = item_projection(data, live_collections={"COLL2222", "GONE2222"})
    zotero_now = item_projection(data, live_collections={"COLL2222"})
    gamma_now = {**before, "collections": ["COLL2222"]}  # the folder delete here refiled the page
    assert zotero_now["collections"] == ["COLL2222"]
    base_z, base_g = ({**before, "collections": ["COLL2222"]},) * 2  # bases re-read without the key
    m = merge(base_z, base_g, zotero_now, gamma_now)
    assert "collections" not in m.push and "collections" not in m.apply


def test_a_record_field_named_like_a_key_is_refused():
    with pytest.raises(ValueError):
        merge({}, {}, {"type": "x", "fields": {"type": "y"}}, {})


# --- the bases a round records ---------------------------------------------------------

def test_an_apply_records_gammas_own_reading_of_the_result():
    b = item()
    z = item(title="Attention")
    m = merge(b, b, z, b)
    zb2, gb2 = bases_after(m, b, b, z, b, pushed_ok=True, G_after=item(title="attention"))
    assert zb2["fields"]["title"] == "Attention"
    assert gb2["fields"]["title"] == "attention"  # Gamma's lossy copy is the base: no change next round
    nxt = merge(zb2, gb2, z, item(title="attention"))
    assert not nxt.apply and not nxt.push


def test_a_landed_push_records_the_saved_object_and_the_value_decided():
    b = item()
    g = item(venue="NeurIPS 2017")
    m = merge(b, b, b, g)
    zb2, gb2 = bases_after(m, b, b, b, g, pushed_ok=True, Z_after=item(venue="NeurIPS 2017 "))
    assert zb2["fields"]["venue"] == "NeurIPS 2017 "
    assert gb2["fields"]["venue"] == "NeurIPS 2017"
    zb3, _ = bases_after(m, b, b, b, g, pushed_ok=True)
    assert zb3["fields"]["venue"] == "NeurIPS 2017"


def test_a_push_that_did_not_land_is_sent_again_and_never_overwritten():
    # Gamma edits the title while Zotero changes the date; the PATCH fails
    base = item()
    z1, g1 = item(year="2018"), item(title="New title")
    m = merge(base, base, z1, g1)
    assert m.apply == {"year": "2018"} and m.push == {"title": "New title"}
    g_after = item(title="New title", year="2018")
    zb2, gb2 = bases_after(m, base, base, z1, g1, pushed_ok=False, G_after=g_after)
    assert zb2["fields"]["year"] == "2018" and gb2["fields"]["year"] == "2018"
    assert gb2["fields"]["title"] == "T"  # still unsynced
    # Zotero edits again; Gamma's title still reads as Gamma's change
    z2 = item(year="2018", volume="30")
    again = merge(zb2, gb2, z2, g_after)
    assert again.push == {"title": "New title"} and again.apply == {"volume": "30"}
    assert again.conflicts == []


def test_a_gamma_win_that_did_not_land_is_reported_once():
    base = item()
    z, g = item(year="2018"), item(year="2019")
    m = merge(base, base, z, g)
    zb2, gb2 = bases_after(m, base, base, z, g, pushed_ok=False)
    nxt = merge(zb2, gb2, z, g)
    assert nxt.push == {"year": "2019"} and nxt.conflicts == []


def test_receive_only_keeps_gamma_changes_for_later():
    base = item()
    g = item(title="Mine")
    m = merge(base, base, base, g)
    zb2, gb2 = bases_after(m, base, base, base, g, pushed_ok=False)
    assert merge(zb2, gb2, base, g).push == {"title": "Mine"}


def test_a_merged_text_records_its_bases_only_once_sent():
    zb = gb = {"comment": "The cat sat."}
    z, g = {"comment": "The cat sat down."}, {"comment": "The black cat sat."}
    m = merge(zb, gb, z, g, texts=("comment",))
    merged = m.merged_text["comment"]
    ok_z, ok_g = bases_after(m, zb, gb, z, g, pushed_ok=True)
    assert ok_z == ok_g == {"comment": merged}
    bad_z, bad_g = bases_after(m, zb, gb, z, g, pushed_ok=False, G_after={"comment": merged})
    assert bad_z == z and bad_g == {"comment": None}  # both changed: Gamma's goes, whatever it then holds
    nxt = merge(bad_z, bad_g, z, {"comment": merged}, texts=("comment",))
    assert nxt.push == {"comment": merged}
    back = merge(bad_z, bad_g, z, gb)  # even Gamma's text set back: it goes, never Zotero's left unseen
    assert back.push == gb and not back.apply


def test_a_field_gamma_won_but_did_not_send_goes_even_once_set_back():
    zb = gb = {"title": "Old"}
    z, g = {"title": "Zotero's"}, {"title": "Gamma's"}
    m = merge(zb, gb, z, g)
    assert m.push == {"title": "Gamma's"} and m.conflicts
    bz, bg = bases_after(m, zb, gb, z, g, pushed_ok=False)
    assert bz == z and bg == {"title": None}
    assert merge(bz, bg, z, {"title": "Old"}).push == {"title": "Old"}  # set back here: that goes
    assert merge(bz, bg, z, g).push == g
    # a field Zotero did not change keeps Gamma's old base: set back, both sides agree again
    m2 = merge(zb, gb, zb, g)
    assert bases_after(m2, zb, gb, zb, g, pushed_ok=False) == (zb, gb)


def test_set_bases_follow_each_member():
    base = item(tags=["a", "b"])
    z, g = item(tags=["a", "b", "z"]), item(tags=["b", "g"])
    m = merge(base, base, z, g)
    ok_z, ok_g = bases_after(m, base, base, z, g, pushed_ok=True)
    assert ok_z["tags"] == ok_g["tags"] == ["b", "g", "z"]
    bad_z, bad_g = bases_after(m, base, base, z, g, pushed_ok=False, G_after=item(tags=["b", "g", "z"]))
    assert bad_z["tags"] == ["a", "b", "z"]
    assert bad_g["tags"] == ["a", "b", "z"]  # Gamma's removal of a and addition of g still unsent
    nxt = merge(bad_z, bad_g, z, item(tags=["b", "g", "z"]))
    assert nxt.push == {"tags": ["b", "g", "z"]} and "tags" not in nxt.apply


def test_bases_keep_the_projection_shape():
    b = item()
    zb2, gb2 = bases_after(merge(b, b, b, b), b, b, b, b, pushed_ok=True)
    assert zb2 == b and gb2 == b


# --- first link -------------------------------------------------------------------------

def test_a_first_link_base_holds_only_what_both_agree_on():
    z = item(year="2017", venue="NeurIPS", tags=["a", "z"], collections=["C1"])
    g = item(year="2017", venue="NIPS", tags=["a", "g"], collections=["C1", "C2"])
    zb, gb = first_link_base("item", z, g)
    assert zb["fields"]["year"] == gb["fields"]["year"] == "2017"
    assert zb["fields"]["venue"] is None and gb["fields"]["venue"] is None
    assert zb["tags"] == gb["tags"] == ["a"]
    mine = merge(zb, gb, z, g, prefer="gamma")
    assert mine.push == {"venue": "NIPS", "tags": ["a", "g", "z"], "collections": ["C1", "C2"]}
    assert mine.apply == {"tags": ["a", "g", "z"]}
    assert mine.conflicts == [("venue", "NIPS", "NeurIPS")]
    theirs = merge(zb, gb, z, g, prefer="zotero")
    assert theirs.apply["venue"] == "NeurIPS" and "venue" not in theirs.push


def test_a_first_link_reads_positions_alike_within_half_a_point():
    pos = {"pageIndex": 0, "rects": [[100.0, 200.0, 300.0, 210.0]]}
    near = {"pageIndex": 0, "rects": [[100.4, 199.6, 300.0, 210.0]]}
    far = {"pageIndex": 0, "rects": [[100.6, 200.0, 300.0, 210.0]]}
    zb, gb = first_link_base("annotation", {"position": pos, "color": "#ffd400"},
                             {"position": near, "color": "#ffd400"})
    assert zb["position"] == pos and gb["position"] == near
    assert merge(zb, gb, {"position": pos}, {"position": near}).push == {}
    zb, gb = first_link_base("annotation", {"position": pos}, {"position": far})
    assert zb["position"] is None
    zb, gb = first_link_base("item", {"position": pos}, {"position": near})  # tolerance for annotations only
    assert zb["position"] is None


def test_a_first_link_reads_notes_alike_by_their_text():
    zb, gb = first_link_base("note", {"hash": "z", "text": "One  two"}, {"hash": "g", "text": "One two"})
    assert zb == {"hash": "z", "text": "One  two"} and gb == {"hash": "g", "text": "One two"}
    zb, _ = first_link_base("note", {"hash": "z", "text": "One"}, {"hash": "g", "text": "Two"})
    assert zb == {"hash": None, "text": None}


def _record(**fields):
    base = {f: "" for f in ("title", "year", "venue", "volume", "pages", "doi", "arxiv_id", "publisher", "isbn",
                            "kind", "cite_key")}
    return {"type": "journalArticle", "fields": {**base, "authors": [], **fields}, "tags": [], "collections": []}


@pytest.mark.parametrize("prefer", ["gamma", "zotero"])
def test_a_first_link_base_lets_a_value_one_side_lacks_go_to_the_other_whatever_adopt_says(prefer):
    z = _record(title="Z's title", venue="J. Novel", volume="3")
    g = _record(title="G's title", publisher="arXiv", cite_key="lee2020")
    zb, gb = zm.first_link_base("item", z, g)
    m = zm.merge(zb, gb, z, g, prefer=prefer)
    assert {k: v for k, v in m.apply.items() if k != "title"} == {"venue": "J. Novel", "volume": "3"}
    assert {k: v for k, v in m.push.items() if k != "title"} == {"publisher": "arXiv", "cite_key": "lee2020"}
    assert m.conflicts == [("title", "G's title", "Z's title")]  # two values apart: adopt decides
    assert (m.push if prefer == "gamma" else m.apply)["title"] == ("G's title" if prefer == "gamma" else "Z's title")


def test_a_first_link_keeps_zoteros_annotation_comment_where_gammas_has_none():
    z = {"color": "#ffd400", "comment": "great point"}
    g = {"color": "#ffd400", "comment": ""}
    zb, gb = zm.first_link_base("annotation", z, g)
    m = zm.merge(zb, gb, z, g, prefer="gamma")
    assert m.apply == {"comment": "great point"} and m.push == {} and m.conflicts == []


def test_a_collections_top_level_parent_is_a_value_at_a_first_link():
    z, g = {"name": "ML", "parent": "PARENT01"}, {"name": "ML", "parent": ""}
    zb, gb = zm.first_link_base("collection", z, g)
    assert zb["parent"] is None and gb["parent"] is None


def test_a_first_link_reads_positions_half_a_point_apart_as_one_place():
    from gamma import zotero_annotations as za
    assert not hasattr(zm, "POSITION_TOLERANCE")  # one tolerance: zotero_annotations.POSITION_TOL
    a, b = (za.projection({"annotationType": "highlight", "annotationPosition": json.dumps(
        {"pageIndex": 0, "rects": [[x, 1.0, 2.0, 3.0]]})}) for x in (0.57, 1.07))
    zb, gb = first_link_base("annotation", a, b, sets=())
    assert merge(zb, gb, a, b, sets=(), prefer="zotero").apply == {}


# --- note regions -----------------------------------------------------------------------

def blk(bid, content="", *children, kind="note", props=None, parent=None):
    return {"id": bid, "content": content, "kind": kind, "properties": props or {}, "parent_id": parent,
            "children": list(children)}


def region_page():
    """A paper page P: a Zotero-note region R holding a nested highlight, a
    commented child, a ref, a picture and an ink child."""
    root = blk("R", "Method", blk("A", "First point"),
               blk("H", "the quote", kind="highlight", props={"pdf_position": {}}),
               blk("B", "Second point", blk("C", "nested"), blk("K", "see [[blk1]]")),
               blk("I", "![fig](/api/uploads/abc.png)"), blk("W", "", kind="ink", props={"ink_url": "x"}),
               blk("E", ""), parent="P")
    return root


def test_represented_splits_what_the_note_renders_from_what_gamma_keeps():
    r = represented(region_page())
    assert r.blocks == [("R", 0, "Method"), ("A", 1, "First point"), ("B", 1, "Second point"), ("C", 2, "nested")]
    assert r.kept == frozenset({"H", "K", "I", "W", "E"})
    assert r.html == "<p>Method</p><ul><li>First point</li><li>Second point<ul><li>nested</li></ul></li></ul>"
    assert r.html == note_html(r.tree)
    assert r.root == "R" and r.root_parent == "P" and not r.root_fixed and not r.unsettable
    assert r.children["R"] == ["A", "H", "B", "I", "W", "E"]


def test_a_kept_blocks_subtree_is_kept_and_other_regions_are_excluded():
    root = blk("R", "Root", blk("H", "q", blk("N", "note under a highlight"), kind="highlight",
                                props={"pdf_position": {}}),
               blk("Z", "Another Zotero note", blk("Z1", "its text")),
               blk("T", "a box", kind="text_box"), blk("S", "", kind="sheet"), blk("L", "link", kind="link"),
               blk("Q", "Imported", props={"zotero_annotation": "ABCD2345"}))
    r = represented(root, exclude={"Z"})
    assert [b for b, _, _ in r.blocks] == ["R"]
    assert r.kept == {"H", "N", "Z", "Z1", "T", "S", "L", "Q"}


def test_text_survives_the_round_trip_whitespace_and_escapes_aside():
    root = blk("R", "Root", blk("A", "a  b"), blk("B", "- dash"), blk("C", "1) one"), blk("D", "`[[code]]`"),
               blk("E", "**bold** and ==mark=="), blk("F", "![[embed]]"), blk("G", "![](https://x.org/p.png)"))
    r = represented(root)
    assert [b for b, _, _ in r.blocks] == ["R", "A", "B", "C", "D", "E"]
    assert dict((b, c) for b, _, c in r.blocks)["A"] == "a b"  # as the note reads back
    assert r.kept == {"F", "G"}


def test_the_note_merge_reads_a_blocks_text_by_the_writers_rule():
    from gamma import zotero_merge, zotero_notes
    assert zotero_merge.block_text is zotero_notes.block_text
    assert not hasattr(zotero_merge, "_content") and not hasattr(zotero_merge, "_HTML_WS")  # no copy of its own


def test_a_root_the_note_cannot_carry_is_represented_but_never_set():
    r = represented(blk("R", "About [[blk1]]", blk("A", "child")))
    assert r.unsettable == {"R"}
    edits = note_edits(r, "<p>About something else</p><ul><li>child</li></ul>")
    assert edits == [{"op": "lossy", "id": "R", "content": "About something else"}]


def test_represented_takes_fetch_subtree_rows():
    rows = [("R", "root", "a", "Root", "{}", "t", "t", "P"), ("A", "R", "a", "child", "{}", "t", "t", "P"),
            ("H", "R", "b", "q", '{"pdf_position": {"pageNumber": 1}}', "t", "t", "P")]
    r = represented(rows)
    assert [b for b, _, _ in r.blocks] == ["R", "A"] and r.kept == {"H"}


def test_gamma_notes_tree_gathers_the_pages_own_top_level_notes():
    subtrees = [blk("X", "top note", blk("Y", "child")), blk("H", "q", kind="highlight"),
                blk("Att", "## Attachments", props={"zotero_attachments": True}), blk("Z", "Zotero's note"),
                blk("V", "other"), blk("L", "", kind="link")]
    tree = gamma_notes_tree(subtrees, page_id="P", exclude={"Z"})
    assert [c["id"] for c in tree["children"]] == ["X", "V"]
    r = represented(tree)
    assert r.root_fixed and r.root == "P"
    assert r.blocks == [("P", 0, zm.NOTES_HEADING), ("X", 1, "top note"), ("Y", 2, "child"), ("V", 1, "other")]
    assert r.html.startswith("<h1>Notes from Gamma</h1>")
    assert gamma_notes_tree([blk("H", "q", kind="highlight")], page_id="P") is None


# A simulated page that applies note_edits as the engine's writer would.

class Page:
    def __init__(self, root, page_id="P", siblings=()):
        self.page_id = page_id
        self.content, self.kids, self.parent, self.kinds = {}, {page_id: []}, {}, {}
        if root["id"] == page_id:
            self._load_children(root)
        else:
            self._load(root, page_id)
        for s in siblings:
            self._load(s, page_id)
        self.lossy = []

    def _load_children(self, node):
        self.content[node["id"]] = node["content"]
        for c in node["children"]:
            self._load(c, node["id"])

    def _load(self, node, parent):
        self.content[node["id"]] = node["content"]
        self.kinds[node["id"]] = (node.get("kind") or "note", node.get("properties") or {})
        self.parent[node["id"]] = parent
        self.kids.setdefault(parent, []).append(node["id"])
        self.kids.setdefault(node["id"], [])
        for c in node["children"]:
            self._load(c, node["id"])

    def _place(self, bid, parent, after, region):
        siblings = self.kids.setdefault(parent, [])
        if after is None:
            first = next((i for i, s in enumerate(siblings) if s in region), len(siblings))
            siblings.insert(first, bid)
        else:
            assert after in siblings, (bid, parent, after, siblings)
            siblings.insert(siblings.index(after) + 1, bid)
        self.parent[bid] = parent

    def apply(self, region, edits):
        members = set(region.parent) | {region.root}
        for e in edits:
            op = e["op"]
            if op == "set":
                assert e["id"] not in region.kept
                self.content[e["id"]] = e["content"]
            elif op == "lossy":
                self.lossy.append(e)
            elif op == "insert":
                assert e["id"] not in self.content
                self.content[e["id"]] = e["content"]
                self.kids[e["id"]] = []
                members.add(e["id"])
                self._place(e["id"], e["parent"], e["after"], members)
            elif op == "move":
                self.kids[self.parent[e["id"]]].remove(e["id"])
                self._place(e["id"], e["parent"], e["after"], members)
            elif op == "delete":
                assert e["id"] not in region.kept
                assert self.kids[e["id"]] == [], f"delete of {e['id']} would take {self.kids[e['id']]}"
                self.kids[self.parent.pop(e["id"])].remove(e["id"])
                del self.content[e["id"]], self.kids[e["id"]]
            else:
                raise AssertionError(op)

    def tree(self, bid):
        """The block as a ``build_tree`` node, as the engine reads it back."""
        kind, props = self.kinds.get(bid, ("note", {}))
        return {"id": bid, "content": self.content.get(bid, ""), "kind": kind, "properties": props,
                "parent_id": self.parent.get(bid), "children": [self.tree(c) for c in self.kids.get(bid, [])]}

    def notes(self, root, kept):
        """``[(depth, content)]`` of the region's non-kept blocks."""
        out = []

        def walk(bid, depth):
            out.append((depth, self.content[bid]))
            for c in self.kids.get(bid, []):
                if c not in kept:
                    walk(c, depth + 1)

        walk(root, 0)
        return out


def apply_note(root, html, *, siblings=(), page_id="P"):
    r = represented(root)
    page = Page(root, page_id=page_id, siblings=siblings)
    edits = note_edits(r, html)
    page.apply(r, edits)
    return r, page, edits


def test_an_unchanged_note_edits_nothing():
    r = represented(region_page())
    assert note_edits(r, r.html) == []
    assert note_edits(r, note_tree(r.html)) == []


def test_a_pulled_paragraph_edit_sets_that_block_alone():
    r, page, edits = apply_note(region_page(), "<p>Method</p><ul><li>First point, revised</li>"
                                               "<li>Second point<ul><li>nested</li></ul></li></ul>")
    assert edits == [{"op": "set", "id": "A", "content": "First point, revised"}]
    assert page.kids["R"] == ["A", "H", "B", "I", "W", "E"]  # nested highlight, picture, ink: in place
    assert page.kids["B"] == ["C", "K"]


def test_a_pulled_paragraph_is_inserted_in_place():
    r, page, edits = apply_note(region_page(), "<p>Method</p><ul><li>First point</li><li>Added</li>"
                                               "<li>Second point<ul><li>nested</li><li>deeper</li></ul></li></ul>")
    assert [e["op"] for e in edits] == ["insert", "insert"]
    assert edits[0] == {"op": "insert", "id": "new:2", "parent": "R", "after": "A", "content": "Added"}
    assert edits[1] == {"op": "insert", "id": "new:5", "parent": "B", "after": "C", "content": "deeper"}
    assert page.notes("R", r.kept) == [(0, "Method"), (1, "First point"), (1, "Added"), (1, "Second point"),
                                       (2, "nested"), (2, "deeper")]


def test_a_block_deleted_in_zotero_hands_its_kept_children_up_first():
    r, page, edits = apply_note(region_page(), "<p>Method</p><ul><li>First point</li></ul>")
    assert {e["id"] for e in edits if e["op"] == "delete"} == {"B", "C"}
    lift = [e for e in edits if e["op"] == "move"]
    assert lift == [{"op": "move", "id": "K", "parent": "R", "after": "B"}]
    assert page.kids["R"] == ["A", "H", "K", "I", "W", "E"]
    assert all(page.content.get(k) is not None for k in r.kept)


def test_a_depth_change_moves_the_block():
    r, page, edits = apply_note(region_page(), "<p>Method</p><ul><li>First point</li><li>Second point</li>"
                                               "<li>nested</li></ul>")
    assert edits == [{"op": "move", "id": "C", "parent": "R", "after": "B"}]
    assert page.parent["C"] == "R" and page.kids["B"] == ["K"]


def test_an_emptied_note_keeps_its_root():
    r, page, edits = apply_note(region_page(), "")
    assert {"op": "set", "id": "R", "content": ""} in edits
    assert not any(e["op"] == "delete" and e["id"] == "R" for e in edits)
    assert page.notes("R", r.kept) == [(0, "")]
    assert all(k in page.content for k in r.kept)


def test_the_gamma_notes_heading_is_never_set():
    tree = gamma_notes_tree([blk("X", "top note"), blk("V", "other")], page_id="P")
    r = represented(tree)
    edits = note_edits(r, "<h1>Renamed in Zotero</h1><ul><li>top note</li><li>other</li></ul>")
    assert edits == []


def test_a_gamma_notes_pull_inserts_under_the_page_among_its_notes():
    hl = blk("H", "q", kind="highlight")
    tree = gamma_notes_tree([hl, blk("X", "top note"), blk("V", "other")], page_id="P")
    r = represented(tree)
    page = Page(tree, page_id="P")
    assert page.kids["P"] == ["X", "V"]  # the page's region children only in this simulation
    page.kids["P"] = ["H", "X", "V"]
    page.parent["H"] = "P"
    edits = note_edits(r, "<h1>Notes from Gamma</h1><ul><li>first</li><li>top note</li><li>other</li></ul>")
    assert edits == [{"op": "insert", "id": "new:1", "parent": "P", "after": None, "content": "first"}]
    page.apply(r, edits)
    assert page.kids["P"] == ["H", "new:1", "X", "V"]


def test_a_note_deleted_in_zotero_lifts_what_gamma_keeps():
    root = region_page()
    r = represented(root)
    page = Page(root, siblings=[blk("after", "after")])
    edits = note_removal(r)
    page.apply(r, edits)
    assert page.kids["P"] == ["H", "K", "I", "W", "E", "after"]
    assert "R" not in page.content and "A" not in page.content


def test_a_gamma_notes_removal_never_deletes_the_page():
    tree = gamma_notes_tree([blk("X", "top", blk("H", "q", kind="highlight")), blk("V", "other")], page_id="P")
    r = represented(tree)
    page = Page(tree, page_id="P")
    edits = note_removal(r)
    assert {"op": "delete", "id": "P"} not in edits
    page.apply(r, edits)
    assert page.kids["P"] == ["H"]


def random_tree(rnd, words, prefix, size, kept_rate=0.0):
    """A random note region of ``size`` blocks (plus kept ones)."""
    root = blk(f"{prefix}0", rnd.choice(words), parent="P")
    nodes, n = [root], 1
    while n < size:
        parent = rnd.choice(nodes)
        if rnd.random() < kept_rate:
            child = blk(f"{prefix}k{n}", "q", kind="highlight")
            if rnd.random() < 0.5:
                child["children"].append(blk(f"{prefix}kn{n}", rnd.choice(words)))
        else:
            child = blk(f"{prefix}{n}", rnd.choice(words))
            nodes.append(child)
        parent["children"].append(child)
        n += 1
    return root


def rows_of(tree):
    out, stack = [], [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        out.append((depth, node["content"]))
        stack.extend((c, depth + 1) for c in reversed(node["children"]))
    return out


@pytest.mark.parametrize("seed", range(60))
def test_note_edits_reach_the_pulled_note_and_keep_every_kept_block(seed):
    rnd = random.Random(seed)
    words = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta"]
    old = random_tree(rnd, words, "o", rnd.randint(1, 14), kept_rate=0.3)
    new = random_tree(rnd, words, "n", rnd.randint(1, 14))
    r = represented(old)
    page = Page(old)
    page.apply(r, note_edits(r, rows_of(new)))
    assert page.notes(r.root, r.kept) == rows_of(new)
    for k in r.kept:
        assert k in page.content
        if r.parent[k] in r.kept:  # inside a kept subtree: still where it was
            assert page.parent[k] == r.parent[k]


def test_a_round_trip_through_zotero_lands_on_zoteros_note():
    root = region_page()
    r = represented(root)
    zotero = note_html({"content": "Method", "children": [
        {"content": "First point, edited", "children": []}, {"content": "Second point", "children": []},
        {"content": "Third", "children": []}]})
    page = Page(root)
    page.apply(r, note_edits(r, zotero))
    again = represented(page.tree("R"))
    assert again.html == zotero
    assert again.kept == r.kept  # every kept block is still in the region
    assert note_edits(again, zotero) == []


# --- what a Zotero note holds that Gamma loses ---------------------------------------------

def test_a_plain_note_and_one_gamma_wrote_are_not_lossy():
    assert not note_lossy('<div data-schema-version="9"><p>Plain para</p><p>Second</p></div>')
    assert not note_lossy("<h1>Title</h1><p>Body <strong>b</strong> <em>i</em> <u>u</u> <s>s</s></p>")
    assert not note_lossy('<p><span style="color: #ff2020">red</span> <span style="background-color: #ffd40080">hl'
                          '</span> <span style="text-decoration: line-through">gone</span></p>')
    assert not note_lossy("<p>See https://example.org now</p>")
    assert not note_lossy('<table><tr><th>a</th></tr><tr><td>1</td></tr></table><pre class="math">$$x$$</pre>')
    assert not note_lossy(represented(region_page()).html)
    assert not note_lossy("")


@pytest.mark.parametrize("html", [
    "<p>H<sub>2</sub>O</p>",
    "<p>x<sup>2</sup></p>",
    '<p><span class="citation" data-citation="%7B%7D">(Vaswani, 2017)</span></p>',
    '<p><span class="highlight" data-annotation="%7B%7D">"quoted"</span></p>',
    '<p><img data-attachment-key="ABCD2345" width="300"></p>',
    '<p style="padding-left: 40px">indented</p>',
    '<p style="text-align: center">centred</p>',
])
def test_what_gamma_cannot_write_back_is_lossy(html):
    assert note_lossy(html)


def test_note_marks_counts_in_one_spelling():
    # <b> and <strong> are one mark, as are <s> and a line-through style, and a colour is its value whatever
    # its case: a note written back in Gamma's spelling of them loses none
    assert not note_lossy('<p><b>a</b> and <strong>b</strong>, <s>c</s> and <span style="text-decoration: '
                          'line-through">d</span>, <span style="color: #FF2020 ">e</span></p>')


# --- the PDF ---------------------------------------------------------------------------------

def att(key, *, mode="imported_file", ctype="application/pdf", added="2023-01-01T00:00:00Z", url="", md5="m",
        deleted=None, whole=False):
    data = {"key": key, "itemType": "attachment", "linkMode": mode, "contentType": ctype, "dateAdded": added,
            "url": url, "md5": md5}
    if deleted is not None:
        data["deleted"] = deleted
    return {"key": key, "data": data} if whole else data


def test_the_primary_pdf_is_zoteros_best_attachment():
    oldest = att("ZZZZ2222", added="2020-01-01T00:00:00Z")
    newer = att("AAAA2222", added="2021-01-01T00:00:00Z")
    assert primary([newer, oldest]) is oldest
    same_day = att("BBBB2222", added="2020-01-01T00:00:00Z")
    assert primary([oldest, same_day]) is same_day  # then the smallest key
    by_url = att("CCCC2222", added="2022-01-01T00:00:00Z", url="https://x.org/p.pdf", mode="imported_url")
    assert primary([oldest, by_url], "https://x.org/p.pdf") is by_url
    assert primary([oldest, by_url], "") is oldest


def test_a_trashed_or_non_pdf_attachment_is_never_primary():
    trashed = att("AAAA2222", added="2019-01-01T00:00:00Z", deleted=1)
    whole_trashed = {**att("BBBB2222", added="2019-01-01T00:00:00Z", whole=True), "deleted": True}
    html = att("CCCC2222", ctype="text/html", added="2018-01-01T00:00:00Z")
    link = att("DDDD2222", mode="linked_url", added="2018-01-01T00:00:00Z")
    note = {"key": "EEEE2222", "itemType": "note"}
    linked = att("FFFF2222", mode="linked_file", added="2024-01-01T00:00:00Z", md5=None)
    keep = att("GGGG2222", added="2025-01-01T00:00:00Z", whole=True)
    assert primary([trashed, whole_trashed, html, link, note, keep]) is keep
    assert primary([trashed, linked, keep]) is linked  # a linked file is Zotero's choice too
    assert primary([]) is None and primary([html]) is None


def test_fetchable_says_why_a_file_cannot_come():
    assert fetchable(att("A"), True) == (True, "")
    assert fetchable(att("A", mode="imported_url", whole=True), True) == (True, "")
    assert fetchable(att("A", mode="linked_file"), True) == (False, zm.LINKED)
    assert fetchable(att("A", md5=None), True) == (False, zm.UNAVAILABLE)
    assert fetchable(att("A", mode="linked_url"), True) == (False, zm.UNAVAILABLE)
    assert fetchable(att("A"), False) == (False, zm.NO_ACCESS)


# --- first-link matching ---------------------------------------------------------------------

def zitem(key, title="A paper", *, year="2020", doi="", arxiv="", added="2020-01-01T00:00:00Z", md5=""):
    data = {"key": key, "itemType": "journalArticle", "title": title, "date": year, "DOI": doi, "dateAdded": added}
    if arxiv:
        data["extra"] = f"arXiv: {arxiv}"
    return {"key": key, "data": data, "md5": md5}


def page(pid, title="A paper", *, year="2020", doi="", arxiv="", created="2020-01-01T00:00:00.000000Z", md5="",
         zotero_key=""):
    meta = {"title": title, "year": year, "doi": doi, "arxiv_id": arxiv}
    return {"id": pid, "created_at": created, "title": title, "meta": meta, "md5": md5, "zotero_key": zotero_key}


def test_match_takes_the_strongest_evidence_first():
    items = [zitem("KEY22222", "Keyed"), zitem("DOI22222", "By DOI", doi="10.1234/abc"),
             zitem("ARX22222", "By arXiv", arxiv="1706.03762"), zitem("MD522222", "By file", md5="ABC"),
             zitem("TTL22222", "Only a title")]
    pages = [page("p-key", "Something else", zotero_key=item_uri(LIB, "KEY22222")),
             page("p-doi", "Other title", doi="https://doi.org/10.1234/ABC"),
             page("p-arx", "Other", arxiv="1706.03762v7"), page("p-md5", "Renamed", md5="abc"),
             page("p-ttl", "Only a Title")]
    got = {m.page_id: (m.key, m.reason) for m in match(pages, items, library=LIB)}
    assert got == {"p-key": ("KEY22222", "zotero_key"), "p-doi": ("DOI22222", "doi"),
                   "p-arx": ("ARX22222", "arxiv"), "p-md5": ("MD522222", "md5"), "p-ttl": ("TTL22222", "title")}


def test_match_is_one_to_one_oldest_first():
    items = [zitem("BBBB2222", doi="10.1234/x", added="2021-01-01T00:00:00Z"),
             zitem("AAAA2222", doi="10.1234/x", added="2020-01-01T00:00:00Z")]
    pages = [page("new", doi="10.1234/x", created="2022-01-01T00:00:00Z"),
             page("old", doi="10.1234/x", created="2021-01-01T00:00:00Z")]
    got = match(pages, items, library=LIB)
    assert got == [zm.Match("old", "AAAA2222", "doi"), zm.Match("new", "BBBB2222", "doi")]
    single = match(pages[:1] + pages[1:], items[:1], library=LIB)
    assert single == [zm.Match("old", "BBBB2222", "doi")]


def test_a_duplicated_pages_key_goes_to_the_oldest_page():
    items = [zitem("KEY22222", "Paper", doi="10.1234/k")]
    uri = item_uri(LIB, "KEY22222")
    pages = [page("copy", "Paper", doi="10.1234/k", zotero_key=uri, created="2024-01-01T00:00:00Z"),
             page("orig", "Paper", doi="10.1234/k", zotero_key=uri, created="2020-01-01T00:00:00Z")]
    assert match(pages, items, library=LIB) == [zm.Match("orig", "KEY22222", "zotero_key")]


def test_a_key_of_another_library_is_no_evidence():
    items = [zitem("KEY22222", "Paper")]
    pages = [page("p", "Unrelated", zotero_key=item_uri("groups/9", "KEY22222"))]
    assert match(pages, items, library=LIB) == []
    props_page = {"id": "q", "created_at": "", "title": "x", "props": {"meta": {"title": "x"},
                                                                       "zotero_key": item_uri(LIB, "KEY22222")}}
    assert match([props_page], items, library=LIB) == [zm.Match("q", "KEY22222", "zotero_key")]


def test_a_title_alone_matches_only_without_identifiers_or_differing_pdfs():
    items = [zitem("EDIT2222", "Editorial", year="2021")]
    assert match([page("p", "Editorial", year="2021", doi="10.1234/p")], items, library=LIB) == []
    with_doi = [zitem("EDIT2222", "Editorial", year="2021", doi="10.1234/z")]
    assert match([page("p", "Editorial", year="2021")], with_doi, library=LIB) == []
    assert match([page("p", "Editorial", year="2020")], items, library=LIB) == []
    assert match([page("p", "Editorial", year="")], [zitem("E", "Editorial", year="")], library=LIB) == []
    pdfs = [zitem("EDIT2222", "Editorial", year="2021", md5="aaa")]
    assert match([page("p", "Editorial", year="2021", md5="bbb")], pdfs, library=LIB) == []
    assert match([page("p", "Editorial", year="2021")], pdfs, library=LIB) == [zm.Match("p", "EDIT2222", "title")]
    multi = [{**zitem("EDIT2222", "Editorial", year="2021"), "md5s": ["x", "bbb"]}]
    assert match([page("p", "Editorial", year="2021", md5="bbb")], multi, library=LIB) == \
        [zm.Match("p", "EDIT2222", "md5")]


def test_folders_match_collections_by_path_ignoring_case():
    collections = paths({"C1": {"name": "Reading", "parent": ""}, "C2": {"name": "Physics", "parent": "C1"},
                         "C3": {"name": "physics", "parent": "C1"}, "LOOP": {"name": "x", "parent": "LOOP"}})
    assert collections["C2"] == ("Reading", "Physics") and collections["LOOP"] == ("x",)
    folders = paths({"f1": {"name": "reading", "parent": ""}, "f2": {"name": "PHYSICS", "parent": "f1"},
                     "f3": {"name": "Physics", "parent": "f1"}, "f4": {"name": "Physics", "parent": "f1"}})
    assert match_paths(folders, collections) == [("f1", "C1"), ("f2", "C2"), ("f3", "C3")]


# --- which pages travel -------------------------------------------------------------------------

NOW = "2026-10-28T12:00:00.000000Z"


def test_a_paper_is_a_page_with_a_titled_record():
    assert is_paper({"meta": {"title": "T"}})
    assert not is_paper({"meta": {"title": "  "}}) and not is_paper({"meta": "T"}) and not is_paper({})


def test_a_pdf_page_waits_for_its_lookup_then_goes_only_with_a_record_or_as_a_document():
    young = "2026-10-28T11:59:30.000000Z"
    old = "2026-10-28T11:50:00.000000Z"
    pdf = {"doc_id": "abc"}
    assert wants_item({"meta": {"title": "T"}, **pdf}, now=NOW, send_unrecorded=False) == zm.WANT_PAPER
    assert wants_item(pdf, now=NOW, send_unrecorded=True, created_at=young) == zm.WANT_WAIT
    assert wants_item(pdf, now=NOW, send_unrecorded=True, created_at=old) == zm.WANT_DOCUMENT
    assert wants_item(pdf, now=NOW, send_unrecorded=False, created_at=old) == ""
    assert wants_item({**pdf, "meta_error": "no match"}, now=NOW, send_unrecorded=True, created_at=young) == \
        zm.WANT_DOCUMENT
    assert wants_item({}, now=NOW, send_unrecorded=True, created_at=old) == ""  # a note page stays in Gamma
    from datetime import datetime, timezone
    assert wants_item(pdf, now=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc), send_unrecorded=True,
                      created_at=young) == zm.WANT_WAIT


def test_a_standalone_pdf_gets_a_parent_only_from_a_person_made_record():
    assert standalone_gets_parent({"title": "T", "source": "manual"})
    assert standalone_gets_parent({"title": "T", "source": "arxiv", "user_verified": True})
    assert not standalone_gets_parent({"title": "T", "source": "arxiv"})
    assert not standalone_gets_parent({"title": "", "source": "manual"})
    assert not standalone_gets_parent(None)


# --- readable lines --------------------------------------------------------------------------------

def test_field_lines_read_as_a_person_says_them():
    assert field_line("year", "2017") == "Year: 2017"
    assert field_line("authors", ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]) == \
        "Authors: A. Vaswani, N. Shazeer, …"
    assert field_line("authors", ["Plato"]) == "Authors: Plato"
    assert field_line("kind", "journalArticle") == "Type: Journal article"
    assert field_line("venue", "") == "Venue: (none)"
    assert field_line("tags", ["a", "b"]) == "Tags: a, b"
    assert field_line("doi", None) == "DOI: (none)"
    long = field_line("title", "word " * 100)
    assert len(long) == zm.LINE_MAX and long.endswith("…")
    assert field_line("some_field", 3) == "Some field: 3"


def test_annotation_lines_name_colour_page_quote_and_comment():
    p = {"type": "highlight", "color": "#ffd400", "text": "Attention is all you need", "comment": "<b>key</b> idea"}
    assert annotation_line(p, "3") == "yellow · p. 3 · “Attention is all you need” · key idea"
    assert annotation_line({"type": "image", "color": "#5fb236"}, "iv") == "green · p. iv · area"
    assert annotation_line({}, "") == "(empty)"
    washes = ("#ffe28f", "#aaebaa", "#9bcdff", "#ff6666", "#aaaaaa", "#ffd400a6")
    assert [colour_name(c) for c in washes] == ["yellow", "green", "blue", "red", "gray", "yellow"]
    assert colour_name("nope") == ""
    assert len(annotation_line({**p, "text": "x" * 500, "comment": "y" * 500}, "1")) <= zm.LINE_MAX


# --- edges ------------------------------------------------------------------------------------------

def test_odd_values_take_no_part():
    p = item_projection({"itemType": "book", "tags": [None, {"tag": 3}, {"tag": " x "}], "collections": [None, 5, "K"]})
    assert p["tags"] == ["3", "x"] and p["collections"] == ["K"]
    m = merge({"tags": "nope"}, {"tags": None}, {"tags": ["a", 1]}, {"tags": []})
    assert m.apply == {"tags": ["a"]}
    with pytest.raises(ValueError):
        represented(None)


def test_a_pulled_list_with_odd_depths_reads_as_a_tree():
    r = represented(blk("R", "root", blk("A", "a")))
    edits = note_edits(r, [(3, "root"), (5, "a"), (9, "b"), (0, "c")])
    assert edits == [{"op": "insert", "id": "new:2", "parent": "A", "after": None, "content": "b"},
                     {"op": "insert", "id": "new:3", "parent": "R", "after": "A", "content": "c"}]


def test_a_comment_line_reads_its_text():
    line = annotation_line({"type": "note", "color": "#2ea8e5", "comment": "a &amp; b<br/>c"}, "2")
    assert line == "blue · p. 2 · note · a & bc"
    assert merge({"c": "x"}, {"c": "x"}, {"c": "x y"}, {"c": "w x"}, texts={"c": None}).merged_text == {"c": "w x y"}


def test_a_record_without_a_title_is_no_paper_and_waits_no_more():
    props = {"doc_id": "abc", "meta": {"doi": "10.1234/x"}}
    assert not is_paper(props)
    assert wants_item(props, now=NOW, send_unrecorded=True, created_at=NOW) == zm.WANT_DOCUMENT
    assert wants_item({"doc_id": "abc"}, now=NOW, send_unrecorded=False, created_at="garbled") == ""
