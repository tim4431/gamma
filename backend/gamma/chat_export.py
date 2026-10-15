"""An AI chat conversation as a file to keep (docs/dev/chat_history.md
"Exporting a conversation"): the transcript in Markdown — who said what, the
pictures each message carried, the steps a reply took with the pictures its
tools looked at, and the reply itself — with every picture written beside it
under ``assets/``. The pictures are read or drawn here as the chat shows
them (``ai_pictures.picture_at``), so the file holds what the chat showed and
opens without a server. A conversation without pictures is the bare .md.

What the chat shows is what goes: no reasoning, no tool output, no prompt
(the context export, ``ai_context.context_markdown``, is that view)."""

import hashlib
import io
import re
import zipfile

from . import ai_pictures
from .blocks_store import PATH_SEP, folder_path
from .db import page_now
from .logbuf import log
from .storage import IMAGE_EXTENSIONS

ASSETS = "assets/"
# A Markdown image of the reply whose target is one of the chat's own
# pictures: ``![alt](/api/…)``, optionally ``<…>`` and a title.
_IMAGE_RE = re.compile(r'(!\[([^\]\n]*)\]\(\s*<?)(/api/(?:uploads|ai/page-image|ai/ink-image|ai/selection-crop)/[^)\s>]+)'
                       r'(>?(?:\s+"[^"\n]*")?\s*\))')
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\s]+')


def download_base(title: str) -> str:
    """The download's file name without its extension (as the chat names the
    context export)."""
    return _UNSAFE_NAME.sub(" ", title).strip()[:80].strip() or "AI chat"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _alt(text) -> str:
    return " ".join(str(text or "").replace("[", "(").replace("]", ")").split())[:200] or "picture"


class _Assets:
    """The pictures one export writes, each once: by the URL the chat shows
    it from, then by its bytes (``<content hash>.<ext>``)."""

    def __init__(self, ws: str, conn):
        self.ws, self.conn = ws, conn
        self.by_url: dict[str, str | None] = {}
        self.files: dict[str, bytes] = {}

    def place(self, url: str) -> str | None:
        """The relative path the picture ``url`` is written at, None when it
        can no longer be read or drawn."""
        if url in self.by_url:
            return self.by_url[url]
        try:
            shown = ai_pictures.picture_at(self.ws, self.conn, url)
        except Exception as error:  # one picture that fails never fails the export
            log.warning(f"[chat_export] picture skipped: {error}")
            shown = None
        arcname = None
        if shown:
            data, media = shown
            arcname = f"{ASSETS}{hashlib.sha256(data).hexdigest()[:20]}{IMAGE_EXTENSIONS.get(media, '.img')}"
            self.files.setdefault(arcname, data)
        self.by_url[url] = arcname
        return arcname

    def image(self, url: str, alt: str) -> str:
        """The Markdown that shows ``url``, or says it is gone."""
        arcname = self.place(url) if url else None
        return f"![{_alt(alt)}]({arcname})" if arcname else f"*(picture no longer available: {_alt(alt)})*"


# --- what a message carries, as the chat reads it (chat/chatPictures.js) ----------------

def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def picture_url(picture) -> str:
    """The URL a message's picture shows from (``pictureUrl``)."""
    if isinstance(picture, str):
        return picture
    if not isinstance(picture, dict):
        return ""
    if picture.get("kind") in ai_pictures.REGION_KINDS:
        box = ai_pictures.parse_box(picture.get("box")) if picture.get("box") is not None else None
        return ai_pictures.picture_url_of_page(str(picture.get("page_id") or ""), _int(picture.get("page"), 1),
                                               box, bool(picture.get("ink")))
    return str(picture.get("url") or "")


def picture_alt(picture) -> str:
    """What a message's picture reads as (``pictureAlt``)."""
    if not isinstance(picture, dict):
        return "Pasted image"
    kind, page = picture.get("kind"), picture.get("page")
    if kind == "file":
        return f"Image file {picture['name']}" if picture.get("name") else "Image file"
    if kind == "area":
        return f"Region of PDF page {page}"
    if kind == "view":
        return f"PDF page {page} as you see it" + (", with your handwriting" if picture.get("ink") else "")
    return "Pasted image"


def chip_picture(action) -> str:
    """The picture a reply's tool chip expands to (``chipPicture``)."""
    if not isinstance(action, dict) or action.get("error"):
        return ""
    if isinstance(action.get("picture"), str) and action["picture"]:
        return action["picture"]
    if action.get("kind") == "view" and action.get("page_id") and action.get("pdf_page"):
        return ai_pictures.picture_url_of_page(str(action["page_id"]), _int(action["pdf_page"], 1))
    if action.get("kind") == "ink" and action.get("block_id"):
        return ai_pictures.ink_picture_url(str(action["block_id"]))
    return ""


