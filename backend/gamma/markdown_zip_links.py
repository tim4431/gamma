"""What a link of a Markdown zip is and where it goes (the import itself is
gamma/markdown_zip_import.py): the link regexes, the file names links are
resolved by, and ``Links``, which turns a note's links into ``[[page]]``
mentions and uploads. The bottom of the zip's modules: the dialects, and
notion_import.py, and the import read its names, and it reads none of them.
A run's dialect (``run.dialect``, gamma/markdown_zip_dialects.py) says what
differs.
"""

import posixpath
import re
import unicodedata
from urllib.parse import unquote

from .note_markup import LITERAL_SPANS
from .storage import IMAGE_MEDIA_TYPES

_MD_EXTS = (".md", ".markdown")
_NOTION_ID_RE = re.compile(r"(?<!\s)\s+[0-9a-f]{32}$", re.I)
# What Obsidian reads as a space when it matches a link to a heading (its
# stripHeading): ``## Learn C#`` is linked as ``[[Note#Learn C]]``.
_HEADING_PUNCT_RE = re.compile(r"""[!"#$%&()*+,.:;<=>?@^`{|}~/\[\]\\\r\n]""")
# [label](target "title") / ![alt](target) — label may hold one level of [],
# target balanced () as CommonMark allows (Notion leaves them unencoded:
# ``Tasks%20(grid%20view)%20<id>.csv``), else up to the first ")" (a name
# Notion cut at 50 characters inside a parenthesis: ``Plan%20(PHASE%20ST%20<id>.md``).
# Nothing here scans past where the next "[" or "](" would start again (nor
# gives back what it took: no shorter run is followed by what has to come
# next), so a body of unclosed links costs its length, not the square of it.
_LINK_RE = re.compile(r"(!?)\[((?:[^\[\]]++|\[[^\[\]]*+\])*+)\]\(\s*"
                      r"(<[^<>\n]*>|(?:[^()\s]|\([^()\s]*\))++|(?:[^)\s\]]|\](?!\())++)"
                      r"((?:\s+\"[^\"]*\")?)\s*\)")
# [[target#sub#sub|alias]] / ![[…]] — Obsidian's wikilink and embed; the
# alias pipe may be escaped (``\|``) inside a table cell. The same shape
# matches Gamma's own [[id]] refs, which simply resolve to nothing here. A
# sub is up to the next "#": a run of them read two ways doubles the work per "#".
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]|#]*)((?:#[^\[\]|#]*)*)(?:\\?\|([^\[\]]*))?\]\]")
# What ``rewrite_links`` looks for (a page's source says which): a link or an
# escape, which makes the "[" after it text; in Markdown ``to_gamma`` wrote,
# where nothing in code or math is a link, those spans too. They come after the
# link: code in a link's label is the label's.
MD_LINKS_RE = re.compile(_LINK_RE.pattern + r"|\\[!-/:-@\[-`{-~]")
WRITTEN_LINKS_RE = re.compile(_LINK_RE.pattern + "|" + LITERAL_SPANS)
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_IMAGE_SIZE_RE = re.compile(r"^\d+(?:x\d+)?$")


def _notion_id(stem: str):
    m = _NOTION_ID_RE.search(stem)
    return m.group(0).strip().lower() if m else None


def _clean_stem(stem: str) -> str:
    return _NOTION_ID_RE.sub("", stem).strip() or stem.strip()


def _split_ext(path: str):
    leaf = posixpath.basename(path)
    dot = leaf.rfind(".")
    return (leaf[:dot], leaf[dot:].lower()) if dot > 0 else (leaf, "")


def _norm_heading(text: str) -> str:
    return re.sub(r"\s+", " ", _HEADING_PUNCT_RE.sub(" ", text)).strip().lower()


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def _nearest(base_dir, paths):
    same = [p for p in paths if posixpath.dirname(p) == base_dir]
    return min(same or paths, key=lambda p: (p.count("/"), p.lower()))


