"""What a work folder tracks, as its tracking dialog chooses it
(docs/dev/work_folders.md "The tracking dialog"). The dialog names the
vault by its link and its files by their paths in it, never a place on this
computer; its routes are the vault maker's alone (routers/folder_links.py).

``listing`` is the dialog's tree: a directory of the vault listed whole,
breadth first, at most ``LIST_ENTRIES`` entries and ``LIST_SECONDS``, the
directories not reached left closed (``walked`` false, ``capped``). What the
scan never tracks is one closed row each, with why (``never``: a dot
directory, ``node_modules``, a link, a name Windows refuses, two names that
differ only by case, a name that is no UTF-8); what git ignores is marked
(``ignore_rules.hint_stack``), a directory git ignores left closed for the
dialog to list by itself. No file is opened or hashed, and nothing is
written. Each row says whether it is selected under the base asked: what
the vault's ``.gammaignore`` tracks (the default without one), or the preset
of a new work folder (``preset``).

``evaluate`` reads a selection over a fresh walk of the whole directory, the
never-tracked entries left out: the base, then the dialog's changes in order
(a file, a directory whole, every file of a format), then the other file of
a page one of whose two files is selected (a paper's PDF and its notes are
tracked together: ``joined``), then the PDFs of the vaults of one file the
save takes in (``locked``). ``preview`` says what that tracks and which pages
leave; ``write`` puts the ``.gammaignore`` that says it (``ignore_rules.write``,
the shortest) in place of the one the dialog read, a hand-written one kept
in the vault's trash. The link's state and its round are
gamma/folder_links.py's (``save_selection``)."""

import json
import os
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

from .. import vault_disk, vault_store
from ..db import connect_pages_db
from ..documents.formats import format_of
from . import ignore_rules

MAX_FILES = 20_000      # the files a work folder tracks at most, of any format
LIST_ENTRIES = 50_000   # the entries one listing of a vault's directory answers at most
LIST_SECONDS = 3.0      # and how long it walks
FOLLOWED = "followed"   # a row's ``locked``: the PDF of a vault of one file that the save takes in
CHANGED = "The .gammaignore changed on disk since it was read."
_SHOWN = ("pdf", "markdown", "image")   # the formats a preview counts apart, the rest "other"