def selection_crops(message: dict) -> list[str]:
    """The URLs of the selection pictures a reply sent the model (the
    coverage pill "Picture of the selection sent")."""
    urls = []
    for entry in message.get("context") or []:
        if not isinstance(entry, dict) or not entry.get("doc_id"):
            continue
        for passage in (entry.get("selection") or {}).get("passages") or []:
            box = passage.get("box") if isinstance(passage, dict) and passage.get("crop") else None
            if isinstance(box, list) and len(box) == 4:
                urls.append(f"/api/ai/selection-crop/{entry['doc_id']}?page={_int(passage.get('page'), 1)}"
                            f"&box={','.join(str(v) for v in box)}")
    return urls


# --- the transcript ---------------------------------------------------------------------

def _reply_text(text: str, assets: _Assets) -> str:
    """The reply's Markdown with its pictures pointing at ``assets/``;
    fenced code stays as written."""
    def swap(match):
        arcname = assets.place(match.group(3))
        if not arcname:
            return f"*(picture no longer available: {_alt(match.group(2))})*"
        return f"{match.group(1)}{arcname}{match.group(4)}"

    out, fence = [], ""
    for line in text.split("\n"):
        opened = _FENCE_RE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line.strip()[len(opened.group(1)):].strip():
                fence = ""
            out.append(line)
        elif opened:
            fence = opened.group(1)
            out.append(line)
        else:
            out.append(_IMAGE_RE.sub(swap, line))
    return "\n".join(out)


def _message(message: dict, assets: _Assets) -> list[str]:
    user = message.get("role") == "user"
    text = str(message.get("text") or "").strip()
    if user:
        head = "## You"
    else:
        head = "## AI" + "".join(f" · {bit}" for bit in (message.get("model"), message.get("effort")) if bit)
    out = [head, ""]
    for picture in message.get("images") or []:
        out += [assets.image(picture_url(picture), picture_alt(picture)), ""]
    pdfs = [str(name) for name in message.get("pdfs") or [] if name]
    if pdfs:
        out += [f"*Attached: {', '.join(pdfs)}*", ""]
    pages = [f"“{p['title']}”" for p in message.get("contextPages") or [] if isinstance(p, dict) and p.get("title")]
    if user and pages:
        out += [f"*Pages in context: {', '.join(pages)}*", ""]
    if user:
        return out + ([text, ""] if text else [])
    crops = selection_crops(message)
    if crops:
        out += ["*Picture of the selection sent:*", ""]
        for url in crops:
            out += [assets.image(url, "Picture of the selection sent"), ""]
    steps = [a for a in message.get("actions") or [] if isinstance(a, dict) and a.get("summary")]
    if steps:
        out += ["**Steps**", ""]
        for action in steps:
            out.append(f"- {' '.join(str(action['summary']).split())}" + (" *(failed)*" if action.get("error") else ""))
            url = chip_picture(action)
            if url:
                out += ["", f"  {assets.image(url, action['summary'])}", ""]
        out.append("")
    if message.get("error"):
        detail = " ".join(text.split())[:300]
        return out + [f"*This reply failed{': ' + detail if detail else '.'}*", ""]
    if text:
        out += [_reply_text(text, assets), ""]
    if message.get("errorKind"):
        out += ["*The reply broke off here.*", ""]
    return out


def _scope(conn, bucket: str) -> str:
    """What the conversation is the chat of, in words."""
    if not bucket or bucket == "home":
        return "the library"
    path = folder_path(conn, bucket)
    if path:
        return f"the folder “{PATH_SEP.join(path)}”"
    row = conn.execute("SELECT content FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (bucket,)).fetchone()
    return f"“{(row[0] or '').strip() or 'Untitled'}”" if row else ""


def transcript(ws: str, conn, messages: list, title: str, bucket: str = "") -> tuple[str, dict[str, bytes]]:
    """``(markdown, {arcname: bytes})``: the conversation and the pictures it
    names, read or drawn now in the workspace ``ws``."""
    assets = _Assets(ws, conn)
    messages = [m for m in messages or [] if isinstance(m, dict)]
    scope = _scope(conn, bucket)
    out = [f"# {title}", "",
           f"AI chat{' of ' + scope if scope else ''} · {_plural(len(messages), 'message')} · "
           f"exported {page_now()[:16].replace('T', ' ')} UTC", ""]
    for message in messages:
        out += _message(message, assets)
    return "\n".join(out).rstrip() + "\n", assets.files


def export_file(ws: str, conn, messages: list, title: str, bucket: str = "") -> tuple[bytes, str, str]:
    """``(data, download name, media type)``: the transcript as a bare .md,
    or a zip of it with its ``assets/`` when it names any picture."""
    base = download_base(title)
    text, files = transcript(ws, conn, messages, title, bucket)
    if not files:
        return text.encode("utf-8"), f"{base}.md", "text/markdown; charset=utf-8"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.md", text)
        for arcname, data in sorted(files.items()):
            z.writestr(arcname, data, compress_type=zipfile.ZIP_STORED)  # already compressed
    return out.getvalue(), f"{base}.zip", "application/zip"
