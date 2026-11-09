"""gamma/vault_merge.py: a note changed on disk merged into its page, the
target built from the page and the file's changes since the base
(docs/dev/synced_vaults.md "Push"), with the counter-examples that once lost
or doubled a block."""

import random
import time

import pytest
from fractional_indexing import generate_key_between, generate_n_keys_between, validate_order_key

from gamma import md_lens, sync_tree
from gamma.blocks_store import block_kind
from gamma.vault_merge import file_wins, merge_page, premerge

P = "page"
INK = {"ink_url": "/api/uploads/strokes.ink"}
MARK = {"pdf_position": {"page": 3, "rects": [[0, 0, 1, 1]]}, "quote": "the quoted words"}


def kind_of(block):
    return block_kind(block["parent"], P, block["props"])


def snap(*blocks):
    """The page root and ``(id, parent, key, content, props?)`` blocks."""
    out = {P: {"parent": "root", "position": "a0", "content": "Title", "props": {}}}
    for block_id, parent, key, content, *props in blocks:
        out[block_id] = {"parent": parent, "position": key, "content": content, "props": dict(props[0] if props else {})}
    return out


def node(block_id, content, *children, **extra):
    return {"id": block_id, "content": content, "children": list(children), **extra}


def pairs_of(base, file, gone=()):
    return md_lens.match(md_lens.flatten(base), md_lens.flatten(file), gone)


def merged(page, base, file, pairs=None, **kw):
    """``merge_page`` with ``pairs`` matched by the lens when not given, its
    ops checked against its target."""
    out = merge_page(page, P, base, file, pairs_of(base, file) if pairs is None else pairs, kind_of=kind_of, **kw)
    landed(page, out)
    return out


def landed(page, out):
    """The ops are what a server applies to reach the target: every key a
    valid order key, no two siblings on one key, no loop, nothing left over,
    and no ``set`` with a ``base``."""
    assert sync_tree.apply(page, out.ops) == out.target
    for op in out.ops:
        assert "base" not in op
        if op["op"] in ("insert", "move"):
            validate_order_key(op["position"])
    for parent, ids in sync_tree.children_of(out.target).items():
        keys = [out.target[b]["position"] for b in ids]
        assert len(set(keys)) == len(keys), parent
    for block_id in out.target:
        assert block_id == P or P in sync_tree.ancestors(out.target, block_id)


def ops_of(out):
    return [(op["op"], op["id"]) for op in out.ops]


def under(target, parent):
    return [target[b]["content"] for b in sync_tree.children_of(target).get(parent, [])]


def lens_tree(text, ids=None):
    return md_lens.tree_of(md_lens.parse(text), ids)


# --- counter-examples -----------------------------------------------------------

def test_a_child_typed_in_gamma_survives_a_change_on_disk():
    """The target is the page with the file's changes, never the
    file with the page's spanless blocks, which would delete X with no record."""
    base = lens_tree("# H\n\npara one\n", ["h", "p"])
    page = snap(("h", P, "a0", "# H"), ("p", "h", "a0", "para one"), ("x", "h", "a1", "my thought"))
    out = merged(page, base, lens_tree("# H\n\npara two\n"))
    assert ops_of(out) == [("set", "p")]
    assert under(out.target, "h") == ["para two", "my thought"]
    assert out.ids == ["h", "p"] and not out.conflicts and not out.level   # pull writes X to the file


def test_a_drag_in_gamma_is_not_undone():
    base = lens_tree("a\n\nb\n\nc\n", ["a", "b", "c"])
    page = snap(("a", P, "a0", "a"), ("b", P, "a1", "b"), ("c", P, "Zz", "c"))   # c dragged to the top
    out = merged(page, base, lens_tree("a\n\nb!\n\nc\n"))
    assert ops_of(out) == [("set", "b")]
    assert under(out.target, P) == ["c", "a", "b!"]


def test_a_block_deleted_in_gamma_and_only_kept_on_disk_stays_deleted():
    base = lens_tree("a\n\nb\n\nc\n", ["a", "b", "c"])
    page = snap(("a", P, "a0", "a"), ("c", P, "a2", "c"))
    out = merged(page, base, lens_tree("c\n\na\n\nb\n"), pairs={0: 2, 1: 0, 2: 1})   # b only moved on disk
    assert "b" not in out.target and out.ids == ["c", "a", "b"] and not out.level
    assert under(out.target, P) == ["c", "a"]


