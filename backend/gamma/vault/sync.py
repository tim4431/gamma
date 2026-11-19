"""A synced vault's rounds (docs/research/vaults.md "A vault is a folder",
"Notes both ways"): a directory on this computer and a folder of Gamma kept
in step, the disk's changes read first (**push**, gamma/vault/push.py),
Gamma's written after (**pull**, gamma/vault/pull.py), each only where the
vault's switch for it is on. A round (gamma/vault/round.py ``Round``) first
settles what one that died left in its journal (gamma/vault/journal.py,
**crash safety**), then reads the scan; ``run`` is the whole round.

A round reads and writes Gamma through a **remote**, the small interface of
what it needs there: the pages below the folder with their change-log seqs
(``layout``), a page's blocks (``page``), ops applied under the workspace's
write lock with the ops built under it (``apply``), a page made with its
blocks (``create``), the trash, filing, folders, titles, labels by name
(``label_ids``, made where missing), a PDF read where it is or stored, and
the conflicts the file won, recorded in the transaction of the ops. No connection crosses it: gamma/vault/local_remote.py's ``LocalRemote`` writes this
server's workspace in-process as the account that made the link and asks before each
write whether that account may still edit there; for a vault of another
server gamma/vault/http_remote.py's ``HttpRemote`` takes its place, over HTTP with
the link's token (gamma/vault/remote.py ``remote_for``, which also holds
what a round and both remotes share). What a round does with a file is its **class's**
(gamma/vault/kinds.py: ``notes``, ``pdf``, ``picture`` or, in a work folder,
``file``, decided by the name alone), the round's steps dispatched to it
where they differ by class.

**What a vault tracks** is its ``.gammaignore`` (gamma/work_folders/
ignore_rules.py), else the default (notes, PDFs and pictures): one gone
since a round read it stops the vault, and pull writes nothing it leaves out.
**A work folder** (docs/dev/work_folders.md) is a vault that pushes
everything into a folder that carries the mark (``Layout.work``): its
directory is the source. Every file it tracks is a page: a note, a PDF, or
a **file** of any other format, pictures included (``kinds.FILE``: read in
place, its page's document the file, titled by its leaf, never in the
library by the switch; one past ``config.vault_keep_bytes`` is listed with
no bytes). Its new pages are stamped as its files, a move on
disk files a page first in its new directory's folder and names it, pull
renames or moves only a note that follows Gamma (no document, where the
vault pulls), copies no PDF out and writes no note beside a file, and a
file the selection leaves out, or a file whose page left, stays where it
is: its page to Recently deleted
first, then its row an in-place tombstone (``store.bury_in_place``),
which nothing moves or purges. In a kept folder a file left out is
forgotten, its page staying. A whole round of a work folder holds to its cap
of files and writes its mark again where the folder lost it.

**Force**: ``pull`` makes every file its page, where the file is now (the
rows follow the disk's moves first, Gamma keeping its places), ``push`` every
page's blocks with a span its file; both record what they replaced, written resolved, and a
block a force push deletes is kept in its base's ``gone`` with its properties
(a highlight's quote, page and rectangles). A force push is pushing
everything, refused where that is (``folder_links.forced``).

Errors are a file's (its row's ``problem``, the status's ``kept``), never the
vault's, which stops only for its marker (``disk.Stopped``, a copy of
the vault at its place too; its ``.gammaignore`` gone or unreadable, and a
work folder past its cap, the same way), its disk (``Offline``) and its account
(``Refused``). No part of a round writes vaults.db while it holds a
workspace's write lock.

``sync(link)`` is a whole round; ``sync_page(link, page_id)`` the hot loop's,
for one page's files: no scan, so nothing is removed, and nothing is made or
moved in a directory the disk changed since the last scan.
"""

import fnmatch
import json
import time
from dataclasses import dataclass

from .. import folder_links, gamma_sync
from ..db import page_now
from ..folder_links import KEPT_SHOWN
from ..work_folders import ignore_rules, selection, takeover
from ..work_folders.selection import MAX_FILES
from . import disk, journal, pull, push, store
from .remote import Refused, remote_for
from .round import Round, _Rows


PLACE = "marker_file"      # the state's key of the marker's file id (``_same_place``)
SELECTION = "selection"    # the state's key of the hash of the .gammaignore a whole round read ("" for none)


# --- the whole round -------------------------------------------------------------------

