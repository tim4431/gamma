"""The agent's tool loop: rounds of provider turns whose tool calls the
server runs, until the model answers.

One round streams one provider turn. Its tool calls are executed here, their
results are appended to the conversation, and the provider is reopened with
them — until a turn calls nothing. The loop yields what a caller wants to
show or save::

    ("delta", str)        assistant text as it streams
    ("step", dict)        a call (or a batch of them) about to run
    ("approval", dict)    a card asking the user to allow a call
    ("handoff", dict)     a card asking the user's browser for a blocked PDF
    ("action", dict)      a call that ran (reads and failures included)
    ("progress", dict)    a note edit the model is still writing
    ("reasoning", dict)   one provider turn's thinking, as text ({wire field: text})
    ("usage", dict)       one provider turn's token counts
    ("truncated", True)   the turn hit the provider's output cap

Two callers drive it: the AI chat streams these to the browser
(``routers/ai.py``), and a :class:`Helper` runs it inside one of the chat's
calls, for one document. What differs between them is wired in, not
branched on:

- ``gate`` decides whether a call may run at all. The chat passes
  :class:`ApprovalGate`, so a permission set to Ask stops on a card first.
- ``settle`` gets the calls of a round once they ran and may replace their
  results. The chat passes :class:`PaperWait`, so a fetch a publisher
  blocked waits for the PDF from the user's own browser inside the same
  turn.

A helper passes neither: it has no card to show, so a blocked fetch reports
what stopped it and the chat's own wait takes over once the helper answered.

Reads of one round run at the same time (:data:`MAX_PARALLEL_CALLS`), since
four papers fetched one after another cost four round trips of waiting.
Anything that changes the library, and anything that may stop on a card,
runs on its own in call order.
"""

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import ai_permissions
from .ai_client import UpstreamError, failure_kind, partial_json_object
from .ai_context import canonical_tool, elide_live_results
from .ai_protocols.base import reasoning_text, truncated_stop
from .ai_tools import (MAX_TOOL_ACTIONS, MAX_TOOL_ROUNDS, MUTATING_TOOLS, approval_preview,
                       ensure_tally, find_selection, run_agent_tool, settled_action, skipped_fetch,
                       tool_action, tool_permission, unanswered_fetch)
from .logbuf import log

# Tool calls of one round that run at the same time. Reads are network- or
# disk-bound, so a handful at once is the whole win; more would hammer the
# registries and publishers of one message.
MAX_PARALLEL_CALLS = 4

# Provider round-trips a Helper may use on the one document it was given.
HELPER_ROUNDS = 12
# The document's name in a Helper's status: a title, cut to one line.
STATUS_LABEL_MAX = 120

# Note edits are previewed in the notes panel while the model writes them
# (the "progress" event). No other tool's arguments are streamed.
PREVIEW_TOOLS = frozenset({"edit_block", "create_block"})

# The short arguments a step event repeats, for the label the chat shows
# while a call runs ("Searching library for …"). Never a note's content.
STEP_ARGS = ("page_id", "block_id", "query", "title", "folder", "label", "source",
             "pdf_page", "mode", "question")

# What the model hears for a call the user did not allow (with what they want
# done instead, when they said it), or did not answer in time.
DECLINED = ("declined: the user did not allow this call, so nothing was changed. Do not "
            "repeat it or make the same change another way; carry on without it, and say "
            "what you would have changed.")
DECLINED_SAYING = ('declined: the user did not allow this call, so nothing was changed, and '
                   'told you what to do instead: "{note}". Do that; do not repeat the declined call.')
UNANSWERED = ("declined: the user did not answer the approval request in time, so nothing "
              "was changed. Do not repeat it; finish your reply and say what you would have "
              "changed.")


def step_event(name: str, call: dict) -> dict:
    """The ``{"step"}`` payload of one call: its tool and the short arguments
    the running label reads."""
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    return {"id": call.get("id") or "", "tool": name,
            "args": {k: str(args[k])[:120] for k in STEP_ARGS if args.get(k) not in (None, "")}}


