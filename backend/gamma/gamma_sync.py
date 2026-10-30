#!/usr/bin/env python3
"""gamma-sync: keep a folder of your Gamma library as a folder on your disk.

One file, Python 3.10 or newer, standard library only: copy it anywhere.
It reads a Gamma server with an integration token (Settings → Integrations;
read access is enough unless PDFs go back) and writes the folder's papers
and notes to a directory of your choice, then keeps them up to date. Gamma
to disk: what you change on disk is never overwritten (see the rules
below), and goes back only when asked. A directory linked with
``init --push`` sends a PDF you change in it to Gamma as its page's new
file, and ``push`` does the same for one PDF anywhere on disk (a manuscript
compiled again from its TeX); the page keeps its notes and highlights.
Notes files never go back. The same rounds run inside the desktop app's
own server for the folders and files it keeps on the PC
(gamma/folder_links.py), which is why this file lives in the gamma package;
it imports nothing from it.

    python gamma_sync.py folders --server https://gamma.example.com --token gamma_…
    python gamma_sync.py init  ~/Papers/Quantum --server https://gamma.example.com \\
                               --folder "Papers / Quantum" --token gamma_… --save-token
    python gamma_sync.py sync  ~/Papers/Quantum             # once
    python gamma_sync.py sync  ~/Papers/Quantum --watch 60  # and after every change
    python gamma_sync.py status ~/Papers/Quantum
    python gamma_sync.py push  build/main.pdf --page <page id> --server https://gamma.example.com \\
                               --token gamma_… --watch 5    # each new build becomes the page's PDF

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
writes every file again. A PDF goes back once it is whole (written a few
seconds ago, ending in ``%%EOF``) and only when its bytes are not Gamma's
already; one changed both on disk and in Gamma is kept and reported, and
``--force`` takes Gamma's. A token comes from ``--token``, the
``GAMMA_TOKEN`` environment variable, or the link when ``init --save-token``
kept it there; sending PDFs back takes a token that may write.
"""

import argparse
import copy
import hashlib
import http.client
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
DOC_CHARS = 24              # a stored PDF's id: the first hex characters of its SHA-256 (the server's DIGEST_CHARS)
SETTLE_S = 3.0              # a file written more recently than this is still being written
EOF_TAIL = 1024             # a whole PDF has "%%EOF" within its last bytes
KEEP_OLD_DAYS = 1           # how long the server keeps a file a push replaced, once nothing references it
CHUNK = 1 << 20             # bytes read from disk at a time
NOT_WHOLE = "not a whole PDF yet"   # ``settled``'s answer for a file at rest that does not end as a PDF ends
default_open = None         # tests: (method, path, headers, body) → (status, headers, body stream)


class SyncError(Exception):
    pass


class NotFound(SyncError):
    """A 404: the server has no such thing (a page that is gone, among others)."""


class Changed(SyncError):
    """A file that changed while it was read to be sent (``_parts``): a push
    lets it wait for the next pass rather than keep it."""


# --- the server --------------------------------------------------------------

