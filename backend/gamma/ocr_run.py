"""The runs that recognize a stored PDF's scanned pages, and the forget that
drops what they kept (gamma/pdf_ocr.py is the store they keep it in).

The ``ocr`` job (routers/ocr.py) reads the words of a file's scanned pages
from their pictures (gamma/ocr_engine.py, ``run``); where the server reads
no text itself, the ``ocr-tab`` job waits while the window that asked reads
them with tesseract.js and sends each page's words, which the same page
rules judge (``window_run``). Either stages each page it reads and applies
them all at once (pdf_ocr ``stage_page``, ``apply``), then runs the
finishing steps (``_finish``: the document indexed again, its kept briefs
dropped, its highlights' quotes respelled); one of them failing, or a
restart among them, leaves the text applied and the file pending, and the
next run finishes it first. The ``ocr-drop`` job forgets it all again
(``forget_recognized``), and every apply that found pages staged, and every
forget, is told to the open tabs that may show the file (``announce``).
"""

import re
import threading
import time
import unicodedata
from collections import Counter
from contextlib import closing
from statistics import median

from . import collab, db, highlight_relocate, jobs, ocr_engine, pdf_ocr, pdf_text, translate
from .blocks_store import pages_showing
from .logbuf import log
from .search import index, indexer

# A scan page keeps its own layer when the recognition read at least this
# share of the layer's words too (docs/research/ocr.md: the good layers
# share 98-99% with a fresh recognition, the ones it helps 82-90%)...
KEEP_SHARED = 0.93
# ...and the recognition of the first SAMPLE layered scan pages decides
# whether the rest is read at all: when all of them keep their layer, the
# file's own text reads well.
SAMPLE = 3
# The most scanned pages one run recognizes (hours on a NAS past it).
MAX_PAGES = 2000
# A page with no layer holds real text when the recognition read at least
# PICTURE_WORDS words of confidence PICTURE_CONF or more; otherwise (a
# cover, a plate, a photo) it keeps the text it shows of its own (a title
# over the picture, a caption, a stamp line), or is stored without words
# when it shows none.
PICTURE_WORDS = 10
PICTURE_CONF = 60
# The job kinds that change a file's recognized text: a run on the server,
# a run in a window, the forgetting of what was recognized. One of them at a
# time for a workspace's file, whoever starts it (jobs.start's exclusive).
OCR_KINDS = ("ocr", "ocr-tab", "ocr-drop")
# A run in a window ends, cancelled, when no page came from it for so long
# (a window closed, a device asleep): what it sent stays staged for the next.
WINDOW_IDLE_S = 600
# How often its job reports while it waits for the window's pages (a stop
# lands at each report).
WINDOW_WAIT_S = 2.0
# A scan page's recognition replaces its layer only when it read at least
# this share of the layer's count of words: a recognition that read far
# fewer (a page drawn too small for it, turned sideways, mostly a figure)
# would put less text in place of what the layer has.
MIN_WORDS_READ = 0.5


def to_lines(recognized, raster) -> list:
    """``ocr_engine.recognize``'s lines (boxes in the raster's pixels) as
    the page rules read them: each word ``[text, x0, y0, x1, y1,
    confidence]`` with its box in pdf_ocr ``UNITS`` of the page as shown,
    through the zoom it was drawn at and the size of what was drawn
    (``raster.scale``, ``raster.width_pt``, ``raster.height_pt``, not the
    pixel size, which MuPDF rounds outward). ``stage_page`` keeps the first
    five of a word."""
    w = raster.width_pt * raster.scale or 1.0
    h = raster.height_pt * raster.scale or 1.0
    u = pdf_ocr.UNITS
    return [[[word[0], round(u * word[1] / w), round(u * word[2] / h), round(u * word[3] / w), round(u * word[4] / h),
              *word[5:6]] for word in line] for line in recognized]


def reads_well(found: dict) -> bool:
    """Whether a file's recognition (pdf_ocr ``state``) found that its own text
    reads well: the first ``SAMPLE`` scan pages with a layer kept it, each
    sharing ``KEEP_SHARED`` of its words with the recognition (all of
    them, on a file with fewer: every scanned page is done), and no page's
    layer was replaced. A picture page that kept its own text has no layer
    to share (no ``shared``) and counts neither way. A run then reads no
    more of its layered pages, and the Document menu need not offer one."""
    kept, shares = set(found["kept"]), found["shared"]
    layered = kept & set(shares)
    if not layered or not set(shares) <= kept or min(shares.values()) < KEEP_SHARED:
        return False
    scanned = found["scanned"]
    return len(layered) >= SAMPLE or (scanned is not None and scanned <= len(found["recognized"]) + len(kept))