def batch_step(items: list) -> dict:
    """The ``{"step"}`` payload of a batch running at the same time: the one
    tool when they share it ("Fetching 4 documents…"), else how many."""
    first = step_event(items[0].name, items[0].call)
    names = {item.name for item in items}
    if len(names) == 1:
        return {**first, "batch": len(items)}
    return {"tool": "", "args": {}, "batch": len(items), "tools": sorted(names)}


@dataclass
class Conversation:
    """The turns one reply grows: what goes to the provider every round."""

    messages: list
    system: str = ""
    files: list = field(default_factory=list)  # native PDF parts (pdf_b64s)


@dataclass
class ToolCall:
    """One call of a round: what the model asked, and what it produced."""

    name: str
    call: dict                                 # {id, name, arguments}
    result: str = ""
    action: dict = field(default_factory=dict)

    @property
    def args(self) -> dict:
        return self.call.get("arguments") or {}

    @property
    def id(self) -> str:
        return self.call.get("id") or ""

    def replace(self, result: str, **fields) -> None:
        """Swap what this call amounts to after the fact (``settle``): the
        model's result and the chip's copy of it stay the same text."""
        self.result = result
        self.action = {**self.action, "result": result, **fields}


class AgentLoop:
    """Run the tool loop over ``conversation`` until the model answers.

    ``open_round(conversation)`` opens one provider turn (the caller owns the
    connection, the model and the window fitting) and ``read_events(resp)``
    parses its SSE into the ``("text" | "tool" | "tool_delta" | "reasoning" |
    "usage" | "stop", data)`` events every wire speaks. ``on_usage`` is
    called with each turn's counts before they are yielded, so a caller that
    meters tokens sees them even if it ignores the event. ``pictures`` False
    (a model that reads text only) leaves out the pictures tool results
    carry.
    """

    def __init__(self, *, ws: str, scope: dict, tools: list, conversation: Conversation,
                 open_round, read_events, on_usage=None, max_rounds: int = 0,
                 gate=None, settle=None, pictures: bool = True):
        self.ws = ws
        self.scope = scope
        self.tools = tools
        self.conversation = conversation
        self.open_round = open_round
        self.read_events = read_events
        self.on_usage = on_usage
        self.max_rounds = max_rounds or MAX_TOOL_ROUNDS
        self.gate = gate
        self.settle = settle
        self.pictures = pictures
        self.armed = {t["name"] for t in tools}
        self.mutations = 0
        # Made before anything can run in parallel, so a batch never races
        # to create the message's counters.
        ensure_tally(scope)

    # --- the loop ---------------------------------------------------------

    def run(self, first_resp):
        """Yield the reply's events. ``first_resp`` is the already-opened
        first provider turn (the caller opens it eagerly so a connection or
        auth failure is a plain HTTP error, not a dead stream)."""
        resp = first_resp
        for round_no in range(self.max_rounds):
            calls = yield from self._round(resp)
            if calls is None:
                return                                  # truncated, or the client left
            if not calls:
                return                                  # the model answered
            if round_no == self.max_rounds - 1:
                yield ("delta", "\n\n*(stopped: tool-round limit reached — "
                                "raise it in Settings → AI → Tool usage → Tool limits)*")
                return
            resp = self._reopen()

    def _round(self, resp):
        """Stream one provider turn, then run its tool calls. Returns the
        calls it ran ([] when the model answered, None to stop)."""
        calls, text_parts = [], []
        previewed = {}   # call id -> content previewed so far (dedup)
        stop = ""
        thinking = None  # what the wire reported of the model's thinking
        try:
            for kind, data in self.read_events(resp):
                if kind == "text":
                    text_parts.append(data)
                    yield ("delta", data)
                elif kind == "stop":
                    stop = data
                elif kind == "tool":
                    calls.append(data)
                elif kind == "tool_delta":
                    progress = self._preview(data, previewed)
                    if progress:
                        yield ("progress", progress)
                elif kind == "reasoning":
                    # Kept whole on the turn the next round replays; its
                    # text goes up for the reply to save and send back.
                    thinking = data
                    said = reasoning_text(data)
                    if said:
                        yield ("reasoning", said)
                elif kind == "usage":
                    if self.on_usage:
                        self.on_usage(data)
                    yield ("usage", data)
        finally:
            resp.close()
        if truncated_stop(stop):
            # The output cap ended the round: say so rather than pass a
            # cut-off reply (or a half-written tool call) as finished.
            yield ("truncated", True)
            return None
        if not calls:
            return []
        self.conversation.messages.append(
            {"role": "assistant", "content": "".join(text_parts), "tool_calls": calls,
             **({"reasoning": thinking} if thinking else {})})
        for group in self._groups(calls):
            if (yield from self._run_group(group)) is None:
                return None
        return calls

    def _reopen(self):
        """Reopen the provider with this round's results. The valve on a
        long reply: once the rounds' results outgrow their budget the oldest
        become stubs, and a provider that still calls the prompt too long
        gets one retry with only the last round's results."""
        elide_live_results(self.conversation.messages)
        try:
            return self.open_round(self.conversation)
        except UpstreamError as e:
            if failure_kind(e) != "too_long":
                raise
            elide_live_results(self.conversation.messages, keep_rounds=1, budget=0)
            log.info("[ai_agent] tool round too long for the provider, retrying with earlier results elided")
            return self.open_round(self.conversation)

    # --- running a round's calls ------------------------------------------

    def _groups(self, calls: list) -> list:
        """The round's calls in the order they run, batched: a maximal run of
        reads that may go at the same time is one batch, everything else is
        a batch of one in call order."""
        groups, batch = [], []
        for call in calls:
            item = ToolCall(canonical_tool(call["name"]), call)
            if self._concurrent(item.name) and len(batch) < MAX_PARALLEL_CALLS:
                batch.append(item)
                continue
            if batch:
                groups.append(batch)
                batch = []
            if self._concurrent(item.name):
                batch = [item]
            else:
                groups.append([item])
        if batch:
            groups.append(batch)
        return groups

    def _concurrent(self, name: str) -> bool:
        """Whether a call of this tool may run beside others: an armed read
        that cannot stop on a card. Changes stay in call order, so the user
        sees them happen one by one and the change budget is exact."""
        if name not in self.armed or name in MUTATING_TOOLS:
            return False
        return not (self.gate and self.gate.asks(name))

    def _run_group(self, group: list):
        """Run one batch and hand its results to ``settle``, then stream the
        actions and append the tool results in call order. Returns None once
        the client is gone: nothing more may run for this reply."""
        if len(group) == 1:
            yield ("step", step_event(group[0].name, group[0].call))
            if (yield from self._run_one(group[0])) is None:
                return None
        else:
            yield ("step", batch_step(group))
            self._run_batch(group)
        if self.settle:
            settled = yield from self.settle.settle(group)
            if settled is None:
                return None
            group = settled
        for item in group:
            # A picture a tool answered with (view_pdf_page, view_ink) goes to
            # the model with its result, never into the streamed/saved chip —
            # and not at all to a model that reads text only.
            images = item.action.pop("images", None)
            if not self.pictures:
                images = None
            # A tool that ran a helper of its own (read_paper) spent tokens
            # the provider reported to it, not to this loop: pass them on so
            # the reply's footer counts the whole answer.
            spent = item.action.pop("spent", None)
            if spent:
                yield ("usage", spent)
            yield ("action", item.action)
            self.conversation.messages.append(
                {"role": "tool", "call_id": item.id, "content": item.result,
                 **({"images": images} if images else {})})
        return group

    def _run_one(self, item: ToolCall):
        """One call on its own: the change budget, then the gate (which may
        ask the user), then the tool. Returns None once the client left."""
        if item.name in self.armed and item.name in MUTATING_TOOLS and self.mutations >= MAX_TOOL_ACTIONS:
            item.result = "error: change limit for one message reached — stop and tell the user"
            item.action = tool_action("error", f"{item.name} — change limit reached",
                                      item.name, item.args, item.result, error=True)
            return item
        if self.gate:
            outcome = yield from self.gate.run(item.name, item.call, self.armed)
        else:
            outcome = self._execute(item.name, item.args)
        if outcome is None:
            return None
        item.result, item.action = outcome
        # Reads and failures render as chips too, but only applied mutations
        # count against the change budget.
        if item.name in MUTATING_TOOLS and not item.action.get("error"):
            self.mutations += 1
        return item

    def _run_batch(self, group: list) -> None:
        """Run a batch's reads at the same time. They are gate-free by
        construction, so each is one plain dispatch in its own thread with
        its own database connection; the message's counters are shared under
        the scope's lock (``ai_tools.Tally``)."""
        with ThreadPoolExecutor(max_workers=len(group), thread_name_prefix="ai-tool") as pool:
            futures = [pool.submit(self._execute, item.name, item.args) for item in group]
            for item, future in zip(group, futures):
                item.result, item.action = future.result()

    def _execute(self, name: str, args: dict) -> tuple:
        return run_agent_tool(self.ws, self.scope, name, args, allowed_tools=self.armed)

    # --- live preview of a note edit --------------------------------------

    def _preview(self, data: dict, previewed: dict) -> dict | None:
        """The ``{"progress"}`` payload for an edit_block / create_block call
        the model is still writing: the block it targets and the markdown so
        far, read out of the partial argument JSON. A call that waits for
        approval is never previewed — the note must not change in front of
        the user before they decided."""
        name = canonical_tool(data.get("name") or "")
        if name not in PREVIEW_TOOLS or name not in self.armed:
            return None
        if self.gate and self.gate.asks(name):
            return None
        args = partial_json_object(data.get("json") or "")
        target = args.get("block_id" if name == "edit_block" else "parent_id")
        mode = str(args.get("mode") or "replace").lower()
        selection = (find_selection(self.scope, args.get("selection"))
                     if name == "edit_block" and mode == "selection" else None)
        if selection:
            target = selection["block_id"]
        content = args.get("content")
        if not target or content is None:
            return None                              # nothing to point at (or say) yet
        if previewed.get(data.get("id")) == content:
            return None
        previewed[data.get("id")] = content
        progress = {"tool": name, "id": data.get("id") or "", "content": content}
        if name != "edit_block":
            progress["parent_id"] = target
            if args.get("after_id"):
                progress["after_id"] = args["after_id"]
            return progress
        progress["block_id"] = target
        if selection:
            # The preview swaps the selected range (re-found by its text if
            # it moved); append/prepend keep the stored text and type the
            # addition at the right end; patch swaps the passage in place.
            progress.update(mode="selection", find=selection["text"], at=selection["from"])
        elif mode == "selection":
            return None                              # no such selection: nothing to preview
        elif mode in ("append", "prepend"):
            progress["mode"] = mode
        elif mode == "patch" and isinstance(args.get("find"), str):
            progress["mode"] = mode
            progress["find"] = args["find"]
        return progress


