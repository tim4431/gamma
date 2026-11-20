"""Files an MCP assistant brings into a workspace for its notes to show:
the ``upload_file`` tool (gamma/mcp_server.py), SDK-free like
``mcp_export``. It stores, as a file dropped into a note is stored, and
answers the line the editor writes for that drop: ``![](url)`` for a
picture, ``[name](url)`` (a file chip) for anything else. The note write
that pastes the line asks the person; storing asks nobody and changes no
note (``registry.STORING_TOOLS``' rule).

Two ways in:

- ``url``: Gamma fetches a public file through the SSRF guard
  (``web_fetch.read_bounded``: public addresses only, every redirect
  checked again, 40 MB for a PDF and 5 MB for anything else, never cut
  short) and stores it. A web page is refused: a note links it.
- ``name`` alone: a one-time upload ticket for a file on the assistant's
  computer, answered as the shell command that sends it (``PUT
  /api/agent-uploads``, routers/uploads.py ``agent_upload``), the ticket in
  a header so no access log keeps it. A ticket lives TICKET_TTL_S, works
  once, is kept as its digest only, and stands for nothing but that one
  file in that one workspace: the token itself still writes nothing on
  /api. Tickets live in this process, as the uploads in parts do; a
  restart forgets them.
"""

import hashlib
import io
import mimetypes
import secrets
import shlex
import threading
import time
from collections import OrderedDict
from typing import NamedTuple
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from . import web_fetch
from .documents.formats import IMAGE_MEDIA_TYPES, extension_of, upload_extension
from .documents.model import upload_url
from .net_guard import BlockedUrlError
from .note_markup import markdown_link
from .picture import picture_type
from .storage import display_filename, path_leaf, store_file_stream, url_filename
from .upload_parts import MB, PART_BYTES_MAX, file_extension
from .workspaces import is_deleting

HEADER = "X-Gamma-Upload"
TICKET_TTL_S = 900
MAX_TICKETS = 8  # per connection: minting another drops its oldest

UPLOAD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "url": {"type": "string", "minLength": 1, "maxLength": 4096,
                "description": "a public http(s) address of the file (a picture, a PDF, a data file — not a "
                               "web page): Gamma fetches and stores it"},
        "name": {"type": "string", "minLength": 1, "maxLength": 500,
                 "description": "without url: the path or name of a file on this computer, with its extension "
                                "(./figures/fig2.png, data.csv), which the returned command sends; with url: the "
                                "name to store it under (default: the URL's)"},
    },
}

UPLOAD_DESCRIPTION = (
    "Store a picture or file in the workspace for a note to show, as a file dropped into a note is stored. "
    "With `url`, Gamma fetches a public file and stores it. With `name` alone, it returns a one-time command "
    "that sends that file from this computer (curl; run it in a shell, `curl.exe` on Windows); the command's "
    "JSON answer carries the file's `url` and `markdown`. Either way, then put the `markdown` line in a note "
    "with create_block or edit_block (`![](…)` shows a picture, `[name](…)` a file chip; give a picture a "
    "caption inside its brackets), or place a picture on a page with place_picture. Storing asks nothing and "
    "changes no note; the note write asks. A command works once, for 15 minutes, for files up to 64 MB. "
    "Without a shell, or when the command cannot reach Gamma, ask the user to drop the file into the note in "
    "Gamma instead."
)


class Ticket(NamedTuple):
    ws: str
    user_id: str
    token_id: str
    name: str
    expires: float


_tickets: OrderedDict = OrderedDict()  # digest of the secret -> Ticket
_lock = threading.Lock()


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8", "replace")).hexdigest()


def mint(ws: str, user_id: str, token_id: str, name: str) -> str:
    """A new ticket's secret, for one file named ``name`` into ``ws``, for
    the connection ``token_id``."""
    secret = secrets.token_urlsafe(24)
    with _lock:
        now = time.monotonic()
        for key in [k for k, t in _tickets.items() if t.expires <= now]:
            del _tickets[key]
        mine = [k for k, t in _tickets.items() if t.token_id == token_id]
        for key in mine[:max(0, len(mine) - MAX_TICKETS + 1)]:
            del _tickets[key]
        _tickets[_digest(secret)] = Ticket(ws, user_id, token_id, name, now + TICKET_TTL_S)
    return secret


