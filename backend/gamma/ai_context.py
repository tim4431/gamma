"""Chat context assembly: pages from the user's knowledge base (title,
properties, notes and — when the page carries a PDF attachment — the
document's text), native PDF attachments, and the common message shapes."""

import base64
import json
import re
import sqlite3
from urllib.request import Request as URLRequest

from .blocks_store import (FOLDERS, LABELS, PATH_SEP, fetch_subtree, filing, folder_paths, label_names,
                           page_attachment, page_for_doc, page_root_id)
from .db import connect_data_db, connect_pages_db, page_now, pdf_upload_path
from .highlights import is_highlight, page_of
from .logbuf import log
from .net_guard import guarded_urlopen
from .notebook import is_sheet
from .pdf_index import doc_pages, pdf_missing
from .pdf_text import (MAX_PAGES, PAGE_LABEL_RE, PDF_EXTRACT_FAILED, extract_pages, extract_text,
                       extract_text_pages, image_part, outline, page_count, page_label, render_page)
from .server_settings import can_store
from .storage import write_atomic
from .text_box import box_page, is_text_box
from .textnorm import normalize_text


MAX_ATTACH_PDF_BYTES = 15 * 1024 * 1024
# Caps on the selected PDF passages a chat request may carry (the chat UI
# holds the same six × 4000) — the prompt copy (final_prompt) and the
# context locator (gather_inputs) both read them through request_selections.
MAX_SELECTIONS = 6
MAX_SELECTION_PASSAGE_CHARS = 4000
MAX_SELECTION_CHARS = 24_000

# Renamed agent tools: old name → current. Saved chats replay their recorded
# tool calls by name (build_messages), so a chat that ran before a rename
# must still replay — and a model that copies the old name from that history
# must still be served (ai_tools.run_agent_tool resolves through this map).
DEPRECATED_TOOLS = {"search_pdfs": "search_library"}

# What precedes the page context in the user turn. The wording matters: an
# unlabelled "here is the text" reads as the whole document, and the model
# answers detail questions from memory (docs/dev/ai_context.md).
CONTEXT_INTRO = (
    "Context — pages from the user's knowledge base. Each page gives its title, "
    "properties and the user's notes; a page that carries a PDF attachment also "
    "gives the document's text, which is often an excerpt (see its label).")
# What precedes the per-message part: the text around a PDF selection, the
# cursor block, attached blocks. It rides with the question it belongs to,
# so the document context above it stays the same from turn to turn (that
# stable prefix is what the providers' prompt caches key on).
MESSAGE_CONTEXT_INTRO = (
    "Context for this message — what the user is pointing at right now (the text "
    "around their PDF selection, the note block their cursor is on, blocks they "
    "attached). The pages above stay as they were.")
# Chars of document text around one selected passage (2,500 of them before
# it — the set-up and definitions a passage leans on sit ahead of it). The
# head excerpt is in the document context already, so a selection adds only
# its windows, not a second copy of the start of the paper.
SELECTION_WINDOW_CHARS = 10_000


def canonical_tool(name: str) -> str:
    return DEPRECATED_TOOLS.get(name, name)


def parse_images(images: list) -> list[tuple[str, str]]:
    """Return validated ``(media_type, base64)`` pairs from image data URLs."""
    parsed = []
    for item in (images or [])[:4]:
        match = re.match(
            r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=]+)$",
            str(item),
        )
        if not match:
            continue
        media_type, data = match.group(1), match.group(2)
        if len(data) > 8_000_000:
            continue
        parsed.append(("image/jpeg" if media_type == "image/jpg" else media_type, data))
    return parsed


def parse_files(files: list) -> list[str]:
    """Return valid, size-limited PDF payloads from uploaded data URLs."""
    parsed = []
    for item in (files or [])[:4]:
        if not isinstance(item, dict):
            continue
        match = re.match(
            r"^data:application/pdf;base64,([A-Za-z0-9+/=]+)$",
            str(item.get("data", "")),
        )
        if not match:
            continue
        data = match.group(1)
        if len(data) <= MAX_ATTACH_PDF_BYTES * 4 // 3:
            parsed.append(data)
    return parsed


def _selection_box(value):
    """A selection's region as ``(x0, y0, x1, y1)`` fractions of its page
    (top-left origin), or None when the value isn't a usable box."""
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in value)
    except (TypeError, ValueError):
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def request_selections(payload) -> list[dict]:
    """The PDF passages a chat message is about, as ``{text, page, box}``:
    ``page`` is the 1-based PDF page the viewer saw the selection start on
    (0 = unknown) and ``box`` its region on that page (None = unknown).
    ``selections`` is the structured field; the older ``selection`` string
    ("---"-joined passages, no positions) is still read when it is absent."""
    passages = []
    for item in getattr(payload, "selections", None) or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        page = item.get("page")
        page = page if isinstance(page, int) and 0 < page <= MAX_PAGES else 0
        if text:
            passages.append({"text": text, "page": page,
                             "box": _selection_box(item.get("box")) if page else None})
    if not passages:
        passages = [{"text": p.strip(), "page": 0, "box": None}
                    for p in re.split(r"\n\s*---\s*\n", getattr(payload, "selection", "") or "")
                    if p.strip()]
    kept, room = [], MAX_SELECTION_CHARS
    for passage in passages[:MAX_SELECTIONS]:
        text = passage["text"][:min(MAX_SELECTION_PASSAGE_CHARS, room)]
        if not text:
            break
        kept.append({**passage, "text": text})
        room -= len(text)
    return kept


def _passage_heading(index: int, count: int, where: dict) -> str:
    """"Passage 2 (PDF page 7; section "3.2 Noise model"):" — where a
    selected passage sits, as far as it is known."""
    bits = []
    if where.get("page"):
        bits.append(f"PDF page {where['page']}")
    if where.get("section"):
        bits.append(f'section "{where["section"]}"')
    if where.get("crop"):
        bits.append("a picture of the selected region is attached, because its extracted "
                    "text is unreliable (a formula, table or odd font): read the picture")
    name = f"Passage {index + 1}" if count > 1 else "Selected passage"
    return name + (f" ({'; '.join(bits)})" if bits else "") + ":"


def final_prompt(payload, located: list | None = None) -> str:
    """Append the selected PDF passage(s) and selected note text to a user's
    current prompt. ``located`` is what the context builder learned about
    each passage (``selection_context``: page, section, attached picture);
    without it a passage is labelled with the page the viewer reported."""
    prompt = payload.prompt
    passages = request_selections(payload)
    if passages:
        blocks = []
        for i, passage in enumerate(passages):
            where = located[i] if located and i < len(located) else {"page": passage["page"]}
            blocks.append(f'{_passage_heading(i, len(passages), where)}\n"""\n{passage["text"]}\n"""')
        several = len(passages) > 1
        prompt = (
            f"{prompt}\n\n"
            f"The user has selected the following passage{'s' if several else ''} from the "
            f"page's PDF attachment. Answer specifically about {'them' if several else 'it'}:"
            "\n\n" + "\n\n".join(blocks)
        )
    selected = request_note_selections(payload)
    if selected:
        shown = "\n\n".join(
            f'{sel["label"]} (in block [{sel["block_id"]}]):\n"""\n'
            + sel["text"][:MAX_NOTE_PASSAGE_CHARS]
            + ("…" if len(sel["text"]) > MAX_NOTE_PASSAGE_CHARS else "") + '\n"""'
            for sel in selected)
        several = len(selected) > 1
        prompt = (
            f"{prompt}\n\n"
            f"The user has selected the following exact passage{'s' if several else ''} "
            "of their own notes (markdown source, labelled; \"this\" and \"the selection\" "
            f"mean {'them' if several else 'it'}). Answer a question about the selection in "
            "the chat; carry out an instruction that transforms it (rewrite, fix, translate, "
            '…) by editing only the selected text with edit_block mode "selection":'
            f"\n{shown}"
        )
    return prompt


# Caps for what a message may point at inside the notes: attached blocks
# (chips), selected note text, and the size of each in the prompt.
MAX_CONTEXT_BLOCKS = 12
MAX_NOTE_SELECTIONS = 6
MAX_NOTE_PASSAGE_CHARS = 4000
MAX_BLOCK_SECTION_CHARS = 12_000
# Attached handwriting blocks whose picture (gamma/ink_view.py) rides with
# one message, so "transcribe this" needs no tool call.
MAX_INK_PICTURES = 2


