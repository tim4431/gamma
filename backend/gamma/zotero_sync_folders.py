"""Collections ↔ folders in a Zotero link's round (see
docs/dev/zotero_sync.md#collections-and-folders). One pass before the units
(``collections_pass``) reads the collections Zotero listed whole, with the
parents they name that the link does not know yet, and decides every
collection row and folder field by field (``_Folders``); the folder writes
and the rows go in one transaction, Zotero's writes after it, then their
outcomes on the rows. A trash on either side never strips an item's
membership: a trashed collection leaves both sides of every item's merge
(``_forget_collection``), and only a removal for good
(``collections_gone``) drops it. A folder shared or kept on disk stays here
when Zotero trashes its collection (``detail.kept``), as it is, and a
Zotero restore finds it whole: deleting it would end its share links and
invitations for good. Of the engine's parts it imports only
gamma/zotero_sync_rows.py.
"""

import json

from . import sync_engine, zotero_local, zotero_merge
from . import zotero_remote as zr
from .blocks_store import FOLDERS, LABELS, filing, new_block_id, tree_rows, write_lock
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log
from .zotero_sync_rows import (KEPT_HERE, Round, drop_rows, field_base, json_dict, new_row, read_rows, record_conflict,
                               signature, without, write_rows)
from .zotero_web import object_key, random_key

TREE_ENTRIES = (zr.PAGE_PREFIX + FOLDERS, zr.PAGE_PREFIX + LABELS)
PARENT_READS_MAX = 16  # reads of the parent collections a pass meets that the link does not know yet


def collections_due(r: Round) -> bool:
    """Whether the round has a collections pass to make: a collection queued
    and due, the folder or label tree listed by the local feed, or (in a
    two-way round) a collection create not yet confirmed."""
    now = page_now()
    with connect_pages_db(r.ws) as conn:
        if zr.take(conn, now=now, limit=1, kinds="collection", exclude=r.excluded):
            return True
        if zr.due(conn, TREE_ENTRIES, now=now):
            return True
        return r.pushes and conn.execute("SELECT 1 FROM zotero_objects WHERE kind = 'collection' "
                                         "AND state = 'pending' LIMIT 1").fetchone() is not None


def _kept_folders(ws: str, ids) -> set[str]:
    """The folders among ``ids`` that a Zotero trash leaves here: shared (a
    share link, the invitations mailed for it) or kept on disk by the
    desktop app (``folder_links``). Deleting one ends that for good, and a
    restore in Zotero could not bring it back."""
    from .routers.shares import shared_folders  # local: the routers import the engine
    ids = sorted(set(ids))
    with connect_users_db() as conn:
        on_disk = {row[0] for row in conn.execute(
            "SELECT folder_id FROM folder_links WHERE workspace_id = ? AND remote_url = '' "
            "AND folder_id IN (SELECT value FROM json_each(?))", (ws, json.dumps(ids)))}
    return shared_folders(ws, ids) | on_disk


def _forget_collection(conn, key: str) -> list[str]:
    """A collection whose row is trashed leaves both bases of every item
    row, as it leaves both projections (``zotero_merge.item_projection``'s
    ``live_collections``): the refiling its folder's removal causes here
    never reads as a change of either side, and a restore reads as
    Zotero's addition, which files the pages again. The item keys whose
    bases named it (the collection's members, for that restore)."""
    members = []
    for item_key, zbase, gbase in conn.execute(
            "SELECT key, zbase, gbase FROM zotero_objects WHERE (kind = 'item' OR (kind IN ('attachment', 'note') "
            "AND parent = '')) AND (zbase LIKE ? OR gbase LIKE ?)", (f'%"{key}"%', f'%"{key}"%')).fetchall():
        bases = [json_dict(zbase), json_dict(gbase)]
        for base in bases:
            if isinstance(base.get("collections"), list):
                base["collections"] = [k for k in base["collections"] if k != key]
        conn.execute("UPDATE zotero_objects SET zbase = ?, gbase = ? WHERE key = ?",
                     (json.dumps(bases[0], sort_keys=True), json.dumps(bases[1], sort_keys=True), item_key))
        members.append(item_key)
    return members


