"""What the PDF translated view's model is told, and how its answer is read:
the system prompt of /api/ai/translate (routers/translate.py) for a
chat-model translation, the paper's brief it carries, and the wire of
numbered segments the texts go out and come back in.

The viewer plans what it sends (frontend/src/pdf/translatePlan.js): a
paragraph that a column or page break cut in two (or three) arrives as ONE
text with BREAK where each cut was, and comes back split there.

The brief is made once per paper, language and model, by a request of its
own (/api/ai/translate/brief, ``BriefTurn``): one model call reads the
paper's title, bibliographic line, outline and opening pages and returns its
field, its notation and a glossary of recurring terms with their renderings.
Every translation request of that paper then carries it in the system prompt
(``wait_brief``), so parallel requests translate terms the same way and the
provider's prefix cache serves it. It lives in memory, and a stored paper's
is kept with its translations (gamma/translate_store.py).
docs/dev/translation.md "The paper's brief" describes it.
"""

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from .ai_client import CallRefused
from .blocks_store import attachment_file, page_attachment
from .db import connect_pages_db, safe_doc_id
from .logbuf import log
from .pdf_text import extract_text, outline
from .storage import find_upload_file
from .translate import Kept
from .translate_engines import TRANSLATE_LANGS

# The marker at a column or page break inside one paragraph (the viewer's
# translatePlan.BREAK).
BREAK = "{br}"

# The fixed rules. "{lang}" is replaced, never str.format-ted, so a brace in
# a rule (or in the brief appended after it) is just text.
_RULES = """\
You translate text extracted from an academic paper into {lang}.
The user message ends with the texts to translate as numbered segments, one per line: <s1>…</s1>, <s2>…</s2> and so on, each one paragraph, heading, caption or list item. The lines above them, when present, are context only, never translated or repeated in the reply: "Section" is the heading the texts sit under, and "Before" and "After" are the paper's text just before and after them.
Reply with ONLY the translated segments: the same numbers in the same order, each as <sN>its translation</sN> on a line of its own, and nothing else: no code fences, no commentary. Never merge two segments into one or split one into two.
Rules:
- A segment containing {br} is ONE paragraph that a column or page break cut at that point. Translate it as one paragraph and keep every {br}, as many as the segment has, each at the sentence or clause boundary closest to where it was.
- A segment may start or end mid-sentence where its paragraph goes on in the Before or After text (past an equation, say). Translate only its own words, so that they join that context naturally.
- Tokens such as {v1} and {v2} stand for formulas, citation markers and URLs, which the reader sees exactly as printed in the paper. Keep each token exactly once and unchanged, where the grammar of {lang} puts it; never translate, renumber, merge, split or drop one, and never write the formula out instead.
- Keep any other math, LaTeX, numbers, units, symbols, variable names and citation markers like [12] exactly as written.
- Use one rendering per term throughout, following the paper's brief below when there is one; keep acronyms and the names of methods, software and datasets as written.
- Render references such as "Fig. 2", "Eq. (3)", "Table I" and "Sec. II" the way papers in {lang} write them, the same way every time.
- Keep the register of an academic paper; translate headings as headings.
- Return a segment's text unchanged when it is already in {lang} or is only math, code or tokens."""

# Conventions of the target language's own academic writing.
_LANG_NOTES = {
    "zh-CN": "Write Simplified Chinese with full-width punctuation (，。：；（）“”), no spaces between "
             "Chinese characters, and a space between Chinese and Latin letters, digits or a token.",
    "zh-TW": "Write Traditional Chinese with the technical terms used in Taiwan, full-width punctuation "
             "(，。：；（）「」), no spaces between Chinese characters, and a space between Chinese and Latin "
             "letters, digits or a token.",
    "ja": "Write in the plain academic style (である調) with Japanese punctuation (、。) and no spaces "
          "between Japanese characters.",
    "ko": "Write in the plain academic style (the -다 form) with standard Korean spacing.",
}


def system_prompt(lang: str, brief: str = "") -> str:
    """The translation system prompt for a TRANSLATE_LANGS code, with the
    paper's rendered brief (``render_brief``) after the rules."""
    rules = _RULES.replace("{lang}", TRANSLATE_LANGS[lang])
    note = _LANG_NOTES.get(lang)
    if note:
        rules += f"\n- {note}"
    return f"{rules}\n\n{brief}" if brief else rules