def test_a_block_deleted_in_gamma_comes_back_when_the_file_changed_its_text():
    base = lens_tree("a\n\nb\n\nc\n", ["a", "b", "c"])
    page = snap(("a", P, "a0", "a"), ("c", P, "a2", "c"))
    out = merged(page, base, lens_tree("a\n\nb, edited\n\nc\n"), pairs={0: 0, 1: 1, 2: 2})
    assert ops_of(out) == [("insert", "b")]
    assert under(out.target, P) == ["a", "b, edited", "c"] and out.level


def test_a_block_held_on_another_page_comes_back_under_a_fresh_id():
    """Inserting the id another page holds is refused (403)
    every round."""
    base = lens_tree("a\n\nb\n", ["a", "b"])
    page = snap(("a", P, "a0", "a"))
    out = merged(page, base, lens_tree("a\n\nb, edited\n"), pairs={0: 0, 1: 1}, held={"b"})
    (kind, block_id), = ops_of(out)
    assert kind == "insert" and block_id != "b" and out.ids == ["a", block_id]


def test_a_child_added_in_gamma_under_a_heading_deleted_on_disk_is_lifted_where_it_stood():
    base = lens_tree("intro\n\n# H\n\nunder h\n\n# K\n\nunder k\n", ["i", "h", "u", "k", "v"])
    page = snap(("i", P, "a0", "intro"), ("h", P, "a1", "# H"), ("u", "h", "a0", "under h"),
                ("g", "h", "a1", "typed in gamma"), ("ink", "u", "a0", "", INK),
                ("k", P, "a2", "# K"), ("v", "k", "a0", "under k"))
    out = merged(page, base, lens_tree("intro\n\n# K\n\nunder k\n"))
    assert out.deleted == ["h", "u"]
    assert under(out.target, P) == ["intro", "", "typed in gamma", "# K"]   # the ink, lifted out of u
    assert [op["op"] for op in out.ops] == ["move", "move", "delete"]


def test_the_file_wins_a_clash_and_gammas_text_is_a_conflict():
    """By characters this merge reads clean and writes
    ``A slow red fox leaps across a catlazy  today``; by words it clashes, the
    file's text stands, and Gamma's is a ``file_won`` row in Gamma space."""
    base_text, file_text = "The quick brown fox jumps over the dog", "A slow red fox leaps across a cat"
    stored = "The quick brown fox jumps over the lazy dog today"
    page = snap(("s", P, "a0", stored))
    out = merged(page, [node("s", base_text)], [node(None, file_text)], pairs={0: 0})
    assert out.target["s"]["content"] == file_text
    assert out.conflicts == [{"block_id": "s", "mine": stored, "theirs": file_text, "result": file_text, "base": base_text}]


def test_edits_of_different_words_merge_clean():
    page = snap(("s", P, "a0", "one two three four five six"))
    out = merged(page, [node("s", "one two three four")], [node(None, "one TWO three four")], pairs={0: 0})
    assert out.target["s"]["content"] == "one TWO three four five six" and not out.conflicts and not out.level


def test_a_dropped_highlight_comes_back_with_its_rectangles():
    """The file dropped the callout (``gone``) and has it again;
    the block comes back with its id and properties."""
    page = snap(("a", P, "a0", "a"))
    gone = [{"id": "hl", "hash": md_lens.content_hash("my note"), "anchor": "hl", "props": MARK, "at": 0}]
    file = [node(None, "a"), node(None, "my note", anchor="hl")]
    out = merged(page, [node("a", "a")], file, pairs=pairs_of([node("a", "a")], file, gone), gone=gone)
    assert ops_of(out) == [("insert", "hl")]
    assert out.target["hl"]["props"] == MARK and out.ids == ["a", "hl"]


def test_a_highlight_the_file_dropped_is_reported_for_gone():
    page = snap(("a", P, "a0", "a"), ("hl", P, "a1", "my note", MARK), ("k", "hl", "a0", "under it"))
    out = merged(page, [node("a", "a"), node("hl", "my note", node("k", "under it"))], [node(None, "a")])
    assert out.deleted == ["hl", "k"] and ops_of(out) == [("delete", "hl")]


def test_a_block_gamma_changed_that_the_file_dropped_is_a_clash_the_file_wins():
    """The file wins, and no text Gamma typed goes without a record: the delete
    lands, Gamma's text a ``file_won`` row."""
    page = snap(("a", P, "a0", "a"), ("b", P, "a1", "b, changed in Gamma"), ("c", P, "a2", "c"))
    base = [node("a", "a"), node("b", "b"), node("c", "c")]
    out = merged(page, base, [node(None, "a"), node(None, "c")], pairs={0: 0, 1: 2})
    assert ops_of(out) == [("delete", "b")] and out.deleted == ["b"] and out.level
    assert out.conflicts == [{"block_id": "b", "mine": "b, changed in Gamma", "theirs": "", "result": "", "base": "b"}]


