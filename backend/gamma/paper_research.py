"""Research that runs in the background: a question the user hands over,
answered by the same agent the chat runs, into a new page of their library.

A chat reply has to finish while the user waits. Some questions do not fit
that — "find what limits the density in an 85Rb lattice at these
parameters, and say which papers measured it" is a dozen searches and
several papers read. This runs that work as a background job
(``gamma/jobs.py``): it survives the tab closing, shows its progress in
Background tasks, and files what it found as a page the user can read,
annotate and keep.

What it is, deliberately:

- **The user starts it**, never the model. An agent that can put itself to
  work for ten minutes unasked is a different product, and the same reason
  the chat asks before it changes anything applies here.
- **Reading only.** Its tools search and read; the one thing it writes is
  its own report page at the end. Nothing can ask a user who is not there,
  so no approval card and no browser handoff wait: a paper a publisher
  blocks is reported as blocked, and its request shows in Background tasks
  for the user to finish by hand.
- **One page, with its sources.** The answer is a page in the folder the
  user started from, so it lands where the rest of their reading is.

It fetches as the account that started it, so the publisher sessions that
account connected apply (``publisher_sessions``), the way they do for its
chats.
"""

import secrets

from . import ai_usage, jobs
from .ai_agent import AgentLoop, Conversation, Helper
from .ai_client import open_ai, sse_events, wire_protocol
from .ai_settings import require_ai_runtime, resolve_model
from .ai_tools import agent_system, agent_tools, available
from .blocks_store import create_page
from .db import connect_pages_db
from .foldertags import add_tag, clean_path
from .logbuf import log
from .ops import after_commit, apply_ops

# What the background researcher is asked to produce. It answers into a
# page, not a chat, so it writes a document rather than a reply.
RESEARCH_PROMPT = (
    "You are researching one question for a physicist, in the background, and your answer "
    "becomes a page in their library — write a short report, not a chat reply.\n"
    "Work the way a researcher would: turn the question into a few short concept queries, "
    "judge candidates from their abstracts, follow the citations of the strongest match, and "
    "read only the papers that decide the question. Prefer the user's own library for anything "
    "it already holds.\n"
    "Then write the report in markdown:\n"
    "- One paragraph answering the question directly, or saying plainly that the literature "
    "does not settle it.\n"
    "- What each decisive paper contributes, one short paragraph or bullet each, with the page "
    "number for every number you quote and a markdown link on the title (its DOI, arXiv or "
    "source URL).\n"
    "- Which papers match the user's case directly and which are analogies (another species, "
    "geometry or regime). Never present one paper's number as a general limit.\n"
    "- A last line naming what you could not reach — a paper a publisher blocked, a search "
    "that found nothing — so they know what is missing.\n"
    "Nobody is at the keyboard: you cannot ask questions, and a blocked paper stays blocked. "
    "Report what you actually read, never anything from memory."
)

# A research job's own budgets. Rounds are generous because the work is a
# dozen searches and several reads; the page is capped so one runaway reply
# cannot fill the library.
MAX_ROUNDS = 40
MAX_QUESTION = 2000
MAX_REPORT = 100_000
TITLE_MAX = 120

# The tools it may use: everything that reads or searches, nothing that
# changes the library (it writes only its own page, at the end).
TOOLS = frozenset({"list_pages", "list_folders", "read_page", "read_block", "search_library",
                   "search_papers", "related_papers", "search_web", "fetch_paper", "read_paper"})


def start(*, user: str, ws: str, question: str, folder: str = "", model: str = "",
          read_chars: int = 0) -> dict:
    """Queue a research job for ``question``. Raises the usual job errors
    (too many at once, no disk) and HTTPException when the account has no
    AI connection to run it on."""
    question = " ".join((question or "").split())[:MAX_QUESTION]
    if not question:
        raise ValueError("a research job needs a question")
    require_ai_runtime(user)  # fail now, not in the worker
    folder = clean_path(folder or "")
    return jobs.start(
        "research", owner=user, ws=ws, title=question[:TITLE_MAX],
        params={"question": question, "folder": folder, "model": model},
        run=lambda job: _work(job, user=user, ws=ws, question=question, folder=folder,
                             model=model, read_chars=read_chars))


