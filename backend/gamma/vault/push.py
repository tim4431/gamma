"""**Push**, disk to Gamma (gamma/vault/sync.py runs it; gamma/vault/pages.py
takes each changed and new file into its page). The scan's files against
the rows (``_classify``): added, changed (a stat that moved, or the racy
rule, then the hash), removed, moved (``disk.pair_moves``, two names swapped
too, a name changed only in case whatever the text), and a directory renamed
is one folder renamed; a vault that does not push everything makes and
removes nothing, its rows following the files moved while Gamma keeps its
places, and says which note went. A file missing is removed once two scans
``store.MISSING_S`` apart agree and an ``lstat`` just before says so (and no
file of its kind waits to be read: that may be it, moved), a note emptied is
held the same way, and a round whose removals since the last quiet one, the
notes emptied counting, pass ``REMOVE_SHARE`` of ``REMOVE_FLOOR`` files or
more stops before it writes anything (``_held``) until asked to go on. A
file the scan leaves out for a fixed rule (a link above it, a case clash)
but is there is never a removal; one the vault's ``.gammaignore`` leaves out
is deselected (``_apply_deselections``).

A force pull reads only where the disk's files went (``_follow_moves``), a
round of pull alone only the notes changed on disk (``_report_kept``).
"""

import time

from ..blocks_store import FOLDERS, PARENT_PAGE
from ..documents import formats
from ..documents.model import document, stored_name
from ..logbuf import log
from ..ops import CYCLE, OpError
from . import disk, kinds, pages, store
from .remote import Edit, Waiting
from .round import LEFT_OUT, Read, _base, _dir, _fields, _gamma_id, _kind_of, _Rows, _stem, _text, _token, _why


REMOVE_SHARE = 0.5         # removals since the last quiet round past this share of the files hold a round
REMOVE_FLOOR = 10          # of this many files at least


def _blank(text: str | None) -> bool:
    """Whether a note's text holds nothing: blanks, line ends, a byte order
    mark (Notepad's empty "UTF-8 with BOM")."""
    return text is not None and not text.lstrip("﻿").strip()


# --- push: the disk's changes --------------------------------------------------------------

def _push(rnd) -> bool:
    """The disk's changes into Gamma; False when the guard held the round."""
    if rnd.only:
        for path, row in list(rnd.rows.items()):
            if row["page_id"] == rnd.only:
                rnd._each(path, _push_one, rnd, path)
        return True
    plan = _classify(rnd)
    if any(plan[k] for k in ("renames", "moves", "changed", "new", "removed", "deselected")):
        rnd.remote.layout()      # ``Stopped`` when the vault's folder went: nothing is filed into it
    if _held(rnd, plan):
        return False
    rnd.total += len(plan["changed"]) + len(plan["new"])
    rnd.bytes_total += rnd._read_bytes(plan["changed"] + plan["new"])
    rnd._report()
    _apply_dirs(rnd, plan)
    _apply_moves(rnd, plan)
    for path in plan["changed"]:
        rnd._counted(path, pages._push_file, rnd, path, rnd.scan.files[path])
    unplaced, rnd.unplaced = rnd.unplaced, []
    for new, old, token in unplaced:      # a change pushed just now may have brought their page back
        rnd._each(new, _refile, rnd, new, old, token)
    pages._push_new(rnd, plan["new"])
    _apply_removals(rnd, plan)
    _apply_deselections(rnd, plan)
    if rnd.work:
        rnd.remote.drop_pageless(rnd.root)
    rnd._report()
    return True


def _follow_moves(rnd) -> None:
    """A force pull's reading of the disk: where its files went, and no
    more. The rows follow each directory renamed and each file moved,
    Gamma keeping its places (``places``), so that Gamma's text is
    written where the note is now, never again where it was beside the
    moved one, which the next round would read as a new page."""
    plan = _classify(rnd)
    _apply_dirs(rnd, plan)
    _apply_moves(rnd, plan)


def _push_one(rnd, path: str) -> None:
    """A file of the page's round, pushed when its stat moved."""
    row, found = rnd.rows[path], rnd._stat(path)
    if found is None or found.placeholder or not (disk.moved(row, found) or rnd.force == "push"):
        return
    cls = kinds.by_kind(row["kind"])
    if cls is kinds.NOTES and not rnd.push_all:
        _kept_note(rnd, path, row, found)
    elif cls.compared:
        pages._push_file(rnd, path, found)