@pytest.mark.parametrize("was, now", [("- alpha beta", "alpha beta"), ("# Plans for the week", "Plans for the week")])
def test_a_block_whose_class_changed_on_disk_while_gamma_edited_it_is_one_block(was, now):
    """The simulator's most frequent loss: the lens pairs
    one class only, so an item made a paragraph read as dropped, and Gamma's
    edit kept it beside the file's: the text twice, no record. The new block
    reads like the dropped one: it is that block, the two changes merged."""
    page = snap(("a", P, "a0", f"{was} epsilon"), ("b", P, "a1", "- gamma delta"))
    base = [node("a", was), node("b", "- gamma delta")]
    file = [node(None, now), node(None, "- gamma delta")]
    out = merged(page, base, file, pairs={1: 1})
    assert out.ids == ["a", "b"] and ops_of(out) == [("set", "a")] and not out.deleted
    assert out.target["a"]["content"] == f"{now} epsilon" and not out.conflicts


def test_a_block_gamma_moved_to_another_page_whose_class_changed_on_disk_goes_with_it():
    """Gamma moved an item to another page while the file moved its line and
    dropped its marker: the lens could not pair the two, and the line went in
    as a new block, its text on both pages. The new block reads like the moved
    one: it is that block, its change for the page that holds it."""
    page = snap(("b", P, "a1", "gamma delta"), ("c", P, "a2", "epsilon"))
    base = [node("a", "- alpha beta"), node("b", "gamma delta"), node("c", "epsilon")]
    file = [node(None, "gamma delta"), node(None, "epsilon"), node(None, "alpha beta")]
    out = merged(page, base, file, pairs={0: 1, 1: 2}, away={"a"})
    assert out.ids == ["b", "c", "a"] and ops_of(out) == [] and not out.deleted
    assert out.away == [{"block_id": "a", "base": "- alpha beta", "file": "alpha beta"}]


@pytest.mark.parametrize("moved", [True, False])
def test_a_marker_typed_back_is_the_block_gamma_moved_or_edited_not_the_one_it_replaced(moved):
    """The person dropped an item's marker (the item ``x`` in ``gone``, the
    line a new block ``a``), Gamma moved ``a`` to another page or edited it,
    and the marker came back: the lens gave the line ``x``'s id by its text,
    and ``x`` came back beside ``a``, the text on two pages, or Gamma's edit a
    conflict. The line is ``a``, its class changed once more."""
    page = snap(("f", P, "a0", "first")) if moved else snap(("f", P, "a0", "first"), ("a", P, "a1", "alpha beta gamma delta"))
    base = [node("f", "first"), node("a", "alpha beta gamma")]
    gone = [{"id": "x", "hash": md_lens.content_hash("- alpha beta gamma"), "anchor": "", "props": {}, "at": 0}]
    file = [node(None, "first"), node(None, "- alpha beta gamma")]
    pairs = pairs_of(base, file, gone)
    assert pairs == {0: 0, 1: 2}
    out = merged(page, base, file, pairs=pairs, gone=gone, away={"a"} if moved else frozenset())
    assert out.ids == ["f", "a"] and "x" not in out.target and not out.deleted and not out.conflicts
    if moved:
        assert ops_of(out) == [] and out.away == [{"block_id": "a", "base": "alpha beta gamma", "file": "- alpha beta gamma"}]
    else:
        assert ops_of(out) == [("set", "a")] and out.target["a"]["content"] == "- alpha beta gamma delta"


@pytest.mark.parametrize("base, file_text, stored", [
    ("meet at 10:16 today", "meet at 10:56 today", "meet at 10:31 today"),
    ("a 2016 b", "a 2056 b", "a 2031 b"),
    ("ask alice16 now", "ask alice56 now", "ask alice31 now"),
    ("ship v1.0.16", "ship v1.0.56", "ship v1.0.31"),
    ("a b c d e", "x y z", "a b c NEW d e"),
    ("a b c d e", "x y z", "a NEW b c d e"),
    ("The quick brown fox jumps over the dog", "A slow red fox leaps across a cat",
     "The quick brown fox jumps over the lazy dog"),
    ("", "check later", "great point"),
    ("see:", "see:two", "see:one"),
])
def test_two_changes_of_one_word_or_a_word_inside_a_rewrite_clash(base, file_text, stored):
    """By characters each of these read clean as a text
    neither side typed (``10:35``, ``x y zNEW``, ``a catlazy``). Round 2: two
    insertions at one place joined into one word (a note typed on both sides
    into a highlight that had none read as ``great pointcheck later``)."""
    assert file_wins(base, file_text, stored) == (file_text, False)