# --- The run ---------------------------------------------------------------------
# The ``ocr`` job's body (routers/ocr.py starts it, one at a time for a file:
# its key is the workspace and the file).

# A run's apply and its finishing steps take the file's turn: a run finishes
# what an interrupted one left due (``pending``) too, and the two must not
# interleave. Striped by file, as gamma/translate.py's turns are.
_TURNS = [threading.Lock() for _ in range(16)]


def _turn(ws: str, doc_id: str) -> threading.Lock:
    return _TURNS[hash((ws, doc_id)) % len(_TURNS)]


# How the study compared a layer with a fresh recognition
# (tools/ocr-study/stage2/score.py ``words``), which KEEP_SHARED was measured
# by: a text's words split at white space, compatibility forms, curly quotes
# and dashes folded, the punctuation at either end of a word dropped, case
# kept, each word counted as often as it comes.
_FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
                       "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"',
                       "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                       "­": None, "​": None, "﻿": None})
_WORD_EDGES_RE = re.compile(r"^[\W_]+|[\W_]+$")


def _words(text: str) -> Counter:
    words = Counter()
    for raw in unicodedata.normalize("NFKC", text or "").translate(_FOLD).split():
        word = _WORD_EDGES_RE.sub("", raw)
        if word:
            words[word] += 1
    return words


def shared_words(layer: str, recognized: str) -> float | None:
    """The share of a page's layer's words (``layer``, its own text) that
    its recognition (``recognized``) read too, a word counted as often as
    both read it (``_words``); None for a layer without a word."""
    have = _words(layer)
    total = sum(have.values())
    return sum((have & _words(recognized)).values()) / total if total else None


def _page_rule(kind: str, lines, layer: str) -> tuple[str, list, float | None]:
    """What a page's recognition makes of it: ``(outcome, lines, shared)``.
    ``lines`` are its recognized lines, each word ``[text, x0, y0, x1, y1,
    confidence]`` in UNITS (``to_lines``' shape, or a window's words; None:
    no result), read as stored (pdf_ocr ``clean_lines``: a word left empty counts for
    nothing), ``kind`` ``pdf_text.page_kinds``' and ``layer`` the page's
    own text (a picture page's: what it shows besides its picture). A scan
    page's recognition replaces its layer ("recognized", its ``lines``)
    when they share under ``KEEP_SHARED`` of the layer's words and the
    recognition read at least ``MIN_WORDS_READ`` of their count; otherwise
    the page keeps its layer ("kept"), ``shared`` saying how much they
    share. A picture page, or a scan page whose layer holds no word, takes
    its recognition when it read real text (``PICTURE_WORDS`` words of
    confidence ``PICTURE_CONF`` or more); otherwise a picture page showing
    words of its own keeps them ("kept", no ``shared``: a cover's title, a
    plate's caption, a stamp line), and one showing none is stored without
    words ("empty": a plate, a photo). "failed": no result, nothing kept,
    the next run reads the page again. ValueError for ``lines`` that are no
    lines of words."""
    if lines is None:
        return "failed", [], None
    lines = pdf_ocr.clean_lines(lines, conf=True)
    own = _words(layer)
    if kind != "scan" or not own:
        real = sum(1 for line in lines for word in line if word[5] >= PICTURE_CONF)
        if real >= PICTURE_WORDS:
            return "recognized", lines, None
        return ("kept", [], None) if own else ("empty", [], None)
    got = _words(pdf_ocr.page_text(lines))
    total = sum(own.values())
    shared = sum((own & got).values()) / total
    if shared >= KEEP_SHARED or sum(got.values()) < MIN_WORDS_READ * total:
        return "kept", [], shared
    return "recognized", lines, shared


def stage_words(ws: str, doc_id: str, page: int, kind: str, lines, layer: str,
                engine: str) -> tuple[str, float | None]:
    """Judge page ``page``'s recognized ``lines`` by the page rules
    (``_page_rule``, by its ``kind`` and its own text ``layer``) and stage
    what they make of it, recording the ``engine`` that read it: what a run
    on the server and a window's page both do. → ``(outcome, shared)``; a
    "failed" page stages nothing. Raises as ``stage_page`` does."""
    outcome, kept_lines, shared = _page_rule(kind, lines, layer)
    if outcome != "failed":
        pdf_ocr.stage_page(ws, doc_id, page, kept_lines, shared=shared, kept=outcome == "kept", engine=engine)
    return outcome, shared