def _classify(rnd) -> dict:
    """The scan against the rows, read and nothing written: directories
    renamed, files moved (one renamed only in case whatever its text,
    ``_recased``), changed, new, missing, removed, and the notes emptied,
    which go as a removal goes (the module doc). The rows the scan meets
    (``known``) are those of a class that compares, of the class the path
    has now: a kept folder's picture row at a path a work folder reads as
    a file is not known, so the path is new, and its page's row takes the
    old one's place (``store.put_pending``). A removal waits while a
    file of its kind waits to be read: that file may be it, moved. A vault
    that does not push everything makes and removes nothing: its rows
    follow the files moved, a new file read only to find one, and a note
    gone is said, Gamma's changes to it waiting for a force pull. A file
    there that the vault's rules leave out is ``deselected``; one the scan
    leaves out for another reason (a link above it, a case clash, a name
    Windows refuses) is no removal at all."""
    found = rnd.scan.files
    renames = _dir_renames(rnd)

    def carried(path):
        for old, new in renames:
            if path.startswith(f"{old}/"):
                return new + path[len(old):]
        return path
    work = bool(rnd.work)
    known = {carried(p): row for p, row in rnd.rows.items()
             if kinds.by_kind(row["kind"]).compared and kinds.class_of(p, work=work) is kinds.by_kind(row["kind"])}
    rnd.origin = {carried(p): p for p in rnd.rows}
    changed = [p for p in found if p in known and not found[p].placeholder
               and (rnd.force == "push" or disk.moved(known[p], found[p]))
               and (rnd.push_pdfs or known[p]["kind"] == kinds.NOTES.name)]   # push off: a PDF changed is not read
    gone, left_out, deselected = [], [], []     # a file the scan leaves out that is there is never a removal
    listed = {p.casefold() for p in found}      # Note.md found as note.md is renamed, not left out
    for p in known:
        if p not in found:
            if not rnd._tracked(p) and rnd._there(p):
                deselected.append(p)
            elif p.casefold() not in listed and rnd._there(p):
                left_out.append(p)
            else:
                gone.append(p)
    new = [p for p in found if p not in known and not found[p].placeholder]
    if not rnd.push_all:
        new = [p for p in new if found[p].kind in {known[g]["kind"] for g in gone}]
    unread = set()
    for p in new + changed:
        try:
            rnd.reads[p] = rnd._read(p, found[p])
        except Waiting:
            rnd.counts["waiting"] += 1
            unread.add(found[p].kind)
        except OSError as e:
            rnd._keep(p, f"cannot be read: {_why(e)}")
            unread.add(found[p].kind)
    cased = _recased(rnd, gone, new, known)
    olds = [rnd._known(p, known[p]) for p in gone + changed if p not in cased.values()]
    news = [rnd._known_read(rnd.reads[p]) for p in new + changed if p in rnd.reads and p not in cased]
    pairs, capped = disk.pair_moves(olds, news)
    pairs.update(cased)
    if capped:
        rnd.status["capped"] = True
        log.info(f"[vault] {rnd.root}: the search for moved notes stopped at {disk.MOVE_COMPARES} comparisons")
    sources = set(pairs.values())
    pairs = {f: g for f, g in pairs.items() if f not in known or f in sources}   # never onto a file that stays
    pairs.update(_papers_notes(rnd, pairs, known, gone, new))
    pairs.update(_named_notes(rnd, pairs, known, gone, new))
    sources = set(pairs.values())
    displaced = {p for p in changed if p in sources and p not in pairs and p in rnd.reads}
    moving = sorted(pairs)
    new = sorted({p for p in new if p in rnd.reads and p not in pairs} | displaced)
    changed = sorted({p for p in changed if p in rnd.reads and p not in sources} | set(moving))
    missing, removed, empty, now = [], [], set(), time.time()
    for p in gone:
        if p in sources:
            continue
        if not rnd.push_all:
            if known[p]["kind"] == kinds.NOTES.name and rnd.force != "pull":       # a force pull writes it
                rnd._keep(p, "not on this computer: a force pull writes Gamma's page here again", True)
        elif not known[p]["missing_since"]:
            missing.append(p)
        elif store.missing_long(known[p], now) and known[p]["kind"] not in unread:
            removed.append(p)
    for p in list(changed):     # a note emptied whose base had text waits as a missing one does
        read, row = rnd.reads[p], known.get(p)
        if (rnd.push_all and read.kind == kinds.NOTES.name and _blank(read.text) and p not in pairs and row
                and not _blank(row["text"] or "")):
            empty.add(p)
            if not row["missing_since"]:
                missing.append(p)
                changed.remove(p)
            elif not store.missing_long(row, now):
                changed.remove(p)
    back = [p for p in [*found, *left_out, *deselected] if p in known and known[p]["missing_since"] and p not in empty]
    return {"renames": renames, "moves": [(f, pairs[f]) for f in moving], "changed": changed,
            "new": new if rnd.push_all else [], "removed": removed, "missing": missing, "back": back,
            "emptied": sorted(empty & set(changed)), "deselected": sorted(deselected)}