@pytest.mark.parametrize("base, file_text, stored, both", [
    ("one two three four", "one TWO three four", "one two three four five six", "one TWO three four five six"),
    ("The budget meeting moved to Thursday.", "The budget meeting moved to Friday.",
     "The budget meeting moved to Thursday at noon.", "The budget meeting moved to Friday at noon."),
    ("the cat sat", "the dog sat", "the black cat sat", "the black dog sat"),
    ("line one\nline two", "line 1\nline two", "line one\nLINE two", "line 1\nLINE two"),
    ("a", "a b", "a c", "a c b"),
    ("", "check later", "great point.\n", "great point.\ncheck later"),
])
def test_changes_of_different_words_merge_clean(base, file_text, stored, both):
    assert file_wins(base, file_text, stored) == (both, True)


@pytest.mark.parametrize("base, file_text, stored", [
    ("alpha beta", "alpha beta ptyped", "alpha beta gtyped"),
    ("alpha beta", "alpha BETA", "alpha beta gtyped"),
    ("one two three", "one TWO three", "one two three four"),
    ("one two three", "zero one two three", "one two three four"),
    ("a b c d", "a c d", "a c d e"),
])
def test_a_merge_read_again_from_the_same_base_is_left_as_it_is(base, file_text, stored):
    """A block Gamma moved to another page and edited took the file's change
    there (``Round._follow``), and the round died after it or planned its own
    page again: the same change merged into the text it gave went in a second
    time (``alpha beta gtyped ptyped ptyped``), or a word both sides deleted
    read as a clash that took Gamma's other words. A change the text holds is
    made once."""
    once, clean = file_wins(base, file_text, stored)
    assert clean and file_wins(base, file_text, once) == (once, True)


@pytest.mark.parametrize("base, landed, file_text, both", [
    ("alpha beta", "alpha beta gamma", "alpha beta gamma delta", "alpha beta gamma delta"),
    ("one two three", "one TWO three", "one TWO three four", "one TWO three four"),
    ("alpha beta", "alpha beta gamma epsilon", "alpha beta gamma", "alpha beta gamma epsilon"),
])
def test_a_plan_the_journal_lost_and_the_file_typed_on_adds_only_what_is_new(base, landed, file_text, both):
    """A plan landed and its base was never saved (a crash, an answer lost
    with the log pruned past it): the next push merges from the old base into
    a page that holds the plan's words, and the person typed on, or Gamma did.
    The words go in once."""
    assert file_wins(base, file_text, landed) == (both, True)


def test_a_moved_blocks_line_the_lens_paired_by_its_place_with_a_deleted_block_goes_with_it():
    """Gamma moved an item to another page; the file deleted the paragraph and
    the item above it and dropped the moved item's marker. The lens paired
    that line with the deleted paragraph (one class, in order), which took its
    text on the note's page while the other page kept the item: the text on
    both. The line reads like the moved block, not like its pair: it is that
    block, its change for the page that holds it, and the paragraph goes."""
    base = lens_tree("Opening remarks\n\n- second point\n- the mover sentence here\n\ngamma line\n- final item\n",
                     ["o", "s", "m", "g", "f"])
    page = snap(("o", P, "a0", "Opening remarks"), ("s", P, "a1", "- second point"), ("g", P, "a3", "gamma line"),
                ("f", P, "a4", "- final item"))
    file = lens_tree("\n\n\nthe mover sentence here\n\ngamma line\n- final item\n")
    assert pairs_of(base, file)[0] == 0          # the lens's pair: by place
    out = merged(page, base, file, away={"m"})
    assert out.ids == ["m", "g", "f"] and sorted(out.deleted) == ["o", "s"]
    assert out.away == [{"block_id": "m", "base": "- the mover sentence here", "file": "the mover sentence here"}]
    assert under(out.target, P) == ["gamma line", "- final item"]


def test_a_block_with_no_span_now_is_never_deleted_by_the_file():
    page = snap(("a", P, "a0", "a"), ("b", P, "a1", ""))   # emptied in Gamma
    out = merged(page, [node("a", "a"), node("b", "b")], [node(None, "a")], pairs={0: 0})
    assert "b" in out.target and out.deleted == []


