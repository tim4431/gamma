"""The GoodNotes import: an upload of GoodNotes' own export (one
``.goodnotes``, several picked together, a ``.zip`` of a folder's export,
a ``.goodnotes.zip`` backup) → one Gamma page per notebook, with its PDF
or its sheets of paper, its handwriting, shapes, text boxes, pictures and
recordings (docs/dev/import_export.md "GoodNotes notebooks").

It is a source of the reviewed import (routers/imports.py): a preview,
then a commit run as a background job, through what every note app's
import shares (gamma/note_app_import.py: the guards on the upload, one
notebook at a time, the commit's transaction, the storing of files). A
notebook is read by gamma/goodnotes.py and placed by
gamma/goodnotes_map.py; its key (``goodnotes_import``) is a digest of its
files as the zip's directory lists them (``goodnotes.notebook_digest``),
so the same notebook imported twice makes one page, and an edited one a
new page.
"""

import contextlib
from collections import Counter

from fastapi import HTTPException

from . import goodnotes as gn
from . import goodnotes_map as gmap
from . import note_app_import as app
from . import notability as nb
from .blocks_store import attachment_props
from .logbuf import log
from .note_app_import import Skip, Stop, compose_pdf
from .storage import display_filename, store_pdf

MAX_STICKER_SIDE = 1024          # px: a sticker's PDF rendered for its box, its longer side at most this


def import_goodnotes(ws: str, data, conn, folder: str = "", *, name: str = "", actor: str = "",
                     preview: bool = False, selected: set[str] | None = None, progress=None) -> dict:
    """Import the GoodNotes notebooks in the upload ``data`` into ``ws``,
    through the open pages.db connection ``conn``: one .goodnotes, or a .zip
    of them. The parameters, the report and the refusals are the Notability
    import's (``notability_import.import_notes``): one page per notebook
    below the folder ``folder``, made by ``actor``; a ``preview`` writes
    nothing; ``selected`` the notebooks' paths to import (None: all);
    400 for an upload with no notebook."""
    name = display_filename(name)
    run = _Import(ws, conn, folder, actor, preview, progress)
    return run.run_upload(data, selected, refused=gn.NO_NOTEBOOKS_REASON,
                          find=lambda entries: gn.find_notebooks(entries, name), nested=gn.nested_zip)