def classify(src, progress=None) -> tuple[int, dict]:
    """A run's phase ``reading`` (unit pages): every page of the PDF at
    ``src`` classified (``pdf_text.page_kinds``, one page a turn of the
    PyMuPDF lock), ``progress`` told after each (a stop lands there). →
    ``(pages, kinds)``: the file's page count and {page: "scan" |
    "picture"} of its scanned pages. ValueError for a file that does not
    open."""
    progress = progress or jobs.no_progress
    total = pdf_text.page_count(src)
    if not total:
        raise ValueError("the PDF cannot be read")
    kinds = {}
    progress(done=0, total=total, unit="pages", phase="reading")
    with closing(pdf_text.page_kinds(src)) as walk:
        for page, kind in walk:
            if kind:
                kinds[page] = kind
            progress(done=page, total=total, unit="pages", phase="reading")
    return total, kinds


def _record_kinds(ws: str, doc_id: str, total: int, kinds: dict) -> None:
    """``classify``'s finding recorded (``set_scanned``), then ValueError
    when more than ``MAX_PAGES`` pages are scanned: a run does not read
    them."""
    pdf_ocr.set_scanned(ws, doc_id, total, len(kinds), kinds)
    if len(kinds) > MAX_PAGES:
        raise ValueError(f"This PDF has {len(kinds)} scanned pages; text recognition reads at most "
                         f"{MAX_PAGES} pages of a file.")


def plan_of(found: dict | None, kinds: dict, failed=()) -> dict:
    """What a run reads of a file now: its recognition (``state``; None:
    nothing yet), its scanned pages' ``kinds`` ({page: "scan" |
    "picture"}, ``classify``'s) and the pages the run found no result on
    (``failed``: it does not read them again). → ``{sample, done, todo,
    good_layer}``:

    - ``sample``: the first ``SAMPLE`` scan pages with a layer;
    - ``done``: the scanned pages staged or applied at this VERSION and
      LANGS, kept or not (sorted);
    - ``good_layer``: whether the file's own text reads well, by its
      sample: True when every sample page kept its layer, sharing
      ``KEEP_SHARED`` of its words with the recognition, False when one
      did not (its layer replaced, a recognition that read too few of its
      words, no result) or there is no sample, None while a sample page is
      still to read;
    - ``todo``: the pages to read, in order: the sample's not done, then
      every other scanned page not done, but for the layered ones once
      ``good_layer`` is True; ``failed`` pages left out.

    What a run on the server goes by, and what a window is told to read.
    Pure."""
    failed = set(failed)
    found = found or {"recognized": [], "kept": [], "staged": [], "shared": {}}
    kept, shares = set(found["kept"]), found["shared"]
    done = (set(found["recognized"]) | kept | set(found["staged"])) & set(kinds)
    scanned = sorted(kinds)
    sample = [page for page in scanned if kinds[page] == "scan"][:SAMPLE]
    # Kept for sharing its words, not for a recognition that read too few of them.
    if not sample or any(page in failed or (page in done and not (page in kept and shares.get(page, 0) >= KEEP_SHARED))
                         for page in sample):
        good_layer = False
    else:
        good_layer = True if all(page in done for page in sample) else None
    first = [page for page in sample if page not in done and page not in failed]
    rest = [page for page in scanned if page not in done and page not in failed and page not in sample
            and (kinds[page] == "picture" or good_layer is not True)]
    return {"sample": sample, "done": sorted(done), "todo": first + rest, "good_layer": good_layer}


def _counts(plan: dict, failed) -> tuple[int, int]:
    """A run's count in phase ``recognizing``: ``(done, total)``, the pages
    staged or applied and those it found no result on, of those and the
    pages it still reads (``plan_of``'s)."""
    done = len(set(plan["done"]) | set(failed))
    return done, done + len(plan["todo"])


def _state_to_keep(ws: str, doc_id: str) -> dict:
    """The file's ``state``, for a run that goes on to stage pages:
    ValueError when data.db cannot be read now."""
    found = pdf_ocr.state(ws, doc_id)
    if found is None:
        raise ValueError("the recognized text cannot be kept now")
    return found


