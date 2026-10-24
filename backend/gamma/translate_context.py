"""What the PDF translated view's model is told: the system prompt of
/api/ai/translate (routers/ai.py) for a chat-model translation, and the
paper's brief it carries.

The viewer plans what it sends (frontend/src/pdf/translatePlan.js): a
paragraph that a column or page break cut in two (or three) arrives as ONE
text with BREAK where each cut was, and comes back split there.

The brief is made once per paper, language and model: one model call reads
the paper's title, bibliographic line, outline and opening pages and returns
its field, a summary, its notation and a glossary of recurring terms with
their renderings. Every translation request of that paper then carries it in
the system prompt, so parallel requests translate terms the same way and the
provider's prefix cache serves it. It lives in memory only, like the
paragraph cache. docs/dev/translation.md "The paper's brief" describes it.
"""

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from .ai_client import CallRefused
from .blocks_store import page_attachment
from .db import connect_pages_db, safe_doc_id
from .logbuf import log
from .pdf_text import extract_text, outline
from .storage import find_upload_file
from .translate_engines import TRANSLATE_LANGS

# The marker at a column or page break inside one paragraph (the viewer's
# translatePlan.BREAK).
BREAK = "{br}"

# The fixed rules. "{lang}" is replaced, never str.format-ted, so a brace in
# a rule (or in the brief appended after it) is just text.
_RULES = """\
You translate text extracted from an academic paper into {lang}.
The user message is a JSON object. "texts" is an array of strings to translate; each is one paragraph, heading, caption or list item. "section", when present, is the heading they sit under, and "before" and "after" are the paper's text just before and after them: context only, never translated or repeated in the reply.
Reply with ONLY a JSON array of strings, one per element of "texts", in the same order: element i is the translation of texts[i]. No code fences, no commentary.
Rules:
- A string containing {br} is ONE paragraph that a column or page break cut at that point. Translate it as one paragraph and keep every {br}, as many as the string has, each at the sentence or clause boundary closest to where it was.
- A string may start or end mid-sentence where its paragraph goes on in "before" or "after" (past an equation, say). Translate only its own words, so that they join that context naturally.
- Tokens such as {v1} and {v2} stand for formulas, citation markers and URLs, which the reader sees exactly as printed in the paper. Keep each token exactly once and unchanged, where the grammar of {lang} puts it; never translate, renumber, merge, split or drop one, and never write the formula out instead.
- Keep any other math, LaTeX, numbers, units, symbols, variable names and citation markers like [12] exactly as written.
- Use one rendering per term throughout, following the paper's brief below when there is one; keep acronyms and the names of methods, software and datasets as written.
- Render references such as "Fig. 2", "Eq. (3)", "Table I" and "Sec. II" the way papers in {lang} write them, the same way every time.
- Keep the register of an academic paper; translate headings as headings.
- Return a string unchanged when it is already in {lang} or is only math, code or tokens."""

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
    paper's rendered brief (``Brief.text``) after the rules."""
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


def checked(src: str, out: str) -> str | None:
    """``out`` with its tokens and breaks written the way the viewer reads
    them, when it keeps each of ``src``'s tokens exactly once and adds none;
    None when a token went missing, doubled or appeared from nowhere."""
    fixed = _BREAK_VARIANT.sub(BREAK, _TOKEN_VARIANT.sub(lambda m: "{v" + m.group(1) + "}", out or ""))
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


# How much context a request may carry around its texts.
CONTEXT_CHARS = 1500
SECTION_CHARS = 200


def user_turn(texts: list, before: str = "", after: str = "", section: str = "") -> list:
    """The translation call's messages: one user turn, the texts and their
    context as a JSON object. The context is cut to size here: the end of
    ``before`` and the start of ``after`` are the parts next to the texts."""
    body = {}
    section = " ".join((section or "").split())[:SECTION_CHARS]
    if section:
        body["section"] = section
    before = (before or "").strip()[-CONTEXT_CHARS:]
    if before:
        body["before"] = before
    body["texts"] = texts
    after = (after or "").strip()[:CONTEXT_CHARS]
    if after:
        body["after"] = after
    return [{"role": "user", "content": json.dumps(body, ensure_ascii=False)}]


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
    (``head:<hash>``). Reading it is ``material()``: only the request that
    makes the brief opens the file."""
    source: str
    title: str
    about: str
    path: str
    head: str

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
            attachment = page_attachment(props)
            doc_id = attachment["id"] if attachment else ""
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
    return Paper(source=source, title=title, about=about, path=path, head=head)


# --- the brief ----------------------------------------------------------------

_BRIEF_PROMPT = """\
You prepare a translator's brief for an academic paper that several translators will translate into {lang}, paragraph by paragraph, in parallel. The brief keeps their wording consistent.
The user message holds the paper's title, its bibliographic line, its outline and the text of its first pages.
Reply with ONLY a JSON object, no code fences, of this shape:
{"field": "the field and subfield, in English, in a few words",
 "summary": "what the paper does, in two sentences of {lang}",
 "notation": ["a symbol the paper defines - what it denotes"],
 "terms": [{"source": "a term as the paper writes it", "target": "its rendering in {lang}"}],
 "keep": ["a name that stays as written"]}
