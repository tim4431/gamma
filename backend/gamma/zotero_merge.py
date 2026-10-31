"""The Zotero link's pure decisions (docs/dev/zotero_sync.md): how each side
of a synced object reads in comparable form, what a round does with a
difference, how a pulled note lands on the blocks it came from, and which
page goes with which item. The Zotero counterpart of sync_tree.py: no
database, network or file access, so every rule is tested on plain data and
the engine (zotero_sync) only gathers, writes and records.

Every synced object is compared as two projections in Zotero's terms:
Zotero's object as fetched (``item_projection``, ``collection_projection``)
and Gamma's as it would be sent (``page_projection``, ``folder_projection``;
annotation and note projections arrive as plain dicts). Tags compare by
name ignoring case, as Gamma finds labels; automatic tags take part only
when the link brings them as labels, and otherwise go back to Zotero
untouched (``tags_for_push``). A collection takes part only while its row
is live, so the refiling a collection trashed in Zotero causes here never
reads as a Gamma change and its items keep their membership for a restore.

A row keeps two bases, Zotero's projection as last seen or written and
Gamma's as last synced: Zotero changed a field when its value left the
first, Gamma when its value left the second. A mapping that loses something
one way (a quote clipped, a mark dropped, a position rounded) then never
reads as an edit of the other side, and a version bump that changed nothing
gives nothing. ``merge`` decides field by field (sets member by member);
``bases_after`` gives the bases a round may record once its writes are done,
keeping a Gamma change whose write did not land visible to the next round.

A note region is a block subtree kept in step with one Zotero note.
``represented`` splits it into what the note renders and what Gamma keeps
for itself (annotations at any depth, links, sheets, drawings, text boxes,
recordings, pictures, references, empty blocks); ``note_edits`` turns a
pulled note into block edits that keep unchanged blocks' ids and never
delete or lose a kept block; ``note_lossy`` says whether a Zotero note holds
what Gamma cannot write back, so it is never overwritten.

``match`` pairs an existing workspace's pages with a library's items at a
first link and ``first_link_base`` gives each pair bases holding only what
both agree on; ``primary`` picks an item's PDF as Zotero's reader does;
``is_paper``, ``wants_item`` and ``standalone_gets_parent`` say which pages
become items; ``field_line`` and ``annotation_line`` word a conflict's sides.
"""

import colorsys
import difflib
import html as html_mod
import re
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from typing import NamedTuple

from . import item_types, textmerge, zotero_annotations, zotero_fields
from .blocks_store import block_kind
from .colors import parse_color
from .db import parse_stamp
from .note_markup import LITERAL_SPANS
from .zotero_import import _ARXIV_ID, _work_title
from .zotero_notes import block_text, note_html, note_tree, title_text
from .zotero_web import parse_item_uri

# The projection key whose members merge one by one as fields of their own
# (an item's record): every other key is one field.
FIELDS = "fields"
# The set-valued keys of an item projection, merged member by member.
SETS = ("tags", "collections")
MANUAL, AUTOMATIC = 0, 1  # Zotero's tag types

# The Gamma-notes region's root: the note "Notes from Gamma" gathers a paper
# page's own top-level notes, each subtree one child of this heading.
NOTES_HEADING = "# Notes from Gamma"

# What a page waits, after it was made with a PDF and no record, for the
# metadata lookup before it is sent (or kept) without one.
LOOKUP_WAIT_S = 120
WANT_PAPER, WANT_DOCUMENT, WANT_WAIT = "paper", "document", "wait"

# Zotero's attachment link modes that hold a file, and the reasons a PDF
# cannot come (``fetchable``): a file on the user's disk, one Zotero
# Storage does not serve (WebDAV, file sync off), a key without file access.
FILE_LINK_MODES = ("imported_file", "imported_url", "linked_file")
STORED_LINK_MODES = ("imported_file", "imported_url")
PDF_TYPE = "application/pdf"
LINKED, UNAVAILABLE, NO_ACCESS = "linked", "unavailable", "no_access"

MATCH_REASONS = ("zotero_key", "doi", "arxiv", "md5", "title")

LINE_MAX = 200       # characters of a readable conflict line
AUTHORS_SHOWN = 2    # authors a line names before "…"
QUOTE_SHOWN = 80     # characters of an annotation's quote or comment in its line


# --- projections -------------------------------------------------------------------

def _fold(name) -> str:
    """A tag or label name as compared: trimmed and lower-cased, as Gamma
    finds a label (``blocks_store.named``: ``lower()``, not ``casefold()``)."""
    return str(name or "").strip().lower()


fold_tag = _fold  # the engine spells tags by the same rule


def _folded_tags(names) -> list[str]:
    """Label or tag names as a projection holds them: folded, sorted, once each."""
    return sorted({_fold(n) for n in names if _fold(n)})


def _live_keys(data: dict, live_collections=None) -> set[str]:
    """The collection keys an object is filed in that take part: only those
    in ``live_collections`` (None keeps every key)."""
    keys = {k for k in (data or {}).get("collections") or () if isinstance(k, str) and k}
    return keys & set(live_collections) if live_collections is not None else keys


def is_pdf_attachment(data: dict) -> bool:
    """Whether an item's ``data`` is a PDF attachment of a file link mode
    (stored, downloaded from a page, or a linked file)."""
    data = data or {}
    return (data.get("itemType") == "attachment" and data.get("linkMode") in FILE_LINK_MODES
            and str(data.get("contentType") or "").lower() == PDF_TYPE)


def stored_size(obj: dict) -> int:
    """The bytes of an attachment's stored file as Zotero lists it
    (``links.enclosure.length``), 0 when it does not say."""
    try:
        return int((((obj or {}).get("links") or {}).get("enclosure") or {}).get("length") or 0)
    except (TypeError, ValueError):
        return 0


def _tag_type(tag: dict) -> int:
    try:
        return int(tag.get("type") or MANUAL)
    except (TypeError, ValueError):
        return MANUAL


def tag_names(data: dict, *, auto_tags: bool) -> dict[str, str]:
    """``{folded name: name as Zotero spells it}`` of the tags an item
    brings as labels: its manual tags, and its automatic ones too when
    ``auto_tags``; the first spelling of a name wins."""
    out = {}
    for tag in (data or {}).get("tags") or ():
        if not isinstance(tag, dict):
            continue
        name = str(tag.get("tag") or "").strip()
        if name and (auto_tags or _tag_type(tag) != AUTOMATIC):
            out.setdefault(_fold(name), name)
    return out


def _record_fields(projected: dict) -> dict:
    """``zotero_fields.project``'s form as ``{compare field: value}``, a
    missing field empty."""
    meta = projected.get("meta") or {}
    out = {f: meta.get(f, [] if f == "authors" else "") for f in zotero_fields.COMPARE_FIELDS if f != "cite_key"}
    out["cite_key"] = projected.get("cite_key") or ""
    return out