def _finish_pending(ws: str, doc_id: str, actor: str) -> Counter:
    """A run's step 1: a file left ``pending`` by an interrupted one
    finished first (``_finish``, under the file's turn). → the respell's
    counts."""
    respelled = Counter()
    found = pdf_ocr.state(ws, doc_id)
    if found is not None and found["pending"]:
        with _turn(ws, doc_id):
            respelled.update(_finish(ws, doc_id, actor))
    return respelled


def _apply_and_finish(ws: str, doc_id: str, actor: str, respelled: Counter, staged: bool) -> dict | None:
    """A run's phase ``updating``, under the file's turn: what is staged
    applied in one transaction when the run may have staged pages
    (``staged``; ``apply``, each page recording the engine that read it),
    then the finishing steps when the file is pending (``_finish``, its
    counts added to ``respelled``). A failure in those is logged and the run
    ends as done: its text is live, and the file stays ``pending`` for the
    next run's step 1. An apply that found pages staged is told to the open
    tabs (``announce``) once the steps are through, failed or not: even
    pages that all kept their layer change what the Document menu offers.
    → the file's ``state`` before them."""
    applied = None
    with _turn(ws, doc_id):
        if staged:
            applied = pdf_ocr.apply(ws, doc_id)
        try:
            found = pdf_ocr.state(ws, doc_id)
            if found is not None and found["pending"]:
                try:
                    respelled.update(_finish(ws, doc_id, actor))
                except Exception:  # noqa: BLE001 — the text is live: the run did its work, the next one finishes it
                    log.exception(f"[ocr] the finishing steps of {doc_id} failed; it stays pending for the next run")
        finally:
            if applied is not None:
                announce(ws, doc_id)
    return found


def _result(ws: str, doc_id: str, found: dict | None, scanned: int, plan: dict, failed, respelled: Counter) -> dict:
    """A run's result (``run``'s), from the file's ``state`` after its
    apply, its ``scanned`` pages, its last ``plan_of`` and the pages it
    found no result on."""
    found = found or {"recognized": [], "kept": [], "shared": {}}
    words, empty = pdf_ocr.live_counts(ws, doc_id, found["recognized"])
    shares, good_layer = found["shared"], plan["good_layer"] is True
    sampled = [shares[page] for page in plan["sample"] if page in shares]
    shared = median(sampled) if good_layer and sampled else median(shares.values()) if shares else None
    return {"scanned": scanned, "recognized": words, "kept": len(found["kept"]), "empty": empty,
            "failed": len(set(failed)), "good_layer": good_layer,
            "shared": None if shared is None else round(shared, 3),
            "highlights_respelled": respelled["respelled"], "highlights_kept": respelled["kept"],
            "highlights_changed": respelled["changed"]}


