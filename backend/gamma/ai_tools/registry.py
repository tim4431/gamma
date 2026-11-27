"""The tool registry and its dispatch. ``TOOLS`` joins the families'
entries in the order the model and an MCP client list them; each declares
its wire spec, the Settings permission key (Settings → AI → Tool usage →
Tools), the scopes it exists in, whether it mutates, its executor and, for
a changing tool, the preview of its approval card. Arming a chat is one
filter (``agent_tools``) and dispatch one lookup (``run_agent_tool``), with
the in-scope check shared by every executor. A permission is Allow, Ask or
Off (``ai_permissions.py``): Ask tools are armed, and the chat's tool loop
shows ``approval_preview`` to the user before such a call runs. Renamed
tools stay callable under their old name (``ai_context.DEPRECATED_TOOLS``)
so saved chats replay; every successful call is streamed to the UI as an
``action`` event (``tool_action``)."""

import json
import time

from .. import agent_changes
from ..ai_permissions import permission_state
from ..ai_context import DEPRECATED_TOOLS, canonical_tool
from ..db import connect_pages_db
from ..jobs import error_text
from ..logbuf import log
from . import annotations, commenting, drawing, folders, library, notes, organizing, pages, reading, skills, viewing, web
from .base import READ_CHARS_DEFAULT, _ARG_CAP, _DETAIL_CAP, _read_cap, ensure_tally, notes_seen

# One entry per tool, each family's beside its executors: wire spec, Settings
# permission key, the action kind its chip carries, the scopes the tool
# exists in, whether it mutates the library, its executor and, for a tool
# that changes something, the preview its approval card shows
# (approval_preview). The order is the one the model and an MCP client list.
TOOLS = [*reading.TOOLS, *annotations.TOOLS, *viewing.TOOLS, *library.TOOLS, *web.TOOLS, *organizing.TOOLS,
         *folders.TOOLS, *notes.TOOLS, *commenting.TOOLS, *drawing.TOOLS, *pages.TOOLS, *skills.TOOLS]
_BY_NAME = {t["spec"]["name"]: t for t in TOOLS}
# Deprecated names stay dispatchable (never offered) so a model copying an
# old name out of replayed history is still served, not refused.
for _old, _new in DEPRECATED_TOOLS.items():
    if _new in _BY_NAME:
        _BY_NAME[_old] = _BY_NAME[_new]
MUTATING_TOOLS = {t["spec"]["name"] for t in TOOLS if t["mutating"]}
# The writing tools: what an agent writes as itself (a comment, a resolve, a
# highlight), offered where the account may comment (``can_comment``), not
# only where it edits, and over MCP at the Comment level (gamma/mcp_server.py).
WRITING_TOOLS = frozenset(t["spec"]["name"] for t in TOOLS if t.get("commenting"))
# The tools that only store a file for a note to show (clip_region's
# picture), changing no note: they ask nobody, in the chat (ApprovalGate)
# and over MCP, since the note write that shows the file asks with it in
# view.
STORING_TOOLS = frozenset(t["spec"]["name"] for t in TOOLS if t.get("stores"))
# The tools whose answer is a picture: a model that reads text only gets
# neither (routers/ai.py, _chat_tools).
PICTURE_TOOLS = frozenset({"view_pdf_page", "view_ink", "view_image"})
# The tools whose answers are read against today's date (a result "3 days
# ago", a page published last week, "the latest release"): a chat armed with
# one is told it on its oldest turn (ai_context.build_messages' date_line),
# never in agent_system.
DATED_TOOLS = frozenset({"search_web", "fetch_url"})


def mcp_tools() -> frozenset:
    """The registry's tools the read-only MCP adapter offers
    (gamma/mcp_server.py): every reading tool that stays inside the library,
    so a new one is offered by rule — not the web tools (the assistant has
    its own web access), not the trash (``list_deleted``: its pages are out
    of the reading tools' reach and restoring one is a change), no tool
    that ``needs`` what only Gamma's own chat has (a web engine, a helper,
    the app it shows pages and files in), and no change tool.
    ``backend/tests/test_mcp.py`` pins the resulting names."""
    return frozenset(t["spec"]["name"] for t in TOOLS
                     if not t["mutating"] and t["perm"] not in ("web_search", "web_read") and not t.get("needs")
                     and t["spec"]["name"] != "list_deleted")