Rules:
- terms: the 20 to 40 technical terms, quantities and method names that recur in this paper, each with the rendering a specialist in the field uses in {lang}; never a term that stays as written.
- keep: acronyms and the names of methods, software, datasets, experiments and theorems that a {lang} paper keeps as written.
- notation: at most 15 symbols, only those the paper defines, as it defines them.
- Write nothing that is not supported by the text."""

_BRIEF_CAP = 300               # briefs kept in memory (LRU)
_BRIEF_FAILED_TTL = 120.0      # seconds a failed brief is not tried again
_BRIEF_WAIT = 150.0            # seconds a request waits for a brief another one is making
_BRIEF_WAITERS = 6             # requests of one account waiting at once (ai_client.MAX_OPEN_CALLS)
_BRIEF_MAX_CHARS = 4000        # the rendered brief, at most


@dataclass
class Brief:
    text: str     # rendered for the system prompt
    hash: str     # names it in the paragraph cache key
    data: dict    # the parsed reply


_briefs: "OrderedDict[str, Brief]" = OrderedDict()   # key -> Brief (LRU)
_failed: dict = {}                                     # key -> when its brief last failed
_making: dict = {}                                     # key -> threading.Event while one request makes it
_waiting: dict = {}                                    # account -> its requests waiting for a brief
_lock = threading.Lock()


def brief_key(user_id: str, ws: str, paper: Paper, lang: str, model: str) -> str:
    return hashlib.sha256(f"{user_id}\x00{ws}\x00{paper.source}\x00{lang}\x00{model}".encode()).hexdigest()


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
    summary = " ".join(str(data.get("summary") or "").split())[:600]
    if field:
        lines.append(f"Field: {field}")
    if summary:
        lines.append(f"Summary: {summary}")
    notation = _strings(data.get("notation"), 15)
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


def parse_brief(reply: str) -> Brief | None:
    """The model's brief reply → a Brief, tolerating code fences and prose
    around the object; None when nothing usable came back."""
    s = (reply or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(s[i:j + 1])
    except ValueError:
        return None
    text = render_brief(data)
    if not text:
        return None
    return Brief(text=text, hash=hashlib.sha256(text.encode()).hexdigest()[:16], data=data)


def brief_known(key: str) -> tuple[Brief | None, bool]:
    """``(brief, settled)``: the brief made for ``key``, and whether asking for
    it would come back at once — it is made, or failed within
    _BRIEF_FAILED_TTL — rather than make it or wait for it."""
    with _lock:
        if key in _briefs:
            _briefs.move_to_end(key)
            return _briefs[key], True
        failed = _failed.get(key)
        return None, failed is not None and time.monotonic() - failed < _BRIEF_FAILED_TTL


def brief_for(key: str, make, user_id: str = "", wait: bool = True) -> Brief | None:
    """The brief cached under ``key``, else ``make()``'s (one request makes
    it while the others asking for the same key wait for it, up to
    _BRIEF_WAIT). ``make`` returns the model's reply text or raises; a
    failure is remembered for _BRIEF_FAILED_TTL so a paper whose brief
    won't come is not asked for again on every chunk — but not a call Gamma
    itself refused (ai_client.CallRefused: the account's calls were all in
    use, or its allowance), which says nothing about the paper. An account
    has at most _BRIEF_WAITERS requests waiting at once; past that one goes
    on without the brief. ``wait=False`` only reads the cache: a quick
    one-off request never waits for a brief."""
    brief, settled = brief_known(key)
    if brief is not None or settled or not wait:
        return brief
    with _lock:
        if key in _briefs:
            return _briefs[key]
        event = _making.get(key)
        owner = event is None
        if owner:
            event = _making[key] = threading.Event()
        elif _waiting.get(user_id, 0) >= _BRIEF_WAITERS:
            return None
        else:
            _waiting[user_id] = _waiting.get(user_id, 0) + 1
    if not owner:
        try:
            event.wait(_BRIEF_WAIT)
        finally:
            with _lock:
                _waiting[user_id] -= 1
                if not _waiting[user_id]:
                    del _waiting[user_id]
        return brief_known(key)[0]
    brief, refused = None, False
    try:
        brief = parse_brief(make())
        if brief is None:
            log.warning("[translate] the paper's brief came back unusable; translating without one")
    except CallRefused as e:
        refused = True
        log.warning(f"[translate] the paper's brief was not asked for: {e}")
    except Exception as e:
        log.warning(f"[translate] making the paper's brief failed: {e}")
    finally:
        with _lock:
            if brief is not None:
                _briefs[key] = brief
                _briefs.move_to_end(key)
                while len(_briefs) > _BRIEF_CAP:
                    _briefs.popitem(last=False)
                _failed.pop(key, None)
            elif not refused:
                now = time.monotonic()
                _failed[key] = now
                for k in [k for k, t in _failed.items() if now - t >= _BRIEF_FAILED_TTL]:
                    del _failed[k]
            _making.pop(key, None)
        event.set()
    return brief