def _work(job, *, user: str, ws: str, question: str, folder: str, model: str, read_chars: int) -> dict:
    runtime = require_ai_runtime(user)
    entry = resolve_model(runtime, model)
    count_usage = ai_usage.recorder("chat", entry, runtime)
    job.progress(phase="searching", unit="steps", done=0)

    scope = {
        "type": "folder", "folder": folder, "actor": user, "read_chars": read_chars,
        "can_write": False, "context_pages": [], "read_texts": {},
        # A wall is reported, not waited on: nobody is here to pass a check,
        # but the request still shows in Background tasks to finish by hand.
        "handoff_user": user, "paper_wait": False,
        "publisher_user": user, "delegates": True,
        # Every read is allowed, nothing may change the library, and no tool
        # may ask: a job has no one to ask.
        "permissions": {name: "allow" for name in TOOLS},
    }
    tools = agent_tools("folder", scope["permissions"], read_chars, can_write=False,
                        has=available(scope), allowed_tools=TOOLS)
    # Tools decide the wire (an OpenAI call with them goes over /v1/responses),
    # and a helper's turn carries them too, so one protocol serves both.
    proto = wire_protocol(runtime, entry, tools)
    scope["helper"] = Helper(
        ws=ws, scope=scope, on_usage=count_usage,
        open_call=lambda talk, htools: _open(talk, htools, entry, runtime),
        read_events=lambda resp: sse_events(resp, proto),
    )
    # agent_system appends the scope and tool lines to the base prompt, the
    # same way a chat's system prompt is built.
    talk = Conversation([{"role": "user", "content": question}],
                        agent_system(scope, scope["permissions"], RESEARCH_PROMPT))
    loop = AgentLoop(ws=ws, scope=scope, tools=tools, conversation=talk,
                     open_round=lambda c: _open(c, tools, entry, runtime),
                     read_events=lambda resp: sse_events(resp, proto),
                     on_usage=count_usage, max_rounds=MAX_ROUNDS)

    report, actions = [], []
    for kind, data in loop.run(_open(talk, tools, entry, runtime)):
        if kind == "delta":
            report.append(data)
        elif kind == "action":
            actions.append(data)
            # Every action is also where the job can be stopped.
            job.progress(phase=_phase(data), unit="steps", done=len(actions),
                         item=(data.get("summary") or "")[:120])
    text = "".join(report).strip()[:MAX_REPORT]
    if not text:
        raise ValueError("the assistant answered nothing — try the question in a chat first")
    job.progress(phase="filing", unit="steps", done=len(actions), stoppable=False)
    page_id, title = _file_report(ws, user, question, folder, text, actions)
    return {"page_id": page_id, "title": title, "steps": len(actions),
            "blocked": [a["handoff"]["host"] for a in actions if a.get("handoff", {}).get("host")]}


def _open(talk: Conversation, tools, entry: dict, runtime: dict):
    """One provider turn for the job's conversation, or a helper's."""
    return open_ai(talk.messages, talk.system, entry, runtime, talk.files,
                   timeout=300, stream=True, tools=tools)


def _phase(action: dict) -> str:
    """Which part of the work an action belongs to, for the tray's line."""
    return {"websearch": "searching", "fetch": "reading", "read": "reading",
            "search": "searching", "list": "searching"}.get(action.get("kind"), "searching")


def _file_report(ws: str, user: str, question: str, folder: str, text: str,
                 actions: list) -> tuple[str, str]:
    """Put the report in the library: a page in ``folder`` holding the
    question and the answer. Returns ``(page_id, title)``."""
    title = f"Research: {question[:TITLE_MAX]}"
    props = {"research": 1}
    if folder:
        props["folder"] = ", ".join(add_tag([], folder))
    with connect_pages_db(ws) as conn:
        page = create_page(conn, title, props)
        # Each insert appends as the page's last block, so they land in the
        # order written: the question, the report, then what it read.
        blocks = [{"op": "insert", "id": secrets.token_urlsafe(9), "parent": page["id"],
                   "content": content}
                  for content in (f"**Question.** {question}", text, _sources_line(actions))
                  if content]
        after_commit(ws, conn, apply_ops(conn, page["id"], blocks, actor=user))
    log.info(f"[research] filed {len(blocks)} blocks in page {page['id']}")
    return page["id"], title


def _sources_line(actions: list) -> str:
    """What the run read, as a closing list — the documents it fetched, each
    once, so the page says where its answer came from."""
    seen, lines = set(), []
    for action in actions:
        url = action.get("url")
        if action.get("kind") != "fetch" or not url or url in seen:
            continue
        seen.add(url)
        lines.append(f"- [{(action.get('title') or url)[:200]}]({url})")
    return ("**Read for this report**\n\n" + "\n".join(lines)) if lines else ""