# --- tokens -------------------------------------------------------------------
# The viewer sends a paragraph's formulas, citation markers and URLs as
# {v1}, {v2}… (frontend/src/pdf/pdfMath.js) and puts the originals back. A
# translation has to keep every one of them, once.

TOKEN_RE = re.compile(r"\{v\d+\}")
# A token or a break as models sometimes write it back: spaced, in
# full-width braces, with a capital V.
_TOKEN_VARIANT = re.compile(r"[{｛]\s*[vV]\s*(\d+)\s*[}｝]")
_BREAK_VARIANT = re.compile(r"[{｛]\s*br\s*[}｝]", re.IGNORECASE)


def checked(src: str, out: str | None) -> str | None:
    """``out`` with its tokens and breaks written the way the viewer reads
    them, when it keeps each of ``src``'s tokens exactly once and adds none;
    None when a token went missing, doubled or appeared from nowhere, or
    when ``out`` is empty or None (only text that says something is sent;
    a segment the reply lacks)."""
    if not (out or "").strip():
        return None
    fixed = _BREAK_VARIANT.sub(BREAK, _TOKEN_VARIANT.sub(lambda m: "{v" + m.group(1) + "}", out))
    if sorted(TOKEN_RE.findall(fixed)) != sorted(TOKEN_RE.findall(src or "")):
        return None
    return fixed


# --- reasoning effort -----------------------------------------------------------

def default_effort(levels, wire: str) -> str:
    """The effort a translation asks for when the user picked none: the
    model's lowest level (``levels``, lowest first, from ai_catalog), since
    thinking costs a bulk job time for little gain. "" (the parameter left
    out) when the levels are unknown or the model takes none. On the
    Anthropic wire "none" leaves the parameter out, which means the model's
    default, so its lowest is the first level after it."""
    if not levels:
        return ""
    if wire == "anthropic":
        levels = [e for e in levels if e != "none"]
    return levels[0] if levels else ""


# --- the wire -------------------------------------------------------------------
# The texts go to the model as numbered segments, <s1>…</s1> one per line
# under their context, and come back the same way. A reply is read by
# number, so a segment the model drops or doubles costs that segment alone,
# where a positional array lost the alignment of every text after it.

# How much context a request may carry around its texts.
CONTEXT_CHARS = 1500
SECTION_CHARS = 200


def user_turn(texts: list, before: str = "", after: str = "", section: str = "") -> list:
    """The translation call's messages: one user turn in plain text, the
    context a line each, a blank line, then the texts as numbered segments,
    one per line. The context is cut to size here: the end of ``before``
    and the start of ``after`` are the parts next to the texts. Text that
    reads as a segment's tag goes out masked (``_mask``)."""
    lines = []
    section = " ".join((section or "").split())[:SECTION_CHARS]
    if section:
        lines.append(f"Section: {_mask(section)}")
    before = " ".join((before or "").split())[-CONTEXT_CHARS:]
    if before:
        lines.append(f"Before (context only, not to translate): {_mask(before)}")
    after = " ".join((after or "").split())[:CONTEXT_CHARS]
    if after:
        lines.append(f"After (context only, not to translate): {_mask(after)}")
    if lines:
        lines.append("")
    lines += [f"<s{k}>{_mask(text)}</s{k}>" for k, text in enumerate(texts, 1)]
    return [{"role": "user", "content": "\n".join(lines)}]


# A segment's tag as models write it back: spaced, with a capital S, in
# full-width brackets. Group 1 is a closing tag's slash.
_TAG = re.compile(r"[<＜]\s*([/／]?)\s*[sS]\s*(\d+)\s*[>＞]")
# A tag still being written at the end of a streamed reply: "<", "</",
# "</s", "</s1"…
_HALF_TAG = re.compile(r"[<＜]\s*[/／]?\s*(?:[sS]\s*\d*\s*)?$")
_FENCED = re.compile(r"^```[\w-]*[ \t]*\n?(.*?)\n?[ \t]*```$", re.S)


def _mask(text: str) -> str:
    """``text`` with whatever reads as a segment's tag (a paper quoting
    markup, this wire's own included) between ‹ › instead of its brackets.
    Sent as written, the model would echo it inside its segment, where it
    would end that segment early or open another."""
    return _TAG.sub(lambda m: f"‹{m.group(0)[1:-1]}›", text)


def unmask(src: str, out: str | None) -> str | None:
    """A translation ``out`` of ``src`` with the tag-like text that went out
    masked (``_mask``) back as the source writes it."""
    for tag in {m.group(0) for m in _TAG.finditer(src or "")}:
        out = out and out.replace(_mask(tag), tag)
    return out