class Server:
    """A thin client of one Gamma server: JSON answers, file downloads, and
    the two writes a push makes (a JSON body, a PDF upload), with the token
    as a bearer header."""

    def __init__(self, url, token, timeout=120):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout   # seconds a request may wait on a silent server
        self.open_ = default_open or self._urllib_open

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "gamma-sync/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _urllib_open(self, method, path, headers, body=None):
        # a body is bytes or an iterable of them, with its Content-Length among the headers
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            return resp.status, resp.headers, resp
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SyncError(f"cannot reach {self.url}: {e}") from e

    def source(self, folder_id):
        """The folder's reads for a ``Round``."""
        return RemoteSource(self, folder_id)

    def _json(self, method, path, headers, body=None) -> dict:
        """The server's JSON answer. An answer cut short, or one that is no
        JSON (a sign-in or captive portal's page, answered with 200), is a
        SyncError like a refusal, so a round reports it."""
        status, _headers, resp = self.open_(method, path, headers, body)
        try:
            data = resp.read()
        except http.client.HTTPException as e:   # IncompleteRead among others; not an OSError
            raise SyncError(f"{path}: the answer broke off ({e!r})") from e
        if not 200 <= status < 300:
            raise (NotFound if status == 404 else SyncError)(_refusal(status, data, path))
        try:
            return json.loads(data.decode("utf-8")) if data else {}
        except ValueError as e:
            raise SyncError(f"{path}: the server did not answer JSON") from e

    def get_json(self, path):
        return self._json("GET", path, self._headers())

    def send_json(self, method, path, obj) -> dict:
        """``obj`` sent as a JSON body: the server's JSON answer."""
        body = json.dumps(obj).encode("utf-8")
        return self._json(method, path, {**self._headers(), "Content-Type": "application/json",
                                         "Content-Length": str(len(body))}, body)

    def upload_pdf(self, path: Path) -> dict:
        """The PDF at ``path`` stored on the server (``POST /api/uploads``,
        one multipart part streamed from disk): ``{doc_id, ...}``. Bytes the
        server stores already are stored once."""
        boundary = f"gamma-sync-{os.urandom(12).hex()}"
        name = path.name.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/pdf\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("ascii")
        size = path.stat().st_size
        headers = {**self._headers(), "Content-Type": f"multipart/form-data; boundary={boundary}",
                   "Content-Length": str(len(head) + size + len(tail))}
        return self._json("POST", "/api/uploads", headers, _parts(head, path, size, tail))

    def download(self, path, target: Path) -> None:
        """GET ``path`` into ``target``, written beside it and moved into
        place once whole. A body shorter than the announced length is an
        error, never a file."""
        status, headers, body = self.open_("GET", path, self._headers())
        if status != 200:
            raise SyncError(_refusal(status, body.read(), path))
        length = headers.get("Content-Length")
        part = target.with_name(target.name + PART)
        size = 0
        try:
            with open(part, "wb") as f:
                while chunk := body.read(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
            if length is not None and size != int(length):
                raise SyncError(f"{path}: received {size} of {length} bytes")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)


def _parts(head: bytes, path: Path, size: int, tail: bytes):
    """A multipart body: ``head``, the first ``size`` bytes of ``path``,
    ``tail``. A file that shrank meanwhile is ``Changed``, never a short
    body."""
    yield head
    left = size
    with open(path, "rb") as f:
        while left:
            chunk = f.read(min(left, CHUNK))
            if not chunk:
                raise Changed(f"{path.name} changed while it was sent")
            left -= len(chunk)
            yield chunk
    yield tail


class RemoteSource:
    """What a round reads of one folder, over HTTP: the manifest, the notes
    files of some pages, a stored file. The server's own rounds read the
    same three things in-process (gamma/folder_links.py ``LocalSource``)."""

    def __init__(self, server: Server, folder_id: str):
        self.server = server
        self.folder = urllib.parse.quote(folder_id, safe="")

    def manifest(self) -> dict:
        return self.server.get_json(f"/api/sync/folders/{self.folder}")

    def notes(self, page_ids) -> dict:
        return self.server.get_json(f"/api/sync/folders/{self.folder}/notes?pages={','.join(page_ids)}")["pages"]

    def download(self, name: str, target: Path) -> None:
        self.server.download(f"/api/uploads/{name}", target)