def run(ws: str, doc_id: str, src: str, *, actor: str, progress=None) -> dict:
    """Recognize the text of the scanned pages of the workspace's stored
    PDF ``doc_id``, read from ``src`` (``stored_path``), the ``ocr`` job's
    work; ``actor`` the account its highlight writes go under, ``progress``
    its report (a stop lands at each call, jobs.py).

    1. A file left ``pending`` by an interrupted run is finished first
       (``_finish``), with no report.
    2. Phase ``reading`` (unit pages): every page classified
       (``classify``), and which are scans or pictures recorded
       (``set_scanned``). More than ``MAX_PAGES`` of them: ValueError.
       None: the run ends here.
    3. Phase ``recognizing`` (unit pages, counting from the pages already
       done, staged or live, at this VERSION and LANGS; ``since``, when the
       phase began in ms, and ``from``, the count then, for the time left):
       the pages ``plan_of`` names, first the sample, the first ``SAMPLE``
       scan pages with a layer, then every other scan and picture page not
       done, each drawn grey without its annotations
       (``ocr_engine.render_zoom``), recognized outside the lock and staged
       by the page rules (``stage_words``): a failed page is not kept and
       is read again next time. When every page of the sample keeps its
       layer, the file's own text reads well and its other layered pages
       are not read (``reads_well``); its picture pages are. A stop lands
       between two pages and leaves what was staged for the next run,
       unseen.
    4. ``updating``, no longer stoppable: what is staged applied in one
       transaction (``apply``, the file's text changes at once), then the
       finishing steps (``_finish``), under the file's turn. A failure in
       those is logged and the run ends as done: its text is live, and the
       file stays ``pending`` for the next run's step 1. Then the open tabs
       are told (``announce``).

    → ``{scanned, recognized, kept, empty, failed, good_layer, shared,
    highlights_respelled, highlights_kept, highlights_changed}``: the
    file's scan and picture pages, its pages reading recognized words,
    keeping their own text and recognized without words (live, every
    run's), this run's pages without a result, whether its own text reads
    well and the median share of a layer's words a recognition read too
    (the sample's when it reads well, else every layered page's; None for
    no layer), and what the respell did (``respell_quotes``). ValueError
    for a file that does not open."""
    progress = progress or jobs.no_progress
    respelled = _finish_pending(ws, doc_id, actor)
    total, kinds = classify(src, progress)
    _record_kinds(ws, doc_id, total, kinds)
    failed, plan = set(), {"sample": [], "good_layer": False}
    if kinds:
        plan = plan_of(_state_to_keep(ws, doc_id), kinds)
        engine = ocr_engine.engine()
        count = {"done": len(plan["done"]), "total": len(kinds)}
        since, start = int(time.time() * 1000), count["done"]

        def report():
            progress(done=count["done"], total=count["total"], unit="pages", phase="recognizing", since=since,
                     **{"from": start})

        def read(pages):
            """Recognize and stage each of ``pages`` in turn, a report after
            each (a stop lands there), each by its own text, read in step
            (``pdf_text.layer_texts``: the file opened once for them, those
            pages alone read, one held at a time); a page that did not draw
            is failed."""
            seen = set()
            rasters = pdf_text.page_rasters(src, pages, ocr_engine.render_zoom, annots=False, grey=True)
            layers = pdf_text.layer_texts(src, pages)  # both follow ``pages``: the rasters skip a page, never reorder
            with closing(rasters), closing(layers):
                for page, raster in rasters:
                    seen.add(page)
                    layer = next((text for at, text in layers if at == page), "")
                    found = ocr_engine.recognize(raster)
                    lines = None if found is None else to_lines(found, raster)
                    if stage_words(ws, doc_id, page, kinds[page], lines, layer, engine)[0] == "failed":
                        failed.add(page)
                    count["done"] += 1
                    report()
            failed.update(page for page in pages if page not in seen)

        report()
        read([page for page in plan["todo"] if page in plan["sample"]])
        plan = plan_of(_state_to_keep(ws, doc_id), kinds, failed)  # the sample read: its verdict
        if plan["good_layer"]:
            count["total"] = count["done"] + len(plan["todo"])  # the layered pages left are not read
            report()
        read(plan["todo"])

    progress(phase="updating", stoppable=False)
    found = _apply_and_finish(ws, doc_id, actor, respelled, bool(kinds))
    return _result(ws, doc_id, found, len(kinds), plan, failed, respelled)


# --- A run in a window ---------------------------------------------------------------
# The ``ocr-tab`` job's body (routers/ocr.py starts it where the server reads
# no text itself: ocr_engine ``capability``). It classifies the file, then
# waits while the window that asked reads the pages ``plan_of`` names with
# tesseract.js and sends each one's words (``window_page``), and applies what
# they made when the window is done. The window's CPU does the reading; the
# page rules, the store and the finishing steps are the server run's.

class WindowRefusal(Exception):
    """A window's page or word refused (``window_page``, ``window_done``):
    ``reason`` is "stopped" (no run in a window takes the file's pages now:
    none going, its job not yet or no longer waiting for them), "elsewhere"
    (another account's run), "other window" (the account's run in another
    window), "not scanned" (no scan or picture page of the file) or
    "applied" (the page reads recognized text already)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Window:
    """A run in a window while its job goes: the job, the account and the
    window (its per-load ``client`` id) it takes pages from, the pages the
    window found no result on (``failed``, which its plan leaves out), what
    each page sent made (``answers``, so a page sent again is answered
    without work), when it goes idle (``deadline``), whether it takes pages
    (``open``: from its phase ``recognizing`` until it applies), whether
    the window said it is done, the pages being taken now (``working``,
    which its apply waits for) and the event that wakes its job to
    report. Changed under ``_windows_lock``."""

    def __init__(self, job_id: str, account: str, client: str):
        self.job_id, self.account, self.client = job_id, account, client
        self.failed: set = set()
        self.answers: dict = {}
        self.deadline = time.monotonic() + WINDOW_IDLE_S
        self.open = self.done = False
        self.working = 0
        self.wake = threading.Event()


_WINDOWS: dict[tuple[str, str], _Window] = {}  # (ws, doc_id) → the run in a window going for the file
_windows_lock = threading.Lock()


def window(ws: str, doc_id: str) -> dict | None:
    """The run in a window going for the file, as the routes check it:
    ``{job_id, account, client, open, failed}`` (``failed`` a frozenset);
    None when there is none."""
    with _windows_lock:
        run = _WINDOWS.get((ws, doc_id))
        if run is None:
            return None
        return {"job_id": run.job_id, "account": run.account, "client": run.client, "open": run.open,
                "failed": frozenset(run.failed)}


def window_take(ws: str, doc_id: str, job_id: str, account: str, client: str) -> bool:
    """The account's run in a window going for the file, of the job
    ``job_id``, goes on in the window ``client`` (a reloaded window, whose
    new load has an id of its own): the one before is refused at its next
    page ("other window"). A job whose run has not yet registered (it
    started a moment ago) finds the run here when it does (``window_run``).
    Whether the run is the account's."""
    with _windows_lock:
        key = (ws, doc_id)
        run = _WINDOWS.get(key)
        if run is None or run.job_id != job_id:
            run = _WINDOWS[key] = _Window(job_id, account, client)
        if run.account != account:
            return False
        run.client = client
        return True