def _opened(reply: str) -> list:
    """Every segment the reply opens, in order, as ``(number, text,
    closed)``. Its text runs to the next tag: its closing one (or a closing
    tag whose number the model miswrote: the text still ends there), else
    the next segment's opening one, else the end of the reply. Text between
    segments is skipped."""
    tags = list(_TAG.finditer(reply))
    out = []
    for k, tag in enumerate(tags):
        if tag.group(1):
            continue
        following = tags[k + 1] if k + 1 < len(tags) else None
        end = following.start() if following else len(reply)
        out.append((int(tag.group(2)), reply[tag.end():end], bool(following and following.group(1))))
    return out


def segments(reply: str, n: int, capped: bool = False) -> list:
    """The reply's text for each of the ``n`` segments sent, in order. None
    for a number the reply leaves out, opens twice (which of the two is
    right is unknown) or leaves empty, and for the last segment of a reply
    cut short: left open at its end while the provider said the output cap
    stopped it (``capped``), or while the reply closed the others, which
    says the same when the provider said nothing. Numbers outside 1..n and
    text outside the segments are ignored. Asked for one text, a model may
    answer with the translation alone: a reply without any tag is then that
    translation (``_bare``), unless the cap cut it."""
    reply = reply or ""
    if n == 1 and not _TAG.search(reply):
        return [None if capped else _bare(reply)]
    opened = _opened(reply)
    cut = bool(opened) and not opened[-1][2] and (capped or any(closed for _, _, closed in opened[:-1]))
    seen = {}
    for k, (number, text, _) in enumerate(opened):
        if 1 <= number <= n:
            text = None if cut and k == len(opened) - 1 else text.strip() or None
            seen[number] = None if number in seen else text
    return [seen.get(number) for number in range(1, n + 1)]


def translations(texts: list, reply: str, capped: bool = False) -> list:
    """Each of ``texts``' translation in ``reply`` (``segments``), its masked
    tags back (``unmask``) and checked (``checked``): None where the reply
    left its segment out, doubled, emptied or cut it, or broke its tokens."""
    return [checked(src, unmask(src, out)) for src, out in zip(texts, segments(reply, len(texts), capped))]


def _bare(reply: str) -> str | None:
    """A one-text reply without tags: its text out of a code fence, or a JSON
    string's value. An object or an array is no translation (a reply of
    some other shape), and neither is nothing."""
    s = reply.strip()
    m = _FENCED.match(s)
    text = (m.group(1) if m else s).strip()
    try:
        value = json.loads(text)
    except ValueError:
        return text or None
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return text  # a number or a word JSON reads ("true") is still text


def partial_segments(text: str) -> dict:
    """What a reply still being streamed has written of each segment it
    opened, by number: the closed ones and the one in progress, less a tag
    half-written at its end. Never raises; {} before the first tag."""
    opened = _opened(text or "")
    out = {}
    for k, (number, seg, closed) in enumerate(opened):
        if not closed and k == len(opened) - 1:
            seg = _HALF_TAG.sub("", seg)
        seg = seg.strip()
        if seg:
            out[number] = seg
    return out


# --- the paper ----------------------------------------------------------------

# How much of the paper the brief reads, and of a client's `head`.
BRIEF_TEXT_CHARS = 8000
HEAD_MAX_CHARS = 12000
_OUTLINE_ENTRIES = 40
# Bookmarks that aren't sections (some PDFs bookmark every figure).
_CAPTION_RE = re.compile(r"(?i)(?:fig(?:ure)?|tab(?:le)?|eq(?:uation)?)\.?\s*\S*\d")


@dataclass
class Paper:
    """The paper behind a translation request. ``source`` names it for the
    caches: the stored file (``doc:<id>``) or the client's opening text
    (``head:<hash>``). ``doc_id`` is the stored file's id ("" without one):
    only such a paper keeps its translations and brief. Reading it is
    ``material()``: only the request that makes the brief opens the file."""
    source: str
    title: str
    about: str
    path: str
    head: str
    doc_id: str = ""

    def material(self) -> tuple[list, str]:
        """The outline's section titles and the opening text: the stored
        file's (a scanned file has none), else the client's head."""
        sections, text = [], ""
        if self.path:
            text = extract_text(self.path, BRIEF_TEXT_CHARS)
            sections = [t[:100] for level, t, _ in outline(self.path)
                        if level <= 2 and not _CAPTION_RE.match(t)][:_OUTLINE_ENTRIES]
        if not text.strip():
            text = self.head
        return sections, text[:BRIEF_TEXT_CHARS]