class _Folders:
    """One collections pass: the folder tree and the collection rows as it
    read them, and the folder ops, rows and Zotero writes it decides. Every
    collection row names its folder (``block_id``) and its Zotero parent;
    ``zbase`` / ``gbase`` are ``{"name", "parent"}`` in Zotero's terms."""

    def __init__(self, r: Round):
        self.r = r
        with connect_pages_db(r.ws) as conn:
            # {folder id: {"name", "parent": its parent folder's id, "" at the top}}, parents first
            self.folders = {fid: {"name": (name or "").strip(), "parent": "" if parent == FOLDERS else parent}
                            for fid, parent, name, _props in tree_rows(conn, FOLDERS)}
            self.stamps = zotero_local.stamps(conn, list(self.folders))
            self.rows = read_rows(conn, "kind = 'collection'")
        self.by_block = {row["block_id"]: row for row in self.rows.values() if row["block_id"]}
        self._derived: dict[str, str] = {}
        self.ops: list[dict] = []            # folder ops on FOLDERS, a parent's insert before its children's
        self.changed: dict[str, dict] = {}   # key -> the row as the pass writes it
        self.dropped: set[str] = set()       # keys whose rows go
        self.removals: list[tuple] = []      # (folder id, [rows]): a Zotero trash deletes it here, its rows with it
        self.forget: set[str] = set()        # collections trashed in Zotero by this pass's writes, or kept here
        self.kept: list[tuple] = []          # (folder id, name): folders a Zotero trash leaves here (``_trash_here``)
        self.writes = zr.Writes(r.client, r.library)
        self.sent: dict[str, dict] = {}      # key -> what was sent for it and what it was decided on
        self.conflicts: list[tuple] = []     # (folder id, field, Gamma's side, Zotero's side, decided by adopt)
        self.refile: set[str] = set()        # collections restored in Zotero: their items are looked at again
        self.rekeyed: set[str] = set()       # folders whose collection key changed or was confirmed: their pages
        #                                      are looked at again
        self.refiled: set[str] = set()       # refile and rekeyed whose follow-ups are queued already (``follow_ups``)
        self.regained: set[str] = set()      # collections whose folder deleted here comes back: their members too
        self.stale: set[str] = set()         # collections whose write Zotero refused as stale: read again soon
        self.pulled = {"add": 0, "del": 0, "mod": 0}
        self.pushed = {"add": 0, "del": 0, "mod": 0}

    # what Gamma holds

    def key_of(self, fid: str) -> str:
        """The collection key of a folder: its row's, or the key derived for
        its create (its parent's key in the seed)."""
        if not fid:
            return ""
        row = self.by_block.get(fid)
        if row is not None:
            return row["key"]
        if fid not in self._derived:
            parent = self.folders.get(fid, {}).get("parent", "")
            self._derived[fid] = object_key(self.r.ws, self.r.library, "collection", self.key_of(parent), fid, "")
        return self._derived[fid]

    def gamma(self, fid: str) -> dict | None:
        """A folder as its collection would hold it; None when it is gone."""
        folder = self.folders.get(fid)
        if folder is None:
            return None
        return zotero_merge.folder_projection(folder["name"], self.key_of(folder["parent"]))

    def gsig(self, fid: str) -> str:
        folder = self.folders.get(fid) or {}
        return signature(folder.get("name"), folder.get("parent"))

    def folder_of(self, key: str) -> str | None:
        """The folder a collection is here; None for the top level (no key,
        or a collection with no live folder)."""
        row = self.rows.get(key) if key else None
        if row is None or row["state"] == "trashed" or row["block_id"] not in self.folders:
            return None
        return row["block_id"]

    def _put(self, row: dict) -> None:
        row["synced_at"] = page_now()
        self.rows[row["key"]] = row
        if row["block_id"]:
            self.by_block[row["block_id"]] = row
        self.changed[row["key"]] = row
        self.dropped.discard(row["key"])

    def _drop(self, key: str) -> None:
        self.dropped.add(key)
        self.changed.pop(key, None)

    def _below(self, top: str) -> list[str]:
        """The folders under ``top``, at any depth."""
        out, todo = [], [top]
        while todo:
            at = todo.pop()
            kids = [fid for fid, folder in self.folders.items() if folder["parent"] == at]
            out += kids
            todo += kids
        return out

    def _make_folder(self, key: str, Z: dict, version: int, row: dict | None = None) -> str:
        """A folder for a collection Zotero has and Gamma not (a new one, a
        restore): last under its parent's folder; the row says so. A
        restore takes the folder's old id when nothing has it, so what names
        the folder by its id (a desktop folder link, a reading position)
        finds it again."""
        parent = self.folder_of(Z["parent"])
        fid, name = new_block_id(), Z["name"] or "Untitled"
        if row is not None and row["block_id"] and row["block_id"] not in self.folders:
            with connect_pages_db(self.r.ws) as conn:
                if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (row["block_id"],)).fetchone():
                    fid = row["block_id"]
        self.ops.append(zotero_local.folder_insert(fid, name, parent))
        self.folders[fid] = {"name": name, "parent": parent or ""}
        self.pulled["add"] += 1
        row = row or new_row(key, "collection", page_id=FOLDERS)
        row.update(block_id=fid, parent=Z["parent"], version=version, state="live", zbase=Z, gbase=self.gamma(fid),
                   gsig=self.gsig(fid), detail=without(row["detail"], "first", "collateral", "sent", "kept"))
        self._put(row)
        return fid

    # Zotero's changes

    def pull(self, objects: dict) -> dict:
        """What the collections Zotero listed mean here: new ones made,
        trashed ones deleted, restored ones made again, our own creates
        confirmed. ``{key: (Z, version)}`` of the live ones, for ``merge``."""
        live: dict[str, tuple] = {}
        projected = {key: zotero_merge.collection_projection(obj.get("data")) for key, obj in objects.items()}
        matched = self._matches(objects, projected)
        derived = {self.key_of(fid): fid for fid in self.folders if fid not in self.by_block}
        for key in sorted(objects, key=lambda k: self._depth(k, projected)):
            obj, Z = objects[key], projected[key]
            version, trashed = int(obj.get("version") or 0), bool((obj.get("data") or {}).get("deleted"))
            row = self.rows.get(key)
            if row is None:
                fid = matched.get(key)
                if fid is None and key in derived and self.gamma(derived[key])["parent"] == Z["parent"]:
                    fid = derived[key]  # Zotero holds a folder's derived key under the same parent: ours
                if fid and fid in self.folders and fid not in self.by_block:
                    # a first link's pair, or our own collection: one object, decided as a first link
                    # decides (only what both hold alike is synced)
                    self._put(new_row(key, "collection", page_id=FOLDERS, block_id=fid, parent=Z["parent"],
                                      version=version, state="trashed" if trashed else "live",
                                      detail={"first": True}))
                    if not trashed:
                        live[key] = (Z, version)
                elif not trashed:
                    self._make_folder(key, Z, version)
                continue
            if row["state"] == "pending":
                # our create, which Zotero now names: right after it, Zotero held what was sent
                sent = row["detail"].get("sent") or Z
                row.update(state="live", version=version, zbase=sent, gbase=sent,
                           detail=without(row["detail"], "sent"))
                self._put(row)
                self.rekeyed.add(row["block_id"])
            if trashed:
                if row["state"] == "trashed":
                    # a folder kept here keeps its bases, so what Zotero changed meanwhile is merged at its restore
                    row.update(version=version, **({} if row["detail"].get("kept") else {"zbase": Z}))
                    self._put(row)
                else:
                    self._trash_here(row, Z, version)
                continue
            if row["state"] == "trashed":
                if self._restore_here(row, Z, version):
                    live[key] = (Z, version)  # kept here: merged against the bases it kept
                continue
            live[key] = (Z, version)
        return live

    def _depth(self, key: str, projected: dict) -> int:
        depth, at, seen = 0, key, set()
        while at in projected and at not in seen and projected[at]["parent"]:
            seen.add(at)
            at = projected[at]["parent"]
            depth += 1
        return depth

    def _matches(self, objects: dict, projected: dict) -> dict[str, str]:
        """At a first link of an existing workspace: ``{key: folder id}`` for
        the collections and folders without rows whose paths of names match
        (ignoring case), one to one."""
        if self.r.state.get("first") != "existing":
            return {}
        free = [key for key, obj in objects.items()
                if key not in self.rows and not (obj.get("data") or {}).get("deleted")]
        if not free:
            return {}
        nodes = {key: row["zbase"] for key, row in self.rows.items() if row["zbase"]}
        nodes.update(projected)
        cpaths, fpaths = zotero_merge.paths(nodes), zotero_merge.paths(self.folders)
        pairs = zotero_merge.match_paths({fid: fpaths[fid] for fid in self.folders if fid not in self.by_block},
                                         {key: cpaths[key] for key in free})
        return {key: fid for fid, key in pairs}

    def _trash_here(self, row: dict, Z: dict, version: int) -> None:
        """Zotero trashed the collection: its folder goes here, with every
        folder under it, and their items keep their membership in Zotero for
        a restore. The rows of folders under it whose collections Zotero did
        not trash wait (``collateral``) to come back with it. When one of
        those folders is shared or kept on disk (``_kept_folders``), none
        goes: the rows are trashed and ``kept``, the folders and their pages
        stay as they are (a trashed collection is in neither side of an
        item's merge), and Zotero's restore, or Gamma's own delete, decides
        later; emptying Zotero's trash removes them as any removal for good
        does."""
        fid = row["block_id"]
        if fid not in self.folders:
            row.update(state="trashed", version=version, zbase=Z, synced_at=page_now())
            self._put(row)
            return
        below = self._below(fid)
        if _kept_folders(self.r.ws, [fid, *below]):
            row.update(state="trashed", version=version, synced_at=page_now(), detail={**row["detail"], "kept": True})
            self._put(row)
            self.forget.add(row["key"])
            for f in below:
                child = self.by_block.get(f)
                if child is not None and child["state"] != "trashed":
                    child.update(state="trashed", synced_at=page_now(),
                                 detail={**child["detail"], "collateral": True, "kept": True})
                    self._put(child)
                    self.forget.add(child["key"])
            self.kept.append((fid, self.folders[fid]["name"]))
            return
        row.update(state="trashed", version=version, zbase=Z, synced_at=page_now())
        rows = [row]
        for f in below:
            child = self.by_block.get(f)
            if child is not None and child["state"] != "trashed":
                child.update(state="trashed", detail={**child["detail"], "collateral": True}, synced_at=page_now())
                rows.append(child)
        self.removals.append((fid, rows))
        for gone in [fid, *below]:
            self.folders.pop(gone, None)
        self.pulled["del"] += 1 + len(below)

    def _restore_here(self, row: dict, Z: dict, version: int) -> bool:
        """Zotero restored the collection: its folder comes back (the same
        block when it is still here), with the folders that went with it,
        and its items are looked at again, which files their pages. True for
        a folder kept here (``detail.kept``): live again with the bases it
        had, so ``merge`` takes what either side changed while it was in
        Zotero's trash."""
        kept = bool(row["detail"].get("kept")) and row["block_id"] in self.folders
        if kept:
            row.update(state="live", version=version, detail=without(row["detail"], "collateral", "first", "kept"))
            self._put(row)
        elif row["block_id"] in self.folders:
            row.update(state="live", version=version, zbase=Z, gbase=self.gamma(row["block_id"]),
                       detail=without(row["detail"], "collateral", "first", "kept"))
            self._put(row)
        else:
            self._make_folder(row["key"], Z, version, row)
        self.refile.add(row["key"])
        for child in [c for c in self.rows.values() if c["parent"] == row["key"] and c["state"] == "trashed"
                      and c["detail"].get("collateral")]:
            self._restore_here(child, child["zbase"], child["version"])
        return kept

    # field by field

    def merge(self, live: dict) -> None:
        """Every live row, decided field by field: Zotero's changes applied
        as folder ops, Gamma's sent (two-way rounds); both apart, Gamma's
        wins and a ``zotero_field`` row keeps Zotero's (a first link's or a
        force's pair: ``adopt``'s side, the row resolved). A folder gone
        here sends its collection to Zotero's trash, unless Zotero changed
        it meanwhile (an edit beats a delete)."""
        for key, row in list(self.rows.items()):
            if row["state"] not in ("live", "rejected") or key in self.dropped or int(row["version"]) <= 0:
                continue
            fid = row["block_id"]
            Z, version = live.get(key, (row["zbase"], row["version"]))
            G = self.gamma(fid)
            if G is None:
                self._gone_here(row, Z, version, zotero_changed=key in live and Z != row["zbase"])
                continue
            sig = self.gsig(fid)
            if row["state"] == "rejected" and key not in live and row["gsig"] == sig:
                continue  # refused for what Gamma still holds: tried again once either side changes it
            first = bool(row["detail"].get("first"))
            zbase, gbase = (zotero_merge.first_link_base("collection", Z, G) if first
                            else (row["zbase"], row["gbase"]))
            prefer = "zotero" if first and (row["detail"].get("adopt") or self.r.adopt) == "theirs" else "gamma"
            m = zotero_merge.merge(zbase, gbase, Z, G, sets=(), prefer=prefer)
            G_after = dict(G)
            for name, value in m.apply.items():
                if name == "name":
                    self.ops.append(zotero_local.folder_rename(fid, value or "Untitled"))
                    self.folders[fid]["name"] = value or "Untitled"
                    G_after["name"] = value or "Untitled"
                elif name == "parent":
                    parent = self.folder_of(value)
                    self.ops.append(zotero_local.folder_move(fid, parent))
                    self.folders[fid]["parent"] = parent or ""
                    G_after["parent"] = value if parent else ""
            if m.apply:
                self.pulled["mod"] += 1
            push = m.push if self.r.pushes else {}
            body = {}
            if "name" in push:
                body["name"] = push["name"]
            if "parent" in push:
                body["parentCollection"] = push["parent"] or False
            if body:
                self.writes.update("collections", key, version, body)
                self.sent[key] = {"what": "update", "m": m, "zbase": zbase, "gbase": gbase, "Z": Z, "G": G,
                                  "G_after": G_after, "sig": sig}
            for name, ours, theirs in m.conflicts:
                self.conflicts.append((fid, name, ours, theirs, first))
            # Gamma's fields that go to Zotero move in gbase only once the write lands (``record``)
            zb, gb = zotero_merge.bases_after(m, zbase, gbase, Z, G, pushed_ok=False, G_after=G_after)
            unchanged = not (m.apply or key in live or first or body or row["gsig"] != sig or row["state"] != "live")
            row.update(version=version, zbase=zb, gbase=gb, state="live", parent=Z["parent"],
                       gsig=row["gsig"] if body else sig, detail=without(row["detail"], "first"))
            if not unchanged:
                self._put(row)

    def _gone_here(self, row: dict, Z: dict, version: int, *, zotero_changed: bool) -> None:
        """A folder deleted here whose collection is live in Zotero: trashed
        there, unless Zotero changed it since (an edit beats a delete): then
        the folder comes back with its members, as from a restore: the
        collection leaves the items' bases, so their membership in Zotero
        reads as Zotero's and files their pages in it again."""
        if zotero_changed:
            fid = self._make_folder(row["key"], Z, version, row)
            self.conflicts.append((fid, "", "(deleted here)", Z["name"], False))
            self.regained.add(row["key"])
            self.refile.add(row["key"])
            return
        if version != int(row["version"]):  # Zotero's version as read now: the trash below goes with it
            row["version"] = version
            self._put(row)
        if self.r.pushes:
            self.writes.trash("collections", row["key"], version)
            self.sent[row["key"]] = {"what": "trash", "sig": row["gsig"]}

    # Gamma's new folders

    def push_new(self) -> None:
        """Two-way: folders Gamma made, as creates under their derived keys
        (the row written pending first, so a create whose answer was lost is
        confirmed by the next listing and never made twice), creates not
        confirmed yet sent again, a create Zotero refused sent again once
        its folder changed, and folders back here whose collections are in
        Zotero's trash (an undo) restored there. A pending row whose folder
        went before its create was confirmed goes too."""
        if not self.r.pushes:
            return
        for fid in list(self.folders):
            row = self.by_block.get(fid)
            if row is not None and row["key"] in self.changed and row["state"] not in ("pending", "trashed"):
                continue  # decided by pull or merge
            if row is not None and row["state"] == "trashed" and row["detail"].get("kept"):
                continue  # kept here while its collection is in Zotero's trash: no undo of that trash
            G, sig = self.gamma(fid), self.gsig(fid)
            refused = row is not None and row["state"] == "rejected" and int(row["version"]) <= 0
            if row is None or row["state"] == "pending" or (refused and row["gsig"] != sig):
                key = row["key"] if row is not None else self.key_of(fid)
                self._put(new_row(key, "collection", page_id=FOLDERS, block_id=fid, parent=G["parent"], version=-1,
                                  state="pending", gsig=sig, detail={"sent": G}))
                self.writes.create("collections", key, {"name": G["name"], "parentCollection": G["parent"] or False})
                self.sent[key] = {"what": "create", "G": G, "sig": sig}
            elif row["state"] == "trashed":
                body = {"name": G["name"], "parentCollection": G["parent"] or False}
                self.writes.restore("collections", row["key"], row["version"])
                self.writes.update("collections", row["key"], row["version"], body)
                self.sent[row["key"]] = {"what": "restore", "G": G, "sig": sig}
        for key, row in list(self.rows.items()):
            if row["state"] == "pending" and row["block_id"] not in self.folders:
                self._drop(key)

    # writing

    def apply_local(self) -> bool:
        """The folder ops and the rows in one transaction, if no folder the
        decisions read changed meanwhile (False when one did: nothing is
        written); then each folder a Zotero trash deletes, with its rows."""
        stamps = self.stamps
        removed = {id(row) for _, rows in self.removals for row in rows}

        def check(conn):
            return zotero_local.stamps(conn, list(stamps)) == stamps

        def rows(conn):
            write_rows(conn, [row for row in self.changed.values() if id(row) not in removed])
            drop_rows(conn, self.dropped)
            self.follow_ups(conn)  # with the folders they are for: a process killed after this loses none

        try:
            zotero_local.apply(self.r.ws, FOLDERS, self.ops, check=check, rows=rows)
        except zotero_local.Changed:
            return False
        for fid, gone in self.removals:
            def trashed(conn, gone=gone):
                for row in gone:
                    row["detail"] = {**row["detail"], "members": _forget_collection(conn, row["key"])}
                write_rows(conn, gone)
            zotero_local.delete_folder(self.r.ws, fid, rows=trashed)
        return True

    def follow_ups(self, conn) -> None:
        """What the pass's decisions so far send back to the queue, in the
        transaction that records them (and only once): the items of the
        collections restored, so their pages are filed again (each one's
        ``detail.members`` and every item whose ``detail.collections``
        names it), and the pages filed in folders whose collection key was
        confirmed or changed, so their items join it."""
        tops = "(kind = 'item' OR (kind = 'attachment' AND parent = ''))"  # an item, a standalone PDF
        for key in sorted(self.refile - self.refiled):
            members = set(self.rows[key]["detail"].get("members") or ()) if key in self.rows else set()
            if key in self.regained:
                members |= set(_forget_collection(conn, key))
            members |= {row[0] for row in conn.execute(
                f"SELECT key FROM zotero_objects WHERE {tops} AND detail LIKE ?", (f'%"{key}"%',))}
            versions = dict(conn.execute(  # the members' rows alone, by the key index (+: no other index)
                "SELECT key, version FROM zotero_objects WHERE key IN (SELECT value FROM json_each(?)) "
                "AND (+kind = 'item' OR (+kind = 'attachment' AND +parent = ''))",
                (json.dumps(sorted(members)),)).fetchall())
            zr.enqueue(conn, [{"id": item, "kind": "top", "version": versions.get(item, 0)}
                              for item in sorted(members)])
            if key in self.rows:
                self.rows[key]["detail"] = without(self.rows[key]["detail"], "members")
                write_rows(conn, [self.rows[key]])
        zr.enqueue(conn, [zr.page_entry(page_id) for page_id in _filed_pages(conn, self.rekeyed - self.refiled)])
        self.refiled |= self.refile | self.rekeyed

    def record(self, outcomes: dict) -> list[str]:
        """Each write's outcome on its row; the keys whose writes did not land
        and are decided again by a later pass."""
        again = []
        for key, outcome in outcomes.items():
            row, sent = self.rows.get(key), self.sent.get(key)
            if row is None or sent is None:
                continue
            what = sent["what"]
            if outcome.status == zr.CONFLICT:  # Zotero changed it since it was read: read again, soon
                self.stale.add(key)
                continue
            if outcome.status == zr.TRANSIENT or (outcome.status == zr.EXISTS and not outcome.data):
                again.append(key)
                continue
            if outcome.status == zr.REJECTED:
                row.update(state="rejected", gsig=sent["sig"], detail={**row["detail"], "reason": outcome.message})
                self._put(row)
                self.conflicts.append((row["block_id"], "rejected", outcome.message, "", False))
                continue
            saved = zotero_merge.collection_projection((outcome.data or {}).get("data")) if outcome.data else None
            if what == "create":
                G = sent["G"]
                if outcome.status == zr.EXISTS and (saved["parent"] != G["parent"]
                                                    or (outcome.data.get("data") or {}).get("deleted")):
                    # Zotero holds another object under the derived key (trashed, or elsewhere): ours gets a
                    # fresh key, sent by the next pass
                    self._drop(key)
                    fresh = new_row(random_key(), "collection", page_id=FOLDERS, block_id=row["block_id"],
                                    parent=G["parent"], version=-1, state="pending", gsig=sent["sig"],
                                    detail={"sent": G})
                    self._put(fresh)
                    self.rekeyed.add(row["block_id"])
                    continue
                row.update(state="live", version=outcome.version, zbase=saved or G, gbase=G, gsig=sent["sig"],
                           detail=without(row["detail"], "sent"))
                self.rekeyed.add(row["block_id"])  # its pages' items now join it
                self.pushed["add"] += 1
            elif what == "update":
                zb, gb = zotero_merge.bases_after(sent["m"], sent["zbase"], sent["gbase"], sent["Z"], sent["G"],
                                                  pushed_ok=True, G_after=sent["G_after"], Z_after=saved)
                row.update(version=outcome.version, zbase=zb, gbase=gb, gsig=sent["sig"])
                self.pushed["mod"] += 1
            elif what == "trash":
                row.update(state="trashed", version=outcome.version)
                self.forget.add(key)
                self.pushed["del"] += 1
            elif what == "restore":
                row.update(state="live", version=outcome.version, zbase=saved or sent["G"], gbase=sent["G"],
                           gsig=sent["sig"], detail=without(row["detail"], "collateral", "first"))
                self.refile.add(key)
                self.pushed["add"] += 1
            self._put(row)
        return again