def _taken(ws: str, doc_id: str, account: str, client: str) -> _Window:
    """The run in a window that takes the file's pages from the window
    ``client`` of ``account`` (``_windows_lock`` held), its deadline moved
    on; else WindowRefusal."""
    run = _WINDOWS.get((ws, doc_id))
    if run is None or not run.open:
        raise WindowRefusal("stopped")
    if run.account != account:
        raise WindowRefusal("elsewhere")
    if run.client != client:
        raise WindowRefusal("other window")
    run.deadline = time.monotonic() + WINDOW_IDLE_S
    return run


def _layer(src, page: int) -> str:
    """Page ``page``'s own text (``pdf_text.layer_texts``)."""
    with closing(pdf_text.layer_texts(src, [page])) as walk:
        return next((text for at, text in walk if at == page), "")


def window_page(ws: str, doc_id: str, page: int, lines, *, account: str, client: str, src) -> dict:
    """Page ``page`` of the file as the window ``client`` of ``account``
    read it, for its run in a window: ``lines`` (each word ``[text, x0,
    y0, x1, y1, confidence]`` in UNITS) judged by the page rules against
    the page's own text read from ``src`` and staged (``stage_words``,
    recorded as read by ``ocr_engine.BROWSER_ENGINE``); None: the window
    found no result, and the run's plan leaves the page out. A page that
    run's plan no longer reads (its sample's verdict moved on, or staged
    before) is "skipped"; a page it took before gets the answer it got,
    with no work (a window sending it again). → ``{outcome, shared, todo,
    done, total}``: what the page made and the run's plan and count after
    it. The run's deadline moves on and its job reports. WindowRefusal for
    a page refused; ValueError when data.db cannot be read now."""
    with _windows_lock:
        run = _taken(ws, doc_id, account, client)
        answer = run.answers.get(page)
        failed = set(run.failed)
        run.working += 1
    try:
        found = _state_to_keep(ws, doc_id)
        kinds = found["kinds"] or {}
        if page not in kinds:
            raise WindowRefusal("not scanned")
        if answer is None:
            if page in found["applied"]:
                raise WindowRefusal("applied")
            if page not in plan_of(found, kinds, failed)["todo"]:
                answer = {"outcome": "skipped", "shared": None}
            else:
                if lines is None:
                    answer = {"outcome": "failed", "shared": None}
                else:
                    outcome, shared = stage_words(ws, doc_id, page, kinds[page], lines, _layer(src, page),
                                                  ocr_engine.BROWSER_ENGINE)
                    answer = {"outcome": outcome, "shared": shared}
                with _windows_lock:
                    run.answers[page] = answer
                    if answer["outcome"] == "failed":
                        run.failed.add(page)
                    failed = set(run.failed)
                found = _state_to_keep(ws, doc_id)
        plan = plan_of(found, kinds, failed)
        done, total = _counts(plan, failed)
        return {**answer, "todo": plan["todo"], "done": done, "total": total}
    finally:
        with _windows_lock:
            run.working -= 1
        run.wake.set()


def window_done(ws: str, doc_id: str, *, account: str, client: str) -> None:
    """The window ``client`` of ``account`` read what it would: its run
    applies what is staged at its next report. WindowRefusal as for
    ``window_page``."""
    with _windows_lock:
        run = _taken(ws, doc_id, account, client)
        run.done = True
    run.wake.set()