def run(rnd) -> dict:
    """The round ``rnd``, opened (``_open``), pushed, then pulled, each where
    it runs; → its status."""
    started = time.time()
    try:
        _open(rnd)
        if rnd.pushing and not push._push(rnd):
            return _done(rnd, started)
        if rnd.force == "pull" and rnd.scan is not None:
            push._follow_moves(rnd)
        elif not rnd.pushing and rnd.scan is not None:
            push._report_kept(rnd)
        if rnd.pulling:
            pull._pull(rnd)
        if rnd.scan is not None and rnd.state.get("placing"):
            rnd._find_highlights_again()
    except disk.Stopped as e:
        rnd.status.update(stopped=str(e), last_error=str(e))
    except disk.Offline as e:
        rnd.status.update(offline=True, last_error=str(e))
    except Refused as e:   # with what was in the way of another server (a redirect, its certificate)
        rnd.status.update(last_error=str(e), **gamma_sync.status_fields(e))
    finally:
        rnd.remote.tell()
    return _done(rnd, started)


def _done(rnd, started: float) -> dict:
    status = {"running": False, "started_at": started, "last_sync": page_now(), "last_error": "",
              "counts": rnd.counts, "kept": [{"path": p, "why": w, "force": f} for p, w, f in rnd.kept[:KEPT_SHOWN]],
              **rnd.status}
    if rnd.scan is not None:
        status["untracked"] = rnd.scan.untracked
        if rnd.scan.refused:
            status["refused"] = [{"path": p, "why": w} for p, w in rnd.scan.refused[:KEPT_SHOWN]]
    return status


def _open(rnd) -> None:
    """The marker, the mark of the vault's folder (a work folder's whole
    round writes it again where it is not the link's, after a restore or
    a mirror's merge, but one of another server whose mark is gone there
    stops, ``folder_links.MARK_GONE``: that server let go of it, which
    its state keeps (``folder_links.LET_GO_THERE``), so a mark another
    vault writes on that folder later is never taken for its own; a vault
    that does not push everything, of another server's work folder,
    keeps a kept folder's round), what the vault
    tracks (``_read_rules``), the journal settled (it may move files),
    then the scan, which a work folder's round holds to its cap
    (``_capped``); one page's round reads no scan."""
    vault_id = rnd.state.get("vault") or ""
    rnd.marker = disk.check_marker(rnd.root, vault_id)
    _same_place(rnd)
    rnd.work = rnd.remote.mark() if rnd.link.get("push") == folder_links.PUSH_ALL else None
    if not rnd.only and rnd.state.get(takeover.TAKEN_OVER) and folder_links.work_vault(rnd.link):
        if rnd.link["remote_url"] and rnd.work is None:   # let go there: marked again, its files would be refused
            rnd.state[folder_links.LET_GO_THERE] = page_now()   # and never again: a mark there names no vault
            raise disk.Stopped(folder_links.MARK_GONE)
        rnd.work = takeover.write_mark(rnd.link, rnd.remote)
    rnd.rules = _read_rules(rnd)
    journal._recover(rnd)
    if not rnd.only:
        rnd.scan = disk.scan(rnd.root, vault_id, rnd.rules, work=bool(rnd.work))
        _capped(rnd)
        disk.clean_temps(rnd.root, rnd.scan.temps)
        journal._recover_moved(rnd)
    rnd.rows = _Rows(store.files(rnd.id))
    for path, old, token in rnd.refiles:
        if path in rnd.rows:
            rnd._each(path, push._refile, rnd, path, old, token)


def _read_rules(rnd):
    """What the vault tracks: its ``.gammaignore`` (gamma/work_folders/
    ignore_rules.py), else the default. One gone since a whole round read
    it (the state's ``SELECTION``, its hash) stops the vault rather than
    reading every note and PDF anew, and so does one that cannot be read;
    one edited by hand is read as it is, its removals going through the
    guard. A whole round keeps the hash it read. The first whole round of
    a vault whose marker names an older app's ignore globs writes them
    once as its ``.gammaignore`` (``_globs_written``)."""
    try:
        rules = ignore_rules.read(rnd.root)
    except ignore_rules.Unreadable as e:
        raise disk.Stopped(f"its .gammaignore cannot be read: {e.why}") from e
    known = rnd.state.get(SELECTION)
    if not rules.exists and known:
        raise disk.Stopped("its .gammaignore is gone: put it back, or choose its files again")
    if rnd.only:
        return rules
    if not rules.exists and known is None:
        rules = _globs_written(rnd) or rules
    rnd.state[SELECTION] = rules.hash
    return rules