def collections_pass(r: Round) -> None:
    """One collections pass: the collections Zotero listed, read whole (and
    the parents they name that the link does not know), then every
    collection row and folder decided field by field; the folder writes and
    the rows in one transaction, Zotero's writes after it, then their
    outcomes on the rows. A trash on either side never strips an item's
    membership: a trashed collection leaves both sides of every item's
    merge (``_forget_collection``), and only a removal for good drops
    it."""
    now = page_now()
    with connect_pages_db(r.ws) as conn:
        entries = zr.take(conn, now=now, kinds="collection", exclude=r.excluded)
        trees = zr.due(conn, TREE_ENTRIES, now=now)
        known = {row[0] for row in conn.execute("SELECT key FROM zotero_objects WHERE kind = 'collection'")}
    listed = {e.id: e.version for e in entries}
    objects, absent = {}, []
    if listed:
        fetched = zr.fetch_collections(r.client, r.library, list(listed))
        objects, absent = dict(fetched.objects), list(fetched.absent)
    for _ in range(PARENT_READS_MAX):  # the parents Zotero names that the link does not know yet, read too
        missing = {zotero_merge.collection_projection(obj.get("data"))["parent"] for obj in objects.values()}
        missing = sorted(k for k in missing if k and k not in objects and k not in known)
        if not missing:
            break
        more = zr.fetch_collections(r.client, r.library, missing)
        objects.update(more.objects)
        known.update(more.absent)
    if absent:
        gone = zr.hard_deleted(r.client, r.library, absent, kind="collections")
        again = collections_gone(r, sorted(gone)) if gone else []
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.drop(conn, sorted(gone - set(again)))  # (one whose removal waits stays queued)
            for key in absent:
                if key not in gone:  # a read that missed it: tried again, and given up after a while
                    zr.backoff(conn, key, "Zotero did not answer for this collection", now=now, kind="collection")
            conn.commit()
        for key in absent:
            listed.pop(key, None)
    plan = _Folders(r)
    plan.merge(plan.pull(objects))
    plan.push_new()
    if not plan.apply_local():
        # a folder changed while the pass decided: nothing was written; all of it is decided again next round
        r.decide_again(*listed, *TREE_ENTRIES)
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.enqueue(conn, [zr.page_entry(FOLDERS)])
            conn.commit()
        return
    outcomes = plan.writes.flush() if len(plan.writes) else {}
    again = plan.record(outcomes)
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        for key in plan.forget:
            row = plan.rows[key]
            row["detail"] = {**row["detail"], "members": _forget_collection(conn, key)}
        write_rows(conn, plan.changed.values())
        drop_rows(conn, plan.dropped)
        zr.done(conn, listed)
        zr.drop(conn, trees)
        for key in again:
            if key in plan.rows and plan.rows[key]["state"] != "pending":
                zr.backoff(conn, key, "a collection write did not land", now=now, kind="collection")
        # (a write refused as stale: the collection is listed again at once, so the next pass reads it fresh)
        zr.enqueue(conn, [{"id": key, "kind": "collection", "next_at": ""} for key in sorted(plan.stale)])
        if again or any(row["state"] == "pending" for row in plan.changed.values()):
            # the creates and the folder changes not sent yet: the tree is looked at again, a while later
            zr.backoff(conn, TREE_ENTRIES[0], str(plan.writes.error or "a collection write did not land"),
                       now=now, kind="page")
        plan.follow_ups(conn)  # those of the writes that landed
        conn.commit()
    r.decide_again(*plan.stale)
    _note_collections(r, plan)
    if plan.writes.error is not None:
        raise plan.writes.error