def available(scope: dict) -> frozenset:
    """What this chat can offer beyond its permissions: the ``needs`` of a
    TOOLS entry it meets — a web engine for search_web, a second agent for
    read_paper (only a live chat on a provider connection has one), the app
    for show_page and export (a streamed chat whose client shows pages and
    downloads files: ``app``, routers/ai.py ``_chat_scope``), and a skill
    the assistant may load on its own for load_skill (``skills``, the
    request's catalog)."""
    return frozenset({"web_engine"} if scope.get("web_engine") else ()) | frozenset(
        {"helper"} if scope.get("delegates") else ()) | frozenset({"app"} if scope.get("app") else ()) | frozenset(
        {"skills"} if any(s.get("model") for s in scope.get("skills") or ()) else ())


def replayed(name: str) -> dict | None:
    """The TOOLS entry of a tool whose calls render their own result again
    on a later request (``replay(catalog, action) -> str``: load_skill,
    whose saved action keeps only a stub), else None. ai_context replays
    them outside the replay budget and keeps their results within a reply
    (``elide_live_results``), except on the emergency retry, which leaves
    the entry's ``elided`` stub."""
    tool = _BY_NAME.get(canonical_tool(name or ""))
    return tool if tool and tool.get("replay") else None


def tool_permission(name: str) -> str:
    """The permission a tool (or a deprecated name of it) belongs to; "" when
    there is no such tool."""
    tool = _BY_NAME.get(canonical_tool(name))
    return tool["perm"] if tool else ""


def _state_of(perms, tool: dict) -> str:
    """A tool's state in a request's permission map: its permission's value,
    else the default for a reading or a changing tool."""
    return permission_state(perms, tool["perm"], tool["mutating"])


def tool_states(perms, *, granted=(), can_ask: bool = True) -> dict:
    """``{permission: "allow" | "ask" | "off"}`` for every permission of the
    registry, read from a request's map (ai_permissions.permission_state:
    a permission left out allows reading and asks before a change). The
    permissions in ``granted`` (the conversation's "Allow in this chat") are
    allowed, and a request that cannot ask (``can_ask`` false: no stream to
    show the card on) arms none of its asking tools."""
    states = {}
    for t in TOOLS:
        state = _state_of(perms, t)
        if state == "ask" and t["perm"] in granted:
            state = "allow"
        if state == "ask" and not can_ask:
            state = "off"
        states[t["perm"]] = state
    return states


def agent_tools(scope_type: str, perms: dict | None = None, read_chars: int = 0,
                *, allowed_tools=None, can_write: bool = True, can_comment: bool = True, has: frozenset = frozenset(),
                read_default: int = 0) -> list:
    """The armed tool specs for a chat scope and the user's permission map
    (``tool_states``, or a request's raw map read the same way): every tool
    that is not off, the asking ones included, since the chat asks the user
    before they run. [] = plain chat.
    read_chars is the request's read-window preference — the specs that name
    the cap are formatted with the effective value so the model knows what it
    may ask for (the registry's stored specs are never mutated), and with
    the window a call gets without asking (``read_default``, the scope's
    own when it sets one). A changing tool is armed only where the scope
    may write (``can_write``), a writing tool (``WRITING_TOOLS``) where it
    may comment (``can_comment``: a viewer's chat too). A tool
    that ``needs`` something (a web engine) is armed only when ``has`` it
    (``available(scope)``)."""
    cap = _read_cap(read_chars)
    specs = []
    for t in TOOLS:
        if scope_type not in t["scopes"] or _state_of(perms, t) == "off":
            continue
        if t.get("needs") and t["needs"] not in has:
            continue
        if allowed_tools is not None and t["spec"]["name"] not in allowed_tools:
            continue
        if t["mutating"] and not (can_comment if t.get("commenting") else can_write):
            continue
        spec = t["spec"]
        if "{read_cap}" in spec.get("description", ""):
            spec = {**spec, "description": spec["description"].format(
                read_cap=cap, read_default=min(read_default or READ_CHARS_DEFAULT, cap))}
        specs.append(spec)
    return specs