def test_where_gamma_and_the_file_nest_two_blocks_inside_each_other_the_file_wins():
    page = snap(("a", P, "a0", "a"), ("b", "a", "a0", "b"))          # Gamma: b under a
    base = [node("a", "a"), node("b", "b")]
    file = [node(None, "b", node(None, "a"))]                         # the file: a under b
    out = merged(page, base, file, pairs={0: 1, 1: 0})
    assert out.target["a"]["parent"] == "b" and out.target["b"]["parent"] == P


def test_a_reorder_on_disk_moves_only_the_blocks_out_of_order():
    page = snap(*[(c, P, k, c) for c, k in zip("abcde", generate_n_keys_between(None, None, 5))])
    base = [node(c, c) for c in "abcde"]
    out = merged(page, base, [node(None, c) for c in "eabcd"], pairs={0: 4, 1: 0, 2: 1, 3: 2, 4: 3})   # by anchor
    assert ops_of(out) == [("move", "e")] and under(out.target, P) == list("eabcd")


def test_a_block_indented_on_disk_moves_under_its_new_parent():
    base = lens_tree("- a\n- b\n- c\n", ["a", "b", "c"])
    page = snap(("a", P, "a0", "- a"), ("b", P, "a1", "- b"), ("c", P, "a2", "- c"))
    out = merged(page, base, lens_tree("- a\n  - b\n- c\n"))
    assert ops_of(out) == [("move", "b")] and out.target["b"]["parent"] == "a" and out.level


def test_new_blocks_go_after_their_nearest_predecessor_in_the_file():
    page = snap(("a", P, "a0", "a"), ("ink", P, "a1", "", INK), ("b", P, "a2", "b"))
    out = merged(page, [node("a", "a"), node("b", "b")], [node(None, c) for c in ("first", "a", "x", "b", "last")])
    assert under(out.target, P) == ["first", "a", "x", "", "b", "last"] and out.level


def test_a_new_block_between_two_held_by_an_empty_block_stands_between_them():
    """Projected first: the file reads c1 and c2 as siblings, so
    what goes between them goes beside them, inside the empty block they stand
    under in Gamma."""
    page = snap(("e", P, "a0", ""), ("c1", "e", "a0", "c1"), ("c2", "e", "a1", "c2"), ("d", P, "a1", "d"))
    base = [node("c1", "c1"), node("c2", "c2"), node("d", "d")]
    out = merged(page, base, [node(None, c) for c in ("c1", "x", "c2", "d")])
    assert under(out.target, "e") == ["c1", "x", "c2"] and out.level


def test_a_new_block_after_one_gamma_put_under_a_parent_of_its_own_goes_after_that_parent():
    page = snap(("g", P, "a0", "typed in Gamma"), ("a", "g", "a0", "a"), ("b", P, "a1", "b"))
    base = [node("a", "a"), node("b", "b")]
    out = merged(page, base, [node(None, c) for c in ("a", "x", "b")])
    assert under(out.target, P) == ["typed in Gamma", "x", "b"]


def test_two_file_blocks_never_take_one_id():
    page = snap(("a", P, "a0", "a"))
    out = merged(page, [node("a", "a")], [node(None, "a"), node(None, "a")], pairs={0: 0, 1: 0})
    assert out.ids[0] == "a" and out.ids[1] != "a" and len(out.target) == 3


def test_premerge_answers_what_the_merge_would():
    page = snap(("s", P, "a0", "one two three four five six"))
    base, file = [node("s", "one two three four")], [node(None, "one TWO three four")]
    calls = []
    early = premerge(page, base, file, {0: 0})
    out = merge_page(page, P, base, file, {0: 0}, kind_of=kind_of,
                     merge=lambda *texts: calls.append(texts) or early(*texts))
    assert out.target["s"]["content"] == file_wins("one two three four", "one TWO three four", page["s"]["content"])[0]
    assert calls == [("one two three four", "one TWO three four", "one two three four five six")]


# --- no move storm ----------------------------------------------------------------

def long_page(n):
    keys = generate_n_keys_between(None, None, n)
    page = snap(*[(f"b{i}", P, keys[i], f"paragraph {i}") for i in range(n)])
    return page, [node(f"b{i}", f"paragraph {i}") for i in range(n)]


