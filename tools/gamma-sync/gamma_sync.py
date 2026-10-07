#!/usr/bin/env python3
"""gamma-sync: keep a folder of your Gamma library as a folder on your disk.

One file, Python 3.10 or newer, standard library only: copy it anywhere.
It reads a Gamma server with an integration token (Settings → Integrations;
read access is enough) and writes the folder's papers and notes to a
directory of your choice, then keeps them up to date. Gamma to disk, one
way: what you change on disk is never sent back, and never overwritten
either (see the rules below).

    python gamma_sync.py folders --server https://gamma.example.com --token gamma_…
    python gamma_sync.py init  ~/Papers/Quantum --server https://gamma.example.com \\
                               --folder "Papers / Quantum" --token gamma_… --save-token
    python gamma_sync.py sync  ~/Papers/Quantum             # once
    python gamma_sync.py sync  ~/Papers/Quantum --watch 60  # and after every change
    python gamma_sync.py status ~/Papers/Quantum

What lands on disk (docs/dev/folder_sync.md in the Gamma repository):

    <dest>/
      .gamma-sync.json          the link: server, folder, and what was written
      Subfolder/                a folder below the linked one, empty ones too
        A paper.pdf             the paper, the file Gamma stores
        A paper.md              its notes and highlights (Obsidian dialect)
      A note.md                 a page without a PDF
      attachments/<hash>.png    the pictures the notes show

Rules: the client only ever writes, renames or removes files it wrote
itself. A file it finds in the way, or one you changed on disk, is left
alone and reported; ``--force`` replaces the changed ones. ``--full``
writes every file again. A token comes from ``--token``, the ``GAMMA_TOKEN``
environment variable, or the link when ``init --save-token`` kept it there.
"""

import argparse
import copy
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE = ".gamma-sync.json"
FORMAT = "gamma-sync-1"
PART = ".gamma-sync-part"   # what a file is called while it is being written
NOTES_BATCH = 100           # pages per notes request (the server takes 200)
default_open = None         # tests: (method, path, headers) → (status, headers, body stream)


class SyncError(Exception):
    pass


# --- the server --------------------------------------------------------------