def tool_action(kind: str, summary: str, name: str, args: dict, result: str,
                error: bool = False, **extra) -> dict:
    """Build the UI event for one tool call: the chip's icon/summary plus the
    raw call the chat can expand — the arguments and the output the model got,
    both truncated (chips are streamed AND saved with the message)."""
    trimmed = {}
    for key, value in (args or {}).items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        trimmed[key] = text if len(text) <= _ARG_CAP else text[:_ARG_CAP] + "…"
    out = {"kind": kind, "summary": summary, "tool": name, "args": trimmed,
           "result": result if len(result) <= _DETAIL_CAP
                     else result[:_DETAIL_CAP]
                          + f"\n… (+{len(result) - _DETAIL_CAP} more chars not kept "
                            "in chat history — call the tool again if they're needed)"}
    if error:
        out["error"] = True
    return {**out, **extra}


def settled_action(name: str, args: dict, result: str) -> dict:
    """The chip of a call that ended without an action of its executor's:
    a failure (``error``), or a change tool's call that had nothing to
    change (``noop``, so no list counts it as a change)."""
    tool = _BY_NAME.get(canonical_tool(name))
    failed = result.startswith("error")
    return tool_action("error" if failed or not tool else tool["kind"], result.split("\n")[0][:200],
                       canonical_tool(name), args, result, error=failed,
                       **({"noop": True} if tool and tool["mutating"] and not failed else {}))


def approval_preview(ws: str, scope: dict, name: str, args: dict) -> tuple:
    """What the approval card shows for a call the user must approve first:
    ``(preview, None)``, or ``(None, answer)`` when the call cannot change
    anything (a wrong id, a page out of scope, nothing to do): the model
    gets that answer and the user is not asked. A tool without a preview of
    its own (a reading tool the user set to ask) previews as ``{}``, and the
    card names its arguments. Nothing is changed here."""
    tool = _BY_NAME.get(canonical_tool(name))
    if not tool or not tool.get("preview"):
        return {}, None
    args = args if isinstance(args, dict) else {}
    # The scope run_agent_tool gives a change: the attached pages in it, a page's inside not.
    scope = {**scope, "ws": ws}
    try:
        with connect_pages_db(ws) as conn:
            return tool["preview"](conn, scope, args)
    except Exception as e:  # the chat stream goes on, and the call is not made
        log.warning(f"[ai_tools] {name} preview failed: {e}")
        return None, f"error: {e}"


def _deleting(ws: str) -> bool:
    from ..workspaces import is_deleting  # local: workspaces imports seed, which imports db

    return is_deleting(ws)