class SelectionError(Exception):
    """What a dialog's route answers in place of its result: ``status`` and
    ``detail``, and for a ``.gammaignore`` changed since the dialog read it the
    hash of the one there now (``hash``)."""

    def __init__(self, status: int, detail: str, hash: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.hash = status, detail, hash


def rules_of(root: Path) -> ignore_rules.Rules:
    """What a walk outside a round reads the vault ``root`` by: its
    ``.gammaignore``, the default when it has none or one that cannot be read
    (a round stops then, and says why)."""
    try:
        return ignore_rules.read(root)
    except ignore_rules.Unreadable:
        return ignore_rules.DEFAULT


def tracked(root: Path, rel: str) -> bool:
    """Whether the vault ``root`` tracks its file ``rel`` (``rules_of``)."""
    return rules_of(root).tracks(rel)


def cap_of(link: dict) -> int:
    """The files the vault's rounds read at most: its state's ``cap``, which
    the takeover raises for a vault that tracked more, else ``MAX_FILES``."""
    return int(link["state"].get("cap") or MAX_FILES)


def preset(path: str, hint: ignore_rules.GitHint) -> bool:
    """Whether a new work folder's dialog ticks the file ``path`` to start
    with: a PDF or a note, unless it lies inside a directory git ignores. A
    file git ignores by a pattern of names alone is ticked, a build's
    ``main.pdf`` under ``*.pdf`` among them: the PDF a paper is read by."""
    return vault_disk.kind_of(path) in ("notes", "pdf") and not hint.in_ignored_dir(path)


def _root(link: dict) -> Path:
    """The vault's directory, when it is there and holds the vault's marker;
    409 else."""
    root = Path(link["path"])
    try:
        if link["state"].get("vault"):
            vault_disk.check_marker(root, link["state"]["vault"])
        elif not root.is_dir():
            raise vault_disk.Offline("the vault's directory is not there")
    except (vault_disk.Offline, vault_disk.Stopped) as e:
        raise SelectionError(409, str(e)) from e
    return root


def _rules(root: Path) -> ignore_rules.Rules:
    """The vault's ``.gammaignore`` (the default without one); 409 for one
    that cannot be read, which the person puts right on disk first."""
    try:
        return ignore_rules.read(root)
    except ignore_rules.Unreadable as e:
        raise SelectionError(409, f"Its {ignore_rules.NAME} cannot be read: {e.why}.") from e


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _read(root: Path, rel: str) -> list:
    """The entries of the vault's directory ``rel`` ("" its top) the dialog
    shows: ``[path, entry, directory, stub, never]`` each, ``stub`` for an
    iCloud placeholder (listed by the name it stands for), ``never`` why the
    scan never tracks it ("" for none: ``vault_disk.walk``'s fixed rules). The
    names the scan lists nowhere are left out: the temporary ones and
    ``.gammaignore``. A name that is no UTF-8 (a POSIX name Python keeps as
    escapes) is shown with its bytes escaped. ``Offline`` for a directory that
    does not answer."""
    try:
        entries = vault_disk._entries(root / rel if rel else root)
    except OSError as e:
        raise vault_disk.Offline(f"the vault's directory stopped answering at {rel or '.'}: {vault_disk._why(e)}") from e
    prefix, rows = f"{rel}/" if rel else "", []
    for entry in entries:
        try:
            link = entry.is_symlink() or vault_disk._junction(entry)
            directory = not link and entry.is_dir(follow_symlinks=False)
            if not (link or directory or entry.is_file(follow_symlinks=False)):
                continue
        except FileNotFoundError:
            continue
        except OSError as e:
            raise vault_disk.Offline(f"the vault's directory stopped answering at {prefix}{entry.name}: "
                                     f"{vault_disk._why(e)}") from e
        stub = None if directory else vault_disk._ICLOUD_RE.fullmatch(entry.name)
        shown = stub[1] if stub else entry.name
        if vault_disk.temporary(shown) or not (directory or vault_disk.listed(shown)):
            continue
        utf8 = _utf8(shown)
        never = ("encoding" if not utf8 else "link" if link
                 else ("node_modules" if shown == "node_modules" else "dot") if directory and not vault_disk.listed(shown, True)
                 else "name" if vault_disk.refusal(shown) else "")
        name = shown if utf8 else os.fsencode(shown).decode("utf-8", "backslashreplace")
        rows.append([f"{prefix}{name}", entry, directory, bool(stub), never])
    folds = Counter(vault_disk._fold(row[0]) for row in rows if not row[4])
    for row in rows:
        if not row[4] and folds[vault_disk._fold(row[0])] > 1:
            row[4] = "case"
    return rows


def _stat(entry, stub: bool) -> tuple | None:
    """``(size, mtime in ms, placeholder)`` of an entry, from the stat its
    listing has (``DirEntry.stat``: no file id, no file opened, a placeholder
    never hydrated); None for one gone since."""
    try:
        st = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise vault_disk.Offline(f"the vault's directory stopped answering at {entry.name}: {vault_disk._why(e)}") from e
    return (0 if stub else st.st_size), st.st_mtime_ns // 1_000_000, stub or vault_disk._placeholder(st)


@dataclass(slots=True)
class Walk:
    """The vault's directory as a selection reads it: every file the scan
    may track, whatever the rules say (``files``: path -> ``(size, format,
    language)``), and its directories (``dirs``)."""
    files: dict = field(default_factory=dict)
    dirs: set = field(default_factory=set)


def walk(root: Path) -> Walk:
    """A fresh walk of the vault ``root``, the entries the scan never tracks
    left out and not entered (``_read``). ``Offline`` for one that does not
    answer."""
    out, pending = Walk(), [""]
    while pending:
        rel = pending.pop()
        for path, entry, directory, stub, never in _read(root, rel):
            if never:
                continue
            if directory:
                out.dirs.add(path)
                pending.append(path)
                continue
            seen = _stat(entry, stub)
            if seen is not None:
                out.files[path] = (seen[0], *format_of(path))
    return out


def _pages(link: dict) -> dict:
    """``{page id: [path…]}``: the vault's files that are a page's, its notes
    and its PDF, by their page; a page its selection sent to Recently
    deleted counts by the files where they are (its in-place tombstones), so
    that ticking one of its two files again joins the other."""
    out: dict = {}
    rows = [(path, row) for path, row in vault_store.files(link["id"]).items()]
    rows += [(tomb["was"], tomb) for tomb in vault_store.tombstones(link["id"]) if vault_store.in_place(tomb)]
    for path, row in rows:
        if row["page_id"] and row["kind"] in ("notes", "pdf"):
            out.setdefault(row["page_id"], []).append(path)
    return out


def _followed(link: dict) -> set:
    """The vault paths, folded (``vault_disk._fold``), of the PDFs the vaults
    of one file inside it follow, which its save takes in
    (``folder_links.followed_files``)."""
    from .. import folder_links  # local: it imports this module's callers

    root = Path(link["path"])
    return {vault_disk._fold(Path(os.path.relpath(other["path"], root)).as_posix())
            for other in folder_links.followed_files(link)}


def _of_format(fmt: tuple, word: str) -> bool:
    """Whether a file of ``(format, language)`` is of the dialog's format
    filter ``word``: ``latex`` the LaTeX and BibTeX sources."""
    kind, language = fmt
    if word == "latex":
        return kind == "text" and language in ("latex", "bibtex")
    return kind == word


def listing(link: dict, top: str = "", base: str = "file") -> dict:
    """The tracking dialog's tree of the directory ``top`` of the vault ("" its
    top), as the module doc says: ``{dir, base, rules: {exists, ours, hash},
    capped, entries}``, an entry ``{path, directory, format, language, size,
    mtime, git_ignored, selected, kind, page, pair, never, placeholder}``, a
    directory's with ``walked``, a file's with ``locked`` (``FOLLOWED`` or
    None). ``page`` is the page the vault keeps of the file, ``pair`` the
    other file of that page. SelectionError: 400 for a ``top`` outside the
    vault, below a link, below what the scan never tracks or that is no
    directory; 409 for a vault whose directory is not there, is another
    vault's or holds a ``.gammaignore`` that cannot be read."""
    root = _root(link)
    top = top.strip("/")
    if top:
        try:
            vault_disk.path(root, top)
        except vault_disk.Outside as e:
            raise SelectionError(400, str(e)) from e
        if not all(vault_disk.listed(name, directory=True) for name in top.split("/")):
            raise SelectionError(400, f"{top} is never tracked: Gamma does not list it.")
        if not (root / top).is_dir():
            raise SelectionError(400, f"{top} is no directory of the vault.")
    rules = _rules(root)
    hint = ignore_rules.hint_stack(root)
    pages = _pages(link)
    page_of = {path: page for page, paths in pages.items() for path in paths}
    followed = _followed(link)
    deadline = time.monotonic() + LIST_SECONDS
    entries, capped, queue = [], False, deque([(top, None)])
    try:
        while queue:
            rel, row = queue.popleft()
            if row is not None and time.monotonic() > deadline:
                capped = True
                break
            found = _read(root, rel)
            if row is not None and len(entries) + len(found) > LIST_ENTRIES:
                capped = True
                break
            if row is not None:
                row["walked"] = True
            closed_below = bool(rel) and hint.ignored(rel, True)
            for path, entry, directory, stub, never in found:
                seen = _stat(entry, stub)
                if seen is None:
                    continue
                size, mtime, placeholder = seen
                git_ignored = hint.ignored(path, directory)
                locked = not directory and not never and vault_disk._fold(path) in followed
                if never:
                    selected = False
                elif locked:
                    selected = True
                elif base == "preset":
                    selected = not git_ignored if directory else preset(path, hint)
                else:
                    selected = rules.tracks(path, directory)
                page = None if directory else page_of.get(path)
                fmt, language = (None, None) if directory else format_of(path)
                item = {"path": path, "directory": directory, "format": fmt, "language": language, "size": size,
                        "mtime": mtime, "git_ignored": git_ignored, "selected": selected,
                        "kind": "" if directory else vault_disk.kind_of(path), "page": page,
                        "pair": next((p for p in pages.get(page, ()) if p != path), None) if page else None,
                        "never": never or None, "placeholder": placeholder}
                if directory:
                    item["walked"] = False
                    if not never and (closed_below or not git_ignored):
                        queue.append((path, item))
                else:
                    item["locked"] = FOLLOWED if locked else None
                entries.append(item)
    except vault_disk.Offline as e:
        raise SelectionError(409, str(e)) from e
    return {"dir": top, "base": base, "rules": {"exists": rules.exists, "ours": rules.ours, "hash": rules.hash},
            "capped": capped, "entries": entries}


@dataclass(slots=True)
class Chosen:
    """A selection evaluated (``evaluate``): the walk it was read over, the
    files it selects, those it added beside their page's other file
    (``joined``, ``[{path, with}]``), and the vault's rules as they are."""
    walk: Walk
    selected: set
    joined: list
    rules: ignore_rules.Rules


def evaluate(link: dict, selection: dict) -> Chosen:
    """The files ``selection`` (``{base: "file" | "preset", changes: [{path |
    format, on}]}``) tracks in the vault, as the module doc says: a path that
    ends in ``/`` is every file below it, a format every file of it anywhere.
    SelectionError: 409 as ``listing``'s, 400 for a change that names
    neither a path nor a format."""
    root = _root(link)
    rules = _rules(root)
    try:
        found = walk(root)
    except vault_disk.Offline as e:
        raise SelectionError(409, str(e)) from e
    files = found.files
    if selection.get("base") == "preset":
        hint = ignore_rules.hint_stack(root)
        chosen = {p for p in files if preset(p, hint)}
    else:
        chosen = {p for p in files if rules.tracks(p)}
    for change in selection.get("changes") or ():
        word, path = change.get("format"), change.get("path") or ""
        if word:
            hit = {p for p, (_size, *fmt) in files.items() if _of_format(fmt, word)}
        elif path.endswith("/"):
            hit = {p for p in files if p.startswith(path)}
        elif path:
            hit = {path} & files.keys()
        else:
            raise SelectionError(400, "A change names a file, a directory or a format.")
        chosen = chosen | hit if change.get("on") else chosen - hit
    joined = []
    for paths in _pages(link).values():
        have = [p for p in paths if p in chosen]
        if len(paths) == 2 and len(have) == 1:
            other = next(p for p in paths if p != have[0])
            if other in files:
                chosen.add(other)
                joined.append({"path": other, "with": have[0]})
    followed = _followed(link)
    chosen |= {p for p in files if vault_disk._fold(p) in followed}
    return Chosen(found, chosen, sorted(joined, key=lambda j: j["path"]), rules)


def text_of(chosen: Chosen) -> str:
    """The ``.gammaignore`` that tracks exactly what ``chosen`` selects;
    SelectionError 400 for a selection none says."""
    try:
        return ignore_rules.write(ignore_rules.Listing(set(chosen.walk.files), set(chosen.walk.dirs)), chosen.selected)
    except ignore_rules.Inexact as e:
        raise SelectionError(400, "No .gammaignore says this selection: two of its names read alike, so tick or "
                                  "untick both.") from e


def _leaving(link: dict, selected: set) -> list:
    """The pages of the vault none of whose files ``selected`` keeps, as the
    dialog says what leaves: ``[{path, page, title, notes, open_comments}]``,
    ``path`` its PDF's (else its notes'), ``notes`` how many blocks of its
    own hold something, ``open_comments`` its open threads. A page already
    in Recently deleted is left out."""
    from .. import comments  # local: it reaches the page writers

    gone = {page: paths for page, paths in _pages(link).items() if not any(p in selected for p in paths)}
    if not gone:
        return []
    ids = json.dumps(sorted(gone))
    with connect_pages_db(link["workspace_id"]) as conn:
        titles = dict(conn.execute("SELECT id, content FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) "
                                   "AND parent_id = 'root'", (ids,)).fetchall())
        notes = dict(conn.execute("SELECT page_id, COUNT(*) FROM unified_blocks WHERE page_id IN (SELECT value FROM "
                                  "json_each(?)) AND id != page_id AND (kind != 'note' OR content != '') GROUP BY page_id",
                                  (ids,)).fetchall())
        threads = comments.open_counts(conn, list(titles))
    out = []
    for page, paths in gone.items():
        if page in titles:
            main = next((p for p in paths if vault_disk.kind_of(p) == "pdf"), paths[0])
            out.append({"path": main, "page": page, "title": titles[page] or "", "notes": notes.get(page, 0),
                        "open_comments": threads.get(page, 0)})
    return sorted(out, key=lambda x: x["path"])


def preview(link: dict, selection: dict) -> dict:
    """What saving ``selection`` would do (``evaluate``): ``{files, bytes,
    by_format: {pdf, markdown, image, other}, unread, over_cap, cap, leaving,
    joined, text, rules_ours}`` — the files it tracks, what they weigh and
    how many of each format, those of no class Gamma reads yet (``unread``),
    whether they are more than the vault's rounds read (``cap_of``), the
    pages that leave (``_leaving``), the ``.gammaignore`` it writes
    (``text``), and whether that replaces nothing a person wrote
    (``rules_ours``: the vault has none, or Gamma's)."""
    chosen = evaluate(link, selection)
    files, selected = chosen.walk.files, chosen.selected
    formats = Counter(files[p][1] if files[p][1] in _SHOWN else "other" for p in selected)
    cap = cap_of(link)
    return {"files": len(selected), "bytes": sum(files[p][0] for p in selected),
            "by_format": {k: formats[k] for k in (*_SHOWN, "other")},
            "unread": sum(1 for p in selected if not vault_disk.kind_of(p)), "over_cap": len(selected) > cap, "cap": cap,
            "leaving": _leaving(link, selected), "joined": chosen.joined, "text": text_of(chosen),
            "rules_ours": not chosen.rules.exists or chosen.rules.ours}


def check(link: dict, chosen: Chosen, base_hash: str) -> None:
    """A save's refusals: 409 with the hash there now when the vault's
    ``.gammaignore`` is not the one the dialog read (``base_hash``, "" for
    none), 400 for more files than its rounds read."""
    if chosen.rules.hash != base_hash:
        raise SelectionError(409, CHANGED, chosen.rules.hash)
    cap = cap_of(link)
    if len(chosen.selected) > cap:
        raise SelectionError(400, f"That is {len(chosen.selected):,} files, more than a work folder tracks "
                                  f"({cap:,}): choose fewer.")


def write(link: dict, chosen: Chosen) -> str:
    """The ``.gammaignore`` that says ``chosen`` (``text_of``) written in the
    vault's directory in place of the one there, atomically: a temporary name
    the scan skips, flushed, renamed over it. A hand-written one (not
    ``ours``) is kept first in the vault's trash,
    ``.gamma/trash/<stamp>/.gammaignore``. → the hash of the text written,
    which the vault's rounds read from then on. SelectionError 409 for a
    directory that cannot be written."""
    root, text = Path(link["path"]), text_of(chosen)
    old = chosen.rules
    target = root / ignore_rules.NAME
    try:
        if old.exists and not old.ours:
            kept = vault_disk.trash_path(root, ignore_rules.NAME, vault_disk.stamp())
            vault_disk.write_new(root, kept, old.text.encode("utf-8", "surrogatepass"))
        tmp, _st = vault_disk._temp_beside(target, text.encode("utf-8"))
        try:
            vault_disk._rename_over(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, vault_disk.Offline) as e:
        raise SelectionError(409, f"Its {ignore_rules.NAME} cannot be written: {getattr(e, 'strerror', None) or e}.") from e
    return ignore_rules.Rules.parse(text).hash