def test_an_insert_at_the_top_of_a_long_page_is_one_insert():
    """Keys minted per sibling list would move every block."""
    page, base = long_page(5000)
    started = time.perf_counter()
    file = [node(None, "a new first line"), *[node(None, b["content"]) for b in base]]
    out = merged(page, base, file, pairs={i + 1: i for i in range(5000)})
    assert [op["op"] for op in out.ops] == ["insert"] and out.level
    moved = [node(None, b["content"]) for b in base]
    moved.insert(0, moved.pop(2500))
    out = merged(page, base, moved, pairs={0: 2500, **{k + 1 if k < 2500 else k: k for k in range(5000) if k != 2500}})
    assert ops_of(out) == [("move", "b2500")]
    assert time.perf_counter() - started < 10


def test_keys_a_sibling_holds_are_passed_by():
    """``_parked``'s case: a key minted between two neighbours
    that a sibling moving away still holds would be re-keyed on arrival, and the
    page would drift from what the merge meant."""
    page = snap(("w", P, "a1", "w"), ("z", P, "a2", "z"))
    out = merged(page, [node("w", "w"), node("z", "z")], [node(None, "new"), node(None, "z", node(None, "w"))],
                 pairs={1: 1, 2: 0})
    (insert,) = [op for op in out.ops if op["op"] == "insert"]
    assert insert["position"] not in ("a1", "a2") and insert["position"] < "a2"
    assert under(out.target, P) == ["new", "z"] and under(out.target, "z") == ["w"]


# --- property tests ----------------------------------------------------------------

WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda omicron".split()


def words(rng, low=1, high=6):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(low, high)))


def edited(rng, text):
    """``text`` with a word added at the end, a word changed, or all of it
    rewritten."""
    roll, parts = rng.random(), text.split(" ")
    if roll < 0.4 or not text:
        return (text + " " + words(rng, 1, 2)).strip()
    if roll < 0.7:
        parts[rng.randrange(len(parts))] = rng.choice(WORDS).upper()
        return " ".join(parts)
    return words(rng, 2, 6)


def random_page(rng, n):
    page, made = snap(), []
    for i in range(n):
        parent = rng.choice([P, P, *made[-6:]])
        roll = rng.random()
        content, props = words(rng), {}
        if roll < 0.08:
            content, props = "", INK
        elif roll < 0.12:
            content = ""
        elif roll < 0.22:
            props = MARK
        last = max((b["position"] for b in page.values() if b["parent"] == parent), default=None)
        page[f"b{i}"] = {"parent": parent, "position": generate_key_between(last, None), "content": content, "props": props}
        made.append(f"b{i}")
    return page


def key_among(rng, page, parent):
    keys = sorted(b["position"] for b in page.values() if b["parent"] == parent)
    k = rng.randint(0, len(keys))
    return generate_key_between(keys[k - 1] if k else None, keys[k] if k < len(keys) else None)


def gamma_edits(rng, page, kinds, count):
    """``page`` changed in Gamma ``count`` times, by edits of ``kinds``."""
    page = {k: {**v, "props": dict(v["props"])} for k, v in page.items()}
    for i in range(count):
        blocks = [b for b in page if b != P]
        kind = rng.choice(kinds)
        typed = [b for b in blocks if page[b]["content"]] if kind == "typed" else blocks
        if kind in ("text", "typed") and typed:      # typed: never into an empty block, which would gain a span
            b = rng.choice(typed)
            page[b]["content"] = edited(rng, page[b]["content"])
        elif kind in ("spanless", "note"):
            parent = rng.choice([P, *blocks])
            content, props = ("", INK if rng.random() < 0.7 else {}) if kind == "spanless" else (words(rng), {})
            page[f"g{i}"] = {"parent": parent, "position": key_among(rng, page, parent), "content": content, "props": props}
        elif kind == "delete" and blocks:
            for gone in sync_tree.subtree_ids(page, rng.choice(blocks)):
                del page[gone]
        elif kind == "move" and blocks:
            b = rng.choice(blocks)
            parent = rng.choice([P, *(x for x in blocks if x not in sync_tree.subtree_ids(page, b))])
            page[b] = {**page[b], "parent": parent, "position": key_among(rng, page, parent)}
    return page


def copy(tree):
    return [{"src": n["id"], "content": n["content"], "children": copy(n["children"])} for n in tree]


def places(tree):
    """Every ``(siblings list, index, node)`` of a file tree."""
    out, pending = [], [tree]
    while pending:
        siblings = pending.pop()
        for k, n in enumerate(siblings):
            out.append((siblings, k, n))
            pending.append(n["children"])
    return out