class Helper:
    """A second, smaller agent the chat can hand one job to.

    ``read_paper`` uses it: the helper fetches one document, reads as many
    windows as the question needs, and hands back a cited answer. The
    windows stay in the helper's own conversation, so a forty-page paper
    costs the chat an answer instead of forty thousand characters it has to
    carry for the rest of the reply.

    It runs the same :class:`AgentLoop` with a narrow tool set, no gate and
    no settle: nothing it does can change the library, and a wall it meets
    is reported up to the chat, whose card and wait already exist. Its
    token counts are metered like any other call and handed back so the
    reply's footer can say what the whole answer cost.

    While it works it tells ``on_status`` what it is doing, its whole state
    each time: ``{id, label, state, steps, step?, blocked?}`` — ``state`` is
    reading, answering, done or failed, ``steps`` the calls it finished,
    ``step`` the one running now and ``blocked`` the host that stopped a
    fetch. The parent loop cannot carry this: it is inside the call that
    started the helper and yields nothing until that returns. So
    ``on_status`` is called from whichever thread runs the call, and a
    caller with someone watching puts it on its own stream.
    """

    def __init__(self, *, ws: str, scope: dict, open_call, read_events, on_usage=None, on_status=None):
        self.ws = ws
        self.scope = scope
        self.open_call = open_call
        self.read_events = read_events
        self.on_usage = on_usage
        self.on_status = on_status
        self._ids = itertools.count(1)

    def run(self, *, question: str, system: str, tools: list, label: str) -> dict:
        """Answer ``question`` with ``tools`` armed; ``label`` names the job
        in its status (the document's title). Returns ``{"text", "actions",
        "usage"}`` — the answer, the calls it made (for the parent's chip)
        and what it cost."""
        talk = Conversation([{"role": "user", "content": question}], system)
        usage = {}
        loop = AgentLoop(ws=self.ws, scope=self.scope, tools=tools, conversation=talk,
                         open_round=lambda c: self.open_call(c, tools),
                         read_events=self.read_events, on_usage=self._meter(usage),
                         max_rounds=HELPER_ROUNDS)
        text, actions = [], []
        status = {"id": f"h{next(self._ids)}", "label": label[:STATUS_LABEL_MAX],
                  "state": "reading", "steps": 0}
        self._say(status)
        try:
            for kind, data in loop.run(self.open_call(talk, tools)):
                if kind == "delta":
                    text.append(data)
                    if status["state"] != "answering":
                        self._say(status, state="answering", step=None)
                elif kind == "step":
                    self._say(status, state="reading", step=data)
                elif kind == "action":
                    data.pop("images", None)   # a picture never leaves the helper
                    actions.append(data)
                    blocked = data.get("handoff", {}).get("host")
                    self._say(status, steps=len(actions), step=None,
                              **({"blocked": blocked} if blocked else {}))
        except BaseException:
            self._say(status, state="failed", step=None)
            raise
        answer = "".join(text).strip()
        self._say(status, state="done" if answer else "failed", step=None)
        return {"text": answer, "actions": actions, "usage": usage}

    def _say(self, status: dict, **changes) -> None:
        status.update(changes)
        if self.on_status:
            self.on_status({k: v for k, v in status.items() if v is not None})

    def _meter(self, into: dict):
        def count(one):
            if self.on_usage:
                self.on_usage(one)
            for key, value in (one or {}).items():
                if isinstance(value, int):
                    into[key] = into.get(key, 0) + value
        return count