def _recased(rnd, gone: list, new: list, known: dict) -> dict:
    """``{new path: gone path}`` of files renamed only in case: one file,
    whatever its text, never a second row on it. The old spelling names
    the new file still (a disk that ignores case), or its file id does."""
    spelled, out = {p.casefold(): p for p in new if p in rnd.reads}, {}
    for p in gone:
        q = spelled.get(p.casefold())
        same = q and rnd.scan.files[q].file_id not in ("", "0") and rnd.scan.files[q].file_id == known[p]["file_id"]
        if q and (same or rnd._there(p)):
            out[q] = p
    return out


def _papers_notes(rnd, pairs: dict, known: dict, gone: list, new: list) -> dict:
    """``{new note: notes row}`` beyond ``pairs``: a note new on the disk
    that pairs with a paper (``pages._pdf_of``) whose page's notes are gone from
    where they were is those notes moved, however much its text changed
    (a paper and its notes renamed together, the notes edited)."""
    pdfs = {f: known[g] for f, g in pairs.items() if known[g]["kind"] == kinds.PDF.name}
    pdfs.update({p: row for p, row in known.items() if row["kind"] == kinds.PDF.name and p in rnd.scan.files})
    notes_of = {row["page_id"]: p for p, row in known.items() if row["kind"] == kinds.NOTES.name and row["page_id"]}
    out, taken = {}, set(pairs.values())
    for p in new:
        if p in pairs or p not in rnd.reads or rnd.reads[p].kind != kinds.NOTES.name:
            continue
        paper = pdfs.get(pages._pdf_of(rnd, p, list(pdfs)))
        notes = notes_of.get(paper["page_id"]) if paper else None
        if notes in gone and notes not in taken:
            out[p] = notes
            taken.add(notes)
    return out


def _named_notes(rnd, pairs: dict, known: dict, gone: list, new: list) -> dict:
    """``{new note: notes row}`` beyond ``pairs``: a note new on the disk
    whose front matter's ``gamma_id`` (an older client wrote it, and no
    pull writes it) names the page of notes gone from where they were is
    those notes moved."""
    notes_of = {row["page_id"]: p for p, row in known.items()
                if row["kind"] == kinds.NOTES.name and row["page_id"] and p in gone}
    out, taken = {}, set(pairs.values())
    for p in new:
        if p in pairs or p not in rnd.reads or rnd.reads[p].kind != kinds.NOTES.name:
            continue
        notes = notes_of.get(_gamma_id(rnd.reads[p].text))
        if notes and notes not in taken:
            out[p] = notes
            taken.add(notes)
    return out


def _kept_note(rnd, path: str, row: dict, found=None, read: "Read | None" = None) -> None:
    """A note whose stat moved in a vault that does not push notes: kept
    and reported, unless its bytes are the base's after all, whose stat
    and time of reading it then takes (``pages._push_file``'s rule), so that the
    next quiet round costs a stat. ``read``: the note as this round read
    it already; else it is read now, as the scan ``found`` it."""
    try:
        read = read or rnd.reads.get(path) or rnd._read(path, found)
    except Waiting:
        rnd.counts["waiting"] += 1
        return
    except OSError:
        return
    if read.hash == row["hash"]:
        rnd._put(path, row, **read.stat())
        return
    rnd._hold(path)
    rnd._keep(path, "changed on this computer and not pushed; a force pull replaces it", True)


def _report_kept(rnd) -> None:
    """In a round of pull alone, the notes changed on disk, reported."""
    for path, row in rnd.rows.items():
        found = rnd.scan.files.get(path)
        if row["kind"] == kinds.NOTES.name and found and not found.placeholder and disk.moved(row, found):
            _kept_note(rnd, path, row, found)