def item_projection(data: dict, *, live_collections=None, auto_tags: bool = False) -> dict:
    """An item's ``data`` as compared: ``{"type": itemType, "fields":
    {compare field: value}, "tags": [folded names], "collections": [keys]}``,
    the tags those Gamma brings (``tag_names``) and the collections only
    those in ``live_collections`` (the keys whose rows are live; None keeps
    every key), both sorted."""
    data = data or {}
    return {"type": str(data.get("itemType") or ""),
            "fields": _record_fields(zotero_fields.project(data)),
            "tags": sorted(tag_names(data, auto_tags=auto_tags)),
            "collections": sorted(_live_keys(data, live_collections))}


def _page_kind(meta) -> str:
    """The kind a page reads as: its record's, a page without one a document."""
    return item_types.item_type(meta) if meta else "document"


def write_type(meta, item_type: str = "") -> str:
    """The Zotero type a page's record is compared and written as against
    an item of ``item_type`` ("" for none yet): the item's own type while
    the page reads as the kind the item does (a ``blogPost`` stays one on a
    ``document`` page), else the page's kind, so a kind changed on either
    side shows. A record that names no kind reads as the work type of the
    item it is linked to: what its fields imply (``item_types.infer``)
    picks only a new item's type and the type of a ``document`` placeholder
    after a lookup, so a field pulled or edited never retypes an item."""
    if (meta and not item_types.normalize(meta.get("kind")) and item_type
            and item_types.from_zotero(item_type) not in ("", "document")):
        return item_type
    kind = _page_kind(meta)
    if item_type and kind == (item_types.from_zotero(item_type) or "document"):
        return item_type
    return kind


def page_projection(meta, cite_key: str = "", title: str = "", item_type: str = "", label_names=(),
                    folder_keys=()) -> dict:
    """A page as it would be sent, in ``item_projection``'s form: its record
    normalised as Zotero would hold it (``zotero_fields.normalized``, as
    ``write_type``), its label names folded, and ``folder_keys`` (the live
    collection keys of the folders it is filed in), both sorted.
    ``item_type`` is the linked item's current type, "" for a new page."""
    meta = meta if isinstance(meta, dict) else {}
    kind = write_type(meta, item_type)
    return {"type": kind,
            "fields": _record_fields(zotero_fields.normalized(meta, cite_key or "", kind, title or "")),
            "tags": _folded_tags(label_names),
            "collections": sorted({k for k in folder_keys if isinstance(k, str) and k})}


def _squashed(text) -> str:
    return " ".join(str(text or "").split())


def standalone_projection(data: dict, *, live_collections=None, auto_tags: bool = False, title: bool = True) -> dict:
    """A standalone PDF's or a standalone note's ``data`` as compared:
    ``{"title", "tags", "collections"}`` (``title`` False leaves the title
    out, as a note's), by ``item_projection``'s rules for tags and
    collections; the title (the file's name for an untitled PDF) with its
    whitespace squashed."""
    data = data or {}
    out = {"tags": sorted(tag_names(data, auto_tags=auto_tags)),
           "collections": sorted(_live_keys(data, live_collections))}
    if title:
        out["title"] = _squashed(data.get("title") or data.get("filename"))
    return out


def page_standalone_projection(title: str = "", label_names=(), folder_keys=(), *, with_title: bool = True) -> dict:
    """A standalone PDF's or note's page as its attachment or note would
    hold it, in ``standalone_projection``'s form: its title (the record, if
    any, Gamma's alone until the PDF gets a parent item, never travels),
    its label names folded and ``folder_keys`` (the live collection keys of
    its folders)."""
    out = {"tags": _folded_tags(label_names),
           "collections": sorted({k for k in folder_keys if isinstance(k, str) and k})}
    if with_title:
        out["title"] = _squashed(title)
    return out


def collection_projection(data: dict) -> dict:
    """A collection's ``data`` as compared: ``{"name", "parent"}``, the
    parent "" at the top (Zotero writes ``false`` there). Trash is a
    state, not a field."""
    data = data or {}
    parent = data.get("parentCollection")
    return {"name": str(data.get("name") or "").strip(), "parent": parent if isinstance(parent, str) else ""}


def folder_projection(name: str, parent_key: str = "") -> dict:
    """A folder as its collection would hold it: its name and its parent
    folder's collection key ("" at the top)."""
    return {"name": str(name or "").strip(), "parent": parent_key or ""}


def tags_for_push(zotero_tags, gamma_names, *, auto_tags: bool = False) -> list[dict]:
    """The ``tags`` array a PATCH sends so that the item holds
    ``gamma_names`` (the merged names, in Gamma's spelling for the new ones):
    every tag Zotero has that is kept goes back as Zotero has it, with its
    type; an automatic tag always stays, unless the link brings automatic
    tags and Gamma removed it; each name Zotero lacks goes as a new manual
    tag (``{"tag": name}``, Zotero's own form for type 0)."""
    wanted, out, have = {}, [], set()
    for name in gamma_names or ():
        if _fold(name):
            wanted.setdefault(_fold(name), str(name).strip())
    for tag in zotero_tags or ():
        if not isinstance(tag, dict) or not str(tag.get("tag") or "").strip():
            continue
        folded = _fold(tag["tag"])
        if folded in wanted or (_tag_type(tag) == AUTOMATIC and not auto_tags):
            out.append(dict(tag))
            have.add(folded)
    out += [{"tag": name} for folded, name in wanted.items() if folded not in have]
    return out


# --- the merge -----------------------------------------------------------------------

class Merge(NamedTuple):
    """What ``merge`` decided. ``apply``: ``{field: Zotero's value}`` to
    write into Gamma; ``push``: ``{field: Gamma's value}`` to send;
    ``conflicts``: ``[(field, Gamma's value, Zotero's value)]`` both sides
    changed (the winner's value is in ``push`` or ``apply``);
    ``merged_text``: ``{field: text}`` merged from both sides' edits, in
    Gamma's terms, to write into Gamma and send in Zotero's;
    ``sets``: ``{set field: {"gamma": members, "zotero": members}}``, each
    side's membership once the round is done."""
    apply: dict
    push: dict
    conflicts: list
    merged_text: dict
    sets: dict


def _split(projection) -> tuple[dict, list]:
    """A projection as ``{field: value}``, its ``fields`` dict's members
    as fields of their own, and the names that came from it."""
    projection = projection if isinstance(projection, dict) else {}
    flat = {k: v for k, v in projection.items() if k != FIELDS}
    members = projection.get(FIELDS)
    nested = list(members) if isinstance(members, dict) else []
    for name in nested:
        if name in flat:
            raise ValueError(f"projection field {name!r} is both a key and a record field")
        flat[name] = members[name]
    return flat, nested