def _note_collections(r: Round, plan: _Folders) -> None:
    """The pass's log lines and conflict rows, after its commits. A field
    conflict keeps Zotero's side as the row holds it then
    (``field_base``), so taking Zotero's value later can tell whether
    Zotero changed it again since."""
    for action, stats in (("pulled", plan.pulled), ("pushed", plan.pushed)):
        if any(stats.values()):
            sync_engine._note(r.ws, FOLDERS, action, stats=dict(stats), report=r.report)
    for fid, name in plan.kept:
        sync_engine._note(r.ws, FOLDERS, "kept here", f"“{name}” is in Zotero's trash: its folder stays here, "
                                                       f"shared or kept on disk, until Zotero restores or removes it")
    for fid, name, ours, theirs, decided in plan.conflicts:
        if name == "rejected":
            record_conflict(r.ws, FOLDERS, fid, "zotero_rejected", mine=plan.folders.get(fid, {}).get("name", ""),
                            result=f"Zotero refused this folder: {ours}")
        elif name:
            synced = (plan.by_block.get(fid) or {}).get("zbase") or {}
            kept = not r.pushes and not (decided and r.adopt == "theirs")  # Gamma's side (``merge``), not sent
            record_conflict(r.ws, FOLDERS, fid, "zotero_field", mine=zotero_merge.field_line(name, ours),
                            theirs=zotero_merge.field_line(name, theirs),
                            base=json.dumps(field_base({name: theirs}, synced)),
                            result=f"The folder changed on both sides: {KEPT_HERE}" if kept else "", resolved=decided)
        else:
            record_conflict(r.ws, FOLDERS, fid, "zotero_deleted_edited", mine=ours,
                            theirs=zotero_merge.field_line("name", theirs),
                            result="Zotero changed this collection after its folder was deleted here: the folder is "
                                   "back")