# --- the guard -----------------------------------------------------------------------------

def _held(rnd, plan: dict) -> bool:
    """Whether the round stops before it writes anything: its removals, a
    note emptied counting as one, and in a work folder each page whose
    files its ``.gammaignore`` leaves out, and those since the last quiet
    round pass ``REMOVE_SHARE`` of the files the vault had then,
    ``REMOVE_FLOOR`` or more. A file missing for the first time is noted
    all the same."""
    origin = rnd.origin
    if plan["missing"]:
        store.mark_missing(rnd.id, [origin.get(p, p) for p in plan["missing"]])
    if plan["back"]:
        store.found_again(rnd.id, [origin.get(p, p) for p in plan["back"]])
    back = {origin.get(p, p) for p in plan["back"]}
    waiting = plan["missing"] or [p for p, r in rnd.rows.items() if r["missing_since"] and p not in back]
    compared = sum(1 for r in rnd.rows.values() if kinds.by_kind(r["kind"]).compared)   # a picture's row is no file's
    total = rnd.state.get("total") or compared
    leaving = {rnd.rows[origin.get(p, p)]["page_id"] or p for p in plan["deselected"]} if rnd.work else set()
    going = len(plan["removed"]) + len(plan["emptied"]) + len(leaving)
    removals = rnd.state.get("removed", 0) + going
    if going and not rnd.confirm and total >= REMOVE_FLOOR and removals > REMOVE_SHARE * total:
        rnd.status["held"] = {"removals": removals, "total": total}
        return True
    if not going and not waiting:
        rnd.state.update(removed=0, total=compared + len(plan["new"]))
    else:
        rnd.state.update(removed=0 if rnd.confirm else removals, total=total)
    return False


# --- push: directories ---------------------------------------------------------------------

def _dir_renames(rnd) -> list:
    """``[(old, new)]``: directories gone from where they were and found
    elsewhere, by their file id, else holding most of their files under
    the same names and sizes. Shallowest first; what is below one goes
    with it."""
    recorded, found = store.dirs(rnd.id), rnd.scan.dirs
    olds = sorted((p for p in recorded if p not in found), key=lambda p: p.count("/"))
    news = sorted((p for p in found if p not in recorded), key=lambda p: p.count("/"))
    out = []
    for old in olds:
        if any(old.startswith(f"{a}/") for a, _ in out):
            continue
        file_id = recorded[old]["file_id"]
        hit = next((n for n in news if file_id and found[n] == file_id), None)
        if hit is None:
            held = {p[len(old) + 1:]: row for p, row in rnd.rows.items() if p.startswith(f"{old}/")}
            for n in news:
                same = sum(1 for rel, row in held.items()
                           if (f := rnd.scan.files.get(f"{n}/{rel}")) is not None and f.size == row["size"])
                if held and same * 2 >= len(held):
                    hit = n
                    break
        if hit is not None:
            out.append((old, hit))
            news.remove(hit)
    return out


def _apply_dirs(rnd, plan: dict) -> None:
    """A directory renamed or moved on disk: its folder renamed or moved
    where the vault pushes everything (a failure is that directory's),
    and one rewrite of the rows below it. Where it does not, Gamma's
    folders stay, and pull names the directory by its folder again."""
    recorded = store.dirs(rnd.id)
    for old, new in plan["renames"]:
        folder_id = recorded[old]["folder_id"]
        if folder_id and rnd.places:
            rnd._each(new, _refile_dir, rnd, folder_id, old, new)
        store.repath(rnd.id, old, new)
        store.put_dir(rnd.id, new, folder_id, rnd.scan.dirs.get(new, ""))
        rnd.remote.relink(str(rnd.root / old), str(rnd.root / new))
        rnd.counts["renamed"] += 1
    if plan["renames"]:
        rnd.rows = _Rows(store.files(rnd.id))


def _refile_dir(rnd, folder_id: str, old: str, new: str) -> None:
    """The folder of a directory that went from ``old`` to ``new`` on disk,
    moved and named as the disk has it."""
    if _dir(old) != _dir(new):
        _move_folder(rnd, folder_id, _dir(new))
    if new.rsplit("/", 1)[-1] != old.rsplit("/", 1)[-1]:
        rnd.remote.folder_op({"op": "set", "id": folder_id, "content": new.rsplit("/", 1)[-1]})


