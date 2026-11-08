"""The PDF translated view's paragraph cache, apart from the HTTP side
(routers/translate.py): a translation's key, the in-memory LRU, a stored
paper's kept translations (Kept, over gamma/translate_store.py), and one
request's texts looked up against both (TranslateBatch).

A translation is looked up in memory, per (account, target language,
model, the paper and whether its brief came along, source text): the LRU
makes retries, re-shows and halted-job resumes free until the server
restarts. A paper whose PDF is stored in the workspace also keeps its
translations in data.db, read before memory (they are the workspace's, which
another account's Translate again may have replaced) and written when the
request asks to keep them. The viewer reads them back with no model call
through the lookup route (Kept.read).
docs/dev/translation.md describes the whole path.
"""

import hashlib
import threading
from collections import OrderedDict
from contextlib import ExitStack

from . import translate_store

# Key (see translate_key) → translated text. Process-wide, never written to
# disk; a restart simply starts cold. The lock matters: requests run in
# FastAPI's threadpool and the viewer fires several in parallel, so touches
# and evictions would otherwise race.
_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_CAP = 5000     # cached paragraphs kept in memory (LRU)
_LOCK = threading.Lock()

# (workspace, doc id) → how often the document's translations were
# forgotten (DELETE /api/translations/{doc_id}), and workspace → how often
# all of its were cleared (DELETE /api/translations). A document's
# generation is the two added up. The paper's memory keys, its
# translations' and its brief's (translate_context.brief_key), carry it, so
# a forgotten or cleared document misses memory too and a later job
# translates afresh; the entries under the old keys just age out of their
# LRUs. Both counts only grow, so the sum never returns to one an old key
# carries.
_generation: dict = {}
_ws_generation: dict = {}

# A forget or a clear and a request's kept writes of the same document take
# turns (Kept.put, Kept.put_brief, forget, clear): a write whose generation
# check passed must not land after the delete. Striped by document, so
# writes of different documents (and workspaces) do not wait on each other;
# a clear takes every stripe.
_KEEPING = [threading.Lock() for _ in range(32)]


def _keeping(ws: str, doc_id: str) -> threading.Lock:
    return _KEEPING[hash((ws, doc_id)) % len(_KEEPING)]


def generation(ws: str, doc_id: str) -> int:
    with _LOCK:
        return _generation.get((ws, doc_id), 0) + _ws_generation.get(ws, 0)


def forget(ws: str, doc_id: str) -> int:
    """Forget the document's translations: its kept rows and brief in every
    language and model, and its memory keys (made new). How many rows went.
    The rows go before the generation moves on: a request that reads the new
    generation then finds none of them in the store to bring back."""
    with _keeping(ws, doc_id):
        removed = translate_store.forget(ws, doc_id)
        with _LOCK:
            _generation[(ws, doc_id)] = _generation.get((ws, doc_id), 0) + 1
    return removed


def clear(ws: str) -> int:
    """Forget every translation the workspace keeps, as forget does one
    document's: its kept rows and briefs, and every document's memory keys
    (the workspace's generation moves on). How many rows went. It takes
    every document's turn, always in the same order (a writer holds one
    stripe and a forget one, so none waits on another in a circle), so no
    write whose check passed lands after the delete."""
    with ExitStack() as turns:
        for lock in _KEEPING:
            turns.enter_context(lock)
        removed = translate_store.clear(ws)
        with _LOCK:
            _ws_generation[ws] = _ws_generation.get(ws, 0) + 1
    return removed


def _cache_get(keys: list) -> dict:
    """LRU-touching lookup: {key: translation} for every key already cached."""
    with _LOCK:
        hits = {}
        for k in keys:
            if k in _CACHE:
                _CACHE.move_to_end(k)
                hits[k] = _CACHE[k]
        return hits


def _cache_put(key: str, text: str):
    with _LOCK:
        _CACHE[key] = text
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_CAP:
            _CACHE.popitem(last=False)


def translate_key(user_id: str, lang: str, model: str, paper: str, brief: bool, text: str, stamp: str = "") -> str:
    # Keyed on the bare model NAME (not the registry id): registry ids embed
    # the provider-entry id, which changes when a key is re-added — the cached
    # translation shouldn't die with it. The account id scopes the shared
    # in-memory dict per account. `paper` names the paper and its generation
    # (translate_context.brief_key; "" for none) and `brief` whether its
    # brief came along, so a translation made without the paper's context
    # stays apart from one made with it. The key names the paper, not the
    # brief's text, so the translation outlives the brief in its LRU.
    # `stamp` names the glossary terms the text uses (translate_terms), so a
    # changed term misses; with none ("") the key is what it always was.
    key = f"{user_id}\x00{lang}\x00{model}\x00{paper}\x00{int(brief)}\x00{text}"
    return hashlib.sha256((f"{key}\x00{stamp}" if stamp else key).encode()).hexdigest()