class RemoteSink:
    """Where a push writes, over HTTP with a token that may write: the
    file's bytes stored, a page's PDF replaced by them, and what the page
    shows now. The desktop app's own server writes the same three
    in-process (gamma/folder_links.py ``LocalSink``)."""

    def __init__(self, server: Server):
        self.server = server

    def store(self, path: Path) -> str:
        return self.server.upload_pdf(path)["doc_id"]

    def replace(self, page_id: str, doc_id: str, filename: str) -> dict:
        return self.server.send_json("PUT", f"/api/pages/{urllib.parse.quote(page_id, safe='')}/attachment",
                                     {"doc_id": doc_id, "original_filename": filename, "keep_old_days": KEEP_OLD_DAYS})

    def current(self, page_id: str) -> dict | None:
        """``{doc_id, filename, title}`` of the page now (``""`` for no PDF
        or no file name); None when it is gone, in the trash, or no page."""
        try:
            page = self.server.get_json(f"/api/blocks/{urllib.parse.quote(page_id, safe='')}")
        except NotFound:
            return None
        if page.get("parent_id") != "root":
            return None
        props = page.get("properties") or {}
        return {"doc_id": str(props.get("doc_id") or ""), "filename": str(props.get("original_filename") or ""),
                "title": page.get("content") or ""}


def _refusal(status, data, path):
    try:
        detail = json.loads(data.decode("utf-8")).get("detail", "")
    except Exception:
        detail = ""
    if status == 401:
        return "the server refused the token: expired, revoked, or made for another server"
    if status == 403:
        return f"the token may not do this: {detail or 'forbidden'}"
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


# --- sending PDFs back ------------------------------------------------------

def doc_of(path: Path) -> str:
    """The id Gamma gives the file's bytes: the first DOC_CHARS hex
    characters of their SHA-256. Equal to a page's doc_id, the file is the
    page's PDF, and nothing needs to travel to know it."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()[:DOC_CHARS]


def settled(path: Path) -> str:
    """"" when the file is whole and may be sent: written at least SETTLE_S
    ago, open for reading (Windows refuses while its writer holds it), and
    ending as a PDF ends, with "%%EOF" in its last EOF_TAIL bytes. Otherwise
    why not yet; a compile writes its PDF over a second or more."""
    try:
        written = path.stat().st_mtime
    except OSError:
        return "the file is not there"
    if time.time() - written < SETTLE_S:
        return "still being written"
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - EOF_TAIL))
            tail = f.read()
    except OSError as e:
        return f"cannot be read: {e}"
    return "" if b"%%EOF" in tail else NOT_WHOLE


def moved(path: Path, entry) -> bool:
    """Whether a loop has a round to start for the file: it is there and
    moved since the entry recorded it, or since ``seen``, the stat a push
    recorded when it could not send the file (kept, or not a whole PDF), so
    that file starts no round until it moves again. A round that runs for
    another reason still looks at it (``Push``)."""
    return modified(path, entry.get("seen") or entry)


def pdfs_moved(dest: Path, files: dict) -> bool:
    """Whether a PDF among a link's ``files`` (its state's) moved in ``dest``
    since a round recorded it (``moved``): a link that sends PDFs back has
    a round to run, whatever Gamma's change feed says."""
    return any(kind(e) == "pdf" and safe_rel(p) and moved(dest / p, e) for p, e in files.items())


def _stat(path: Path) -> dict | None:
    try:
        return file_stat(path)
    except OSError:
        return None