def _move_folder(rnd, folder_id: str, directory: str) -> None:
    """The folder moved into the one of ``directory``. Where Gamma moved
    that one below it meanwhile (``B`` under ``A`` while the disk moved
    ``A`` into ``B``), the move would be a loop: the disk's places win, as
    the file's do for blocks, so the folders of ``directory`` and the
    directories above it first go where the disk has them, from the top."""
    move = {"op": "move", "id": folder_id, "parent": rnd._folder_for(directory)}
    try:
        rnd.remote.folder_op(move)
    except OpError as e:
        if e.conflict != CYCLE or not directory:
            raise
        names = directory.split("/")
        for k in range(1, len(names) + 1):
            above = "/".join(names[:k])
            rnd.remote.folder_op({"op": "move", "id": rnd._folder_for(above), "parent": rnd._folder_for(_dir(above))})
        rnd.remote.folder_op(move)


# --- push: moves --------------------------------------------------------------------------

def _apply_moves(rnd, plan: dict) -> None:
    """Files moved on disk: their rows follow (names swapped go through
    names no file has), each in the journal until its page is filed and
    named by its file (``_refile``), which a round that dies leaves to
    the next."""
    moves = plan["moves"]
    if not moves:
        return
    token = _token()
    store.repath_files(rnd.id, moves, token)
    rnd.rows = _Rows(store.files(rnd.id))
    for new, old in moves:
        rnd._each(new, _refile, rnd, new, old, token)
        rnd.counts["renamed"] += 1


def _refile(rnd, new: str, old: str, token: str) -> None:
    """A move's page filed and named by it (``_moved``), its journal let
    go then; one whose page waits in Recently deleted keeps it, tried
    again once this round's changes are pushed (a sub-page's file renamed
    while the page it is under was trashed, a change of that page's file
    bringing both back) and by the rounds after (``journal._recover``)."""
    if _moved(rnd, new, old):
        store.drop(rnd.id, new, token)
    elif (new, old, token) not in rnd.unplaced:
        rnd.unplaced.append((new, old, token))


def _moved(rnd, new: str, old: str) -> bool:
    """A file moved on disk: its page filed in the new directory's folder
    in place of the old one, the same index of its list (a sub-page's
    file moved makes it a library page), and named by the new stem. The
    rows record the first filing pull then reads (``Round._first``), so pull
    never moves the file again: at the top of a vault of the whole library,
    which is no folder, that is the page's next one. A page in Recently
    deleted whose file changed comes back first (``pages._push_note``'s rule),
    so it is named and filed by it too; one whose file did not waits
    there: False. Where the vault does not push everything, and in a
    force pull, only the rows follow: Gamma keeps its places. A work
    folder's file is filed first in its new directory's folder, whatever
    moved, then in its folders outside every work folder, and takes the
    file's name; its title follows the stem only while it is not in the
    library, whose paper's title is free. A file of another format is
    titled by its leaf, and one whose new name says another format or
    extension (``notes.txt`` to ``notes.log``, ``a.txt`` to ``a.png``)
    has its document settled again with the same hash, in the same
    ``set``, its linked row under the new name first and the old one gone
    after (``_file_doc``); a rename that changes the class (``a.txt`` to
    ``a.md``) never pairs, and is a removal and a new page."""
    row = rnd.rows[new]
    cls = kinds.by_kind(row["kind"])
    cls.moved(rnd, new, old, row)
    page_id = row["page_id"]
    if not page_id or not rnd.places:
        return True
    state = rnd.remote.state(page_id)
    if state == "trashed" and new in rnd.reads and rnd.reads[new].hash != row["hash"]:
        rnd.remote.restore(page_id)
        state = "live"
    if state != "live":
        return state is None
    props, folder, free = {}, row["folder"] or "", False
    if rnd.work:
        folders, parent = rnd.remote.filing(page_id)
        layout, tree = rnd.remote.layout(page_id), rnd.remote.work_tree()
        props[FOLDERS] = [rnd._folder_for(_dir(new))] + [f for f in folders if not tree.work_of(f)]
        if parent:
            props[PARENT_PAGE] = None
        if cls is kinds.PDF or kinds.PDF.name not in rnd.rows.of(page_id):
            props["file_name"] = new.rsplit("/", 1)[-1]
        folder = rnd._first(props[FOLDERS], layout.folders)
        free = page_id in layout.pages and layout.pages[page_id].library
    elif _dir(new) != _dir(old):
        folders, parent = rnd.remote.filing(page_id)
        was, now = rnd._folder_for(_dir(old)), rnd._folder_for(_dir(new))
        filed = list(folders)
        if was and was in filed:
            filed[filed.index(was)] = now
        elif now:
            filed.insert(0, now)
        props[FOLDERS] = [f for f in dict.fromkeys(filed) if f] or None
        if parent:
            props[PARENT_PAGE] = None
        folder = rnd._first(props[FOLDERS] or [], rnd.remote.layout(page_id).folders)
    title = row["title"]
    notes = rnd.rows.of(page_id).get(kinds.NOTES.name)
    unlink = ""
    if cls is kinds.FILE:
        leaf, was = new.rsplit("/", 1)[-1], old.rsplit("/", 1)[-1]
        if leaf != was and not free:
            title = leaf
        doc = _file_doc(rnd, new, old, row)
        if doc is not None:
            props["doc"] = doc
            if row["hash"]:
                seen = (row["hash"], row["size"], row["mtime_ns"], row["file_id"])
                rnd.remote.link_doc(str(rnd.root / new), seen, page_id, doc["ext"])
                if doc["ext"] != formats.vault_extension(was):
                    unlink = stored_name(row["hash"], formats.vault_extension(was))
    elif _stem(new) != _stem(old) and (cls is kinds.NOTES or not notes) and not free:
        if not (cls is kinds.NOTES and _fields(row["text"]).get("title")):
            title = _stem(new)
    before, after = rnd.remote.set_page(page_id, title=title if title != row["title"] else None, props=props or None)
    if unlink:
        rnd.remote.unlink_doc(str(rnd.root / new), unlink)
    level = row["seq"] == before
    for path, at_row in rnd.rows.of(page_id).values():
        rnd._put(path, at_row, title=title, folder=folder,
                 seq=after if level and at_row["seq"] == before else at_row["seq"])
    return True