class _Import(app.AppImport):
    KEY = "goodnotes_import"
    TAG = "goodnotes"

    def note(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> None:
        start = len(self.report["warnings"])
        try:
            members = self.open_packed(bundle, stack, gn.notebook_members, "not a GoodNotes notebook")
            digest = gn.notebook_digest(members)
            if self.skip(bundle, digest, start):
                return
            notebook = gn.read_notebook(members, path=bundle.path, digest=digest, ink=False)
        except (Skip, gn.NotebookError) as exc:
            self.warn(bundle.path, f"Not imported: {exc}")
            return
        except HTTPException as exc:
            if exc.status_code == 507:
                raise Stop(str(exc.detail)) from None
            self.warn(bundle.path, f"Not imported: {exc.detail}")
            return
        except Exception as exc:  # noqa: BLE001 — a notebook that does not read is that notebook's
            log.warning(f"[goodnotes] {bundle.path!r} did not read: {exc!r}")
            self.warn(bundle.path, "Not imported: it could not be read")
            return
        title = notebook.title
        said = [0, 0]  # how many of its warnings and missing files are said
        self._said(notebook, said)
        dirs = self.dirs.get(bundle.path) or []
        entry = self.entry(bundle, title, dirs, gmap.preview_kind(notebook), bool(notebook.missing))
        if self.preview:
            self._left_out(title, notebook.left_out, notebook.classes)
            for attachment in _template_pdfs(notebook):
                found = members.find(notebook.attachments.get(attachment, ""))
                if found is not None and found.size > app.MAX_HELD_BYTES:
                    self.warn(title, f"Not imported: its PDF {attachment} is over {app.MAX_HELD_BYTES >> 20} MB")
            self.done(entry, digest, start, False)
            return
        written = self.commit_safely(title, lambda: self._commit(members, notebook, dirs, entry, said))
        if written is not None:
            self.done(entry, digest, start, written)

    def _left_out(self, title: str, counts: Counter, classes: Counter) -> None:
        for reason in gn.left_out_reasons(counts, classes):
            self.warn(title, reason)

    def _said(self, notebook: gn.Notebook, said: list) -> None:
        """The notebook's warnings and missing files not said yet (``said``
        counts those said): the journal's before the preview or the commit,
        the pages' as the commit reads them."""
        for warning in notebook.warnings[said[0]:]:
            self.warn(notebook.title, warning)
        for path in notebook.missing[said[1]:]:
            self.warn(notebook.title, f"Missing file {path}: left out")
        said[:] = [len(notebook.warnings), len(notebook.missing)]

    def _strokes(self, members, notebook: gn.Notebook):
        """``store_ink``'s strokes of a page: the page read now
        (``read_page``, within one budget for the notebook), its strokes
        made, then its ink let go, so one page's ink is held at a time; its
        pictures and text stay for their boxes."""
        budget = gn.Budget()

        def strokes(page, left_out, left):
            gn.read_page(notebook, page.page, members, budget)
            try:
                yield from gmap.page_strokes(page, left_out, left)
            finally:
                page.page.ink, page.page.shapes = [], []
        return strokes

    def _commit(self, members, notebook: gn.Notebook, dirs: list, entry: dict, said: list) -> bool:
        """Store the notebook's files, then write its page; False when
        another import wrote the same notebook meanwhile (the entry then
        names that page)."""
        title = notebook.title
        pdf_pages, readers, sources, colours = self._pdfs(members, notebook)
        plan = gmap.plan_notebook(notebook, pdf_pages, colours)
        for warning in plan.warnings:
            self.warn(title, warning)
        props = {self.KEY: notebook.digest}
        if plan.kind in ("original", "composed"):
            data = (sources[plan.pdf] if plan.kind == "original"
                    else compose_pdf([page.frame for page in plan.pages], plan.papers, readers))
            readers.clear()
            sources.clear()
            self.room(len(data))
            doc_id, existed = store_pdf(self.ws, data)
            del data
            if not existed:
                self.report["pdfs_stored"] += 1
            props.update(attachment_props(doc_id, "", f"{title}.pdf")[0])
            self.progress(item=title)
        groups = self.store_ink(plan, self._strokes(members, notebook), title)
        boxes, counts, uses = [], Counter(), Counter()
        for page in plan.pages:
            found = []
            for media in gmap.page_media(page):
                box = (self.store_picture(members, media, page.frame, title, uses, convert=_sticker)
                       if media.kind == "picture" else gmap.text_box(media, page.frame, plan.left_out))
                if box is not None:
                    found.append(box)
                    counts["pictures" if media.kind == "picture" else "boxes"] += 1
            boxes.append(found)
        recordings = [made for ref in notebook.recordings
                      if (made := self.store_recording(members, ref, title, notebook.created)) is not None]
        self._said(notebook, said)
        self._left_out(title, notebook.left_out + plan.left_out, notebook.classes)
        entry["missing"] = bool(notebook.missing)
        tree = app.markup(plan, groups, boxes) + recordings
        written = self.write(title, notebook.digest, props, tree, dirs, entry)
        if written:
            self.report["strokes_imported"] += sum(g["ink_strokes"] for page in groups for g in page)
            self.report["text_boxes_imported"] += counts["boxes"]
            self.report["pictures_imported"] += counts["pictures"]
            self.report["recordings_imported"] += len(recordings)
            entry["created"] = True
            entry["kind"] = "pdf" if props.get("doc_id") else "page"
        return written

    def _pdfs(self, members, notebook: gn.Notebook) -> tuple[dict, dict, dict, dict]:
        pages, readers, sources, colours, missing = notebook_pdfs(members, notebook)
        for path in missing:
            self.warn(notebook.title, f"Missing PDF {path}: its pages are blank paper")
        return pages, readers, sources, colours


def notebook_pdfs(members, notebook: gn.Notebook) -> tuple[dict, dict, dict, dict, list]:
    """The PDFs the pages are bound to, each checked against MAX_HELD_BYTES
    before it is read (one past it skips the notebook) and opened once:
    ``(display sizes of their pages, readers, bytes, catalogue papers'
    colours)`` by attachment id, and the paths of those the notebook lacks.
    One missing or that does not open is left out, and its pages become
    blank paper."""
    pages, readers, sources, colours, missing = {}, {}, {}, {}, []
    designs = {p.template.attachment for p in notebook.pages if p.template is not None and p.template.design}
    for attachment in _template_pdfs(notebook):
        path = notebook.attachments.get(attachment, f"attachments/{attachment}")
        found = members.find(path)
        if found is None:
            missing.append(path)
            continue
        opened = app.open_pdf(found, attachment, _Import.TAG)
        if opened is None:
            continue
        pages[attachment], readers[attachment], sources[attachment] = opened
        if attachment in designs:
            colours[attachment] = _colours(readers[attachment])
    return pages, readers, sources, colours, missing


def _template_pdfs(notebook: gn.Notebook) -> list[str]:
    """The attachment ids the pages' templates name, in page order, once each."""
    return list(dict.fromkeys(p.template.attachment for p in notebook.pages
                              if p.template is not None and p.template.attachment))


def _colours(reader) -> tuple | None:
    """A catalogue paper's colours (``goodnotes_map.colours_of``) from its
    PDF's first page and its first Form XObject; None when they do not read."""
    try:
        page = reader.pages[0]
        contents = page.get_contents()
        resources = page.get("/Resources")
        xobjects = resources.get_object().get("/XObject") if resources is not None else None
        first = next(iter(xobjects.get_object().values()), None) if xobjects is not None else None
        rules = first.get_object().get_data() if first is not None else None
        return gmap.colours_of(contents.get_data() if contents is not None else b"", rules)
    except Exception:  # noqa: BLE001 — a paper whose colours do not read is white
        return None


def _sticker(data: bytes, media: nb.Media) -> bytes:
    """A picture whose file is a PDF (a die-cut sticker, GoodNotes' vector
    art) as a PNG of its first page, its longer side twice the box's in
    points, at most MAX_STICKER_SIDE px, its background transparent; any
    other file as it is. ValueError when it does not render."""
    if not data.lstrip()[:5] == b"%PDF-":
        return data
    from . import pdf_text

    try:
        with pdf_text._lock:
            pymupdf = pdf_text._pymupdf()
            doc = pdf_text._mupdf_open(data)
            try:
                page = doc[0]
                longer = max(page.rect.width, page.rect.height)
                shown = max(media.w, media.h) * gn.CANVAS_PT
                zoom = min(MAX_STICKER_SIDE / longer, max(1.0, 2 * shown / longer)) if longer > 0 else 1.0
                return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")
            finally:
                doc.close()
    except Exception as exc:  # noqa: BLE001 — PyMuPDF missing, or a PDF it cannot render
        log.info(f"[goodnotes] a sticker did not render ({exc!r})")
        raise ValueError("a sticker that does not render") from None