def run_agent_tool(ws: str, scope: dict, name: str, args: dict,
                   *, allowed_tools=None, approved: str = "", asked: bool = False) -> tuple[str, dict]:
    """Execute one tool call against a trusted, caller-resolved workspace/scope.

    Chat and MCP share this dispatcher; each passes the tool names it armed
    (`allowed_tools`, None = every tool of the scope). The caller
    authenticates the workspace before invoking this layer. ``approved`` is
    the digest of the approval card the user allowed for this call
    (``ApprovalGate``): a call naming several pages runs only as that card
    showed it. ``asked``: the person allowed this very call before it ran
    (a card, a client's question).

    A change made where the scope names its ``journal`` (``{source,
    token}``: the chat's, an MCP connection's) is journaled for the
    person's review (gamma/agent_changes.py), kept already when ``asked``,
    and its action carries the ``change_id``.

    Returns ``(result_text, action)`` — result_text goes back to the model;
    action is the ``{kind, summary, tool, args, result}`` UI event for EVERY
    call (reads and failures included), so nothing the agent does is invisible.
    Failures carry ``error: True``; the executors' own actions are enriched
    with the same raw-call fields. A deprecated name runs its current tool
    and the action carries the current name. A tool that answers with
    pictures (view_pdf_page) puts them on the action as ``images``
    (``[(media_type, base64)]``): the caller moves them onto the model's tool
    result and must drop them before the chip is streamed or saved.
    """
    name = canonical_tool(name)
    tool = _BY_NAME.get(name)
    args = args if isinstance(args, dict) else {}
    if not tool or (scope.get("type") or "") not in tool["scopes"]:
        result = f"error: unknown tool {name}"
        return result, tool_action("error", result[:200], name, args, result, error=True)
    if tool["mutating"] and not scope.get("can_comment" if tool.get("commenting") else "can_write", True):
        result = ("error: you cannot comment in this workspace" if tool.get("commenting")
                  else "error: you can only view this workspace — no changes are possible")
        return result, tool_action("error", result[:200], name, args, result, error=True)
    if tool["mutating"] and _deleting(ws):
        # a turn that began before the workspace's delete: its tools were
        # armed then, its changes would be in neither the final copy nor
        # anywhere else (workspaces.refuse_writes)
        result = "error: this workspace is being deleted — no changes are possible"
        return result, tool_action("error", result[:200], name, args, result, error=True)
    permitted = {s["name"] for s in agent_tools(scope.get("type") or "", allowed_tools=allowed_tools,
                                                has=available(scope))}
    if name not in permitted:
        result = "error: tool not enabled — the user's permission settings do not allow it"
        return result, tool_action("error", f"{name} — blocked by permissions", name, args, result, error=True)
    # A page the user attached (@) is in reach for every tool, changes
    # included. A page's inside widens reading alone: a page chat reads its
    # sub-pages and edits only its page and the attached ones.
    # The message's counters (saves, web searches, the works already listed)
    # live in one Tally the copy shares.
    ensure_tally(scope)
    notes_seen(scope)  # made on the caller's scope, so the reads of one turn carry over to its edits
    # The planners check stored files against the workspace (missing_uploads).
    scope = {**scope, "ws": ws, "approved": approved, "inside": not tool["mutating"]}
    started = time.monotonic()
    try:
        with connect_pages_db(ws) as conn:
            result, action = tool["run"](conn, ws, scope, args)
    except Exception as e:  # a tool failure must never kill the chat stream
        log.warning(f"[ai_tools] {name} failed: {e}")
        result, action = f"error: {error_text(e)}", None
    # How long the call took, for its chip: a fetch that waited 20 s on a
    # publisher and one served from the cache read very differently.
    took = {"ms": int((time.monotonic() - started) * 1000)}
    if action is None:
        # No-op or refused call (empty title, page out of scope, …): still
        # show it (settled_action).
        return result, {**settled_action(name, args, result), **took}
    images = action.pop("images", None)
    # An executor may keep another copy of its result on the chip than the
    # model got (``result`` on its action): load_skill saves a stub, never
    # the instructions, into a conversation others may read.
    saved = action.pop("result", result)
    chip = tool_action(action["kind"], action["summary"], name, args, saved,
                       error=bool(action.get("error")),
                       **{k: v for k, v in action.items()
                          if k not in ("kind", "summary", "error")}, **took)
    if images:
        chip["images"] = images
    journal = scope.get("journal")
    if journal and tool["mutating"] and not chip.get("error") and not chip.get("noop"):
        try:
            change_id = agent_changes.record(ws, account=scope.get("actor") or "", source=journal["source"],
                                             token_id=journal.get("token") or "", agent=scope.get("agent"),
                                             tool=name, action=chip, kept=asked)
        except Exception as e:  # the change is made: a journal that fails only loses its review
            log.warning(f"[ai_tools] {name}: the change could not be journaled: {e}")
            change_id = ""
        if change_id:
            chip["change_id"] = change_id
    return result, chip