def file_edits(rng, base, count):
    """The base tree changed on disk ``count`` times: text, inserts, deletes
    (a subtree, or the block alone, its children taking its place), moves,
    splits and merges. Each node's ``src`` is the base block it came from."""
    tree = copy(base)
    for _ in range(count):
        spots = places(tree)
        kind = rng.choice(["text", "insert", "delete", "move", "split", "merge"])
        if not spots:
            kind = "insert"
        if kind == "insert":
            siblings = rng.choice([tree, *(n["children"] for _s, _k, n in spots)])
            siblings.insert(rng.randint(0, len(siblings)), {"src": None, "content": words(rng), "children": []})
            continue
        siblings, k, n = rng.choice(spots)
        if kind == "text":
            n["content"] = edited(rng, n["content"])
        elif kind == "delete":
            siblings[k:k + 1] = [] if rng.random() < 0.5 else n["children"]
        elif kind == "move":
            del siblings[k]
            spots = [(s, j, m) for s, j, m in places(tree)]
            into = rng.choice([tree, *(m["children"] for _s, _j, m in spots)])
            into.insert(rng.randint(0, len(into)), n)
        elif kind == "split":
            parts = n["content"].split(" ")
            if len(parts) > 1:
                cut = rng.randint(1, len(parts) - 1)
                n["content"] = " ".join(parts[:cut])
                siblings.insert(k + 1, {"src": None, "content": " ".join(parts[cut:]), "children": []})
        elif kind == "merge" and k + 1 < len(siblings):
            other = siblings.pop(k + 1)
            n["content"] = f"{n['content']} {other['content']}"
            n["children"] += other["children"]
    return tree


def pairs_from(base, file):
    at = {n["id"]: i for i, n in enumerate(md_lens.flatten(base))}
    return {n: at[f["src"]] for n, f in enumerate(md_lens.flatten(file)) if f["src"] is not None}


def check(page, base, file, out):
    """What a merge promises, whatever the two sides did."""
    olds, news = md_lens.flatten(base), md_lens.flatten(file)
    was = {n["id"]: n["content"] for n in olds}
    on_disk = {f["src"]: f["content"] for f in news if f["src"] is not None}
    spanned = {n["id"] for n in md_lens.flatten(md_lens.project(page, kind_of))}
    target, clashes = out.target, {c["block_id"]: c for c in out.conflicts}
    assert len(clashes) == len(out.conflicts)
    for block_id in set(page) - set(was):            # Gamma's own blocks, the spanless ones among them
        assert block_id in target
    for block_id in set(was) & set(page):
        stored, changed = page[block_id]["content"], on_disk.get(block_id, was[block_id]) != was[block_id]
        if stored != was[block_id] and block_id not in on_disk and block_id in spanned:
            if block_id not in out.ids:               # dropped by the file: it wins, Gamma's text recorded
                assert block_id not in target and clashes[block_id] == {
                    "block_id": block_id, "mine": stored, "theirs": "", "result": "", "base": was[block_id]}
        elif stored != was[block_id]:                 # Gamma's text is kept, merged, or recorded
            assert block_id in target
            if not changed:
                assert target[block_id]["content"] == stored and block_id not in clashes
            else:
                text, clean = file_wins(was[block_id], on_disk[block_id], stored)
                if clean:
                    assert target[block_id]["content"] == text and block_id not in clashes
                else:
                    assert target[block_id]["content"] == on_disk[block_id]
                    assert clashes[block_id] == {"block_id": block_id, "mine": stored, "theirs": on_disk[block_id],
                                                 "result": on_disk[block_id], "base": was[block_id]}
        elif block_id not in on_disk and block_id in spanned:
            assert block_id not in target            # the file's delete lands
        else:
            assert block_id not in clashes
    for n, f in enumerate(news):                     # every change on disk lands
        block_id = out.ids[n]
        if f["src"] is None and block_id in was:      # a new block that is a dropped one Gamma changed
            text, clean = file_wins(was[block_id], f["content"], page[block_id]["content"])
            assert target[block_id]["content"] == (text if clean else f["content"])
        elif f["src"] is None or f["src"] not in page:
            if f["src"] is not None and f["content"] == was[f["src"]]:
                assert block_id not in target         # deleted in Gamma, only kept on disk
            else:
                assert target[block_id]["content"] == f["content"]
        elif page[f["src"]]["content"] == was[f["src"]]:
            assert target[block_id]["content"] == f["content"]
    up = file_parents(file, out.ids)
    base_up = {c["id"]: p["id"] if p else P for p in [None, *olds] for c in (p["children"] if p else base)}
    for n, f in enumerate(news):                     # a block the file moved or made stands where the file has it
        block_id = out.ids[n]
        if block_id in target and up[n] in target and (f["src"] is None or base_up.get(f["src"]) != up[n]):
            assert projected_parent(target, block_id) == up[n]