class Kept:
    """A stored paper's kept translations and brief in one language and
    model (translate_store's rows of ``doc_id`` in ``ws``), as one request
    reads and adds to them. ``write``: the request asked to keep them and its
    account may change the workspace. ``generation``: the document's, read
    once as the request began (its memory keys carry the same). A forget
    after that drops what the request would add, so a translation in flight
    does not bring a forgotten document's back. ``stamp``: a text's stamp of
    the glossary terms it uses (translate_terms.Glossary.stamp), None for a
    request that takes no terms (a service)."""

    def __init__(self, ws: str, doc_id: str, lang: str, model: str, write: bool, generation: int, stamp=None):
        self.ws, self.doc_id, self.lang, self.model, self.write = ws, doc_id, lang, model, write
        self.generation = generation
        self.stamp = stamp

    def _where(self) -> tuple:
        return self.ws, self.doc_id, self.lang, self.model

    def _writable(self) -> bool:
        """(Under the document's _keeping lock, held through the write.)"""
        return self.write and generation(self.ws, self.doc_id) == self.generation

    def src(self, text: str) -> str:
        """A text's row name: what it is kept under and found by, with the
        stamp of the glossary terms it uses. Every read and write of a row
        names it through here, the lookup route's too."""
        return translate_store.src_hash(text, self.stamp(text) if self.stamp else "")

    def lookup(self, srcs: list, need_brief: bool) -> dict:
        return translate_store.lookup(*self._where(), srcs, need_brief)

    def read(self, texts: list) -> list:
        """The kept translation of each of ``texts``, None where there is none
        (a blank text has none), made with the brief or not. Reads the store
        alone: not memory, which is an account's, and nothing is written."""
        names = [self.src(t) if t.strip() else "" for t in texts]
        found = self.lookup([n for n in names if n], False)
        return [found.get(n) if n else None for n in names]

    def put(self, rows: list, brief: bool, replace: bool = False) -> None:
        if not rows:
            return
        with _keeping(self.ws, self.doc_id):
            if self._writable():
                translate_store.put(*self._where(), rows, brief, replace)

    def get_brief(self) -> str | None:
        return translate_store.get_brief(*self._where())

    def put_brief(self, brief: str) -> None:
        with _keeping(self.ws, self.doc_id):
            if self._writable():
                translate_store.put_brief(*self._where(), brief)


class TranslateBatch:
    """One request's texts against the paragraph cache and the paper's kept
    translations (``kept``, None for a paper without a stored file): their
    keys, the translations found already, and the misses that go upstream
    (whitespace never; duplicates once). ``brief``: the request carries the
    paper's brief, so only kept translations made with it count, and its
    own are kept as made with it. ``fresh``: the reader asked for these texts
    afresh ("Translate again"), so neither memory nor the kept translations
    answer them, and what comes back replaces what both had."""

    def __init__(self, texts: list, keys: list, kept: Kept | None, brief: bool, fresh: bool = False):
        self.texts, self.keys = texts, keys
        self.kept, self.brief, self.fresh = kept, brief, fresh
        self.hits = {}
        if not fresh:
            # A stored paper's kept rows answer first: they are the
            # workspace's, and memory is per account, so another editor's
            # Translate again (which replaced the row) would otherwise be
            # shadowed by this account's older words until the LRU let them go.
            if kept:
                self._read_kept()
            self.hits.update(_cache_get([k for k in keys if k not in self.hits]))
        groups = {}
        for i, t in enumerate(texts):
            if keys[i] not in self.hits and t.strip():
                groups.setdefault(keys[i], []).append(i)
        self.targets = list(groups.values())  # per upstream element, the request indices sharing its key
        self.miss = [group[0] for group in self.targets]
        self.miss_texts = [texts[i] for i in self.miss]

    def _read_kept(self):
        """Fill the hits from the kept translations, and put what they had in
        memory, over what it held."""
        wanted = {self.kept.src(t): k for t, k in zip(self.texts, self.keys) if t.strip()}
        for src, text in self.kept.lookup(list(wanted), self.brief).items():
            self.hits[wanted[src]] = text
            _cache_put(wanted[src], text)

    def at(self, misses: list) -> list:
        """The request indices the misses at positions ``misses`` fill."""
        return sorted(i for j in misses for i in self.targets[j])

    def settle(self, translated: list) -> list:
        """Record the misses' translations (memory, the kept ones, hits);
        every text's translation, in order. Identity fallbacks are neither
        cached nor kept, so a retry can improve them, and one asked for afresh
        leaves what was there."""
        rows = []
        for i, t in zip(self.miss, translated):
            self.hits[self.keys[i]] = t
            if t != self.texts[i]:
                _cache_put(self.keys[i], t)
                if self.kept:
                    rows.append((self.kept.src(self.texts[i]), t))
        if self.kept:
            self.kept.put(rows, self.brief, replace=self.fresh)
        return [self.hits.get(k, self.texts[i]) for i, k in enumerate(self.keys)]