class ApprovalGate:
    """The chat's Ask permissions: a call of an asking tool shows what it
    would do on an approval card and waits for the user's decision
    (``gamma/ai_permissions.py``).

    Once runs the call; for the chat or always also allow that permission's
    later calls of this reply; a decline, or no answer, leaves the call
    unmade and tells the model why — with what to do instead when the user
    said it. A call that could not change anything is answered from its own
    plan without asking anybody.
    """

    def __init__(self, ws: str, scope: dict, user_id: str, stopped: threading.Event | None = None):
        self.ws = ws
        self.scope = scope
        self.user_id = user_id
        self.stopped = stopped

    def asks(self, name: str) -> bool:
        return self.scope["permissions"].get(tool_permission(name)) == "ask"

    def run(self, name: str, call: dict, armed):
        """Yield the card event if there is one, then return
        ``(result, action)`` — or None once the client is gone."""
        args = call.get("arguments") or {}
        if name not in armed or not self.asks(name):
            return run_agent_tool(self.ws, self.scope, name, args, allowed_tools=armed)
        preview, answer = approval_preview(self.ws, self.scope, name, args)
        if answer is not None:
            return answer, settled_action(name, args, answer)
        approval_id = ai_permissions.open_approval(self.user_id)
        decision, note = "expired", ""
        if approval_id:
            yield ("approval", {"id": approval_id, "call_id": call.get("id") or "", "tool": name,
                                "perm": tool_permission(name), "args": step_event(name, call)["args"],
                                "preview": preview, "timeout": int(ai_permissions.APPROVAL_TIMEOUT)})
            decision, note = ai_permissions.wait_for(approval_id, self.stopped)
        if decision == "stopped":
            return None
        if decision in ("chat", "always"):
            self.scope["permissions"][tool_permission(name)] = "allow"
        if decision in ai_permissions.ALLOWING:
            result, action = run_agent_tool(self.ws, self.scope, name, args, allowed_tools=armed)
            return result, {**action, "approval": decision}
        # The chip names the refused change by its page's title (and a note
        # edit's mode), but carries no page_id: nothing changed on that page.
        result = (DECLINED_SAYING.format(note=note) if note else DECLINED) if decision == "deny" else UNANSWERED
        return result, tool_action("error", f"Not allowed: {name}", name, args, result,
                                   error=True, declined=True, approval=decision,
                                   **{k: preview[k] for k in ("title", "mode") if preview.get(k)},
                                   **({"note": note} if note else {}))