def window_run(ws: str, doc_id: str, src: str, *, actor: str, client: str, progress=None, job_id: str = "") -> dict:
    """The ``ocr-tab`` job's work: the text of the stored PDF ``doc_id``'s
    scanned pages, read from ``src``, recognized in the window ``client``
    of ``actor`` (who starts it and whose account its highlight writes go
    under), its job ``job_id``, ``progress`` its report (a stop lands at
    each call).

    1. A file left ``pending`` is finished first, as ``run`` does.
    2. Phase ``reading``: the pages classified and their kinds recorded
       (``classify``, ``set_scanned``; past ``MAX_PAGES``, ValueError).
    3. Phase ``recognizing`` (unit pages, counting the pages staged or
       live and those the window found no result on; ``since`` and
       ``from`` as ``run``'s): the run takes the window's pages
       (``window_page``) and reports about every ``WINDOW_WAIT_S`` and
       after each page. It goes on to apply when the plan has no page left
       to read, or when the window says it is done (``window_done``); when
       no page came for ``WINDOW_IDLE_S`` (counted from the phase's start,
       then from each page: steps 1 and 2 are the server's time, not the
       window's), it ends cancelled, its progress saying ``idle`` (those
       seconds: nobody stopped it), nothing applied, what was staged kept
       for the next run.
    4. ``updating``, as ``run``'s, once the pages being taken are through.

    The run is registered from the start, as the window ``client``'s, or
    as the one a window took before it registered (``window_take``).
    → ``run``'s result, ``failed`` the pages the window found no result on.
    ValueError for a file that does not open."""
    progress = progress or jobs.no_progress
    key = (ws, doc_id)
    with _windows_lock:
        run = _WINDOWS.get(key)
        if run is None or not job_id or run.job_id != job_id:  # else a window took the run before it registered
            run = _WINDOWS[key] = _Window(job_id, actor, client)
    try:
        respelled = _finish_pending(ws, doc_id, actor)
        total, kinds = classify(src, progress)
        _record_kinds(ws, doc_id, total, kinds)
        plan, failed = {"sample": [], "good_layer": False}, set()
        if kinds:
            with _windows_lock:
                run.open = True
                # from now: the idle time is the window's, never the server's own steps above
                run.deadline = time.monotonic() + WINDOW_IDLE_S
            since, start = int(time.time() * 1000), None
            while True:
                run.wake.clear()
                with _windows_lock:
                    failed, done, idle = set(run.failed), run.done, time.monotonic() >= run.deadline
                plan = plan_of(_state_to_keep(ws, doc_id), kinds, failed)
                count, of = _counts(plan, failed)
                start = count if start is None else start
                progress(done=count, total=of, unit="pages", phase="recognizing", since=since, **{"from": start})
                if done or not plan["todo"]:
                    break
                if idle:
                    log.info(f"[ocr] no page of {doc_id} came from its window for {WINDOW_IDLE_S} s: the run ends")
                    progress(idle=WINDOW_IDLE_S)  # its row says why it ended: nobody stopped it
                    raise jobs.Cancelled()
                run.wake.wait(WINDOW_WAIT_S)
        with _windows_lock:
            run.open = False
        while True:  # a page being taken now is staged before the apply, never after it
            with _windows_lock:
                if not run.working:
                    failed = set(run.failed)
                    break
            run.wake.wait(0.05)
            run.wake.clear()
        if kinds:
            plan = plan_of(_state_to_keep(ws, doc_id), kinds, failed)  # with the pages taken since its last report
        progress(phase="updating", stoppable=False)
        found = _apply_and_finish(ws, doc_id, actor, respelled, bool(kinds))
        return _result(ws, doc_id, found, len(kinds), plan, failed, respelled)
    finally:
        with _windows_lock:
            if _WINDOWS.get(key) is run:
                del _WINDOWS[key]


# --- Forgetting it -------------------------------------------------------------------
# The ``ocr-drop`` job's body (routers/ocr.py starts it, never beside a run of
# the file: its key and exclusive are theirs). It reads no page, so it needs
# no engine.