def _file_doc(rnd, new: str, old: str, row: dict) -> dict | None:
    """The document of a work folder's file moved from ``old`` to ``new``
    when the new leaf says another format or extension than the old one
    did (``kinds.file_format`` with the file's first bytes, which the
    rename kept; ``formats.vault_extension``), the hash kept: a listed
    file's with no bytes. None when they say the same."""
    leaf, was = new.rsplit("/", 1)[-1], old.rsplit("/", 1)[-1]
    try:
        with open(disk.path(rnd.root, new), "rb") as f:
            head = f.read(formats.SNIFF_BYTES)
    except OSError:
        head = None
    shape = (kinds.file_format(leaf, head), formats.vault_extension(leaf))
    if shape == (kinds.file_format(was, head), formats.vault_extension(was)):
        return None
    if row["hash"]:
        return document(row["hash"], name=leaf, at="path", format=shape[0], ext=shape[1], size=row["size"])
    return document("", name=leaf, at="listed", format=shape[0], ext=shape[1], size=row["size"])


# --- push: removed files -------------------------------------------------------------------

def _apply_removals(rnd, plan: dict) -> None:
    """Files gone from two scans and gone still (``lstat``): a page whose
    every file went goes to Recently deleted, its rows kept as tombstones;
    a PDF gone leaves its page reading a missing file; a paper's notes gone
    take their plain blocks, its highlights staying. A directory gone goes
    as a folder only when no page is filed in it; a work folder's
    directory its ``.gammaignore`` leaves out is not gone."""
    by_page = {}
    for path in plan["removed"]:
        if not rnd._gone(path):
            store.found_again(rnd.id, [path])
            continue
        by_page.setdefault(rnd.rows[path]["page_id"], []).append(path)
    for page_id, paths in by_page.items():
        rnd._each(paths[0], _removed, rnd, page_id, paths)
    for path, row in list(rnd.rows.items()):      # a kept folder's picture gone: its row, no page's, goes with it
        if not kinds.by_kind(row["kind"]).compared and path not in rnd.scan.images and not rnd._there(path):
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
    if not rnd.push_all:
        return
    recorded = store.dirs(rnd.id)
    live = list(store.files(rnd.id))
    for directory in sorted((d for d in recorded if d not in rnd.scan.dirs), key=len, reverse=True):
        if any(p.startswith(f"{directory}/") for p in live) or rnd.work and rnd._there(directory):
            continue
        folder_id = recorded[directory]["folder_id"]
        if not folder_id or rnd.remote.drop_folder(folder_id):
            store.forget_dir(rnd.id, directory)