def _join(flat: dict, nested) -> dict:
    """``_split`` undone: the names in ``nested`` back under ``fields``."""
    out = {k: v for k, v in flat.items() if k not in nested}
    if nested:
        out[FIELDS] = {k: flat.get(k) for k in nested}
    return out


def _ordered(*names_lists) -> list:
    seen = {}
    for names in names_lists:
        for name in names:
            seen.setdefault(name, None)
    return list(seen)


def _members(value) -> set:
    """A set field's members (names, keys); a missing or odd value has none."""
    return {m for m in value if isinstance(m, str)} if isinstance(value, (list, tuple, set, frozenset)) else set()


def _merge_set(zb, gb, z, g, prefer: str) -> tuple[set, set]:
    """One set merged member by member against both bases: ``(Gamma's
    members after, Zotero's members after)``. A member one side added or
    removed is added or removed on both; both changed it the same way, so
    it stays; both changed it apart (possible only where the bases
    disagree), ``prefer``'s side holds; neither changed it, each side keeps
    what it has."""
    zb, gb, z, g = _members(zb), _members(gb), _members(z), _members(g)
    after_g, after_z = set(), set()
    for m in zb | gb | z | g:
        in_z, in_g = m in z, m in g
        z_changed, g_changed = in_z != (m in zb), in_g != (m in gb)
        if not z_changed and not g_changed:
            if in_g:
                after_g.add(m)
            if in_z:
                after_z.add(m)
            continue
        if z_changed and g_changed and in_z != in_g:
            keep = in_g if prefer == "gamma" else in_z
        else:
            keep = in_z if z_changed else in_g
        if keep:
            after_g.add(m)
            after_z.add(m)
    return after_g, after_z


def _converters(texts) -> dict:
    if isinstance(texts, dict):
        return {k: (fn or (lambda v: v)) for k, fn in texts.items()}
    return {k: (lambda v: v) for k in texts or ()}


def merge(zbase, gbase, Z, G, *, sets=SETS, texts=(), prefer: str = "gamma") -> Merge:
    """Decide every field of one object against two bases (see
    docs/dev/zotero_sync.md#merging): ``zbase`` Zotero's projection as last
    seen or written, ``gbase`` Gamma's as last synced, ``Z`` and ``G`` the two
    now. Zotero changed a field when ``Z`` differs from ``zbase`` there, Gamma
    when ``G`` differs from ``gbase``; a field one side changed goes to the
    other, unless the other already holds that value. Fields named in ``sets``
    merge member by member (``_merge_set``). A field both changed apart is
    merged with ``textmerge`` when it is in ``texts`` — field names, or
    ``{field: to_gamma}`` turning a value into Gamma's terms (``comment_in``) —
    and the edits do not overlap; otherwise ``prefer``'s side wins
    (``"gamma"``, or ``"zotero"`` for a first link adopting Zotero's, a force
    pull) and the difference is in ``conflicts``. A field missing from a base
    (a new row, ``first_link_base``'s None for two values apart) reads as
    changed. A version bump that changed no field gives an empty merge."""
    if prefer not in ("gamma", "zotero"):
        raise ValueError(f"prefer is 'gamma' or 'zotero', not {prefer!r}")
    (zb, _), (gb, _), (z, _), (g, _) = _split(zbase), _split(gbase), _split(Z), _split(G)
    convert = _converters(texts)
    apply, push, conflicts, merged_text, set_after = {}, {}, [], {}, {}
    for f in _ordered(z, g, zb, gb):
        if f in sets:
            after_g, after_z = _merge_set(zb.get(f), gb.get(f), z.get(f), g.get(f), prefer)
            set_after[f] = {"gamma": sorted(after_g), "zotero": sorted(after_z)}
            if after_g != _members(g.get(f)):
                apply[f] = sorted(after_g)
            if after_z != _members(z.get(f)):
                push[f] = sorted(after_z)
            continue
        zv, gv = z.get(f), g.get(f)
        z_changed, g_changed = zv != zb.get(f), gv != gb.get(f)
        if not (z_changed or g_changed) or zv == gv:
            continue
        if not g_changed:
            apply[f] = zv
            continue
        if not z_changed:
            push[f] = gv
            continue
        if f in convert and all(isinstance(v, str) for v in (gb.get(f), gv, zv)):
            to_gamma = convert[f]
            base, ours, theirs = to_gamma(gb[f]), to_gamma(gv), to_gamma(zv)
            if textmerge.contains(base, theirs, ours):  # Gamma's text already holds Zotero's edit
                text, clean = ours, True
            elif textmerge.contains(base, ours, theirs):
                text, clean = theirs, True
            else:
                text, clean = textmerge.merge(base, ours, theirs)
            if clean:
                if text == ours:
                    push[f] = gv
                elif text == theirs:
                    apply[f] = zv
                else:
                    merged_text[f] = text
                continue
        conflicts.append((f, gv, zv))
        if prefer == "gamma":
            push[f] = gv
        else:
            apply[f] = zv
    return Merge(apply, push, conflicts, merged_text, set_after)


def bases_after(m: Merge, zbase, gbase, Z, G, *, pushed_ok: bool, G_after=None,
                Z_after=None) -> tuple[dict, dict]:
    """``(zbase, gbase)`` to record once a round acted on ``m``. A field
    applied to Gamma: Zotero's base is ``Z``'s value, Gamma's is ``G_after``'s
    (Gamma's projection re-read after the write; the applied value without
    one), so a lossy mapping never reads as a Gamma edit. A field pushed
    (``m.push``, and a merged text, which goes both ways): with ``pushed_ok``
    Zotero's base is ``Z_after``'s (the saved object's projection,
    ``successful[i].data``; the value sent without one) and Gamma's the value
    as decided; without it Zotero's base is ``Z``'s and Gamma's keeps its old
    value, so the next round sees Gamma's change and sends it again — and when
    Zotero changed the field too (Gamma's won a conflict), Gamma's base is
    None: whatever Gamma holds of it then goes, even its old value (a change
    taken back), which Zotero's newer one would otherwise outlast unseen on
    both sides. Any other field: both bases take the values now, Gamma's as
    ``G_after`` reads it when given, so a field an applied one implies (a kind
    inferred from the fields) never reads as a Gamma edit. A set member follows
    the same rule by member."""
    (zb, zbn), (gb, gbn) = _split(zbase), _split(gbase)
    (z, zn), (g, gn) = _split(Z), _split(G)
    ga, gan = _split(G_after) if G_after is not None else ({}, [])
    za, zan = _split(Z_after) if Z_after is not None else ({}, [])
    nested = _ordered(zn, gn, zbn, gbn, gan, zan)
    new_z, new_g = {}, {}
    for f in _ordered(z, g, zb, gb):
        if f in m.sets:
            after = m.sets[f]
            want_g, want_z = set(after["gamma"]), set(after["zotero"])
            now_z = _members(z.get(f))
            seen_g = _members(ga[f]) if f in ga else want_g
            if pushed_ok:
                new_z[f] = sorted(_members(za[f]) if f in za else want_z)
                new_g[f] = sorted(seen_g)
            else:
                # the members Gamma's write was to change keep their old base
                sent, old_g = want_z ^ now_z, _members(gb.get(f))
                new_z[f] = sorted(now_z)
                new_g[f] = sorted(x for x in seen_g | old_g if (x in old_g if x in sent else x in seen_g))
            continue
        if f in m.apply:
            new_z[f] = z.get(f)
            new_g[f] = ga[f] if f in ga else m.apply[f]
        elif f in m.push or f in m.merged_text:
            sent = m.push[f] if f in m.push else m.merged_text[f]
            if pushed_ok:
                new_z[f] = za[f] if f in za else sent
                new_g[f] = g.get(f) if f in m.push else (ga[f] if f in ga else sent)
            else:
                new_z[f] = z.get(f)
                new_g[f] = gb.get(f)
                if z.get(f) != zb.get(f):  # both changed it and Gamma's did not go: Gamma's goes, whatever it is
                    if g.get(f) is not None:
                        new_g[f] = None
                    else:  # (no value to read as changed: Zotero's change stays one instead)
                        new_z[f] = zb.get(f)
        else:
            new_z[f], new_g[f] = z.get(f), (ga[f] if f in ga else g.get(f))
    return _join(new_z, nested), _join(new_g, nested)