def forget_recognized(ws: str, doc_id: str, *, actor: str, progress=None) -> dict:
    """Forget the text recognized on the workspace's stored PDF ``doc_id``,
    the ``ocr-drop`` job's work (``actor`` the account its highlight writes
    go under, ``progress`` its report), in phase ``updating`` from the
    start (no longer stoppable), under the file's turn:

    1. the quotes of the highlights on its recognized pages respelled back
       as its own layer reads (``respell_quotes(back=True)``), while the
       recognized text is still there to read them by: a highlight drawn on
       the recognized words takes the layer's under it when they read
       alike, one on a page that is only a picture keeps the recognized
       words (``unread``);
    2. its page rows deleted in one transaction (``unapply``);
    3. the finishing steps (``_finish``: the document indexed again, its
       kept briefs dropped; nothing left to respell), a failure in them
       logged, as a run's is: the text is forgotten, and the file stays
       ``pending`` for the next run's step 1;
    4. automatic recognition never takes the file up again (ocr_auto's
       marker ``declined``);

    then, however those went, the open tabs told (``announce``). →
    ``{pages, highlights_respelled, highlights_kept, highlights_changed,
    highlights_unread}``: the pages that read recognized text before (a
    picture page recognized without words among them), and what the respell
    did. ValueError when data.db cannot be read now."""
    from . import ocr_auto  # local: it imports this module

    progress = progress or jobs.no_progress
    progress(phase="updating", stoppable=False)
    try:
        with _turn(ws, doc_id):
            found = _state_to_keep(ws, doc_id)
            counts = highlight_relocate.respell_quotes(ws, doc_id, found["recognized"], actor=actor, back=True)
            pdf_ocr.unapply(ws, doc_id)
            try:
                _finish(ws, doc_id, actor)
            except Exception:  # noqa: BLE001 — the text is forgotten: the next run finishes it
                log.exception(f"[ocr] the finishing steps of {doc_id} failed; it stays pending for the next run")
        ocr_auto.mark(ws, doc_id, ocr_auto.DECLINED, actor)
    finally:
        announce(ws, doc_id)
    return {"pages": len(found["recognized"]), "highlights_respelled": counts["respelled"],
            "highlights_kept": counts["kept"], "highlights_changed": counts["changed"],
            "highlights_unread": counts["unread"]}


def announce(ws: str, doc_id: str) -> None:
    """Tell the open tabs that may show the stored PDF ``doc_id`` that its
    recognized text changed (an apply, a forget): ``{t: "ocr", doc_id,
    stamp}`` to the rooms of the pages showing it and the workspace's
    members (collab ``publish_file``), each of which reads it again over
    HTTP (GET /api/ocr/{doc_id}). Nothing is read when no room of the
    workspace is open. Never raises: a tab that misses it asks again on its
    next hello or poll round."""
    try:
        if not any(open_ws == ws for open_ws, _page in collab.open_pages()):
            return
        with db.connect_pages_db(ws) as conn:
            pages = pages_showing(conn, doc_id)
        collab.publish_file(ws, pages, {"t": "ocr", "doc_id": doc_id, "stamp": pdf_ocr.stamp(ws, doc_id)})
    except Exception as e:  # noqa: BLE001 — a nudge never fails the work that sent it
        log.warning(f"[ocr] telling the open tabs that {doc_id}'s text changed failed: {e}")


def _finish(ws: str, doc_id: str, actor: str) -> dict:
    """The steps due after an apply (the file ``pending``), each harmless
    run again, in the order a reader needs them (under the file's turn):
    the document indexed again for search in this thread (the workspace's
    indexing job drops a start while it runs, and the document would read
    as pending until a search kicked it); its kept briefs dropped and its
    paid paragraph translations kept (gamma/translate.py ``forget_brief``);
    the quotes of the highlights on its recognized pages respelled
    (highlight_relocate ``respell_quotes``, under ``actor``); then
    ``pending`` off, unless an apply since has steps of its own due. A
    failed re-index is left to the lazy indexer (the apply marked the
    document's index row stale); any other failure raises with the file
    still pending, for the next run to finish. → the respell's counts."""
    found = pdf_ocr.state(ws, doc_id)
    if found is None or not found["pending"]:
        return {}
    try:
        indexed = indexer.index_doc(ws, index.Doc(doc_id, "pdf", ".pdf", "store"))
        if indexed is not None:
            with db.connect_data_db(ws) as conn:
                index.store_docs(conn, [indexed])
    except Exception as e:  # noqa: BLE001 — the row is stale: the next search indexes it
        log.warning(f"[ocr] indexing {doc_id} again failed, left to the next search: {e}")
    translate.forget_brief(ws, doc_id)
    counts = highlight_relocate.respell_quotes(ws, doc_id, found["recognized"], actor=actor)
    pdf_ocr.finished(ws, doc_id, found["stamp"])
    return counts