class Push:
    """PDFs changed on disk made their pages' new files: one pass over
    ``items``, each ``(key, file, entry)`` with the entry what was last known
    of the file (``{page, doc, size, mtime}``, and ``seen``: the stat of a
    file a pass could not send), brought up to date in place. It writes
    through a **sink** (``store``, ``replace``, ``current``: ``RemoteSink``
    over HTTP, or the desktop server's ``LocalSink``). A file is sent only
    when it differs from what the entry recorded, is whole (``settled``)
    and holds other bytes than the page shows. It says what it sends; what
    waits and what is kept it lists (``waiting``, ``kept``) for its caller
    to report. Every pass looks at a kept file again (and sends it again
    when it is whole): the next one that runs for any reason. ``seen``
    only keeps the loops (``moved``) from starting a pass for it before it
    moves, or, for the desktop server's file link, before its retry wait
    (``RETRY_S``) ends. A file that changed while it was read to be sent
    waits, without ``seen``.

    ``mode`` "folder" is a kept folder's PDFs, sent before its pull: a
    missing file is the pull's to fetch again, and one whose page Gamma gave
    another file meanwhile is left to the pull, which keeps the disk's and
    says so. ``mode`` "file" is one followed file, where the disk wins: a
    file replaced in Gamma by hand gives way at the file's next change."""

    def __init__(self, items, sink, *, mode, dry_run=False, say=print):
        self.items, self.sink, self.mode, self.dry_run, self.say = items, sink, mode, dry_run, say
        self.counts = {"sent": 0, "waiting": 0, "kept": 0}
        self.sent = []      # (key, the server's answer to the replacement)
        self.waiting = []   # (key, why): not sent yet, tried again at the next pass
        self.kept = []      # (key, why): not sent; the next pass that runs tries again (``seen``)

    def run(self) -> dict:
        for key, file, entry in self.items:
            try:
                self._push(key, file, entry)
            except (SyncError, OSError) as e:
                self._keep(key, file, entry, str(e))
        return self.counts

    def _push(self, key, file: Path, entry):
        before = _stat(file)
        if before is None:
            if self.mode == "file":
                self._wait(key, "the file is not there")
            return
        if not modified(file, entry):
            return
        why = settled(file)
        if why:
            self._wait(key, why)
            if why == NOT_WHOLE:
                # at rest and still no PDF's end (a download cut short, another file saved over it): no
                # loop starts a pass for it until it is written again, which moves its size or time. A
                # file still being written or locked has no such mark: a lock can end with no new stat.
                entry["seen"] = before
            return
        doc = doc_of(file)
        if doc == entry.get("doc"):
            self._record(entry, doc, before)   # touched, the same bytes
            return
        page = self.sink.current(entry["page"])
        if page is None:
            self._keep(key, file, entry, "the page is no longer in Gamma")
            return
        if page["doc_id"] == doc:
            self._record(entry, doc, before)   # Gamma has these bytes already
            return
        if self.mode == "folder" and page["doc_id"] != entry.get("doc"):
            entry["seen"] = before             # changed here and in Gamma: the pull keeps the file
            return
        if self.dry_run:
            self.say("would send", key)
            self.counts["sent"] += 1
            return
        try:
            stored = self.sink.store(file)
        except Changed:
            stored = None
        except (SyncError, OSError):
            if _stat(file) == before:
                raise             # the server's refusal, or the disk's: kept
            stored = None         # the file moved under the read, whatever the sink made of that
        if stored != doc or _stat(file) != before:
            self._wait(key, "the file changed while it was sent")
            return
        answer = self.sink.replace(entry["page"], stored, page["filename"] or file.name)
        self._record(entry, stored, before)
        self.sent.append((key, answer))
        self.counts["sent"] += 1
        self.say("sent", key)

    @staticmethod
    def _record(entry, doc, stat):
        entry.update(doc=doc, **stat)
        entry.pop("seen", None)

    def _wait(self, key, why):
        self.waiting.append((key, why))
        self.counts["waiting"] += 1

    def _keep(self, key, file: Path, entry, why):
        self.kept.append((key, why))
        self.counts["kept"] += 1
        seen = _stat(file)
        if seen is not None:
            entry["seen"] = seen


# --- one round --------------------------------------------------------------