def redeem(secret: str) -> Ticket | None:
    """The live ticket ``secret`` names, spent by asking; None for any other."""
    if not secret:
        return None
    with _lock:
        ticket = _tickets.pop(_digest(secret), None)
    return ticket if ticket and ticket.expires > time.monotonic() else None


def markdown_line(name: str, filename: str) -> str:
    """The line a note shows the stored file ``filename`` with, as the
    editor writes a dropped one (frontend transfers/FileChip.jsx): a
    picture's ``![](url)``, any other file's chip ``[name](url)``."""
    url = upload_url(filename)
    return f"![]({url})" if extension_of(filename) in IMAGE_MEDIA_TYPES else markdown_link(name, url)


def upload_file(ws: str, user_id: str, token_id: str, base: str, args: dict) -> tuple[str, dict]:
    """``upload_file``'s answer: ``(text, structured)``. Raises ValueError
    with a message for the model."""
    url, name = str(args.get("url") or "").strip(), str(args.get("name") or "").strip()
    if url:
        return _fetched(ws, url, name)
    if not name:
        raise ValueError("Pass url (a public file) or name (a file on this computer, which the command sends).")
    shown = display_filename(path_leaf(name), "file")
    try:
        file_extension(shown)
    except HTTPException as e:
        raise ValueError(f"{shown}: {e.detail}") from None
    secret = mint(ws, user_id, token_id, shown)
    target = base + "/api/agent-uploads"
    command = (f"curl -sS --fail-with-body -T {shlex.quote(name)} -H {shlex.quote(f'{HEADER}: {secret}')} "
               f"{shlex.quote(target)}")
    text = (f"Run this in a shell to send {shown} (it works once, for {TICKET_TTL_S // 60} minutes, files up to "
            f"{PART_BYTES_MAX // MB} MB; give the file's path where it names {shlex.quote(name)}):\n\n{command}\n\n"
            "Its JSON answer carries `url` and `markdown`: put the markdown line in a note with create_block or "
            "edit_block. If curl cannot reach this server (a sandbox without network, a Gamma on another "
            "computer's localhost), run it outside the sandbox or ask the user to drop the file into the note in "
            "Gamma.")
    return text, {"upload_url": target, "header": HEADER, "ticket": secret, "expires_in": TICKET_TTL_S,
                  "max_bytes": PART_BYTES_MAX}


def _fetched(ws: str, url: str, name: str) -> tuple[str, dict]:
    """The file at ``url`` fetched and stored (the module's docstring)."""
    try:
        got = web_fetch.read_bounded(url, web_fetch.BROWSER_HEADERS)
    except BlockedUrlError as e:
        raise ValueError(f"that address is not public: {e.reason}") from None
    except HTTPError as e:
        raise ValueError(f"{url} answered HTTP {e.code}") from None
    except (URLError, OSError, ValueError, web_fetch.FetchError) as e:
        raise ValueError(f"{url} could not be fetched: {e}") from None
    if got.truncated:
        raise ValueError(f"{url} is over {web_fetch.PAGE_MAX_BYTES // 1_000_000} MB, the most Gamma fetches "
                         "(40 MB for a PDF); send it from this computer with upload_file(name=…) instead")
    media = picture_type(got.body)
    ctype = got.ctype.split(";", 1)[0].strip()
    if not media and ctype in ("text/html", "application/xhtml+xml"):
        raise ValueError(f"{url} is a web page, not a file — link it in the note instead")
    shown = display_filename(name or url_filename(got.url), "file")
    if not media and not extension_of(shown):
        shown += mimetypes.guess_extension(ctype) or ""
    try:
        ext = upload_extension(shown, media)
        if ext == ".ink":
            raise ValueError("a drawing is not stored this way")
        if is_deleting(ws):
            raise ValueError("this workspace is being deleted — no changes are possible")
        filename, existed = store_file_stream(ws, io.BytesIO(got.body), ext)
    except HTTPException as e:  # the quota (507)
        raise ValueError(str(e.detail)) from None
    except ValueError as e:  # blocked, or not what its extension says (storage.content_refusal)
        raise ValueError(f"{shown}: {e}") from None
    line = markdown_line(shown, filename)
    text = (f"ok — stored {shown} ({len(got.body):,} bytes) as {upload_url(filename)}. Put it in a note with "
            f"create_block or edit_block as this line: {line}")
    return text, {"upload": upload_url(filename), "name": shown, "size": len(got.body), "markdown": line,
                  "already_existed": existed}