def _globs_written(rnd):
    """The marker's ``ignore`` globs, which an older app read, written as
    the vault's ``.gammaignore``: the default, then each glob left out (one
    with a ``/`` anchored at the root, as it matched the path from there).
    Where git's reading of a glob, always case-insensitive, takes other
    entries of the directory than the old ``fnmatch`` did (case-sensitive
    on macOS and Linux), lines that name those entries follow it, so the
    file leaves out what the old scan left out, no more and no less.
    → the rules read back; None when the marker names none."""
    try:
        raw = json.loads((rnd.root / disk.MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    globs = raw.get("ignore") if isinstance(raw, dict) else None
    globs = [g for g in (str(g).strip().strip("/") for g in globs or ()) if g] if isinstance(globs, list) else []
    if not globs:
        return None
    lines = [ignore_rules.HEADER, *(line for line in ignore_rules.DEFAULT_TEXT.splitlines()
                                    if line != ignore_rules.HEADER)]
    found = selection.walk(rnd.root)
    entries = [(d, True) for d in found.dirs] + [(f, False) for f in found.files]
    for glob in globs:
        line = f"\\{glob}" if glob[0] in "!#" else glob
        line = f"/{line}" if "/" in glob else line
        one = ignore_rules.Rules.parse(line)
        old = {(p, d) for p, d in entries if fnmatch.fnmatch(p if "/" in glob else p.rpartition("/")[2], glob)}
        new = {(p, d) for p, d in entries if one.decide(p, d)}
        lines.append(line)
        lines += [f"!{ignore_rules.anchored(p, d)}" for p, d in sorted(new - old)
                  if d or ignore_rules.DEFAULT.decide(p, False) is not True]
        lines += [ignore_rules.anchored(p, d) for p, d in sorted(old - new)]
    try:
        disk.write_new(rnd.root, ignore_rules.NAME, ("\n".join(lines) + "\n").encode("utf-8"))
    except FileExistsError:
        pass            # one written meanwhile is read as it is
    return ignore_rules.read(rnd.root)


def _capped(rnd) -> None:
    """``Stopped`` before a work folder's round writes anything when its
    scan selects more files than its cap (the state's ``cap``, else
    ``MAX_FILES``), the status saying how many (``too_many``)."""
    if not rnd.work:
        return
    files = len(rnd.scan.files) + rnd.scan.pictures + rnd.scan.untracked
    cap = int(rnd.state.get("cap") or MAX_FILES)
    if files > cap:
        rnd.status["too_many"] = {"files": files, "cap": cap}
        what = "its .gammaignore tracks" if rnd.rules.exists else "it tracks"
        raise disk.Stopped(f"{what} {files:,} files, more than {cap:,}: choose fewer")


def _same_place(rnd) -> None:
    """``Stopped`` for a copy of the vault at its place (a backup disk given
    its drive letter, a directory restored over it): the marker is the
    vault's, its file another than the one the vault's first round found,
    which the state keeps (``PLACE``). A relink there takes the copy."""
    here, known = disk.marker_file(rnd.root), rnd.state.get(PLACE)
    if known and here and here != known:
        raise disk.Stopped("the directory holds a copy of the vault, not the vault itself: locate the vault, "
                                 "or relink to this copy to sync it")
    if here and not known:
        rnd.state[PLACE] = here


@dataclass(slots=True)
class Result:
    """A round's outcome for its link's row: the status, the ``state`` to keep
    (the marker's id, the guard's count), the cursor pull read (None: no
    pull ran)."""
    status: dict
    state: dict
    cursor: str | None


def sync(link: dict, *, direction: str = "both", force: str = "", confirm: bool = False, remote=None,
         progress=None) -> Result:
    """A whole round of the vault ``link``, telling ``progress`` how far it is
    (``Round._report``)."""
    rnd = Round(link, remote or remote_for(link), direction=direction, force=force, confirm=confirm,
                progress=progress)
    status = run(rnd)
    return Result(status, rnd.state, None if rnd.cursor is None else str(rnd.cursor))


def sync_page(link: dict, page_id: str, *, remote=None) -> Result:
    """The hot loop's round: only the files of ``page_id``, pushed when their
    stat moved, then the page pulled when it moved. No scan, so no file of the
    disk reads as new or removed; pull may write the page's own files (its
    notes, a stored PDF it has, the pictures its links name), in directories
    still as the last scan recorded them (``Round._steady``)."""
    rnd = Round(link, remote or remote_for(link), page_id=page_id)
    status = run(rnd)
    return Result(status, rnd.state, None)