def _note_text(value) -> str:
    return " ".join(str(value or "").split())


def _blank(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def first_link_base(kind: str, Z, G, *, sets=SETS) -> tuple[dict, dict]:
    """``(zbase, gbase)`` for a pair a first link matched (or a force cleared):
    only what both sides agree on counts as synced. A field the two hold alike
    keeps each side's own value in its base, so neither reads as changed; a
    field they hold apart is None in both, a value neither has, so both read as
    changed and the first link's ``adopt`` decides it (``merge(...,
    prefer=...)``). A value only one side holds (the other's empty) is that
    side's addition, as a set member only one side has is: both bases take the
    empty value, so it goes to the other side whatever ``adopt`` says and
    nothing is blanked (a collection's top-level parent "" is a place, not a
    missing value). A set's base holds the members both have: what only one
    side has reads as that side's addition and goes to the other, so nothing is
    removed. ``kind`` ("item", "collection", "note", "annotation",
    "attachment") picks the agreement: an annotation's ``position`` agrees
    within ``zotero_annotations.POSITION_TOL`` points (``positions_close``, the
    rule ``zotero_annotations.same`` reads), a note's fields agree when its
    ``text`` does (whitespace aside), anything else when equal."""
    (z, zn), (g, gn) = _split(Z), _split(G)
    nested = _ordered(zn, gn)
    zb, gb = {}, {}
    notes_agree = kind == "note" and _note_text(z.get("text")) == _note_text(g.get("text"))
    for f in _ordered(z, g):
        if f in sets:
            zb[f] = gb[f] = sorted(_members(z.get(f)) & _members(g.get(f)))
            continue
        a, b = z.get(f), g.get(f)
        agree = a == b or (_blank(a) and _blank(b)) or notes_agree or (
            kind == "annotation" and f == "position" and zotero_annotations.positions_close(a, b))
        if agree:
            zb[f], gb[f] = a, b
        elif kind != "collection" and _blank(a) != _blank(b):  # one side lacks it: the other's addition
            zb[f] = gb[f] = a if _blank(a) else b
        else:
            zb[f], gb[f] = None, None
    return _join(zb, nested), _join(gb, nested)


# --- note regions ---------------------------------------------------------------------

_LITERALS_RE = re.compile(LITERAL_SPANS)
# What note HTML cannot carry from a block: a reference or an embed (written
# out as the text it names) and a picture (pictures in Gamma notes stay in
# Gamma). Searched with code and math spans taken out, where they are text.
_GAMMA_ONLY_RE = re.compile(r"!?\[\[[^\]\n]+\]\]|!\[[^\]\n]*\]\(")
_ORDERED_MARK_RE = re.compile(r"(?m)^([ \t]*\d{1,9})\)")
# Properties that make a block Zotero's or Gamma's own whatever its kind: an
# annotation, the import's Attachments heading and its chips.
_KEPT_PROPS = ("zotero_annotation", "imported_annot", "zotero_attachments", "zotero_attachment")


def _kind(node: dict) -> str:
    kind = node.get("kind")
    if kind:
        return kind
    return block_kind(node.get("parent_id") or "_", node.get("page_id") or "", node.get("properties") or {}) or ""


def _canon(text: str) -> str:
    """Text as compared for survival: its plain words (``title_text``),
    numbered-item marks in one spelling, whitespace dropped."""
    return "".join(title_text(_ORDERED_MARK_RE.sub(r"\1.", text)).split())


def _gamma_only(text: str) -> bool:
    return bool(_GAMMA_ONLY_RE.search(_LITERALS_RE.sub(" ", text)))


def _candidate(node: dict, exclude) -> bool:
    """Whether a block may be represented in a note: a note block with text
    note HTML can carry, not another region's root, not an annotation's or
    the Attachments heading's."""
    props = node.get("properties") or {}
    text = block_text(node)
    return (_kind(node) == "note" and node.get("id") not in exclude and not any(props.get(k) for k in _KEPT_PROPS)
            and bool(text) and not _gamma_only(text))


def _preorder(tree) -> list[tuple[int, str]]:
    """A ``{content, children}`` tree as ``[(depth, content)]``, root first."""
    out, stack = [], [(tree, 0)] if tree else []
    while stack:
        node, depth = stack.pop()
        out.append((depth, node.get("content") or ""))
        stack.extend((c, depth + 1) for c in reversed(node.get("children") or []))
    return out


def _rendered(tree, resolve_ref=None) -> list[tuple[int, str]]:
    """``tree`` as Zotero holds it once sent and read back."""
    try:
        return _preorder(note_tree(note_html(tree, resolve_ref=resolve_ref)))
    except RecursionError:
        return []


class Region(NamedTuple):
    """A note region split by ``represented``. ``blocks``: the represented
    blocks in preorder, ``(block id, depth, content as the note reads back)``,
    the root first at depth 0. ``kept``: every other block of the region
    (kept blocks' subtrees included). ``parent`` and ``children``: the
    region's structure, kept blocks included (``children`` in order).
    ``tree``: the represented blocks as a ``{content, children}`` tree;
    ``html``: the note Gamma sends for it. ``root``: the root's id;
    ``root_parent``: its parent's. ``root_fixed``: the root is the page (the
    Gamma-notes region), whose content is its title and is never set.
    ``unsettable``: represented blocks whose own text a pull may not
    overwrite (a root whose text the note cannot carry: its pulled changes
    come out as ``lossy`` edits)."""
    blocks: list
    kept: frozenset
    parent: dict
    children: dict
    tree: dict
    html: str
    root: str
    root_parent: str
    root_fixed: bool
    unsettable: frozenset


def _region_tree(tree):
    if isinstance(tree, (list, tuple)):
        from .markdown_export import build_tree  # rows of fetch_subtree, root first
        return build_tree(tree, tree[0][0]) if tree else None
    return tree


def represented(tree, *, exclude=(), resolve_ref=None) -> Region:
    """Split a note region (a ``markdown_export.build_tree`` node, or the
    ``fetch_subtree`` rows of its root; ``gamma_notes_tree`` for the
    Gamma-notes region) into what its note renders and what Gamma keeps. The
    root is always represented. Below it a block is represented when its parent
    is and it is a note block with text whose content survives
    ``note_tree(note_html(...))`` (its words unchanged; no reference, embed or
    picture), and is not in ``exclude`` (another region's root). Every other
    block is kept with its subtree: annotations at any depth, links, sheets,
    drawings, text boxes, recordings, pictures, empty blocks. ``resolve_ref``
    renders a root's references as the note will
    (``markdown_export.block_ref_resolver``)."""
    root = _region_tree(tree)
    if not root:
        raise ValueError("a note region needs its root")
    exclude = set(exclude or ())
    root_id = root.get("id") or ""
    parent, children, order, nodes = {}, {}, [], {}
    stack = [root]
    while stack:  # every block of the region, preorder
        node = stack.pop()
        bid = node.get("id") or ""
        nodes[bid] = node
        order.append(bid)
        kids = node.get("children") or []
        children[bid] = [c.get("id") or "" for c in kids]
        for child in kids:
            parent[child.get("id") or ""] = bid
        stack.extend(reversed(kids))
    rep = {root_id}
    for bid in order[1:]:
        if parent[bid] in rep and _candidate(nodes[bid], exclude):
            rep.add(bid)
    root_text = block_text(root)
    unsettable = {root_id} if _gamma_only(root_text) else set()
    rendered = None
    for _ in range(8):  # drop what does not read back, until all of it does
        tree_out, flat = _build(nodes, children, rep, root_id)
        back = _rendered(tree_out, resolve_ref)
        if [d for d, _ in back] != [d for _, d, _ in flat]:
            break
        lost = {bid for (bid, _, text), (_, again) in zip(flat, back)
                if bid != root_id and _canon(again) != _canon(text)}
        if not lost:
            rendered = [(bid, d, again) for (bid, d, _), (_, again) in zip(flat, back)]
            if _canon(back[0][1]) != _canon(root_text):
                unsettable.add(root_id)
            break
        rep = _without(rep, lost, children)
    if rendered is None:  # the note does not read back block for block (an empty root): block by block
        rep, rendered, root_lost = _each(nodes, children, rep, root_id, order, resolve_ref)
        if root_lost:
            unsettable.add(root_id)
        tree_out, _ = _build(nodes, children, rep, root_id)
    fixed = _kind(root) == "page"
    if fixed:
        unsettable.discard(root_id)
    return Region(blocks=rendered, kept=frozenset(b for b in order if b not in rep), parent=parent,
                  children=children, tree=tree_out, html=note_html(tree_out, resolve_ref=resolve_ref),
                  root=root_id, root_parent=root.get("parent_id") or "", root_fixed=fixed,
                  unsettable=frozenset(unsettable))


def _build(nodes, children, rep, root_id):
    """The represented blocks as a ``{content, children}`` tree and their
    preorder ``(id, depth, text)``."""
    flat, root = [], {"content": block_text(nodes[root_id]), "children": []}
    stack = [(root_id, 0, root)]
    while stack:
        bid, depth, out = stack.pop()
        flat.append((bid, depth, out["content"]))
        below = []
        for child in children.get(bid, ()):
            if child in rep:
                node = {"content": block_text(nodes[child]), "children": []}
                out["children"].append(node)
                below.append((child, depth + 1, node))
        stack.extend(reversed(below))
    return root, flat


def _without(rep: set, lost: set, children: dict) -> set:
    out, stack = set(rep), list(lost)
    while stack:
        bid = stack.pop()
        out.discard(bid)
        stack.extend(children.get(bid, ()))
    return out


def _each(nodes, children, rep, root_id, order, resolve_ref):
    """``represented``'s fallback: each block read back alone, a child as
    one item of a list, the root as a note of its own."""
    root_back = _rendered({"content": block_text(nodes[root_id]), "children": []}, resolve_ref)
    root_again = root_back[0][1] if len(root_back) == 1 else ""
    again = {root_id: root_again}
    for bid in order[1:]:
        if bid not in rep:
            continue
        back = _rendered({"content": "x", "children": [{"content": block_text(nodes[bid]), "children": []}]})
        text = back[1][1] if len(back) == 2 else None
        if text is None or _canon(text) != _canon(block_text(nodes[bid])):
            rep = _without(rep, {bid}, children)
        else:
            again[bid] = text
    _, flat = _build(nodes, children, rep, root_id)
    return rep, [(bid, d, again[bid]) for bid, d, _ in flat], _canon(root_again) != _canon(block_text(nodes[root_id]))


def gamma_notes_tree(subtrees, *, page_id: str, exclude=()) -> dict | None:
    """The Gamma-notes region of a paper page: a root standing for the page
    (``NOTES_HEADING``, which the note "Notes from Gamma" opens with) over the
    page's top-level ``subtrees`` (``build_tree`` nodes, in page order) that
    are note blocks: not annotations, links, drawings, text boxes, pictures,
    sheets or recordings, not the import's Attachments heading, and not in
    ``exclude`` (the roots of the page's Zotero-note regions). None when no
    subtree qualifies."""
    exclude = set(exclude or ())
    kids = [n for n in subtrees or () if _kind(n) == "note" and n.get("id") not in exclude
            and not any((n.get("properties") or {}).get(k) for k in _KEPT_PROPS)]
    if not kids:
        return None
    return {"id": page_id, "parent_id": "root", "page_id": page_id, "kind": "page", "content": NOTES_HEADING,
            "properties": {}, "children": kids}


def _new_rows(new) -> list[tuple[int, str]]:
    """A pulled note as ``[(depth, content)]``, root first: from its HTML,
    its ``note_tree``, or such a list (depths clamped to a tree's). An empty
    note is a root without text."""
    if isinstance(new, str):
        new = note_tree(new)
    rows = _preorder(new) if isinstance(new, dict) else [(int(d), str(c or "")) for d, c in new or ()]
    out = []
    for depth, content in rows:
        depth = 0 if not out else max(1, min(depth, out[-1][0] + 1))
        out.append((depth, content))
    return out or [(0, "")]


def note_edits(old: Region, new) -> list[dict]:
    """The edits that turn a note region's represented blocks into a pulled
    note: ``old`` is the region (``represented``), ``new`` the note's HTML, its
    ``note_tree`` or ``[(depth, content)]``. The two preorders are aligned on
    ``(depth, content)`` (``difflib.SequenceMatcher``, no autojunk): an equal
    block keeps its id, content, properties and comments; a replaced one is
    paired in order and set (and moved when its parent changed); an added one
    is inserted under its new parent; a removed one goes after its kept
    children were lifted into its place. Kept blocks are never set or deleted,
    only lifted.

    The edits, to apply in order: ``{"op": "set", "id", "content"}``;
    ``{"op": "insert", "id": "new:<n>", "parent", "after", "content"}`` (the
    caller gives the ref a fresh block id and uses it for that ref in later
    edits); ``{"op": "move", "id", "parent", "after"}``; ``{"op": "delete",
    "id"}`` (the block alone: it has no children left by then);
    ``{"op": "lossy", "id", "content"}`` (Zotero changed a block whose text
    Gamma may not overwrite: a ``zotero_lossy`` row). ``after`` is the
    sibling (id or ref) the block goes right after, None for before the
    parent's first child in the region (``Region.children``)."""
    rows = _new_rows(new)
    sm = difflib.SequenceMatcher(None, [(d, c) for _, d, c in old.blocks], rows, autojunk=False)
    ids: list = [None] * len(rows)
    edits, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            continue
        paired = i2 - i1 if tag == "equal" else min(i2 - i1, j2 - j1)
        for k in range(paired):
            bid, _, text = old.blocks[i1 + k]
            ids[j1 + k] = bid
            content = rows[j1 + k][1]
            if content == text or (bid == old.root and old.root_fixed):
                continue
            op = "lossy" if bid in old.unsettable else "set"
            edits.append({"op": op, "id": bid, "content": content})
        removed += [old.blocks[i][0] for i in range(i1 + paired, i2)]
    ids = [bid if bid is not None else f"new:{j}" for j, bid in enumerate(ids)]
    path, last_child = [ids[0]], {}
    for j in range(1, len(rows)):
        depth, content = rows[j]
        del path[depth:]
        parent, node = path[-1], ids[j]
        path.append(node)
        after = last_child.get(parent)
        last_child[parent] = node
        if node.startswith("new:"):
            edits.append({"op": "insert", "id": node, "parent": parent, "after": after, "content": content})
        elif old.parent.get(node) != parent:
            edits.append({"op": "move", "id": node, "parent": parent, "after": after})
    return edits + _lifts(old, set(removed)) + _deletes(old, removed)


def _region_order(region: Region) -> list[str]:
    out, stack = [], [region.root]
    while stack:
        bid = stack.pop()
        out.append(bid)
        stack.extend(reversed(region.children.get(bid, ())))
    return out


def _lifts(region: Region, removed: set) -> list[dict]:
    """Moves that take each kept block whose parent goes out of the way: to
    its nearest ancestor that stays, right after the removed block it was
    under there (and after the kept blocks lifted before it), in order."""
    out, anchors = [], {}
    for bid in _region_order(region):
        if bid not in region.kept or region.parent.get(bid) not in removed:
            continue
        top = region.parent[bid]
        while top != region.root and region.parent.get(top) in removed:
            top = region.parent[top]
        target = region.root_parent if top == region.root else region.parent[top]
        out.append({"op": "move", "id": bid, "parent": target, "after": anchors.get(top, top)})
        anchors[top] = bid
    return out


def _deletes(region: Region, removed) -> list[dict]:
    rank = {bid: i for i, bid in enumerate(_region_order(region))}
    return [{"op": "delete", "id": bid} for bid in sorted(removed, key=lambda b: -rank[b])]


def note_removal(region: Region) -> list[dict]:
    """The edits for a note Zotero deleted while Gamma left its region alone:
    each kept block is lifted out of the represented blocks, to the region
    root's parent right after the root (for the Gamma-notes region, to the page
    right after its top-level block), then the represented blocks are deleted,
    deepest first. The page itself is never deleted."""
    removed = [bid for bid, _, _ in region.blocks if not (bid == region.root and region.root_fixed)]
    return _lifts(region, set(removed)) + _deletes(region, removed)


# --- what a Zotero note holds that Gamma loses -----------------------------------------

_SAME_TAGS = {"b": "bold", "strong": "bold", "i": "italic", "em": "italic", "u": "underline",
              "s": "strike", "strike": "strike", "del": "strike"}
_COUNTED_TAGS = {"sub", "sup", "code", "pre", "blockquote", "table", "img", "hr",
                 "h1", "h2", "h3", "h4", "h5", "h6"}
_COUNTED_ATTRS = ("data-citation", "data-annotation", "data-attachment-key")


class _Marks(HTMLParser):
    """Counts what a note's HTML holds besides plain structure, and its text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.marks, self.text, self.hidden = Counter(), [], 0

    def handle_starttag(self, tag, attrs):
        self._tag(tag, dict(attrs))

    def handle_startendtag(self, tag, attrs):
        attrs = dict(attrs)
        self._tag(tag, attrs)
        if tag in ("script", "style"):
            self.hidden -= 1

    def _tag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in _SAME_TAGS:
            self.marks[_SAME_TAGS[tag]] += 1
        elif tag in _COUNTED_TAGS:
            self.marks[tag] += 1
        elif tag == "a" and attrs.get("href"):
            self.marks["a"] += 1
        for name in _COUNTED_ATTRS:
            if name in attrs:
                self.marks[name] += 1
        if "math" in str(attrs.get("class") or "").split():
            self.marks["math"] += 1
        for prop, value in _declarations(attrs.get("style")):
            if prop == "text-decoration":
                for word in value.split():
                    if word in ("line-through", "underline"):
                        self.marks["strike" if word == "line-through" else "underline"] += 1
            elif prop in ("color", "background-color"):
                self.marks[f"{prop}:{value}"] += 1
            else:
                self.marks[f"style:{prop}:{value}"] += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def _declarations(style) -> list[tuple[str, str]]:
    out = []
    for part in str(style or "").split(";"):
        prop, sep, value = part.partition(":")
        if sep and prop.strip():
            out.append((prop.strip().lower(), "".join(value.lower().split())))
    return out


def _marks_and_text(html_text: str) -> tuple[Counter, str]:
    parser = _Marks()
    parser.feed(html_text or "")
    parser.close()
    return parser.marks, "".join("".join(parser.text).split())


def note_lossy(html_text: str) -> bool:
    """Whether Gamma would lose part of a Zotero note by writing it back (see
    docs/dev/zotero_sync.md#notes): read into blocks and written out again
    (``note_html(note_tree(html))``), it holds fewer of some mark (a citation,
    an annotation embed, a picture, a subscript, a style, counted in one
    spelling: ``bold`` for ``<b>`` and ``<strong>``) or other text. Paragraphs
    becoming a list do not count: that is how every note Gamma writes is
    shaped. Such a note is never overwritten from Gamma."""
    try:
        tree = note_tree(html_text or "")
        again = note_html(tree) if tree else ""
    except RecursionError:
        return True
    marks, text = _marks_and_text(html_text)
    marks_again, text_again = _marks_and_text(again)
    return text != text_again or any(marks_again.get(k, 0) < n for k, n in marks.items())


# --- the PDF ------------------------------------------------------------------------------

def _data(obj) -> dict:
    if not isinstance(obj, dict):
        return {}
    return obj["data"] if isinstance(obj.get("data"), dict) else obj


def _trashed(obj) -> bool:
    return bool((obj or {}).get("deleted") or _data(obj).get("deleted"))


def primary(attachments, item_url: str = ""):
    """The attachment Zotero's reader opens for an item, as
    ``Zotero.Item.getBestAttachments`` picks it (see
    docs/dev/zotero_sync.md#pdfs): among the non-trashed attachments of a file
    link mode (stored, downloaded from a page, or a linked file) holding a PDF,
    the one whose URL is the item's, then the oldest, then the smallest key.
    Takes and returns whole objects or their ``data``; None when none
    qualifies."""
    url = str(item_url or "").strip()
    best = None
    for obj in attachments or ():
        data = _data(obj)
        if not is_pdf_attachment(data) or _trashed(obj):
            continue
        rank = (0 if url and str(data.get("url") or "").strip() == url else 1,
                str(data.get("dateAdded") or "\uffff"), str(data.get("key") or ""))
        if best is None or rank < best[0]:
            best = (rank, obj)
    return best[1] if best else None


def fetchable(attachment, files_access: bool) -> tuple[bool, str]:
    """``(True, "")`` when Gamma can download an attachment's file: a stored
    file (``imported_*``) Zotero has an MD5 for, and a key with file access.
    Else ``(False, reason)``: ``LINKED`` (a file on the user's disk),
    ``UNAVAILABLE`` (Zotero Storage does not hold it: WebDAV, file sync
    off, no file), ``NO_ACCESS`` (the key may not read files). A 404 on the
    download itself is the caller's ``UNAVAILABLE``."""
    data = _data(attachment)
    mode = data.get("linkMode")
    if mode == "linked_file":
        return False, LINKED
    if mode not in STORED_LINK_MODES or not data.get("md5"):
        return False, UNAVAILABLE
    if not files_access:
        return False, NO_ACCESS
    return True, ""


# --- first link: matching -----------------------------------------------------------------

class Match(NamedTuple):
    page_id: str
    key: str
    reason: str  # one of MATCH_REASONS


_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.I)
_ARXIV_RE = re.compile(_ARXIV_ID, re.I)


def norm_doi(value) -> str:
    """A DOI in one spelling: the DOI a string names (a ``doi:`` or
    ``https://doi.org/`` prefix aside), lower-cased; "" for none."""
    m = _DOI_RE.search(str(value or ""))
    return m.group(0).rstrip(".,;)]}").lower() if m else ""


def norm_arxiv(value) -> str:
    """An arXiv id without its version, lower-cased; "" for none."""
    m = _ARXIV_RE.search(str(value or ""))
    return m.group(0).lower() if m else ""


def _identity(meta, title: str = "") -> tuple[str, str, str, str]:
    """``(doi, arxiv, title key, year)`` a record is matched by."""
    meta = meta if isinstance(meta, dict) else {}
    year = re.search(r"\d{4}", str(meta.get("year") or ""))
    return (norm_doi(meta.get("doi")), norm_arxiv(meta.get("arxiv_id")),
            _work_title(meta.get("title") or title), year.group(0) if year else "")


def _md5s(item: dict) -> set:
    values = item.get("md5s")
    if values is None:
        values = [item.get("md5")]
    return {str(v).lower() for v in values or () if v}


def match(pages, items, *, library: str) -> list[Match]:
    """Pairs of an existing workspace's pages and a library's items for a first
    link (see docs/dev/zotero_sync.md#first-link-and-force): one to one,
    greedily by strength — the page's ``zotero_key`` naming this ``library``
    and the item's key, then DOI, then arXiv id (no version), then the page's
    PDF MD5 among the item's PDF MD5s, then title and year, only when neither
    side has a DOI or an arXiv id and the two do not both hold PDFs that differ
    — each page and item used once, ties going to the oldest page, then the
    oldest item. ``pages``: ``{"id", "created_at", "title", "meta",
    "zotero_key", "md5"}`` (``meta`` and ``zotero_key`` may sit in ``"props"``
    instead); ``items``: ``{"key", "data", "md5"}`` or ``"md5s"`` (its PDF
    attachments'). Pages and items left over are new on their side."""
    by_key, item_ids, item_rank = {}, {}, {}
    for item in items or ():
        data = item.get("data") or {}
        key = item.get("key") or data.get("key")
        if not key or key in by_key:
            continue
        by_key[key] = item
        meta = zotero_fields.meta_from_item(data)
        item_ids[key] = (*_identity(meta), _md5s(item))
        item_rank[key] = (str(data.get("dateAdded") or item.get("date_added") or "\uffff"), key)
    index = {}
    for key, (doi, arxiv, name, year, md5s) in item_ids.items():
        for level, value in (("doi", doi), ("arxiv", arxiv), ("title", name and year and (name, year))):
            if value:
                index.setdefault((level, value), []).append(key)
        for md5 in md5s:
            index.setdefault(("md5", md5), []).append(key)
    candidates = []
    for page in pages or ():
        props = page.get("props") or {}
        pid = page.get("id")
        if not pid:
            continue
        rank = (str(page.get("created_at") or "\uffff"), pid)
        meta = page.get("meta", props.get("meta"))
        doi, arxiv, name, year = _identity(meta, page.get("title") or "")
        md5 = str(page.get("md5") or "").lower()
        linked = parse_item_uri(page.get("zotero_key", props.get("zotero_key")))
        found = []
        if linked and linked[0] == library and linked[1] in by_key:
            found.append(("zotero_key", linked[1]))
        for level, value in (("doi", doi), ("arxiv", arxiv), ("md5", md5)):
            if value:
                found += [(level, key) for key in index.get((level, value), ())]
        if name and year and not doi and not arxiv:
            for key in index.get(("title", (name, year)), ()):
                k_doi, k_arxiv, _, _, k_md5s = item_ids[key]
                if not k_doi and not k_arxiv and (not md5 or not k_md5s or md5 in k_md5s):
                    found.append(("title", key))
        candidates += [(MATCH_REASONS.index(level), rank, item_rank[key], pid, key, level) for level, key in found]
    out, used_pages, used_items = [], set(), set()
    for _, _, _, pid, key, level in sorted(candidates):
        if pid not in used_pages and key not in used_items:
            used_pages.add(pid)
            used_items.add(key)
            out.append(Match(pid, key, level))
    return out


def paths(nodes: dict) -> dict[str, tuple]:
    """``{id: names from the top}`` of folders or collections given as
    ``{id: {"name", "parent"}}`` (``folder_projection`` /
    ``collection_projection``); a parent the set lacks ends the path there."""
    out = {}
    for start in nodes:
        names, seen, at = [], set(), start
        while at in nodes and at not in seen:  # a cycle ends where it closes
            seen.add(at)
            names.append(str(nodes[at].get("name") or ""))
            at = nodes[at].get("parent") or ""
        out[start] = tuple(reversed(names))
    return out


def match_paths(folders: dict, collections: dict) -> list[tuple[str, str]]:
    """``[(folder id, collection key)]`` for a first link: folders and
    collections whose paths of names (``paths``) are alike, ignoring case, one
    to one, in the order given (oldest first)."""
    free = {}
    for key, path in collections.items():
        free.setdefault(tuple(_fold(n) for n in path), []).append(key)
    out = []
    for fid, path in folders.items():
        keys = free.get(tuple(_fold(n) for n in path))
        if keys:
            out.append((fid, keys.pop(0)))
    return out


# --- which pages travel --------------------------------------------------------------------

def is_paper(props) -> bool:
    """Whether a page is a paper: it has a record with a title."""
    meta = (props or {}).get("meta")
    return isinstance(meta, dict) and bool(str(meta.get("title") or "").strip())


def _when(value) -> datetime | None:
    return value if isinstance(value, datetime) else parse_stamp(value) if value else None


def wants_item(props, *, now, send_unrecorded: bool, created_at="") -> str:
    """What a Gamma page without an item becomes in Zotero (see
    docs/dev/zotero_sync.md#what-syncs): ``WANT_PAPER`` for a paper
    (``is_paper``); for a page with a stored PDF and no record, ``WANT_WAIT``
    while its lookup may still come (no ``meta``, no ``meta_error``, made less
    than ``LOOKUP_WAIT_S`` before ``now``), then ``WANT_DOCUMENT`` when the
    link sends such pages (``send_unrecorded``), else ""; "" for any other page
    (a note page stays in Gamma). ``now`` and ``created_at`` (the page root's)
    are datetimes or ``page_now`` stamps."""
    props = props or {}
    if is_paper(props):
        return WANT_PAPER
    if not props.get("doc_id"):
        return ""
    if not props.get("meta") and not props.get("meta_error"):
        made, at = _when(created_at), _when(now)
        if made and at and (at - made).total_seconds() < LOOKUP_WAIT_S:
            return WANT_WAIT
    return WANT_DOCUMENT if send_unrecorded else ""


def standalone_gets_parent(meta) -> bool:
    """Whether a standalone PDF's page has a record worth a parent item in
    Zotero: one with a title that a person made or confirmed (``source``
    "manual", or ``user_verified``), never the automatic lookup's alone."""
    return (isinstance(meta, dict) and bool(str(meta.get("title") or "").strip())
            and (meta.get("source") == "manual" or bool(meta.get("user_verified"))))


# --- readable conflict lines ------------------------------------------------------------------

_FIELD_LABELS = {"title": "Title", "authors": "Authors", "year": "Year", "venue": "Venue", "volume": "Volume",
                 "pages": "Pages", "doi": "DOI", "arxiv_id": "arXiv", "publisher": "Publisher", "isbn": "ISBN",
                 "kind": "Type", "cite_key": "Citation key", "type": "Zotero type", "tags": "Tags",
                 "collections": "Collections", "name": "Name", "parent": "Parent"}
_ZOTERO_COLOURS = {"#ffd400": "yellow", "#ff6666": "red", "#5fb236": "green", "#2ea8e5": "blue",
                   "#a28ae5": "purple", "#e56eee": "magenta", "#f19837": "orange", "#aaaaaa": "gray"}
_HUES = [(colorsys.rgb_to_hsv(*parse_color(c)[:3])[0], name) for c, name in _ZOTERO_COLOURS.items()
         if name != "gray"]
_ANNOTATION_WORDS = {"underline": "underline", "image": "area", "ink": "ink", "note": "note", "text": "text"}
_TAG_RE = re.compile(r"<[^>]*>")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _short_name(name: str) -> str:
    """A name with its given names as initials (Ashish Vaswani → A.
    Vaswani); a single name stays whole."""
    parts = str(name or "").split()
    return " ".join([f"{p[0]}." for p in parts[:-1]] + parts[-1:]) if len(parts) > 1 else " ".join(parts)


def _type_words(value: str) -> str:
    """An item type in words (journalArticle → Journal article)."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or "")).lower()
    return words[:1].upper() + words[1:]


def field_line(field: str, value) -> str:
    """One side of a field conflict as a person reads it: "Year: 2017",
    "Authors: A. Vaswani, N. Shazeer, …", "Type: Journal article"; "(none)" for
    an empty value, a long one clipped to ``LINE_MAX``."""
    label = _FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()
    if field == "authors" and isinstance(value, (list, tuple)):
        names = [_short_name(a) for a in value if str(a or "").strip()]
        shown = ", ".join(names[:AUTHORS_SHOWN]) + (", …" if len(names) > AUTHORS_SHOWN else "")
    elif isinstance(value, (list, tuple, set)):
        shown = ", ".join(str(v) for v in value if str(v).strip())
    elif field in ("kind", "type"):
        shown = _type_words(value)
    else:
        shown = "" if value is None else str(value)
    return _clip(f"{label}: {shown.strip() or '(none)'}", LINE_MAX)


def colour_name(color) -> str:
    """A colour as a word: the one of Zotero's eight nearest in hue (a pale
    wash reads by its hue), gray for one with little colour; "" for none."""
    rgba = parse_color(color)
    if not rgba:
        return ""
    hue, saturation, value = colorsys.rgb_to_hsv(*rgba[:3])
    if saturation < 0.12 or value < 0.15:
        return "gray"
    return min(_HUES, key=lambda named: min(abs(hue - named[0]), 1 - abs(hue - named[0])))[1]


def annotation_line(projection: dict, page_label="") -> str:
    """One side of an annotation conflict as a person reads it:
    "yellow · p. 3 · “the quote” · the comment", the kind named for any
    annotation but a highlight, the quote and comment clipped."""
    projection = projection or {}
    kind = str(projection.get("type") or "highlight")
    quote = _clip(projection.get("text"), QUOTE_SHOWN)
    comment = _clip(html_mod.unescape(_TAG_RE.sub("", str(projection.get("comment") or ""))), QUOTE_SHOWN)
    parts = [colour_name(projection.get("color")), f"p. {page_label}" if str(page_label or "").strip() else "",
             _ANNOTATION_WORDS.get(kind, "" if kind == "highlight" else kind),
             f"“{quote}”" if quote else "", comment]
    return _clip(" · ".join(p for p in parts if p), LINE_MAX) or "(empty)"