def request_note_selections(payload) -> list[dict]:
    """The note text the user selected for this message, as exact ranges of
    block sources: [{label: "S1", block_id, from, to, text}]. `text` is the
    source slice the client saw — the edit tool re-finds the passage by it
    when the block changed since. Malformed entries are dropped."""
    out = []
    for raw in (getattr(payload, "note_selections", None) or [])[:MAX_NOTE_SELECTIONS]:
        if not isinstance(raw, dict):
            continue
        block_id = str(raw.get("block_id") or "").strip()[:64]
        text = raw.get("text")
        try:
            start, end = int(raw.get("from")), int(raw.get("to"))
        except (TypeError, ValueError):
            continue
        if (not block_id or not isinstance(text, str) or not text.strip()
                or start < 0 or end - start != len(text)):
            continue
        out.append({"label": f"S{len(out) + 1}", "block_id": block_id,
                    "from": start, "to": end, "text": text})
    return out


def handwriting_label(props: dict) -> str:
    """How an outline line (read_block, the notes-focus section) names a
    handwriting block or a page of paper, whose text is only a caption; ""
    for any other block."""
    if props.get("ink_url"):
        page = page_of(props)
        where = f"on p. {page}" if page else "on the page of paper above"
        return f"(handwriting {where}, {props.get('ink_strokes', 0)} strokes; the text is its caption)"
    if is_sheet(props):
        return "(a page of paper: the handwriting under it is written on it)"
    return ""


def notes_focus_section(ws: str, payload, notes_seen: dict | None = None,
                        crops: list | None = None) -> str:
    """The user's pointer into their notes, as one context section: the
    block their cursor is on and the blocks they attached to this message,
    each as ``[id] text`` with its sub-blocks indented — the same id-labelled
    form ``read_block`` gives, so an agent can edit them straight away. Only
    blocks of the request's context pages are served (a chip from another
    page is silently dropped). Empty when there is nothing to point at.
    ``notes_seen`` receives ``{block_id: text}`` for every block shown in
    full — what an edit_block replace may start from (ai_tools.notes_seen).
    ``crops``, when given, receives the pictures of attached handwriting
    blocks and pages of paper (up to ``MAX_INK_PICTURES``)."""
    focus = str(getattr(payload, "focus_block_id", "") or "").strip()
    chips = [str(b).strip() for b in (getattr(payload, "context_blocks", None) or [])
             if str(b).strip()][:MAX_CONTEXT_BLOCKS]
    # A selection's block rides along whole, so the model sees what surrounds
    # the passage it may rewrite.
    for sel in request_note_selections(payload):
        if sel["block_id"] != focus and sel["block_id"] not in chips:
            chips.append(sel["block_id"])
    if not focus and not chips:
        return ""
    pages = {str(p) for p in (payload.pages or []) if p}
    if payload.page_id:
        pages.add(payload.page_id)
    if not pages:
        return ""
    out = []
    try:
        with connect_pages_db(ws) as conn:
            def outline(block_id: str, budget: int) -> str | None:
                if page_root_id(conn, block_id) not in pages:
                    return None
                rows = fetch_subtree(conn, block_id)
                if not rows:
                    return None
                by_parent: dict = {}
                own = None
                for row in rows:
                    if row[0] == block_id:
                        own = row
                    else:
                        by_parent.setdefault(row[1], []).append(row)
                if own is None:
                    return None
                for children in by_parent.values():
                    children.sort(key=lambda r: r[2])
                lines, used = [], 0

                def props_of(row):
                    try:
                        return json.loads(row[4] or "{}")
                    except ValueError:
                        return {}

                def line(row, depth, on_sheet):
                    props = props_of(row)
                    text = (row[3] or "").strip() or "(empty)"
                    quote = (props.get("quote") or "").strip()
                    if quote:
                        text = f'(highlight: "{quote[:200]}") {text}'
                    elif label := text_box_label(props, on_sheet) or handwriting_label(props):
                        text = f"{label} {text}"
                    pad = "  " * depth
                    return pad + f"- [{row[0]}] " + text.replace("\n", "\n" + pad + "  ")

                def walk(parent, depth, on_sheet):
                    nonlocal used
                    for row in by_parent.get(parent, []):
                        entry = line(row, depth, on_sheet)
                        if used + len(entry) > budget:
                            lines.append("  " * depth + "- … (more sub-blocks not shown)")
                            return
                        used += len(entry)
                        lines.append(entry)
                        if notes_seen is not None:
                            notes_seen[row[0]] = row[3] or ""
                        walk(row[0], depth + 1, on_sheet or is_sheet(props_of(row)))

                on_sheet = under_sheet(conn, block_id)
                head = line(own, 0, on_sheet)
                if len(head) > budget:
                    head = head[:budget] + "…"
                elif notes_seen is not None:
                    notes_seen[own[0]] = own[3] or ""
                used = len(head)
                lines.append(head)
                walk(block_id, 1, on_sheet or is_sheet(props_of(own)))
                return "\n".join(lines)

            if focus and focus not in pages:
                text = outline(focus, 1500)
                if text:
                    out.append("The user's cursor is on this note block (\"this block\", "
                               "\"here\" mean it):\n" + text)
            def ink_picture(block_id: str):
                """An attached handwriting block's (or page of paper's)
                picture, as the wires take it; None for any other block."""
                row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                   (block_id,)).fetchone()
                try:
                    props = json.loads(row[0] or "{}") if row else {}
                except ValueError:
                    props = {}
                if not handwriting_label(props):
                    return None
                from .ink_view import picture
                try:
                    shown = picture(ws, conn, block_id, page_root_id(conn, block_id))
                except Exception as error:  # a picture that fails never fails the message
                    log.warning(f"[ai_chat] handwriting picture of {block_id} failed: {error}")
                    return None
                return image_part(shown["image"]) if shown.get("image") else None

            if chips:
                shown = []
                budget = MAX_BLOCK_SECTION_CHARS
                pictures = 0
                for block_id in chips:
                    if block_id in pages:
                        continue
                    text = outline(block_id, max(500, budget // max(1, len(chips))))
                    if text and crops is not None and pictures < MAX_INK_PICTURES:
                        image = ink_picture(block_id)
                        if image:
                            crops.append(image)
                            pictures += 1
                            text += "\n  (a picture of this handwriting is attached to the message)"
                    if text:
                        shown.append(text)
                if shown:
                    out.append("Note blocks the user attached to this message (ids in "
                               "brackets; edit them by id when asked to change them):\n"
                               + "\n".join(shown))
    except sqlite3.Error as error:
        log.warning(f"[ai_chat] notes focus section failed: {error}")
        return ""
    return "\n\n".join(out)


# Replayed tool results across the whole history share this char budget
# (newest first); older results are elided so long agent sessions don't grow
# each request without bound. The calls themselves are always kept — they are
# what stops the model from repeating work it already did.
TOOL_REPLAY_BUDGET = 8000
_ELIDED_RESULT = "(older result elided to save space — call the tool again if needed)"
# Replayed results are snapshots: the notes may have been edited since (by
# the user, or by the agent's own later calls). Saying so on every replayed
# result stops the model from answering "read X" from a stale outline.
_REPLAYED_NOTE = "[result from an earlier turn — notes may have changed since; call again before quoting or editing]\n"
# A change the user took back from its row under the reply (`reverted` on
# the saved action, gamma/ai_revert.py): the model must not build on it.
_REVERTED_NOTE = "[the user reverted this change afterwards — the notes no longer hold it]\n"


def _replayable(history_item: dict) -> list[dict]:
    """The saved actions of one AI reply that carry the raw call (chips saved
    before tool recording existed have no tool/result and can't be replayed)."""
    if history_item.get("role") != "ai":
        return []
    return [a for a in (history_item.get("actions") or [])
            if isinstance(a, dict) and a.get("tool")]


def _elide_old_results(history: list) -> dict[int, set]:
    """Pick which replayed tool results keep their text: walk newest-first
    under TOOL_REPLAY_BUDGET; everything older is elided. Returns
    {history_index: {action_index, ...}} of the elided ones."""
    elided: dict[int, set] = {}
    budget = TOOL_REPLAY_BUDGET
    for i in range(len(history) - 1, -1, -1):
        for j, action in reversed(list(enumerate(_replayable(history[i])))):
            budget -= len(str(action.get("result") or ""))
            if budget < 0:
                elided.setdefault(i, set()).add(j)
    return elided


# The tool results of ONE reply's rounds share this budget (chars; a picture
# counts as _IMAGE_CHARS). It is a valve, not a per-round trim: the loop
# re-sends every round's results with the next call, and rewriting an
# earlier turn costs the provider's prompt cache the rest of the prefix, so
# nothing is touched until the results outgrow the budget — then the oldest
# rounds' results become the same stub the cross-turn replay uses, the last
# rounds always kept whole.
LIVE_RESULT_BUDGET = 60_000
LIVE_KEEP_ROUNDS = 2
_IMAGE_CHARS = 6000  # a rendered PDF page (1568 px) is ~1.5k tokens


def _result_size(message: dict) -> int:
    return len(message.get("content") or "") + _IMAGE_CHARS * len(message.get("images") or ())


def elide_live_results(messages: list, keep_rounds: int = LIVE_KEEP_ROUNDS,
                       budget: int = LIVE_RESULT_BUDGET) -> int:
    """Cut the oldest tool results of ``messages`` (the common turn list of
    a running reply, edited in place) down to ``budget`` chars, never the
    last ``keep_rounds`` rounds; a round is one run of consecutive tool
    results. Returns how many results were elided (0 = nothing changed)."""
    rounds: list[list[dict]] = []
    previous_was_tool = False
    for message in messages:
        is_tool = message.get("role") == "tool"
        if is_tool and previous_was_tool:
            rounds[-1].append(message)
        elif is_tool:
            rounds.append([message])
        previous_was_tool = is_tool
    total = sum(_result_size(m) for r in rounds for m in r)
    if total <= budget:
        return 0
    elided = 0
    for round_ in rounds[:max(0, len(rounds) - keep_rounds)]:
        for message in round_:
            if total <= budget:
                return elided
            if message.get("content") == _ELIDED_RESULT and not message.get("images"):
                continue
            total -= _result_size(message)
            message["content"] = _ELIDED_RESULT
            message.pop("images", None)
            elided += 1
    return elided


def estimate_tokens(text: str) -> int:
    """A rough token count of ``text`` without a tokenizer: four ASCII
    characters or one other character (CJK, Greek, math symbols) per token
    — close enough to keep a prompt under a window, never a bill."""
    if not text:
        return 0
    ascii_chars = len(text.encode("ascii", "ignore"))
    return ascii_chars // 4 + (len(text) - ascii_chars)


_IMAGE_TOKENS = 1600  # a picture at the viewer's render size


def prompt_tokens(messages: list, system: str = "", tools: list | None = None,
                  images: list | None = None) -> int:
    """estimate_tokens over everything a request carries but its native
    PDF files (their token cost is the provider's; a chat that attaches one
    is already the user's explicit choice)."""
    total = estimate_tokens(system) + estimate_tokens(json.dumps(tools or [])) if (system or tools) else 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        if message.get("tool_calls"):
            total += estimate_tokens(json.dumps(message["tool_calls"], ensure_ascii=False))
        total += _IMAGE_TOKENS * len(message.get("images") or ())
    return total + _IMAGE_TOKENS * len(images or ())


def build_messages(payload, context: str, with_tools: bool = False,
                   located: list | None = None, message_context: str = "",
                   drop_turns: int = 0) -> list[dict]:
    """Build common chat messages, injecting context once before a user turn.

    With ``with_tools`` (an agent chat), each saved reply's tool calls are
    replayed as assistant ``tool_calls`` + ``role:"tool"`` result turns — the
    same common shapes the wire builders translate for the live loop — so the
    model remembers what it already listed/read/changed instead of repeating
    the calls each turn. Plain chats must not replay them: providers reject
    tool blocks without tool definitions in the request. ``located`` labels
    the selected passages in the question (see ``final_prompt``);
    ``message_context`` (the per-message part of gather_inputs) goes in
    front of the question itself, so the document ``context`` — glued to
    the oldest user turn — reads the same on every turn of a conversation.
    ``drop_turns`` leaves out that many of the oldest history items (a
    conversation the model's window can't hold any more).
    """
    history = [h for h in (payload.history or []) if not h.get("error")]
    if drop_turns > 0:
        # The kept history opens on a question, never on a reply to one
        # the model can't see (providers want the first turn to be the user's).
        history = history[drop_turns:]
        while history and history[0].get("role") == "ai":
            history = history[1:]
    elided = _elide_old_results(history) if with_tools else {}
    messages = []
    context_used = False
    for i, history_item in enumerate(history):
        role = "assistant" if history_item.get("role") == "ai" else "user"
        content = history_item.get("text", "")
        if with_tools:
            actions = _replayable(history_item)
            if actions:
                # Calls first, then their results, then the reply prose — the
                # order the turn actually happened in. Synthetic call ids only
                # need to pair within this one request.
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"call_h{i}_{j}", "name": canonical_tool(a["tool"]),
                     "arguments": a.get("args") or {}}
                    for j, a in enumerate(actions)]})
                for j, a in enumerate(actions):
                    result = (_ELIDED_RESULT if j in elided.get(i, ())
                              else _REPLAYED_NOTE + str(a.get("result") or "(empty result)"))
                    if a.get("reverted"):
                        result = _REVERTED_NOTE + result
                    messages.append({"role": "tool", "call_id": f"call_h{i}_{j}",
                                     "content": result})
        if not content.strip():
            # An organizer reply can be tool actions with no prose; providers
            # (Anthropic especially) reject empty content blocks.
            continue
        if role == "user" and context and not context_used:
            content = f"{CONTEXT_INTRO}\n\n{context}\n\nUser question: {content}"
            context_used = True
        messages.append({"role": role, "content": content})
    content = final_prompt(payload, located)
    head = f"{CONTEXT_INTRO}\n\n{context}" if context and not context_used else ""
    if message_context:
        head = (f"{head}\n\n---\n\n" if head else "") + f"{MESSAGE_CONTEXT_INTRO}\n\n{message_context}"
    if head:
        content = f"{head}\n\nUser question: {content}"
    messages.append({"role": "user", "content": content})
    return messages