class Round:
    """One pass: the folder's manifest against the link's state, and the
    difference written to disk — renames first, then new and changed files,
    then the attachments the notes need, then what is gone. A link that
    sends PDFs back (its state's ``push``, and a ``sink`` to write through)
    first sends the ones changed on disk (``Push``)."""

    def __init__(self, link: Link, source, *, sink=None, full=False, force=False, dry_run=False, say=print):
        self.link, self.source = link, source
        self.full, self.force, self.dry_run, self.say = full, force, dry_run, say
        self.dest = link.dest
        self.state = copy.deepcopy(link.state) if dry_run else link.state
        self.sink = sink if self.state.get("push") else None
        self.counts = {"added": 0, "updated": 0, "renamed": 0, "removed": 0, "kept": 0, "unchanged": 0,
                       "sent": 0, "waiting": 0}
        self.kept = []   # (path, why, force): ``force`` when a forced round (``--force``) would replace it

    def keep(self, path, why, force=False):
        self.kept.append((path, why, force))
        self.counts["kept"] += 1
        self.say("kept", f"{path}  ({why})")

    def run(self) -> dict:
        st = self.state
        files = st["files"]
        if self.sink is not None:
            self._push(files)
        manifest = self.source.manifest()
        wanted = self._wanted(manifest)
        old_dirs = list(st["dirs"])
        self._rename(files, wanted)
        self._make_dirs(manifest)
        notes_todo, pdf_todo = self._compare(files, wanted)
        self._fetch_notes(notes_todo, files)
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

    def _push(self, files):
        """The PDFs changed on disk, sent before the manifest is fetched, so
        that it names their new identity and the pull leaves them alone."""
        items = [(path, self.dest / path, e) for path, e in files.items() if kind(e) == "pdf" and safe_rel(path)]
        push = Push(items, self.sink, mode="folder", dry_run=self.dry_run, say=self.say)
        push.run()
        self.counts["sent"] += push.counts["sent"]
        self.counts["waiting"] += push.counts["waiting"]
        for path, why in push.waiting:
            self.say("waiting", f"{path}  ({why})")
        for path, why in push.kept:
            self.keep(path, why)   # not sent: Gamma's file is the same, so --force has nothing to replace

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
                self.keep(path, "changed here and in Gamma; --force takes Gamma's"
                          if self.sink is not None and kind(w) == "pdf" else "changed on disk; --force replaces it",
                          force=True)
                continue
            else:
                verb = "updated"
            (pdf_todo if kind(w) == "pdf" else notes_todo).append((path, w, verb))
        return notes_todo, pdf_todo

    def _count(self, verb):
        self.counts["added" if verb in ("added", "restored") else "updated"] += 1

    def _fetch_notes(self, todo, files):
        for i in range(0, len(todo), NOTES_BATCH):
            batch = todo[i:i + NOTES_BATCH]
            got = {}
            if not self.dry_run:
                got = self.source.notes([w["page"] for _, w, _ in batch])
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
                self.source.download(f"{w['doc']}.pdf", target)
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
                self.source.download(name, self.dest / path)
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
    if args.push and who.get("scope") != "write":
        raise SyncError("--push needs a token that may write")
    folder_id, path = resolve_folder(server, args.folder)
    dest.mkdir(parents=True, exist_ok=True)
    state = {"format": FORMAT, "server": server.url, "workspace": who["workspace"]["id"],
             "workspace_name": who["workspace"].get("name", ""), "folder": folder_id, "folder_path": path,
             "notes": not args.no_notes, "push": args.push, "files": {}, "dirs": [], "cursor": ""}
    if args.save_token:
        state["token"] = token
    Link(dest, state).save()
    where = _path_text(path) or "the whole library"
    print(f"Linked {dest} to {where} in {state['workspace_name'] or who['workspace']['id']} on {server.url}.")
    if args.push:
        print("PDFs you change there go back to Gamma.")
    print(f"Next: python {Path(sys.argv[0]).name} sync {dest}")
    return 0


def _summary(counts) -> str:
    parts = [f"{n} {verb}" for verb, n in counts.items() if n and verb != "unchanged"]
    return ", ".join(parts) if parts else "up to date"


def changed_since(server: Server, cursor: str) -> bool:
    """Whether the server's change feed moved past ``cursor`` (always, for
    no cursor yet): one request, what ``--watch`` and a server's own links
    with a remote source (gamma/folder_links.py) ask between rounds."""
    if not cursor:
        return True
    feed = server.get_json(f"/api/sync/changes?since={urllib.parse.quote(cursor)}&limit=1")
    return bool(feed.get("pages") or feed.get("deleted"))