def _removed(rnd, page_id: str, paths: list) -> None:
    stays = [p for _kind, (p, _row) in rnd.rows.of(page_id).items() if p not in paths]
    if not page_id or rnd.remote.state(page_id) is None:
        for path in paths:
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
        return
    if not stays:
        when, token, buried, taken = disk.stamp(), _token(), [], rnd._tomb_paths()
        for path in paths:
            store.put_pending(rnd.id, path, rnd.rows[path]["kind"], {"op": "trash", "token": token, "disk": True})
            trash = disk.trash_path(rnd.root, path, when, taken)
            store.bury(rnd.id, path, trash)
            buried.append(trash)
            taken.add(trash)
        rnd.remote.trash(page_id)
        for trash in buried:
            store.put(rnd.id, trash, pending=None)
        for path in paths:
            rnd.rows.pop(path, None)
        rnd.counts["removed"] += len(paths)
        return
    for path in paths:
        row = rnd.rows.pop(path)
        if kinds.by_kind(row["kind"]) is kinds.NOTES:
            _notes_gone(rnd, page_id, row)
        store.forget(rnd.id, path)
        rnd.counts["removed"] += 1


def _apply_deselections(rnd, plan: dict) -> None:
    """Files there that the vault's ``.gammaignore`` leaves out now: in a
    work folder each page whose files all left goes to Recently deleted,
    its files where they are (``_deselected``); in a kept folder, whose
    source Gamma is, the row is forgotten and the page stays."""
    by_page = {}
    for path in plan["deselected"]:
        row = rnd.rows.get(path)
        if row is None:
            continue
        if not rnd.work:
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
            continue
        by_page.setdefault(row["page_id"], []).append(path)
    for page_id, paths in by_page.items():
        rnd._each(paths[0], _deselected, rnd, page_id, paths)


def _deselected(rnd, page_id: str, paths: list) -> None:
    """A work folder's page whose files ``paths`` the selection left: with
    every file of the page gone, the page to Recently deleted first, then
    each row an in-place tombstone (``store.bury_in_place``), the
    file never moved, so that a round that dies in between leaves a live
    row the next deselects again, or a tombstone of a live page, which
    ``pull._restore_file`` sends back. One of two files left out (a hand edit:
    the dialog keeps a paper's two together) is forgotten, its page
    keeping the other."""
    stays = [p for _kind, (p, _row) in rnd.rows.of(page_id).items() if p not in paths]
    if page_id and stays:
        for path in paths:
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
            rnd._keep(path, LEFT_OUT)
        return
    if page_id and rnd.remote.state(page_id) is not None:
        rnd.remote.trash(page_id)
        for path in paths:
            store.bury_in_place(rnd.id, path)
            rnd.rows.pop(path, None)
    else:
        for path in paths:
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
    rnd.counts["removed"] += len(paths)


def _notes_gone(rnd, page_id: str, row: dict) -> None:
    """A paper's notes file gone, its PDF left: the plain blocks the file
    held, unchanged since, go (kept in the PDF's row's ``gone``), the
    highlights stay, and no notes file is made until the page changes."""
    base = _base(row)
    if not base:
        return
    texts = dict(zip(base.ids, (_text(n["g"]) for n in base.nodes)))
    kind_of = _kind_of(page_id)

    def plan(page, _held):
        doomed = {b for b, text in texts.items() if b in page.blocks and kind_of(page.blocks[b]) == "note"
                  and page.blocks[b]["content"] == text}
        ops = []
        for b, block in page.blocks.items():
            if b in doomed or b == page_id or block["parent"] not in doomed:
                continue
            up = block["parent"]
            while up in doomed:
                up = page.blocks[up]["parent"]
            ops.append({"op": "move", "id": b, "parent": up})
        ops += [{"op": "delete", "id": b} for b in sorted(doomed) if page.blocks[b]["parent"] not in doomed]
        return Edit(ops, deleted=sorted(doomed))
    page = rnd.remote.page(page_id)
    before, seq, edit = rnd.remote.apply(page_id, plan)
    pdf = rnd.rows.of(page_id).get(kinds.PDF.name)
    if pdf and page:
        rnd._put(*pdf, gone=store.with_gone(pdf[1].get("gone"), pages._dropped(base, page, edit.deleted)),
                 seq=seq if row["seq"] == before else pdf[1]["seq"])