def _filed_pages(conn, fids) -> list[str]:
    """The library pages filed in the folder ``fids`` (an id, or several:
    one read of the pages whatever their number) themselves."""
    fids = {fids} if isinstance(fids, str) else set(fids)
    if not fids:
        return []
    if len(fids) == 1:
        rows = conn.execute("SELECT id, properties FROM unified_blocks WHERE parent_id = 'root' AND properties LIKE ?",
                            (f'%"{next(iter(fids))}"%',))
    else:
        rows = conn.execute("SELECT id, properties FROM unified_blocks WHERE parent_id = 'root' "
                            "AND json_extract(properties, ?) IS NOT NULL", (f"$.{FOLDERS}",))
    return sorted(page_id for page_id, raw in rows if fids & set(filing(json_dict(raw), FOLDERS)))


def collections_gone(r: Round, keys) -> list[str]:
    """Collections Zotero removed for good (``/deleted``): a folder
    unchanged since it last synced goes here (one kept here while its
    collection was in Zotero's trash too: emptying the trash removes it for
    good), unless it changed since this pass read it, when the removal
    waits for the next one; one changed since it last synced stays and
    becomes a new collection under a fresh key (Zotero never takes a
    removed key again), and the pages filed in it are looked at again, so
    their items join it. The keys whose removal waits, queued again."""
    plan = _Folders(r)
    doomed, kept = [], []
    for key in keys:
        row = plan.rows.get(key)
        if row is None:
            continue
        here = row["state"] != "trashed" or bool(row["detail"].get("kept"))
        G = plan.gamma(row["block_id"]) if here else None
        if G is None or G == row["gbase"]:
            doomed.append((row["block_id"] if G is not None else "", row["key"]))
        else:
            kept.append((row["block_id"], row["key"], G))
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        for fid, key, G in kept:
            drop_rows(conn, [key])
            _forget_collection(conn, key)
            write_rows(conn, [new_row(random_key(), "collection", page_id=FOLDERS, block_id=fid, parent=G["parent"],
                                      version=-1, state="pending", gsig=plan.gsig(fid), detail={"sent": G})])
            zr.enqueue(conn, [zr.page_entry(page_id) for page_id in _filed_pages(conn, fid)])
        for fid, key in doomed:
            if not fid:
                drop_rows(conn, [key])
                _forget_collection(conn, key)
        conn.commit()
    deleted, again = 0, []
    for fid, key in doomed:
        if fid:
            def forget(conn, key=key):
                drop_rows(conn, [key])
                _forget_collection(conn, key)

            # the folder and those under it as this pass read them: one renamed or moved meanwhile stays,
            # and the removal is decided again next round
            ids = [fid, *plan._below(fid)]
            stamps = {bid: plan.stamps.get(bid) for bid in ids}
            try:
                if zotero_local.delete_folder(r.ws, fid, rows=forget, check=lambda conn, ids=ids, stamps=stamps:
                                              zotero_local.stamps(conn, ids) == stamps) is not None:
                    deleted += 1
            except zotero_local.Changed:
                again.append(key)
    if again:
        log.info(f"[zotero] {r.ws}: {len(again)} folder(s) changed while Zotero's removal was decided: next round")
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.enqueue(conn, [{"id": key, "kind": "collection", "next_at": ""} for key in again])
            conn.commit()
    if deleted:
        sync_engine._note(r.ws, FOLDERS, "deleted here", stats={"add": 0, "del": deleted, "mod": 0}, report=r.report)
    return again