def file_parents(file, ids):
    """Each file block's parent's id (``P`` at the top), in document order."""
    news = md_lens.flatten(file)
    at = {id(f): n for n, f in enumerate(news)}
    up = [P] * len(news)
    for f in news:
        for child in f["children"]:
            up[at[id(child)]] = ids[at[id(f)]]
    return up


def projected_parent(target, block_id):
    """The block's parent as the file reads it: past the blocks with no span."""
    spanned = {n["id"] for n in md_lens.flatten(md_lens.project(target, kind_of))}
    above = target[block_id]["parent"]
    while above != P and above not in spanned:
        above = target[above]["parent"]
    return above


def file_shape(out, file):
    """The file's tree with the merge's ids, and the target's, as nested
    ``(id, children)``."""
    ids = iter(out.ids)

    def of_file(nodes):
        return [(next(ids), of_file(n["children"])) for n in nodes]

    def of_target(nodes):
        return [(n["id"], of_target(n["children"])) for n in nodes]

    return of_file(file), of_target(md_lens.project(out.target, kind_of))


def trial(seed, kinds, gamma_count, file_count):
    rng = random.Random(seed)
    page0 = random_page(rng, rng.randint(1, 30))
    base = md_lens.project(page0, kind_of)
    page = gamma_edits(rng, page0, kinds, gamma_count)
    file = file_edits(rng, base, file_count)
    out = merged(page, base, file, pairs_from(base, file))
    check(page, base, file, out)
    return page, base, file, out


@pytest.mark.parametrize("seed", range(400))
def test_any_edits_on_both_sides(seed):
    rng = random.Random(seed * 7919)
    trial(seed, ["text", "spanless", "note", "delete", "move"], rng.randint(0, 6), rng.randint(0, 6))


@pytest.mark.parametrize("seed", range(150))
def test_with_only_spanless_changes_in_gamma_the_page_says_what_the_file_says(seed):
    page, _base, file, out = trial(10_000 + seed, ["spanless"], seed % 5, 1 + seed % 6)
    expected, got = file_shape(out, file)
    assert got == expected and out.level and not out.conflicts


@pytest.mark.parametrize("seed", range(150))
def test_with_only_text_changes_in_gamma_the_files_places_land(seed):
    """Gamma typed and nothing else: every block of the file stands under the
    file's parent, in the file's order (Gamma's spanless blocks and the ones
    whose text outlived a delete between them)."""
    page, base, file, out = trial(20_000 + seed, ["typed", "spanless"], 1 + seed % 5, 1 + seed % 6)
    named = set(out.ids)
    order = [b for b in md_lens.flatten(md_lens.project(out.target, kind_of)) if b["id"] in named]
    assert [b["id"] for b in order] == [b for b in out.ids if b in out.target]
    for n, expected in enumerate(file_parents(file, out.ids)):
        above = out.target[out.ids[n]]["parent"]
        while above != P and above not in named:
            above = out.target[above]["parent"]
        assert above == expected


@pytest.mark.parametrize("seed", range(60))
def test_a_clash_on_both_sides_is_exactly_one_conflict_holding_gammas_text(seed):
    rng = random.Random(30_000 + seed)
    page0 = random_page(rng, rng.randint(3, 20))
    base = md_lens.project(page0, kind_of)
    blocks = md_lens.flatten(base)
    if not blocks:
        return
    target_block = rng.choice(blocks)
    page = gamma_edits(rng, page0, ["spanless", "move"], rng.randint(0, 3))
    if target_block["id"] not in page:
        return
    page[target_block["id"]]["content"] = "Gamma rewrote this sentence entirely"
    file = copy(base)
    for f in md_lens.flatten(file):
        if f["src"] == target_block["id"]:
            f["content"] = "the file said something else instead"
    out = merged(page, base, file, pairs_from(base, file))
    assert out.conflicts == [{"block_id": target_block["id"], "mine": "Gamma rewrote this sentence entirely",
                              "theirs": "the file said something else instead",
                              "result": "the file said something else instead", "base": target_block["content"]}]
    assert out.target[target_block["id"]]["content"] == "the file said something else instead"