def _about(meta) -> str:
    if not isinstance(meta, dict):
        return ""
    authors = [str(a).strip() for a in (meta.get("authors") or []) if str(a).strip()]
    bits = [", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")] if authors else []
    bits += [str(meta[k])[:120] for k in ("year", "venue") if meta.get(k)]
    return "; ".join(bits)


def paper_of(ws: str, page_id: str, head: str = "") -> Paper | None:
    """The paper behind a translation request, from one row read: the page
    ``page_id`` and its stored PDF in workspace ``ws`` (the caller has
    checked membership), else the client's ``head``, the text of the
    document's first pages (a page shown from its source URL has no stored
    file). A page without a stored file is named by its id, so a request
    that sends no head (the selection translator) still finds the brief a
    job's request made from one. None when there is neither page nor head."""
    title = about = path = ""
    doc_id = ""
    found_page = False
    if ws and page_id:
        try:
            with connect_pages_db(ws) as connection:
                row = connection.execute(
                    "SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                    (page_id,)).fetchone()
        except Exception as e:
            log.warning(f"[translate] page lookup failed: {e}")
            row = None
        if row:
            found_page = True
            title = (row[0] or "").strip()
            try:
                props = json.loads(row[1] or "{}")
            except ValueError:
                props = {}
            about = _about(props.get("meta"))
            # The file the page shows, as the viewer reads it: a duplicated
            # page carries only the upload's URL, and is the same paper,
            # with the same kept translations, as the page it was copied from.
            doc_id = attachment_file(page_attachment(props))
            if doc_id:
                try:
                    found = find_upload_file(f"{safe_doc_id(doc_id)}.pdf", ws)
                except ValueError:
                    found = None
                path = str(found) if found else ""
    head = (head or "")[:HEAD_MAX_CHARS]
    if path:
        source = f"doc:{doc_id}"
    elif found_page:
        source = f"page:{page_id}"
    elif head.strip():
        source = "head:" + hashlib.sha256(head.encode()).hexdigest()[:16]
    else:
        return None
    return Paper(source=source, title=title, about=about, path=path, head=head, doc_id=doc_id if path else "")


# --- the brief ----------------------------------------------------------------

_BRIEF_PROMPT = """\
You prepare a translator's brief for an academic paper that several translators will translate into {lang}, paragraph by paragraph, in parallel. The brief keeps their wording consistent.
The user message holds the paper's title, its bibliographic line, its outline and the text of its first pages.
Reply with ONLY a JSON object, no code fences, of this shape:
{"field": "the field and subfield, in English, in a few words",
 "notation": ["a symbol the paper defines - what it denotes"],
 "terms": [{"source": "a term as the paper writes it", "target": "its rendering in {lang}"}],
 "keep": ["a name that stays as written"]}
Rules:
- terms: the 20 to 30 technical terms, quantities and method names that recur in this paper, each with the rendering a specialist in the field uses in {lang}; never a term that stays as written.
- keep: acronyms and the names of methods, software, datasets, experiments and theorems that a {lang} paper keeps as written.
- notation: at most 10 symbols, only those the paper defines, as it defines them.
- Write nothing that is not supported by the text."""

_BRIEF_CAP = 300               # briefs kept in memory (LRU)
_BRIEF_FAILED_TTL = 30.0       # seconds a failed brief is not tried again
_BRIEF_WAIT = 150.0            # seconds a request waits for a brief another one is making
_BRIEF_WAITERS = 6             # requests of one account waiting at once (ai_client.MAX_OPEN_CALLS)
_BRIEF_MAX_CHARS = 4000        # the rendered brief, at most


_briefs: "OrderedDict[str, str]" = OrderedDict()    # key -> the rendered brief (LRU)
_failed: dict = {}                                     # key -> (when its brief last failed, why)
_making: dict = {}                                     # key -> threading.Event while one request makes it
_waiting: dict = {}                                    # account -> its requests waiting for a brief
_lock = threading.Lock()


def brief_key(user_id: str, ws: str, paper: Paper, lang: str, model: str, gen: int) -> str:
    """The paper's memory key for one account, language and model: its
    brief's, and the paper's part of its translations' (translate_key). It
    carries the paper's generation (``gen``, gamma/translate.generation), so
    forgetting the document's translations makes it new."""
    return hashlib.sha256(f"{user_id}\x00{ws}\x00{paper.source}\x00{gen}\x00{lang}\x00{model}".encode()).hexdigest()


def cache_key(user_id: str, ws: str, paper: Paper, lang: str) -> str:
    """The provider prompt-cache routing key of a paper's translation
    requests (OpenAI's ``prompt_cache_key``): one per account, workspace,
    paper and language, apart from every chat's."""
    return hashlib.sha256(f"{user_id}\x00{ws}\x00{paper.source}\x00{lang}\x00translate".encode()).hexdigest()[:32]


def brief_messages(paper: Paper, lang: str) -> tuple[str, list]:
    """The brief call's system prompt and user turn; raises ValueError for a
    paper with no text to read (a scanned file and no head)."""
    sections, text = paper.material()
    if not text.strip():
        raise ValueError("the paper has no text to make a brief from")
    parts = []
    if paper.title:
        parts.append(f"Title: {paper.title}")
    if paper.about:
        parts.append(f"Bibliographic line: {paper.about}")
    if sections:
        parts.append("Outline:\n" + "\n".join(sections))
    parts.append(f"Opening text:\n{text}")
    return _BRIEF_PROMPT.replace("{lang}", TRANSLATE_LANGS[lang]), [{"role": "user", "content": "\n\n".join(parts)}]


def _strings(value, limit: int, width: int = 160) -> list:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        s = " ".join(str(item).split())[:width] if isinstance(item, (str, int, float)) else ""
        if s and s not in out:
            out.append(s)
    return out[:limit]


def render_brief(data: dict) -> str:
    """The parsed brief as the system prompt's closing section; "" when it
    holds nothing usable."""
    if not isinstance(data, dict):
        return ""
    lines = []
    field = " ".join(str(data.get("field") or "").split())[:160]
    if field:
        lines.append(f"Field: {field}")
    notation = _strings(data.get("notation"), 10)
    if notation:
        lines.append("Notation: " + "; ".join(notation))
    terms = []
    for item in data.get("terms") or []:
        if not isinstance(item, dict):
            continue
        src = " ".join(str(item.get("source") or "").split())[:100]
        dst = " ".join(str(item.get("target") or "").split())[:100]
        if src and dst and src != dst:
            terms.append(f"{src} → {dst}")
    if terms:
        lines.append("Terms (render each one this way every time): " + "; ".join(terms[:50]))
    keep = _strings(data.get("keep"), 40, 80)
    if keep:
        lines.append("Keep as written: " + ", ".join(keep))
    if not terms and not notation and not keep:
        return ""
    text = "The paper's brief (use it to translate consistently):\n" + "\n".join(lines)
    return text[:_BRIEF_MAX_CHARS]


def parse_brief(reply: str) -> str | None:
    """The model's brief reply → the rendered brief, tolerating code fences
    and prose around the object; None when nothing usable came back."""
    s = (reply or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return render_brief(data) or None


def _remember(key: str, brief: str) -> None:
    """(Under _lock.) ``brief`` as the one made for ``key``."""
    _briefs[key] = brief
    _briefs.move_to_end(key)
    while len(_briefs) > _BRIEF_CAP:
        _briefs.popitem(last=False)
    _failed.pop(key, None)


def made_brief(key: str, kept: Kept | None = None) -> str | None:
    """The brief made for ``key``: in memory, else kept with the paper
    (``kept``; put back in memory then). None when neither has it."""
    with _lock:
        if key in _briefs:
            _briefs.move_to_end(key)
            return _briefs[key]
    brief = kept.get_brief() if kept else None
    if not brief:
        return None
    with _lock:
        _remember(key, brief)
    return brief


def brief_making(key: str) -> bool:
    """Whether a brief request is making the brief for ``key`` right now."""
    with _lock:
        return key in _making


def _join(key: str, user_id: str):
    """(Under _lock.) The event of the request making the brief for ``key``,
    with ``user_id`` counted as one more of its account's waiters; None when
    none is making it, or the account has _BRIEF_WAITERS waiting already."""
    event = _making.get(key)
    if event is None or _waiting.get(user_id, 0) >= _BRIEF_WAITERS:
        return None
    _waiting[user_id] = _waiting.get(user_id, 0) + 1
    return event


def _leave(user_id: str) -> None:
    """(Under _lock.) One fewer of the account's requests waiting for a
    brief, as ``_join`` counted them."""
    _waiting[user_id] -= 1
    if not _waiting[user_id]:
        del _waiting[user_id]


def _wait(event, key: str, user_id: str) -> str | None:
    """Wait (up to _BRIEF_WAIT) for the request making the brief, as one of
    the waiters ``_join`` counted; the brief it made, or None."""
    try:
        event.wait(_BRIEF_WAIT)
    finally:
        with _lock:
            _leave(user_id)
    with _lock:
        return _briefs.get(key)


def wait_brief(key: str, user_id: str = "", kept: Kept | None = None, wait: bool = True) -> str | None:
    """A translation request's brief: the one made (``made_brief``), else,
    with ``wait``, the one a brief request is making right now, waited for
    up to _BRIEF_WAIT, else None at once. A translation request never makes
    the brief. An account has at most _BRIEF_WAITERS requests waiting at
    once; past that one goes on without the brief."""
    brief = made_brief(key, kept)
    if brief is not None or not wait:
        return brief
    with _lock:
        if key in _briefs:
            return _briefs[key]
        event = _join(key, user_id)
    return _wait(event, key, user_id) if event else None


class BriefTurn:
    """A brief request's turn at the brief for ``key``, claimed as it is
    made: the brief made already (``made_brief``: ``brief``), a failure
    remembered (``why``), too many of the account's requests waiting
    (``why``), or ``pending``: this request makes it (the first to ask) or
    waits for the request that does. Claimed before the request says
    anything, so the translation requests a client sends once it hears of
    the brief find it being made (``brief_making``) and wait for it.

    ``settle(make)`` gives ``(brief, why not)``; used as a context manager,
    a turn left unsettled (the client went away first) lets its claim go.
    ``make()`` returns the model's reply text or raises. A brief made here
    is kept with the paper when ``kept`` may write. A failure is
    remembered, with why, for _BRIEF_FAILED_TTL, so a paper whose brief
    won't come is not asked for again at once — but not a call Gamma itself
    refused (ai_client.CallRefused: the account's calls were all in use, or
    its allowance), which says nothing about the paper."""

    def __init__(self, key: str, user_id: str = "", kept: Kept | None = None):
        self.key, self.user_id, self.kept = key, user_id, kept
        self.brief, self.why = made_brief(key, kept), ""
        self._event, self._owner = None, False
        if self.brief is not None:
            return
        with _lock:
            if key in _briefs:
                self.brief = _briefs[key]
                return
            failed = _failed.get(key)
            if failed and time.monotonic() - failed[0] < _BRIEF_FAILED_TTL:
                self.why = failed[1]
                return
            self._owner = key not in _making
            if self._owner:
                self._event = _making[key] = threading.Event()
            else:
                self._event = _join(key, user_id)
                if self._event is None:
                    self.why = "too many of the account's requests are waiting for a paper's brief"

    @property
    def pending(self) -> bool:
        return self._event is not None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        event, self._event = self._event, None
        if event is None:
            return
        with _lock:
            if self._owner:
                _making.pop(self.key, None)
            else:
                _leave(self.user_id)
        if self._owner:
            event.set()

    def settle(self, make) -> tuple[str | None, str]:
        event, self._event = self._event, None
        if event is None:
            return self.brief, self.why
        key = self.key
        if not self._owner:
            brief = _wait(event, key, self.user_id)
            with _lock:
                return brief, "" if brief else _failed.get(key, (0, "the paper's brief could not be made"))[1]
        brief, why, refused = None, "", False
        try:
            brief = parse_brief(make())
            if brief is None:
                why = "the paper's brief came back unusable"
                log.warning(f"[translate] {why}; translating without one")
        except CallRefused as e:
            refused, why = True, str(e)
            log.warning(f"[translate] the paper's brief was not asked for: {e}")
        except Exception as e:
            why = f"making the paper's brief failed: {e}"
            log.warning(f"[translate] {why}")
        finally:
            with _lock:
                if brief is not None:
                    _remember(key, brief)
                elif not refused:
                    now = time.monotonic()
                    _failed[key] = (now, why)
                    for k in [k for k, (t, _) in _failed.items() if now - t >= _BRIEF_FAILED_TTL]:
                        del _failed[k]
                _making.pop(key, None)
            event.set()
        if brief is not None and self.kept:
            self.kept.put_brief(brief)
        return brief, why