class PaperWait:
    """A fetch a sign-in page, a bot check or a paywall stopped: the chat
    shows a card per blocked paper and waits, in this same turn, for the PDF
    the user's own browser can get (``gamma/fetch_handoff.py``).

    The calls of one round wait together, so four blocked papers cost one
    wait, not four. What arrives is read by running the same call again —
    ``fetch_paper`` reads a delivered PDF before any fetch, so the model
    gets the paper's text where it asked for it instead of a dead end and a
    second turn. Skipped, the model hears what the user wants instead;
    unanswered, it hears what blocked the paper and carries on.
    """

    def __init__(self, ws: str, scope: dict, user_id: str, stopped: threading.Event | None = None):
        self.ws = ws
        self.scope = scope
        self.user_id = user_id
        self.stopped = stopped

    def settle(self, items: list):
        from . import fetch_handoff

        blocked = [item for item in items if item.action.get("handoff", {}).get("id")]
        waiting = [item for item in blocked
                   if (fetch_handoff.get(self.user_id, item.action["handoff"]["id"]) or {}).get("status") == "waiting"]
        if not waiting:
            return items
        for item in waiting:
            handoff = item.action["handoff"]
            yield ("handoff", {**handoff, "call_id": item.id,
                               "timeout": int(fetch_handoff.IDLE_TIMEOUT)})
        outcomes = fetch_handoff.wait_for_all(self.user_id, [item.action["handoff"]["id"] for item in waiting],
                                              stopped=self.stopped)
        if self.stopped is not None and self.stopped.is_set():
            return None
        for item in waiting:
            handoff = item.action["handoff"]
            outcome, note = outcomes[handoff["id"]]
            if outcome == "delivered":
                # fetch_paper reads what the browser delivered before it
                # fetches anything, so the call simply runs again.
                item.result, item.action = run_agent_tool(self.ws, self.scope, item.name, item.args,
                                                          allowed_tools={item.name})
                item.action["delivered"] = True
            elif outcome == "dismissed":
                item.replace(skipped_fetch(handoff, note), skipped=True,
                             summary=f"Skipped: {handoff.get('host') or 'this paper'}")
            else:
                # Nobody answered the card. It stays under the reply, so the
                # user can still finish it; the model carries on without it.
                item.replace(unanswered_fetch(handoff),
                             summary=f"Not fetched: {handoff.get('host') or 'this paper'}")
        return items