class Links:
    """Where the links of a run's notes go, worked out once they are chosen
    (``ZipRun.build_links``): ``every`` maps the zip paths of every note to
    their page ids, ``targets`` those of the chosen ones."""

    def __init__(self, run):
        self.run = run
        # A note's parent is read off every note of the zip, chosen or not (a
        # parent left out then names no page, and nothing else takes its place),
        # its links off the chosen ones only.
        self.every = run.targets
        self.targets = {path: pid for path, pid in self.every.items() if pid not in run.excluded_ids}

        # Basename lookup, the way Obsidian resolves a bare [[Note]] or
        # ![[image.png]] wherever the file sits: lower-cased stem (notes) or
        # filename (assets) → zip paths.
        self.assets_by_name = {}
        for path in run.assets:
            self.assets_by_name.setdefault(posixpath.basename(path).lower(), []).append(path)
        self.by_lower, self.notes_by_stem = self.lookup(self.targets)
        self.every_lookup = self.lookup(self.every)

    def lookup(self, notes):
        """``(lower-cased path → path, lower-cased stem → note paths)`` over
        the notes ``notes`` and the bundled files."""
        lower, stems = {path.lower(): path for path in self.run.assets}, {}
        for path in notes:
            lower[path.lower()] = path
            stems.setdefault(_split_ext(path)[0].lower(), []).append(path)
        return lower, stems

    def resolve(self, base_dir, href, tables=None):
        """A link target → the zip path it names, or None. Relative to the
        note first (Markdown links), then by exact vault path, then by
        basename anywhere (wikilinks, "shortest path" Markdown links).
        ``tables``: a ``lookup`` to read instead of the chosen notes'."""
        lower, stems = tables or (self.by_lower, self.notes_by_stem)
        if not href or _SCHEME_RE.match(href) or href.startswith(("#", "/")):
            return None
        target = re.split(r"[#?]", href, maxsplit=1)[0].strip()
        if not target:
            return None
        cands = list(dict.fromkeys((unquote(target), target)))
        for cand in cands:
            cand = unicodedata.normalize("NFC", cand)
            for full in (posixpath.normpath(posixpath.join(base_dir, cand)) if base_dir else cand,
                         posixpath.normpath(cand)):
                for variant in (full, full + ".md"):
                    hit = lower.get(variant.lower())
                    if hit:
                        return hit
        for cand in cands:
            leaf = unicodedata.normalize("NFC", posixpath.basename(cand)).lower()
            stem, ext = _split_ext(leaf)
            paths = (stems.get(stem) if ext in self.run.dialect.page_exts or not ext else None) \
                or stems.get(leaf) or self.assets_by_name.get(leaf)
            if not paths:
                paths = stems.get(self.run.dialect.absent_csv(leaf))
            if paths:
                return _nearest(base_dir, paths)
        return None

    def block_target(self, page_id, sub):
        """``#Heading`` / ``#^id`` inside a link to ``page_id`` → the block it
        names, else the page itself."""
        plan = self.run.plan_of.get(page_id)
        last = next((part.strip() for part in reversed((sub or "").split("#")) if part.strip()), "")
        if plan is None or not last:
            return page_id
        if last.startswith("^"):
            return plan.anchors.get(last[1:].lower(), page_id)
        return plan.headings.get(_norm_heading(last), page_id)

    def rewrite_links(self, body, base_dir, plan):
        run, report, targets = self.run, self.run.report, self.targets
        page_id = plan.page_id

        def md_repl(m):
            bang, label, href, title = m.groups()[:4]
            if href is None:
                return m.group(0)
            href = href.strip("<>").strip()
            path = self.resolve(base_dir, href)
            if path is None:
                if href and not _SCHEME_RE.match(href) and not href.startswith(("#", "/")):
                    run.warn(run.selection, f"Missing or unselected linked file: {href}; the link is kept as written")
                return m.group(0)
            if (path in targets and targets[path] == page_id and _split_ext(path)[1] != ".csv"
                    and run.dialect.absent_csv(unquote(href).lower())):  # a page's own CSV is not in the zip
                run.warn(run.selection, f"Missing or unselected linked file: {href}; the link is kept as written")
                return m.group(0)
            if path in targets:
                report["links_resolved"] += 1
                sub = unquote(href.partition("#")[2])
                return f"[[{self.block_target(targets[path], '#' + sub if sub else '')}]]"
            url = run.store_asset(path)
            if not url:
                return m.group(0)
            return f"{bang}[{label}]({url}{title})"

        def wiki_repl(m):
            bang, target, sub, alias = m.groups()
            target = target.strip()
            if target:
                path = self.resolve(base_dir, target)
            else:
                path = None if not sub else "#self"      # [[#Heading]] — this note
            if path is None:
                if target and not _SCHEME_RE.match(target):
                    run.warn(run.selection,
                             f"Missing or unselected note or attachment: {target}; the link is kept as written")
                return m.group(0)
            if path == "#self" or path in targets:
                report["links_resolved"] += 1
                pid = page_id if path == "#self" else targets[path]
                bid = self.block_target(pid, sub)
                # An embed of one anchored block is a synced block; a whole
                # note or a section has no single block to sync (a Gamma
                # embed shows one block), so those are mentions.
                anchored = sub.split("#")[-1].startswith("^") and bid != pid
                return f"![[{bid}]]" if bang and anchored else f"[[{bid}]]"
            url = run.store_asset(path)
            if not url:
                return m.group(0)
            leaf = posixpath.basename(path)
            alias = (alias or "").strip()
            if bang and _split_ext(path)[1] in IMAGE_MEDIA_TYPES:
                size = alias if _IMAGE_SIZE_RE.match(alias) else ""
                alt = "" if size else alias
                return f"![{alt}|{size}]({url})" if size else f"![{alt}]({url})"
            return f"[{alias or leaf}]({url})"

        if run.dialect.wikilinks(plan):
            body = _WIKILINK_RE.sub(wiki_repl, body)
        return plan.source.link_re.sub(md_repl, body)
