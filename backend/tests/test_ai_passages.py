"""What a chat message points at — selected PDF passages, selected note
text, attached blocks — reaching the model: one budget (the single-paper
context_char_limit) shared fairly, nothing cut without a word to the model
and to the user, and the window fit giving the selection up last."""

import json
from types import SimpleNamespace

import pytest

from ai_fixtures import FakeResp, ai_provider, notes, org  # noqa: F401  (fixtures)
from gamma.ai_context import (MAX_SELECTIONS, MIN_PASSAGE_BUDGET, fair_shares, final_prompt,
                              notes_focus_section, passage_cuts, request_selections)

# The same cases as frontend/tests/passageBudget.test.mjs: the composer's
# chip and the server must cut a passage at the same character.
CASES = [
    ([100, 200], 1000, [100, 200]),  # everything fits
    ([31_400], 12_000, [12_000]),  # one passage: its first 12,000
    ([500, 40_000, 30_000], 60_000, [500, 29_750, 29_750]),  # the short one keeps all; the rest share
    ([100, 100], 151, [75, 76]),  # a tie: the remainder goes to the later one
    ([10, 20], 0, [0, 0]),
    ([], 60_000, []),
]


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """The chats here need a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _offline_model_facts(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


def ask(**fields):
    return SimpleNamespace(**{"prompt": "summarize this", "selection": "", "selections": [],
                              "note_selections": [], **fields})


def test_a_shared_budget_keeps_short_passages_whole_and_splits_the_rest():
    for sizes, budget, want in CASES:
        assert fair_shares(sizes, budget) == want, (sizes, budget)
    shares = fair_shares([7, 9_000, 3, 120_000, 45_000], 60_000)
    assert sum(shares) <= 60_000 and shares[:3] == [7, 9_000, 3] and shares[3] == shares[4]


def test_passages_arrive_whole_and_the_question_says_where_one_was_cut():
    pages = "".join(f"[page {n}] " + "word " * 900 for n in range(1, 8))  # seven dense pages
    payload = ask(selections=[{"text": pages, "page": 2}, {"text": "a short one", "page": 9}],
                  context_char_limit=12_000)
    (first, second) = request_selections(payload)
    assert first["text"] == pages.strip() and second["text"] == "a short one"  # no cap on the way in
    text = final_prompt(payload)
    kept = 12_000 - len("a short one")
    assert pages.strip()[:kept] + "\n…[cut here: the first " in text
    assert f"the first {kept:,} of this passage's {len(pages.strip()):,} characters are quoted" in text
    assert pages.strip()[:kept + 1] not in text
    assert '"""\na short one\n"""' in text
    assert passage_cuts(payload) == [{"kind": "pdf", "n": 1, "of": 2, "kept": kept,
                                      "total": len(pages.strip())}]
    # Within the budget everything goes whole, and nothing is reported.
    roomy = ask(selections=[{"text": pages, "page": 2}], context_char_limit=60_000)
    assert pages.strip() in final_prompt(roomy) and "cut here" not in final_prompt(roomy)
    assert passage_cuts(roomy) == []
    # A smaller budget (the window fit's) overrides the request's.
    assert "the first 5,000 of" in final_prompt(roomy, budget=5_000)


def test_pdf_passages_and_note_selections_share_one_budget():
    note = "n" * 3_000
    payload = ask(selections=[{"text": "p" * 3_000, "page": 1}], context_char_limit=1_000,
                  note_selections=[{"block_id": "b1", "from": 0, "to": 3_000, "text": note}])
    text = final_prompt(payload)
    assert "p" * 500 + "\n…[cut here" in text and "n" * 500 + "\n…[cut here" in text
    assert [(c["kind"], c["kept"], c["total"]) for c in passage_cuts(payload)] == [
        ("pdf", 500, 3_000), ("note", 500, 3_000)]
    assert len(request_selections(ask(selections=[{"text": f"s{i}"} for i in range(20)]))) == MAX_SELECTIONS


def test_attached_blocks_share_the_budget_and_say_what_was_cut(notes):
    c, ids = notes

    def block(parent, content):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    big = block(ids["page"], "chapter " + "x" * 300)
    kids = [block(big, f"part {i} " + "y" * 300) for i in range(8)]
    small = block(ids["page"], "a short attached note")
    seen, quoted = {}, {}
    payload = SimpleNamespace(focus_block_id="", page_id=ids["page"], pages=[], context_blocks=[big, small],
                              note_selections=[], context_char_limit=1_000)
    section = notes_focus_section(ids["ws"], payload, notes_seen=seen, quoted=quoted)
    assert f"[{small}] a short attached note" in section and small in seen
    assert f"[{big}] chapter" in section and "… (more sub-blocks not shown)" in section
    shown = [k for k in kids if k in section]
    assert 0 < len(shown) < len(kids) and all(k not in seen for k in kids if k not in shown)
    (cut,) = quoted["items"]
    assert cut["kind"] == "block" and cut["n"] == 1 and cut["of"] == 2
    assert cut["kept"] <= 1_000 - len(f"- [{small}] a short attached note") < cut["total"]
    assert quoted["wanted"] > cut["total"]  # the budget that would have sent both whole
    # The request's own budget, when it is roomy, sends the whole chapter.
    payload.context_char_limit = 60_000
    quoted = {}
    assert kids[-1] in notes_focus_section(ids["ws"], payload, quoted=quoted) and not quoted.get("items")


def _lines(r):
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_chat_reports_a_passage_the_budget_cut(org, monkeypatch):
    c, _ = org
    import gamma.routers.ai as ai_mod

    seen = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        seen.append(messages[-1]["content"])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    long = "q" * 3_000
    lines = _lines(c.post("/api/ai/chat", json={
        "prompt": "summarize this", "stream": True, "context_char_limit": 1_000,
        "selections": [{"text": long, "page": 0}, {"text": "s" * 200, "page": 0}]}))
    shortened = next(line["shortened"] for line in lines if "shortened" in line)
    assert shortened == {"budget": 1_000, "asked": 1_000, "items": [
        {"kind": "pdf", "n": 1, "of": 2, "kept": 800, "total": 3_000}]}
    assert "q" * 800 + "\n…[cut here" in seen[-1] and "q" * 801 not in seen[-1] and "s" * 200 in seen[-1]
    # Whole: no report.
    lines = _lines(c.post("/api/ai/chat", json={
        "prompt": "summarize this", "stream": True, "selections": [{"text": long, "page": 0}]}))
    assert not [line for line in lines if "shortened" in line] and long in seen[-1]
    # The API takes as many passages as the composer holds, and no more.
    many = [{"text": f"passage {i}", "page": 0} for i in range(MAX_SELECTIONS)]
    assert c.post("/api/ai/chat", json={"prompt": "q", "stream": True, "selections": many}).status_code == 200
    assert c.post("/api/ai/chat", json={"prompt": "q", "stream": True,
                                        "selections": many + many[:1]}).status_code == 422


def test_the_window_fit_gives_up_the_selection_last_and_says_so(org, monkeypatch):
    """With every earlier turn left out and the prompt still over the
    model's window, the passages' budget halves until it fits (never below
    MIN_PASSAGE_BUDGET), and the report names the budget it ran at."""
    c, _ = org
    import gamma.routers.ai as ai_mod
    from gamma.ai_context import prompt_tokens

    seen = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        seen.append((messages, system))
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    reserve = ai_mod.DEFAULT_MAX_TOKENS + ai_mod._ESTIMATE_SLACK
    monkeypatch.setattr(ai_mod.ai_catalog, "context_window", lambda *a: (reserve + 3_000, "test"))
    long = "w" * 40_000  # ~10k tokens against ~3k of room
    history = [{"role": "user", "text": "earlier"}, {"role": "ai", "text": "answer"}]
    lines = _lines(c.post("/api/ai/chat", json={
        "prompt": "summarize this", "stream": True, "history": history,
        "selections": [{"text": long, "page": 0}]}))
    assert next(line["trimmed"] for line in lines if "trimmed" in line) == {"turns": 2}  # the turns go first
    shortened = next(line["shortened"] for line in lines if "shortened" in line)
    (item,) = shortened["items"]
    assert shortened["asked"] == 60_000 and MIN_PASSAGE_BUDGET <= shortened["budget"] < 40_000
    assert item["kept"] == shortened["budget"] and item["total"] == 40_000
    messages, system = seen[-1]
    assert len(messages) == 1 and "w" * item["kept"] + "\n…[cut here" in messages[0]["content"]
    assert prompt_tokens(messages, system) <= 3_000
    # A window nothing fits: the budget stops at its floor and the provider decides.
    monkeypatch.setattr(ai_mod.ai_catalog, "context_window", lambda *a: (reserve + 10, "test"))
    lines = _lines(c.post("/api/ai/chat", json={
        "prompt": "summarize this", "stream": True, "selections": [{"text": long, "page": 0}]}))
    assert next(line["shortened"] for line in lines if "shortened" in line)["budget"] == MIN_PASSAGE_BUDGET