def _fenced(text: str, info: str = "") -> str:
    """``text`` in a code fence longer than any backtick run inside it, so
    notes with their own fences stay whole."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text}\n{fence}"


def _coverage_line(entry: dict) -> str:
    bits = []
    if entry.get("doc_id"):
        shown, pages = entry.get("pages_shown") or 0, entry.get("pages") or 0
        bits.append(f"PDF text, pages 1–{shown} of {pages}" if entry.get("partial") and shown and pages
                    else "PDF text")
    if entry.get("notes"):
        bits.append("notes")
    return f"- {entry.get('title') or 'Untitled'}" + (f" ({', '.join(bits)})" if bits else "")


def context_markdown(title: str, system: str, messages: list, tools: list | None = None,
                     coverage: list | None = None, images: list | None = None) -> str:
    """A chat request as a Markdown file: the pages in context, the system
    prompt, the tools, then every turn exactly as the model receives it —
    text only; pictures are counted, never embedded (the wires put the
    request's own on its last user turn)."""
    def plural(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    tokens = prompt_tokens(messages, system, tools, images)
    out = [f"# {title.strip() or 'AI chat'} — context", "",
           f"Exported {page_now()[:16].replace('T', ' ')} UTC · {plural(len(messages), 'message')} · "
           f"about {tokens:,} tokens (estimate)", ""]
    if coverage:
        out += ["## Pages in context", "", *(_coverage_line(c) for c in coverage), ""]
    if system:
        out += ["## System prompt", "", _fenced(system, "text"), ""]
    if tools:
        summaries = [(t["name"], (t.get("description") or "").split("\n", 1)[0]) for t in tools]
        out += ["## Tools", "", *(f"- `{name}` — {summary}" for name, summary in summaries),
                "", _fenced(json.dumps(tools, ensure_ascii=False, indent=2), "json"), ""]
    out += ["## Messages", ""]
    names = {}  # tool call id -> tool name, for the result headings
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    for i, message in enumerate(messages):
        role = message.get("role")
        calls = message.get("tool_calls") or []
        if role == "tool":
            heading = f"Tool result · {names.get(message.get('call_id'), 'tool')}"
        elif role == "assistant":
            heading = "Assistant" + (f" · calls {', '.join(c['name'] for c in calls)}" if calls else "")
        else:
            heading = "User"
        out += [f"### {i + 1}. {heading}", ""]
        if message.get("content"):
            out += [_fenced(str(message["content"]), "text"), ""]
        for call in calls:
            names[call.get("id")] = call.get("name")
        if calls:
            out += [_fenced(json.dumps([{"name": c.get("name"), "arguments": c.get("arguments")}
                                        for c in calls], ensure_ascii=False, indent=2), "json"), ""]
        pictures = len(message.get("images") or ()) + (len(images or ()) if i == last_user else 0)
        if pictures:
            out += [f"*{plural(pictures, 'picture')} sent with this message (not included).*", ""]
    return "\n".join(out)


def _download_pdf_from_source(ws: str, doc_id: str, pdf_path) -> None:
    """Best-effort download of a missing PDF from its recorded source URL."""
    log.info(f"[ai_chat] PDF NOT FOUND at {pdf_path}, attempting download from source_url")
    try:
        with connect_pages_db(ws) as connection:
            row = connection.execute(
                "SELECT properties FROM unified_blocks WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if not row:
            return
        properties = json.loads(row[0] or "{}")
        source = properties.get("source_url") or ""
        if not source:
            return
        request = URLRequest(
            source,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*;q=0.8"},
        )
        with guarded_urlopen(request, timeout=30) as response:
            pdf_data = response.read()
        if not can_store(ws, len(pdf_data)):
            log.info(f"[ai_chat] not caching {doc_id} ({len(pdf_data)} bytes): over storage limits")
            return
        write_atomic(pdf_path, pdf_data)
        log.info(f"[ai_chat] downloaded {len(pdf_data)} bytes from {source}")
    except Exception as error:
        log.warning(f"[ai_chat] download failed: {error}")


def pdf_path(ws: str, doc_id: str):
    """Return a document's local PDF path, downloading it when possible."""
    try:
        path = pdf_upload_path(ws, doc_id)
    except ValueError:
        return None
    if not path.exists():
        _download_pdf_from_source(ws, doc_id, path)
    return path if path.exists() else None


def truncate(text: str, limit: int) -> str:
    return text[:limit] + "\n…[truncated]" if len(text) > limit else text


def extract_pdf_context(ws: str, doc_id: str, limit: int = 8000) -> str:
    """The head of a document's text, labelled with how little of it that is.

    Without the label the model reads "Here is the PDF text:" as the whole
    paper and answers detail questions from memory rather than looking them
    up — measurably the biggest source of confident wrong answers.
    """
    return head_context(ws, doc_id, limit)[0]


def head_context(ws: str, doc_id: str, limit: int) -> tuple[str, dict]:
    """extract_pdf_context plus its coverage: ``{"partial", "chars",
    "pages", "pages_shown"}`` — what the chat reports back to the user so a
    truncated paper is visible in the UI, not only in the prompt label."""
    text, next_offset, _, span = pdf_excerpt(ws, doc_id, limit, with_pages=True)
    pages_shown = span[1]
    cover = {"partial": next_offset is not None, "chars": len(text),
             "pages": pages_shown, "pages_shown": pages_shown}
    if next_offset is None:
        return text, cover  # the whole document fits (or nothing extracted) — no caveat needed
    path = pdf_path(ws, doc_id)
    pages = cover["pages"] = page_count(str(path)) if path else 0
    # The label names the pages the excerpt reaches, so the model (and the
    # agent prompt, from the same coverage) knows which pages to read for
    # the rest instead of reading page 1 again.
    where = f" of this {pages}-page PDF" if pages else ""
    span_note = (f": PDF pages 1–{pages_shown}, page {pages_shown} cut short" if pages_shown > 1
                 else ": part of PDF page 1") if pages_shown else ""
    rest = (f"Pages {pages_shown + 1}–{pages} are NOT shown below" if pages and pages_shown < pages
            else "The rest of the document is NOT shown below")
    return (f"[EXCERPT — the first {limit:,} characters{where}{span_note}. {rest}. "
            f"Anything outside this excerpt has to be looked up before you can "
            f"answer about it.]\n\n{text}\n…[truncated]"), cover


# One line per PDF page, read from the search index (which already holds the
# per-page text) so building it costs a query rather than a re-parse. It tells
# the agent where things are, so it can jump to a page instead of guessing —
# in testing this cut tool calls and stopped it settling for a plausible but
# wrong neighbouring page.
MAP_BUDGET = 2400
# Chars of page text one outline line shows; the substr fetches a little extra
# so whitespace collapse still fills the line, and the sampling step estimates
# each line's cost as the text plus the "  p.N: " prefix.
_MAP_LINE_CHARS = 80


def ensure_indexed(ws: str, doc_id: str) -> bool:
    """Kick the background indexer for a paper the search index doesn't hold
    at the current version, so a page chat's document map and search_library
    exist by the next turn even when the model never calls search. Returns
    True when the paper is already current."""
    # Local import: keep gamma.* module load free of the routers package.
    from .routers.search import _index_missing_async
    try:
        with connect_data_db(ws) as connection:
            missing = pdf_missing(connection, [doc_id])
    except sqlite3.OperationalError as e:
        log.warning(f"[ai_context] index check for {doc_id} failed: {e}")
        return False
    if not missing:
        return True
    _index_missing_async(ws, missing)
    return False


def document_map(ws: str, doc_id: str, budget: int = MAP_BUDGET, from_page: int = 1) -> str:
    """How each PDF page starts, as a compact outline. "" when the document
    isn't indexed yet (search is unavailable then too; ensure_indexed kicks
    the indexer so the next turn has both). Reads whichever index version is
    stored — a page-start outline barely depends on normalization, and stale
    docs re-index lazily through the search paths anyway. ``from_page``
    starts the map after the pages the excerpt already shows in full."""
    try:
        with connect_data_db(ws) as connection:
            rows = doc_pages(connection, doc_id, _MAP_LINE_CHARS + 10)
    except sqlite3.OperationalError:
        return ""  # the index is busy: no map this turn
    if len(rows) < 3:
        return ""  # too short to need a map
    total = rows[-1][0]
    rows = [r for r in rows if r[0] >= from_page]
    if not rows:
        return ""
    step = max(1, -(-len(rows) * (_MAP_LINE_CHARS + 20) // budget))
    lines = [f"  p.{page}: {' '.join((head or '').split())[:_MAP_LINE_CHARS]}"
             for page, head in rows[::step] if (head or "").strip()]
    if not lines:
        return ""
    every = "" if step == 1 else f", every {step}th page"
    start = f", from page {from_page} (the pages before it are in the excerpt above)" if from_page > 1 else ""
    return (f"[DOCUMENT MAP — how each page of this {total}-page PDF starts{start}"
            f"{every}. Use it to pick the page to read: "
            f"read_page(pdf_page=N).]\n" + "\n".join(lines))


def pdf_excerpt(ws: str, doc_id: str, limit: int, offset: int = 0,
                start_page: int = 1, with_pages: bool = False):
    """Slice ``[offset, offset+limit)`` of a document's extracted text so long
    papers can be read in successive windows; start_page (1-based) starts the
    extraction at that PDF page — the shape search hits come in — and offset
    then counts from there. Returns ``(text, next_offset, seen)``: next_offset
    is where a follow-up read should continue (None = the extraction ended
    inside this window), seen is how many chars were extracted in total — when
    offset points past the end, that's the full extracted length (from
    start_page on). with_pages=True appends the PDF pages the window spans
    as a fourth value, ``(first, last)`` (``(0, 0)`` when there is no
    text): last is the page the window ends on, first the page it starts
    in — after start_page when an offset skips whole pages."""
    path = pdf_path(ws, doc_id)
    if not path:
        log.warning("[ai_chat] PDF still not found after download attempt")
        return ("", None, 0, (0, 0)) if with_pages else ("", None, 0)
    try:
        # extract_text stops after the page that crosses the limit, so a
        # longer-than-requested result means more pages remain.
        if with_pages:
            full, pages = extract_text_pages(str(path), offset + limit, start_page=start_page, label_pages=True)
        else:
            full, pages = extract_text(str(path), offset + limit, start_page=start_page, label_pages=True), 0
    except Exception as error:
        log.warning(f"[ai_chat] extraction error: {error}")
        return (PDF_EXTRACT_FAILED, None, 0, (0, 0)) if with_pages else (PDF_EXTRACT_FAILED, None, 0)
    text = full[offset:offset + limit]
    next_offset = offset + limit if len(full) > offset + limit else None
    first = start_page
    # A continuation may start halfway through a physical page. Repeat its
    # label outside the window; offsets still count only the extracted text.
    if offset and text:
        labels = [m for m in PAGE_LABEL_RE.finditer(full) if m.start() <= offset]
        if labels:
            text = text[max(0, labels[-1].end() - offset):]
            text = page_label(labels[-1].group(1), continued=True) + text
            first = int(labels[-1].group(1))
    if not with_pages:
        return text, next_offset, len(full)
    span = (first, start_page + pages - 1) if text and pages else (0, 0)
    return text, next_offset, len(full), span


def load_pdf_b64(ws: str, doc_id: str) -> str | None:
    """Return a size-limited document PDF as base64."""
    path = pdf_path(ws, doc_id)
    if not path:
        return None
    data = path.read_bytes()
    if len(data) > MAX_ATTACH_PDF_BYTES:
        log.info(f"[ai_chat] PDF too large to attach ({len(data)} bytes), falling back to text")
        return None
    return base64.standard_b64encode(data).decode("ascii")


def pdf_text_from_b64(data: str, limit: int = 8000) -> str:
    """Extract text from a base64 PDF when native attachment is unavailable."""
    return pdf_text_cover_from_b64(data, limit)[0]


def pdf_text_cover_from_b64(data: str, limit: int) -> tuple[str, dict]:
    """pdf_text_from_b64 plus the coverage dict head_context reports."""
    try:
        raw = base64.standard_b64decode(data)
        full, pages_shown = extract_text_pages(raw, limit)
        partial = len(full) > limit
        return (truncate(full, limit),
                {"partial": partial, "chars": min(len(full), limit),
                 "pages": page_count(raw) if partial else pages_shown,
                 "pages_shown": pages_shown})
    except Exception as error:
        log.warning(f"[ai_chat] uploaded-PDF extraction failed: {error}")
        return "", {"partial": False, "chars": 0, "pages": 0, "pages_shown": 0}


# Selected-passage location: how much of a passage's head anchors the search,
# the least head that's trustworthy, and how far the seam window reaches to
# either side of a page boundary.
_ANCHOR_CHARS = 200
_MIN_ANCHOR_CHARS = 12
_SEAM_CHARS = 500
_HEAD_GROUNDING_CHARS = 2000  # head slice (title/abstract) kept for grounding
# How much of a passage's window goes BEFORE it: the set-up, definitions and
# heading a passage leans on sit just ahead of it, not after.
_WINDOW_BEFORE_CHARS = 2500
# Pictures of selected regions whose text is unreliable: at most this many
# per message, grown to at least this much of the page (a lone symbol
# needs its line around it), plus a margin.
_MAX_SELECTION_CROPS = 3
_CROP_MIN_W, _CROP_MIN_H = 0.3, 0.05
_CROP_PAD = 0.01
_CROP_MAX_SIDE = 1200


def _normalized_pages(pages: list[str]) -> tuple[list[str], list[str]]:
    """Each page's normalized text plus the page-seam joins (page i glued to
    i+1, so hyphenated line breaks across the boundary re-join), computed once
    so locating several passages doesn't re-normalize the whole document."""
    norm = [normalize_text(p).lower() for p in pages]
    seams = [normalize_text(a[-_SEAM_CHARS:] + "\n" + b[:_SEAM_CHARS]).lower()
             for a, b in zip(pages, pages[1:])]
    return norm, seams


def _raw_offset(raw: str, norm: str, k: int) -> int:
    """A normalized-text offset mapped back into the raw page text —
    proportionally, which is exact enough to cut a window around it."""
    return int(k * len(raw) / max(1, len(norm)))


def _locate_passage(pages: list[str], norm: list[str], seams: list[str],
                    passage: dict) -> tuple[int, int, bool] | None:
    """Where a selected passage starts: ``(1-based page, offset in that
    page's raw text, found)``. The text is matched on normalized text
    (textnorm rules — the same canon the pdf.js text layer and the
    extractors converge to) on the page the viewer reported first, then
    anywhere — so a phrase the paper repeats lands where the user selected
    it. A passage whose text isn't found (a formula's glyph soup) still gets
    the viewer's page, placed by its box (``found`` False). None = nowhere."""
    hint = passage.get("page") or 0
    hint = hint if 1 <= hint <= len(pages) else 0
    needle = normalize_text(passage["text"]).lower()[:_ANCHOR_CHARS]
    if len(needle) >= _MIN_ANCHOR_CHARS:  # shorter is no trustworthy anchor
        def on_page(page_no):
            k = norm[page_no - 1].find(needle)
            return None if k < 0 else (page_no, _raw_offset(pages[page_no - 1], norm[page_no - 1], k), True)

        def on_seam(page_no):
            # A selection starting near the bottom of a page continues onto
            # the next — the seam covers the boundary and credits the page it
            # starts on.
            k = seams[page_no - 1].find(needle) if page_no <= len(seams) else -1
            return None if k < 0 else (page_no, max(0, len(pages[page_no - 1]) - _SEAM_CHARS) + k, True)

        spot = hint and (on_page(hint) or on_seam(hint))
        if spot:
            return spot
        for page_no in range(1, len(pages) + 1):
            spot = on_page(page_no) or (page_no >= 2 and on_seam(page_no - 1))
            if spot:
                return spot
    if hint:
        box = passage.get("box")
        return hint, int(box[1] * len(pages[hint - 1])) if box else 0, False
    return None


def _join_upto(pages: list[str], start: int, limit: int) -> str:
    """pages[start:] joined with blank lines, cut at limit chars — without
    materializing the whole rest of the document just to slice it."""
    parts, total = [], 0
    for page_no, page in enumerate(pages[start:], start + 1):
        page = page_label(page_no) + page
        parts.append(page)
        total += len(page) + 2
        if total >= limit:
            break
    return "\n\n".join(parts)[:limit]


def _span_text(pages: list[str], starts: list[int], g0: int, g1: int) -> str:
    """The characters [g0, g1) of the pages laid end to end (``starts`` =
    each page's first position), each page's part under its ``[PDF page N]``
    label — "continued" when the span enters it mid-page, where the cut is
    moved to the next line start so the window opens on a whole line."""
    parts = []
    for index, page in enumerate(pages):
        start = starts[index]
        if start + len(page) <= g0:
            continue
        if start >= g1:
            break
        a, b = max(0, g0 - start), min(len(page), g1 - start)
        if a:
            line = page.find("\n", a, min(b, a + 200))
            a = line + 1 if line >= 0 else a
        chunk = page[a:b].strip()
        if chunk:
            parts.append(page_label(index + 1, continued=a > 0) + chunk)
    return "\n\n".join(parts)


# Section headings when the PDF has no outline: the numbered, Roman-numeral
# (APS) and lettered shapes, and the named sections papers share. A heading
# line has no comma (reference entries do), and doesn't end in a page number
# (a table of contents) or punctuation (a sentence).
_NUMBERED_HEADING_RE = re.compile(r"(?:\d+(?:\.\d+){0,3}\.?|[IVX]{1,6}\.|[A-H]\.)\s+[A-Z][A-Za-z].*")
_NAMED_HEADING_RE = re.compile(
    r"(?i:abstract|introduction|methods?|results(?: and discussion)?|discussion|"
    r"conclusions?(?: and outlook)?|summary|references|acknowledge?ments?|"
    r"appendix(?:\s+\S.{0,60})?|supplementary (?:information|materials?|methods)(?:\s+\S.{0,40})?)\.?")
# Outline entries that aren't sections (some PDFs bookmark every figure).
_CAPTION_RE = re.compile(r"(?i)(?:fig(?:ure)?|tab(?:le)?|eq(?:uation)?)\.?\s*\S*\d")


def _heading_line(line: str) -> bool:
    line = line.strip()
    if not 3 <= len(line) <= 90 or len(line.split()) > 12:
        return False
    if _NAMED_HEADING_RE.fullmatch(line):
        return True
    return (bool(_NUMBERED_HEADING_RE.fullmatch(line)) and "," not in line
            and not line[-1].isdigit() and not line.endswith((".", ";", ":")))


_HEADING_NUMBER_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVX]{1,6}\.|[A-H]\.)\s+")


def _title_offset(page: str, title: str) -> int | None:
    """Where an outline title stands as a heading line on its page — the
    line itself (numbering aside) or, for a title that wraps, its first
    line — as a raw offset. None when no line reads as it: a bare word
    like "Attention" also occurs in the prose, and the prose doesn't count."""
    want = normalize_text(_HEADING_NUMBER_RE.sub("", title.strip())).lower()
    if not want:
        return None
    offset = 0
    for line in page.splitlines(keepends=True):
        got = normalize_text(_HEADING_NUMBER_RE.sub("", line.strip())).lower()
        if got and (got == want or (len(got) >= 12 and want.startswith(got))):
            return offset
        offset += len(line)
    return None


def _outline_section(toc: list, pages: list[str], page_no: int, offset: int) -> str | None:
    """The outline path ("Results › Noise model") that a spot falls under,
    "" when no entry precedes it, None when the PDF has no usable outline.
    An entry on the spot's own page counts only when its heading line is
    found before the spot; a lone top-level entry is the document's title
    and is left out."""
    entries = [e for e in toc if not _CAPTION_RE.match(e[1])]
    if not entries:
        return None
    top = min(level for level, _, _ in entries)
    if len(entries) > 1 and sum(level == top for level, _, _ in entries) == 1:
        entries = [e for e in entries if e[0] != top]
    path: dict[int, str] = {}
    for level, title, entry_page in entries:
        if entry_page > page_no or entry_page > len(pages):
            continue
        if entry_page == page_no:
            at = _title_offset(pages[page_no - 1], title)
            if at is None or at > offset:
                continue
        path = {lvl: t for lvl, t in path.items() if lvl < level}
        path[level] = title[:80]
    return " › ".join(path[level] for level in sorted(path)[-3:])


def _text_section(pages: list[str], page_no: int, offset: int) -> str:
    """The nearest heading-shaped line before a spot (no outline to ask);
    looks back at most 30 pages."""
    for index in range(page_no - 1, max(-1, page_no - 31), -1):
        text = pages[index][:offset] if index == page_no - 1 else pages[index]
        for line in reversed(text.splitlines()):
            if _heading_line(line):
                return line.strip()
    return ""


# Characters a formula's text layer is made of: Greek, arrows, math
# operators and symbols, math alphanumerics; private-use glyphs and control
# characters are a font the text layer can't decode.
_MATH_CHAR_RE = re.compile("[Ͱ-Ͽ←-⋿⟀-⟯⦀-⫿"
                           "\U0001d400-\U0001d7ff]")
_BROKEN_CHAR_RE = re.compile("[-�\x00-\x08\x0e-\x1f]")


def text_unreliable(text: str) -> bool:
    """Whether a selection's extracted text is too mangled to stand on its
    own — a formula, a table, a symbol font — so the model should see a
    picture of it. Errs toward yes: a needless crop costs a small image, a
    missed one costs the formula."""
    chars = "".join(text.split())
    if not chars or _BROKEN_CHAR_RE.search(text):
        return True
    tokens = text.split()
    singles = sum(1 for t in tokens if len(t) == 1 and t not in "aAI")
    return (len(_MATH_CHAR_RE.findall(chars)) / len(chars) > 0.04
            or (len(tokens) >= 4 and singles / len(tokens) > 0.35))


def selection_context(ws: str, doc_id: str, passages: list[dict],
                      budget: int, with_head: bool = True) -> tuple[str | None, list[dict]]:
    """Chat context for selected passages (``request_selections``): a small
    head slice (title/abstract grounding) plus a window around each passage —
    starting a little before it, where its set-up and definitions are —
    instead of spending the whole budget on the start of the paper, which
    rarely covers what the selection is about. ``with_head=False`` leaves
    the head slice out (the chat sends the head excerpt separately, as the
    document context every turn shares).

    Returns ``(text, located)``: text is None when nothing could be placed
    (the caller falls back to the head-of-document context); located has one
    ``{page, section, found, crop}`` per passage — the page it was placed on
    (0 = nowhere), the outline section (or nearest heading) it falls under,
    whether its text was matched — which labels the passages in the
    question (``final_prompt``) and the reply's context chip."""
    located = [{"page": p["page"], "section": "", "found": False, "crop": False} for p in passages]
    path = pdf_path(ws, doc_id)
    if not path:
        return None, located
    try:
        pages = extract_pages(str(path))
    except Exception as error:
        log.warning(f"[ai_chat] selection-context extraction error: {error}")
        return None, located
    norm, seams = _normalized_pages(pages)
    toc = outline(str(path))
    spots = []
    for where, passage in zip(located, passages):
        spot = _locate_passage(pages, norm, seams, passage)
        if not spot:
            continue
        page_no, offset, found = spot
        section = _outline_section(toc, pages, page_no, offset)
        where.update(page=page_no, found=found,
                     section=section if section is not None else _text_section(pages, page_no, offset))
        spots.append((page_no, offset, where))
    if not spots:
        return None, located
    sections = []
    head = min(_HEAD_GROUNDING_CHARS, budget // 4) if with_head else 0
    starts, total = [], 0
    for page in pages:
        starts.append(total)
        total += len(page) + 2
    share = max(1, (budget - head) // len(spots))
    before = min(_WINDOW_BEFORE_CHARS, share // 3)
    windows = []  # (g0, g1) already shown — a passage inside one shares it
    for page_no, offset, where in sorted(spots, key=lambda s: (s[0], s[1])):
        pos = starts[page_no - 1] + offset
        if any(g0 <= pos < g1 for g0, g1 in windows):
            continue
        # A window reaching into the head slice starts at the top instead
        # (and the separate head slice is dropped below).
        g0 = pos - before if pos - before > head else 0
        window = _span_text(pages, starts, g0, g0 + share)
        if window:
            windows.append((g0, g0 + share))
            place = f"PDF page {page_no}" + (f', section "{where["section"]}"' if where["section"] else "")
            sections.append(f"Text around the selected passage ({place}) — from a little "
                            f"before it:\n{window}")
    if not windows:
        return None, located
    head_text = _join_upto(pages, 0, head).strip() if head and windows[0][0] else ""
    if head_text:
        sections.insert(0, f"Start of the document (for grounding):\n{head_text}")
    return "\n\n".join(sections), located


def _crop_box(box):
    """A selection's box grown to the minimum crop size and padded, still
    inside the page — rounded, because it is saved on the chat message and
    the picture is rendered again from it (``render_selection_crop``)."""
    x0, y0, x1, y1 = box
    grow_w = max(0.0, _CROP_MIN_W - (x1 - x0)) / 2 + _CROP_PAD
    grow_h = max(0.0, _CROP_MIN_H - (y1 - y0)) / 2 + _CROP_PAD
    return tuple(round(v, 4) for v in (max(0.0, x0 - grow_w), max(0.0, y0 - grow_h),
                                        min(1.0, x1 + grow_w), min(1.0, y1 + grow_h)))


def render_selection_crop(path, page: int, box):
    """The picture of a selected region the model is sent: ``box`` is the
    grown crop box (``_crop_box``) as page fractions, top-left origin.
    ``(bytes, media_type, width, height)`` or None. The chat shows the same
    picture again from the saved box (``GET /api/ai/selection-crop``)."""
    image, _ = render_page(str(path), page, _CROP_MAX_SIDE, box=box)
    return image


def selection_crops(ws: str, doc_id: str, passages: list[dict],
                    located: list[dict]) -> list[tuple[str, str]]:
    """Pictures of the selected regions whose text can't be trusted — the
    passage wasn't found in the extracted text, or its text reads as a
    formula (``text_unreliable``) — rendered from the PDF by the page and
    box the viewer reported, as ``(media_type, base64)`` image parts. Marks
    ``crop`` on each passage's located entry so the question says a picture
    is attached, with the ``box`` it was cut from so the chat can show it."""
    images = []
    path = pdf_path(ws, doc_id)
    if not path:
        return images
    for passage, where in zip(passages, located):
        if len(images) >= _MAX_SELECTION_CROPS:
            break
        if not (passage["page"] and passage["box"]):
            continue
        if where["found"] and not text_unreliable(passage["text"]):
            continue
        box = _crop_box(passage["box"])
        image = render_selection_crop(path, passage["page"], box)
        if image:
            images.append(image_part(image))
            where.update(crop=True, box=list(box))
    return images


# Area highlights (Ctrl+drag rectangles: a highlight block whose
# pdf_position carries area: true and no quote) have no text to show the
# model; their region goes as a picture instead — at most this many per
# page per read, the rest named by page number.
MAX_AREA_CROPS = 4
_AREA_PAD = 0.005


def area_highlight(properties: dict) -> tuple[int, tuple] | None:
    """``(pdf page, box)`` of an area highlight — the box as page fractions,
    top-left origin, the shape ``render_page`` crops by — or None for a
    text highlight, for any block that is not a highlight (a text box has
    no quote either but is never one), or for one without a usable
    rectangle. The stored rectangle is in pixels of a capture-time render
    of the position's ``width`` × ``height``."""
    if not is_highlight(properties):
        return None
    position = properties["pdf_position"]
    rect = position.get("boundingRect")
    if not isinstance(rect, dict):
        return None
    if not (position.get("area") or not (properties.get("quote") or "").strip()):
        return None
    try:
        width, height = float(position.get("width") or 0), float(position.get("height") or 0)
        xs = sorted((float(rect["x1"]) / width, float(rect["x2"]) / width))
        ys = sorted((float(rect["y1"]) / height, float(rect["y2"]) / height))
        page = int(position.get("pageNumber") or 0)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    box = (max(0.0, xs[0] - _AREA_PAD), max(0.0, ys[0] - _AREA_PAD),
           min(1.0, xs[1] + _AREA_PAD), min(1.0, ys[1] + _AREA_PAD))
    if page < 1 or box[2] <= box[0] or box[3] <= box[1]:
        return None
    return page, box


def text_box_label(properties: dict, on_sheet: bool = False, above: bool = False) -> str:
    """A text box's block as the model is told of it, with where it is
    placed: "(text box on p. N)", "(text box on a page of paper)" or
    "(text box, not placed on a page)"; "" for any other block. The
    nearest sheet wins: ``on_sheet`` says a sheet is among the block's
    ancestors, as the walk that reached it knows. ``above`` names that
    sheet as "the page of paper above", for an outline that shows it
    (read_block)."""
    if not is_text_box(properties):
        return ""
    page = box_page(properties, on_sheet)
    if page:
        return f"(text box on p. {page})"
    if on_sheet:
        return "(text box on the page of paper above)" if above else "(text box on a page of paper)"
    return "(text box, not placed on a page)"


def under_sheet(conn, block_id: str) -> bool:
    """Whether a sheet of paper is among a block's ancestors: where a walk
    starting at that block (a focused note, ``read_block`` of one block)
    begins. The walks below it pass that on themselves."""
    row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    for _ in range(10000):  # cycle guard
        if not row or row[0] in (None, "root"):
            return False
        row = conn.execute("SELECT parent_id, kind FROM unified_blocks WHERE id = ?", (row[0],)).fetchone()
        if row and row[1] == "sheet":
            return True
    return False


def render_area_crops(ws: str, doc_id: str, areas: list) -> list[tuple[str, str]]:
    """The pictures of area highlights, ``[(media_type, base64)]`` in the
    order given — ``areas`` are ``(page, box)`` pairs, already capped by
    the caller. A region that fails to render is skipped."""
    images = []
    if not areas:
        return images
    path = pdf_path(ws, doc_id)
    if not path:
        return images
    for page, box in areas:
        image = render_selection_crop(path, page, box)
        if image:
            images.append(image_part(image))
    return images


def paths_text(paths: dict, folders: list[str]) -> list[str]:
    """The paths of ``folders`` (ids) as they read, given ``folder_paths``;
    an id that is no folder is left out."""
    return [PATH_SEP.join(paths[f]) for f in folders if f in paths]


def quoted_paths(paths: dict, folders: list[str]) -> str:
    """``paths_text`` as one phrase: each path in quotes (a name may hold a
    comma), "" for none."""
    return ", ".join(f'"{path}"' for path in paths_text(paths, folders))


def page_properties_line(connection, properties: dict) -> str:
    """One line describing what a page carries and how it is filed —
    folders (their paths), labels (their names), cached metadata (authors,
    year, venue, DOI/arXiv), web source, attachment — so the model can tell
    a paper from a note about one. "" when the page has none of it. A filed
    page's folders and labels are read from their trees, one query each."""
    properties = properties or {}
    bits = []
    folders = filing(properties, FOLDERS)
    if folders:
        quoted = quoted_paths(folder_paths(connection), folders)
        if quoted:
            bits.append("folders: " + quoted)
    labels = filing(properties, LABELS)
    if labels:
        names = label_names(connection)
        quoted = [f'"{names[label]}"' for label in labels if label in names]
        if quoted:
            bits.append("labels: " + ", ".join(quoted))
    meta = properties.get("meta") or {}
    if isinstance(meta, dict):
        authors = [str(a).strip() for a in (meta.get("authors") or []) if str(a).strip()]
        if authors:
            shown = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
            bits.append(f"authors: {shown}")
        for key, label in (("year", "year"), ("venue", "venue"), ("doi", "doi"), ("arxiv_id", "arXiv")):
            if meta.get(key):
                bits.append(f"{label}: {str(meta[key])[:120]}")
    if properties.get("web_url"):
        bits.append(f"web source: {str(properties['web_url'])[:200]}")
    attachment = page_attachment(properties)
    if attachment:
        name = f" ({attachment['name']})" if attachment.get("name") else ""
        bits.append(f"attachment: PDF{name}")
    return ("Properties: " + "; ".join(bits)) if bits else ""


def page_report_section(connection, ws: str, page_id: str, pdf_budget: int,
                        pdf_offset: int = 0, pdf_page: int = 1,
                        document_text: str | None = None,
                        include_notes: bool = True, notes_budget: int = 0,
                        report: dict | None = None,
                        notes_seen: dict | None = None) -> str | None:
    """Render one page as context: title, properties, the attachment's text
    (a windowed excerpt of ``pdf_budget`` chars from ``pdf_offset`` /
    ``pdf_page`` — read_page's shape — or ``document_text`` when the caller
    already built it), then the user's highlights and nested notes when
    ``include_notes`` (the chat sends a page's notes with the chat's
    "include my notes" switch, and always for a page without an
    attachment, which IS its notes; read_page shows them once per page). A
    ``notes_budget`` > 0 caps the highlights and notes at that many chars
    (read_page's window) and says how much was left out. ``report``, when
    given, receives ``pdf_pages`` — the ``(first, last)`` PDF pages the
    window spans, (0, 0) for none — ``next_offset``, and ``areas``: the
    ``(page, box)`` of the area highlights shown whose picture the caller
    should attach (the first MAX_AREA_CROPS per page; the lines say
    "picture N attached" for those, and name the page for the rest). None
    when the page doesn't exist. ``notes_seen`` receives ``{block_id:
    text}`` for every note shown in full — what an edit_block replace may
    start from."""
    rows = fetch_subtree(connection, page_id)
    if not rows:
        return None
    by_parent: dict = {}
    root = None
    for row in rows:
        if row[0] == page_id:
            root = row
        else:
            by_parent.setdefault(row[1], []).append(row)
    for children in by_parent.values():
        children.sort(key=lambda row: row[2])

    properties = json.loads(root[4] or "{}")
    attachment = page_attachment(properties)
    doc_id = attachment["id"] if attachment else ""
    highlights: list[str] = []
    notes: list[str] = []
    areas: list = []  # (page, box) of the area highlights whose picture goes along
    per_page: dict = {}

    def walk(block_id, depth, on_sheet=False):
        for row in by_parent.get(block_id, []):
            child_properties = json.loads(row[4] or "{}")
            quote = (child_properties.get("quote") or "").strip()
            content = (row[3] or "").strip()
            area = area_highlight(child_properties)
            shown_as = None
            if quote:
                entry = f'- Highlighted: "{quote}"'
                if content:
                    entry += f"\n  User note: {content}"
                highlights.append(entry)
                shown_as = entry
            elif area:
                page, box = area
                per_page[page] = per_page.get(page, 0) + 1
                if per_page[page] <= MAX_AREA_CROPS:
                    areas.append((page, box))
                    where = f"picture {len(areas)} attached"
                else:
                    where = "no picture: more than the limit on this page"
                entry = f"- Area highlight (a rectangle on PDF page {page}; {where})"
                if content:
                    entry += f"\n  User note: {content}"
                highlights.append(entry)
                shown_as = entry
            elif content:
                label = text_box_label(child_properties, on_sheet)
                shown_as = "  " * depth + "- " + (f"{label} {content}" if label else content)
                notes.append(shown_as)
            if shown_as and (quote or content):
                seen_rows.append((row[0], row[3] or "", shown_as))
            walk(row[0], depth + 1, on_sheet or is_sheet(child_properties))

    seen_rows: list = []  # (block id, its stored text, the lines that show it)

    if include_notes:
        walk(page_id, 0)
    sections = [f"### {root[3] or 'Untitled'}"]
    sections.append(f"Gamma page ID: {page_id}")
    props_line = page_properties_line(connection, properties)
    if props_line:
        sections.append(props_line)
    if properties.get("summary"):
        sections.append(f"Summary: {properties['summary']}")
    if report is not None:
        report.update(pdf_pages=(0, 0), next_offset=None, areas=areas)
    if document_text is not None:
        if document_text:
            sections.append(f"Document text:\n{document_text}")
    elif doc_id and pdf_budget > 0:
        excerpt, next_offset, seen, span = pdf_excerpt(ws, doc_id, pdf_budget, pdf_offset, pdf_page,
                                                       with_pages=True)
        if report is not None:
            report.update(pdf_pages=span, next_offset=next_offset)
        at_page = f"pdf_page={pdf_page}, " if pdf_page > 1 else ""
        if excerpt:
            where = ([f"from PDF page {pdf_page}"] if pdf_page > 1 else []) + \
                    ([f"from char {pdf_offset}"] if pdf_offset else [])
            label = (f"Document text ({', '.join(where)}):" if where
                     else "Document text (excerpt):")
            if next_offset:
                excerpt += ("\n…[more text remains — call read_page again with "
                            f"{at_page}pdf_offset={next_offset} to continue]")
            sections.append(f"{label}\n{excerpt}")
        elif pdf_offset and seen:
            source = (f"the text from PDF page {pdf_page} on" if pdf_page > 1
                      else "the extracted text")
            sections.append(f"Document text: pdf_offset {pdf_offset} is past the "
                            f"end — {source} is ~{seen} chars long.")
        elif pdf_page > 1:
            sections.append(f"Document text: no text at or after PDF page {pdf_page}.")
    shown = []
    if highlights:
        shown.append("User's highlighted passages:\n" + "\n".join(highlights))
    if notes:
        shown.append("User's notes:\n" + "\n".join(notes))
    elif include_notes and not doc_id and not highlights:
        shown.append("User's notes: (this page has no notes yet)")
    text = "\n\n".join(shown)
    if notes_budget > 0 and len(text) > notes_budget:
        text = (text[:notes_budget] + f"\n… (+{len(text) - notes_budget:,} more chars of notes not "
                "shown — read_block(block_id=<page id>) lists them with ids)")
    if notes_seen is not None:
        # Only a note shown whole can be the base of a replace: the budget
        # above may cut one off, or leave it out.
        for block_id, stored, shown_as in seen_rows:
            if shown_as in text:
                notes_seen[block_id] = stored
    if text:
        sections.append(text)
    return "\n\n".join(sections)


def gather_inputs(ws: str, payload, allow_native: bool,
                  crops: list | None = None,
                  notes_seen: dict | None = None) -> tuple[list[str], str, list[dict], str]:
    """Collect the chat's context: native PDF attachments and the text
    sections for the request's pages.

    The pages come from ``payload.pages`` (several — a report across pages,
    each getting an even share of ``multi_context_char_limit``) or, when that
    is empty, the one page of ``payload.page_id`` with the full
    ``context_char_limit`` (``payload.doc_id`` alone is the compatibility
    input: it resolves to the page carrying that PDF, and a doc no page
    carries contributes nothing). Every page contributes its
    title, properties and notes; a page with a PDF attachment adds the
    document's text (or the file itself when ``attach_pdf`` and the provider
    takes it) and hides the notes unless ``include_notes`` — a page without
    one IS its notes, so they always go.

    Returns ``(pdf_b64s, context, coverage, message_context)``. ``context``
    is the document part — the same text on every turn of a conversation
    while its pages and settings stand (the head excerpt, the notes, the
    map); ``message_context`` is what belongs to this message alone: the
    text around the passages it selected and the notes the user is
    pointing at (build_messages puts it in front of the question).

    ``coverage`` is the report the chat streams back as its first
    `{"context": [...]}` line — one entry per page: ``{"title", "page_id",
    "doc_id" ("" for a page without a PDF), "native" (the file itself went),
    "native_requested" (the user asked for that; requested but not native =
    the provider refused it and text went instead), "partial", "chars",
    "pages", "pages_shown", "notes" (the page's notes are in the context)}``
    — so the UI can say "the model saw pages 1–9 of 22" instead of leaving
    the user to guess, and the agent prompt and read_page can name the pages
    already in context. The open paper's entry also carries ``"selection":
    {"passages": [...]}`` when the message selected passages in it
    (``selection_context``'s located entries).

    ``crops``, when given, receives pictures of selected regions whose text
    is unreliable (``selection_crops``) for the caller to send as images.
    ``notes_seen``, when given, receives ``{block_id: text}`` for every note
    block the context shows in full (``page_report_section``,
    ``notes_focus_section``) — the texts an agent's edit_block replace may
    start from this turn."""
    pdf_b64s = []
    context_sections = []
    message_sections = []
    coverage = []
    attach = payload.attach_pdf and allow_native
    none = {"partial": False, "chars": 0, "pages": 0, "pages_shown": 0}

    def report(title, page_id, doc_id, native, cover=None, notes=False):
        coverage.append({"title": title, "page_id": page_id, "doc_id": doc_id, "native": native,
                         "native_requested": bool(payload.attach_pdf), "notes": notes,
                         **(cover or none)})

    page_ids = list(payload.pages or [])
    single = not page_ids
    with connect_pages_db(ws) as connection:
        if single:
            page_id = str(getattr(payload, "page_id", "") or "")
            if not page_id or not connection.execute(
                    "SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                    (page_id,)).fetchone():
                row = page_for_doc(connection, payload.doc_id)
                page_id = row[0] if row else ""
            page_ids = [page_id] if page_id else []
        pages = []
        for page_id in page_ids:
            row = connection.execute(
                "SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,),
            ).fetchone()
            if row:
                pages.append((page_id, row))
        # Missing/deleted references must not consume the other papers' budget.
        text_budget = (payload.context_char_limit if single
                       else max(1, payload.multi_context_char_limit // max(1, len(pages))))
        total_b64 = 0
        for page_id, row in pages:
            title = row[0] or "Untitled"
            properties = json.loads(row[1] or "{}")
            attachment = page_attachment(properties)
            doc_id = attachment["id"] if attachment else ""
            document_text = ""
            attached = False
            cover = dict(none)
            if doc_id and attach:
                data = load_pdf_b64(ws, doc_id)
                if data and total_b64 + len(data) < 20_000_000:
                    pdf_b64s.append(data)
                    total_b64 += len(data)
                    attached = True
            if doc_id:
                # Index the paper (background) so the map and search_library
                # exist for the next turn — the first chat on a fresh paper
                # otherwise runs without them for as long as the model never
                # calls search.
                ensure_indexed(ws, doc_id)
            with_notes = bool(payload.include_notes) or not doc_id
            if doc_id and attached:
                report(title, page_id, doc_id, True, notes=with_notes)
            elif doc_id:
                # The head excerpt is the document part: the same text on
                # every turn, whatever this message selects.
                document_text, cover = head_context(ws, doc_id, limit=text_budget)
                # The excerpt holds the whole document (nothing to add or
                # map) — never taken for granted from the failure sentinel.
                whole = (not cover["partial"] and bool(document_text)
                         and document_text != PDF_EXTRACT_FAILED)
                passages = (request_selections(payload)
                            if single or page_id == payload.page_id else [])
                selected = {}
                if passages:
                    # A selection adds windows around the passages to THIS
                    # message — only when the excerpt doesn't already hold
                    # the whole document; the passages are located either
                    # way (their page and section label the question).
                    windows, located = selection_context(
                        ws, doc_id, passages, min(text_budget, SELECTION_WINDOW_CHARS * len(passages)),
                        with_head=False)
                    if crops is not None:
                        crops.extend(selection_crops(ws, doc_id, passages, located))
                    selected = {"selection": {"passages": located}}
                    if windows and not whole:
                        message_sections.append(
                            f'Text around the selected passage{"s" if len(passages) > 1 else ""} of '
                            f'"{title}" (Gamma page ID: {page_id}):\n{windows}')
                report(title, page_id, doc_id, False, {**cover, **selected}, notes=with_notes)
            shown: dict = {}
            section = page_report_section(connection, ws, page_id, 0,
                                          document_text=document_text or "",
                                          include_notes=with_notes, report=shown,
                                          notes_seen=notes_seen)
            if section:
                context_sections.append(section)
                if not doc_id:
                    report(title, page_id, "", False, {**none, "chars": len(section)}, notes=True)
                # The area highlights' pictures ride with the message like
                # the selection crops (the wires put images on the last
                # user turn); the report says how many went.
                if doc_id and shown.get("areas") and crops is not None:
                    pictures = render_area_crops(ws, doc_id, shown["areas"])
                    crops.extend(pictures)
                    coverage[-1]["area_pictures"] = len(pictures)
            # Only for a chat with tools: the map is worth its tokens when
            # the model can act on it (read_page), not in plain chat — and
            # only for the pages the excerpt doesn't show in full.
            if doc_id and getattr(payload, "agent_scope", "") in ("page", "folder") and not (
                    not attached and whole):
                from_page = cover["pages_shown"] if cover["partial"] and cover["pages_shown"] else 1
                outline = document_map(ws, doc_id, from_page=from_page)
                if outline:
                    context_sections.append(f"Document map for Gamma page ID: {page_id}\n{outline}")

    names = [str(f.get("name") or "") for f in (payload.files or []) if isinstance(f, dict)]
    for index, data in enumerate(parse_files(payload.files)):
        title = (names[index] if index < len(names) else "") or f"Attached PDF {index + 1}"
        if allow_native:
            pdf_b64s.append(data)
            coverage.append({"title": title, "page_id": "", "doc_id": "", "native": True,
                             "native_requested": True, "notes": False, **none})
        else:
            # Uploaded files get the single-paper budget, like the open paper.
            text, cover = pdf_text_cover_from_b64(data, payload.context_char_limit)
            coverage.append({"title": title, "page_id": "", "doc_id": "", "native": False,
                             "native_requested": True, "notes": False, **cover})
            if text:
                context_sections.append(f"### {title}\n{text}")

    # Where the user is pointing inside the notes (cursor block, attached
    # block chips) belongs to this message, right before the question.
    focus_section = notes_focus_section(ws, payload, notes_seen=notes_seen, crops=crops)
    if focus_section:
        message_sections.append(focus_section)

    return (pdf_b64s, "\n\n---\n\n".join(context_sections), coverage,
            "\n\n---\n\n".join(message_sections))