class Server:
    """A thin reader of one Gamma server: JSON answers and file downloads,
    with the token as a bearer header."""

    def __init__(self, url, token, open_=None):
        self.url = url.rstrip("/")
        self.token = token
        self.open_ = open_ or default_open or self._urllib_open

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "gamma-sync/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _urllib_open(self, method, path, headers):
        req = urllib.request.Request(self.url + path, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            return resp.status, resp.headers, resp
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SyncError(f"cannot reach {self.url}: {e}") from e

    def get_json(self, path):
        status, _headers, body = self.open_("GET", path, self._headers())
        data = body.read()
        if status != 200:
            raise SyncError(_refusal(status, data, path))
        return json.loads(data.decode("utf-8"))

    def download(self, path, target: Path) -> str:
        """GET ``path`` into ``target``, written beside it and moved into
        place once whole; the sha256 of the bytes. A body shorter than the
        announced length is an error, never a file."""
        status, headers, body = self.open_("GET", path, self._headers())
        if status != 200:
            raise SyncError(_refusal(status, body.read(), path))
        length = headers.get("Content-Length")
        part = target.with_name(target.name + PART)
        digest, size = hashlib.sha256(), 0
        try:
            with open(part, "wb") as f:
                while chunk := body.read(1 << 20):
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if length is not None and size != int(length):
                raise SyncError(f"{path}: received {size} of {length} bytes")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        return digest.hexdigest()


def _refusal(status, data, path):
    try:
        detail = json.loads(data.decode("utf-8")).get("detail", "")
    except Exception:
        detail = ""
    if status == 401:
        return "the server refused the token: expired, revoked, or made for another server"
    if status == 403:
        return f"the token may not read this: {detail or 'forbidden'}"
    if status == 404:
        return f"not found on the server: {detail or path}"
    return f"{path}: {status} {detail}".strip()


# --- the link ---------------------------------------------------------------

class Link:
    """A destination directory and its state file: the server, the folder,
    and every file the client wrote (its page, its identity, its size and
    time on disk)."""

    def __init__(self, dest: Path, state=None):
        self.dest = dest
        self.file = dest / STATE
        self.state = state

    @classmethod
    def load(cls, dest: Path):
        link = cls(dest)
        if not link.file.is_file():
            raise SyncError(f"{dest} is not a linked folder: run init first")
        link.state = json.loads(link.file.read_text(encoding="utf-8"))
        if link.state.get("format") != FORMAT:
            raise SyncError(f"{link.file} was written by another version of gamma-sync")
        link.state.setdefault("files", {})
        link.state.setdefault("dirs", [])
        return link

    def save(self):
        part = self.file.with_name(self.file.name + PART)
        part.write_text(json.dumps(self.state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(part, self.file)


def kind(entry) -> str:
    return "pdf" if "doc" in entry else "notes" if "version" in entry else "attachment"


def identity(entry):
    return entry.get("doc") or entry.get("version")


def safe_rel(path: str) -> bool:
    """A relative path the server may ask for: forward slashes, no empty,
    dot or dot-dot parts, nothing that could leave the directory or collide
    with the client's own files."""
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    return all(p not in ("", ".", "..") and p != STATE and not p.endswith(PART) for p in parts)


def file_stat(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3)}


def modified(path: Path, entry) -> bool:
    """Whether the file differs from what the client wrote (size, or a time
    more than a second away). A missing file is not a modified one."""
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size != entry.get("size") or abs(st.st_mtime - float(entry.get("mtime") or 0)) > 1.0


def write_text(target: Path, text: str) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + PART)
    part.write_bytes(text.encode("utf-8"))
    os.replace(part, target)
    return file_stat(target)


# --- one round --------------------------------------------------------------

class Round:
    """One pass: the folder's manifest against the link's state, and the
    difference written to disk — renames first, then new and changed files,
    then the attachments the notes need, then what is gone."""

    def __init__(self, link: Link, server: Server, *, full=False, force=False, dry_run=False, say=print):
        self.link, self.server = link, server
        self.full, self.force, self.dry_run, self.say = full, force, dry_run, say
        self.dest = link.dest
        self.state = copy.deepcopy(link.state) if dry_run else link.state
        self.counts = {"added": 0, "updated": 0, "renamed": 0, "removed": 0, "kept": 0, "unchanged": 0}
        self.kept = []   # (path, why)

    def keep(self, path, why):
        self.kept.append((path, why))
        self.counts["kept"] += 1
        self.say("kept", f"{path}  ({why})")

    def run(self) -> dict:
        st = self.state
        folder = urllib.parse.quote(st["folder"], safe="")
        manifest = self.server.get_json(f"/api/sync/folders/{folder}")
        files = st["files"]
        wanted = self._wanted(manifest)
        old_dirs = list(st["dirs"])
        self._rename(files, wanted)
        self._make_dirs(manifest)
        notes_todo, pdf_todo = self._compare(files, wanted)
        self._fetch_notes(folder, notes_todo, files)
        self._fetch_pdfs(pdf_todo, files)
        self._attachments(files)
        self._remove(files, wanted)
        self._prune_dirs(manifest, old_dirs)
        st["cursor"] = manifest.get("cursor", "")
        st["folder_path"] = manifest["folder"]["path"]
        st["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not self.dry_run:
            self.link.save()
        return self.counts

    def _wanted(self, manifest) -> dict:
        """path → the entry the file should have: its page and identity."""
        wanted = {}
        for p in manifest["pages"]:
            if p.get("pdf") and safe_rel(p["pdf"]):
                wanted[p["pdf"]] = {"page": p["id"], "doc": p["doc_id"]}
            if self.state.get("notes", True) and p.get("notes") and safe_rel(p["notes"]):
                wanted[p["notes"]] = {"page": p["id"], "version": p["version"]}
        return wanted

    def _rename(self, files, wanted):
        """A page's file that moved — the title or a folder changed — is
        renamed on disk rather than fetched again, when it is still what the
        client wrote and nothing is in the way."""
        by_key = {(e["page"], kind(e)): path for path, e in files.items() if "page" in e}
        for new, w in wanted.items():
            old = by_key.get((w["page"], kind(w)))
            if not old or old == new or new in files:
                continue
            src, dst = self.dest / old, self.dest / new
            if src.is_file() and not dst.exists() and not modified(src, files[old]):
                self.say("renamed", f"{old} -> {new}")
                if not self.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, dst)
                files[new] = files.pop(old)
                self.counts["renamed"] += 1

    def _make_dirs(self, manifest):
        for d in manifest["dirs"]:
            if safe_rel(d["path"]) and not self.dry_run:
                (self.dest / d["path"]).mkdir(parents=True, exist_ok=True)

    def _compare(self, files, wanted):
        """Which wanted files to fetch, and why."""
        notes_todo, pdf_todo = [], []
        for path, w in wanted.items():
            have = files.get(path)
            target = self.dest / path
            if have is None:
                if target.exists():
                    self.keep(path, "a file gamma-sync did not write is already there")
                    continue
                verb = "added"
            elif identity(have) == identity(w) and not self.full:
                if target.is_file():
                    self.counts["unchanged"] += 1
                    continue
                verb = "restored"
            elif modified(target, have) and not self.force:
                self.keep(path, "changed on disk; --force replaces it")
                continue
            else:
                verb = "updated"
            (pdf_todo if kind(w) == "pdf" else notes_todo).append((path, w, verb))
        return notes_todo, pdf_todo

    def _count(self, verb):
        self.counts["added" if verb in ("added", "restored") else "updated"] += 1

    def _fetch_notes(self, folder, todo, files):
        for i in range(0, len(todo), NOTES_BATCH):
            batch = todo[i:i + NOTES_BATCH]
            got = {}
            if not self.dry_run:
                ids = ",".join(w["page"] for _, w, _ in batch)
                got = self.server.get_json(f"/api/sync/folders/{folder}/notes?pages={ids}")["pages"]
            for path, w, verb in batch:
                if self.dry_run:
                    self.say(verb, path)
                    self._count(verb)
                    continue
                page = got.get(w["page"])
                if page is None:
                    self.keep(path, "the server did not return this page")
                    continue
                self.say(verb, path)
                self._count(verb)
                files[path] = {"page": w["page"], "version": page.get("version") or w["version"],
                               "attachments": page.get("attachments") or [], **write_text(self.dest / path, page["markdown"])}

    def _fetch_pdfs(self, todo, files):
        for path, w, verb in todo:
            self.say(verb, path)
            self._count(verb)
            if self.dry_run:
                continue
            target = self.dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.server.download(f"/api/uploads/{w['doc']}.pdf", target)
            except SyncError as e:
                self.keep(path, str(e))
                continue
            files[path] = {"page": w["page"], "doc": w["doc"], **file_stat(target)}

    def _attachments(self, files):
        """The pictures and files the notes refer to, by content-hash name:
        fetched once, dropped when no note names them any more."""
        referenced = set()
        for e in files.values():
            referenced.update(e.get("attachments") or [])
        for name in sorted(referenced):
            path = f"attachments/{name}"
            if not safe_rel(path) or "/" in name:
                continue
            if path in files and (self.dest / path).is_file():
                continue
            self.say("added", path)
            self.counts["added"] += 1
            if self.dry_run:
                continue
            (self.dest / "attachments").mkdir(exist_ok=True)
            try:
                self.server.download(f"/api/uploads/{name}", self.dest / path)
            except SyncError as e:
                self.keep(path, str(e))
                continue
            files[path] = {"attachment": True, **file_stat(self.dest / path)}
        for path in [p for p, e in files.items() if kind(e) == "attachment" and p.split("/", 1)[-1] not in referenced]:
            self._delete(path, files)

    def _remove(self, files, wanted):
        for path in [p for p, e in files.items() if "page" in e and p not in wanted]:
            self._delete(path, files)

    def _delete(self, path, files):
        target = self.dest / path
        if target.is_file() and modified(target, files[path]):
            self.keep(path, "changed on disk; left in place, no longer synced")
            files.pop(path)
            return
        self.say("removed", path)
        self.counts["removed"] += 1
        if not self.dry_run:
            target.unlink(missing_ok=True)
        files.pop(path)

    def _prune_dirs(self, manifest, old_dirs):
        """Directories of folders that are gone go too, deepest first, when
        nothing is left in them."""
        new_dirs = sorted({d["path"] for d in manifest["dirs"] if safe_rel(d["path"])})
        for d in sorted(set(old_dirs) - set(new_dirs), key=len, reverse=True):
            p = self.dest / d
            if p.is_dir() and not any(p.iterdir()):
                self.say("removed", d + "/")
                if not self.dry_run:
                    p.rmdir()
        self.state["dirs"] = new_dirs


# --- commands ---------------------------------------------------------------

def resolve_token(args, state=None) -> str:
    token = getattr(args, "token", None) or os.environ.get("GAMMA_TOKEN") or (state or {}).get("token")
    if not token:
        raise SyncError("no token: pass --token, set GAMMA_TOKEN, or link with init --save-token")
    return token


def _path_text(names) -> str:
    return " / ".join(names)


def resolve_folder(server: Server, text: str):
    """The folder ``text`` names — its id, or its path with ``/`` between the
    names (spaces around the slashes do not matter, nor does case); ``root``
    is the whole library — as ``(id, names)``."""
    if text.strip().lower() == "root":
        return "root", []
    folders = server.get_json("/api/sync/folders")["folders"]
    for f in folders:
        if f["id"] == text:
            return f["id"], f["path"]
    norm = "/".join(part.strip().lower() for part in text.split("/"))
    found = [f for f in folders if "/".join(n.strip().lower() for n in f["path"]) == norm
             or _path_text(f["path"]).lower() == text.strip().lower()]
    if len(found) == 1:
        return found[0]["id"], found[0]["path"]
    if found:
        raise SyncError(f"{len(found)} folders are called {text!r}; name one by id: "
                        + ", ".join(f["id"] for f in found))
    raise SyncError(f"no folder called {text!r}; `folders` lists them")


def cmd_folders(args):
    server = Server(args.server, resolve_token(args))
    print("root  (the whole library)")
    for f in server.get_json("/api/sync/folders")["folders"]:
        print(f"{_path_text(f['path'])}  ({f['id']})")
    return 0


def cmd_init(args):
    dest = Path(args.dest).expanduser().resolve()
    if (dest / STATE).exists():
        raise SyncError(f"{dest} is already linked; run sync, or delete {STATE} to link it again")
    token = resolve_token(args)
    server = Server(args.server, token)
    who = server.get_json("/api/sync/whoami")
    folder_id, path = resolve_folder(server, args.folder)
    dest.mkdir(parents=True, exist_ok=True)
    state = {"format": FORMAT, "server": server.url, "workspace": who["workspace"]["id"],
             "workspace_name": who["workspace"].get("name", ""), "folder": folder_id, "folder_path": path,
             "notes": not args.no_notes, "files": {}, "dirs": [], "cursor": ""}
    if args.save_token:
        state["token"] = token
    Link(dest, state).save()
    where = _path_text(path) or "the whole library"
    print(f"Linked {dest} to {where} in {state['workspace_name'] or who['workspace']['id']} on {server.url}.")
    print(f"Next: python {Path(sys.argv[0]).name} sync {dest}")
    return 0


def _summary(counts) -> str:
    parts = [f"{n} {verb}" for verb, n in counts.items() if n and verb != "unchanged"]
    return ", ".join(parts) if parts else "up to date"


def _changed_since(server: Server, cursor: str) -> bool:
    if not cursor:
        return True
    feed = server.get_json(f"/api/sync/changes?since={urllib.parse.quote(cursor)}&limit=1")
    return bool(feed.get("pages") or feed.get("deleted"))


def cmd_sync(args):
    dest = Path(args.dest).expanduser().resolve()
    link = Link.load(dest)
    server = Server(link.state["server"], resolve_token(args, link.state))
    while True:
        try:
            counts = Round(link, server, full=args.full, force=args.force, dry_run=args.dry_run).run()
            print(("would: " if args.dry_run else "") + _summary(counts)
                  + (f"; {counts['unchanged']} unchanged" if counts["unchanged"] else ""))
        except SyncError as e:
            if not args.watch:
                raise
            print(f"error: {e}", file=sys.stderr)
        if not args.watch:
            return 0
        while True:
            time.sleep(args.watch)
            try:
                if _changed_since(server, link.state.get("cursor", "")):
                    break
            except SyncError as e:
                print(f"error: {e}", file=sys.stderr)


def cmd_status(args):
    dest = Path(args.dest).expanduser().resolve()
    st = Link.load(dest).state
    kinds = {"pdf": 0, "notes": 0, "attachment": 0}
    for e in st["files"].values():
        kinds[kind(e)] += 1
    print(f"{dest}")
    print(f"  folder:    {_path_text(st.get('folder_path') or []) or 'the whole library'} ({st['folder']})")
    print(f"  server:    {st['server']} ({st.get('workspace_name') or st.get('workspace')})")
    print(f"  notes:     {'on' if st.get('notes', True) else 'off'}")
    print(f"  last sync: {st.get('synced_at') or 'never'}")
    print(f"  files:     {kinds['pdf']} PDFs, {kinds['notes']} notes, {kinds['attachment']} attachments, "
          f"{len(st['dirs'])} folders")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gamma_sync.py", description=__doc__.split("\n\n", 1)[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("folders", help="list the workspace's folders")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--token", help="an integration token (or set GAMMA_TOKEN)")
    p.set_defaults(run=cmd_folders)

    p = sub.add_parser("init", help="link a directory to a folder")
    p.add_argument("dest", help="the directory on disk (made if missing)")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--folder", required=True, help="the folder: its path (Papers / Quantum), its id, or root")
    p.add_argument("--token", help="an integration token (or set GAMMA_TOKEN)")
    p.add_argument("--save-token", action="store_true", help=f"keep the token in {STATE} (readable by anyone with the directory)")
    p.add_argument("--no-notes", action="store_true", help="PDFs only, no notes files")
    p.set_defaults(run=cmd_init)

    p = sub.add_parser("sync", help="bring the directory up to date")
    p.add_argument("dest")
    p.add_argument("--token")
    p.add_argument("--watch", type=float, default=0, metavar="SECONDS",
                   help="keep running: look for changes every SECONDS and sync when there are any")
    p.add_argument("--full", action="store_true", help="write every file again, not only the changed ones")
    p.add_argument("--force", action="store_true", help="replace files changed on disk")
    p.add_argument("--dry-run", action="store_true", help="say what would be done, change nothing")
    p.set_defaults(run=cmd_sync)

    p = sub.add_parser("status", help="what the directory is linked to and holds")
    p.add_argument("dest")
    p.set_defaults(run=cmd_status)

    args = parser.parse_args(argv)
    try:
        return args.run(args) or 0
    except SyncError as e:
        print(f"gamma-sync: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # titles are not all Latin; a cp1252 console must not stop the sync
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