def cmd_sync(args):
    dest = Path(args.dest).expanduser().resolve()
    link = Link.load(dest)
    server = Server(link.state["server"], resolve_token(args, link.state))
    sink = RemoteSink(server) if link.state.get("push") else None
    while True:
        try:
            counts = Round(link, server.source(link.state["folder"]), sink=sink, full=args.full, force=args.force,
                           dry_run=args.dry_run).run()
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
            if sink is not None and pdfs_moved(dest, link.state["files"]):
                break
            try:
                if changed_since(server, link.state.get("cursor", "")):
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
    print(f"  push:      {'on: PDFs changed here go back' if st.get('push') else 'off'}")
    print(f"  last sync: {st.get('synced_at') or 'never'}")
    print(f"  files:     {kinds['pdf']} PDFs, {kinds['notes']} notes, {kinds['attachment']} attachments, "
          f"{len(st['dirs'])} folders")
    return 0


def _pushed(push: Push, dry_run: bool) -> str:
    """What one push of a followed file did, in a line."""
    if push.counts["sent"]:
        if dry_run:
            return "would send"
        problem = push.sent[0][1].get("relocation_error")
        return f"sent; the highlights stay where they were: {problem}" if problem else "sent"
    if push.waiting:
        return f"waiting: {push.waiting[0][1]}"
    if push.kept:
        return f"not sent: {push.kept[0][1]}"
    return "up to date"


def _rested(path: Path) -> None:
    """Sleep until the file was last written SETTLE_S ago: ``push`` runs the
    moment a build ends, and a file that fresh would only wait. Never more
    than SETTLE_S: a file dated in the future (a clock set wrong) does not
    hold the run up."""
    try:
        fresh = SETTLE_S - (time.time() - path.stat().st_mtime)
    except OSError:
        return
    if fresh > 0:
        time.sleep(min(fresh, SETTLE_S))


def cmd_push(args):
    file = Path(args.file).expanduser().resolve()
    server = Server(args.server, resolve_token(args))
    if server.get_json("/api/sync/whoami").get("scope") != "write":
        raise SyncError("push needs a token that may write")
    sink = RemoteSink(server)
    if sink.current(args.page) is None:
        raise SyncError(f"no page {args.page} in Gamma")
    entry = {"page": args.page}   # what this run knows of the file; the page is the state between runs
    while True:
        _rested(file)
        push = Push([(file.name, file, entry)], sink, mode="file", dry_run=args.dry_run, say=lambda *_: None)
        push.run()
        print(_pushed(push, args.dry_run))
        if not args.watch:
            return 0
        while True:
            time.sleep(args.watch)
            if moved(file, entry):
                break


# Options whose value is free text. A page, folder or block id comes from
# token_urlsafe, so about one in 64 begins with "-", which argparse reads as
# another option ("--folder: expected one argument"): `--folder <id>` is
# passed on as `--folder=<id>`, the one spelling argparse takes as given.
VALUE_OPTIONS = ("--server", "--token", "--folder", "--page", "--watch")


def glued(argv):
    """``argv`` with each VALUE_OPTIONS value joined to its option by "=".
    An option at the very end keeps its own missing-value error."""
    out, rest = [], list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in VALUE_OPTIONS and rest:
            arg = f"{arg}={rest.pop(0)}"
        out.append(arg)
    return out


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
    p.add_argument("--push", action="store_true",
                   help="send PDFs you change in the directory back to Gamma (a token that may write)")
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

    p = sub.add_parser("push", help="make a PDF on disk a page's file, now or at each change")
    p.add_argument("file", help="the PDF on disk")
    p.add_argument("--page", required=True, help="the page's id")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--token", help="an integration token that may write (or set GAMMA_TOKEN)")
    p.add_argument("--watch", type=float, default=0, metavar="SECONDS",
                   help="keep running: look at the file every SECONDS and send each new version")
    p.add_argument("--dry-run", action="store_true", help="say whether it would be sent, send nothing")
    p.set_defaults(run=cmd_push)

    args = parser.parse_args(glued(sys.argv[1:] if argv is None else argv))
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
