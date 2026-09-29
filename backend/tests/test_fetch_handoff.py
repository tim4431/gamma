"""Fetches handed to the user's browser (gamma/fetch_handoff.py) and what
leads there: bot-check / sign-in / paywall detection in ai_web, the chat
card's endpoints and /go page, one cookie jar per fetch, the Connector's
User-Agent on a connected host, and the search merge. The resolver, reader
and cookie path are real; HTTPS is the fake transport of test_net_guard."""

import json
import time
from urllib.request import Request

import pytest

import gamma.routers.metadata as metadata_mod
import gamma.routers.pdf as pdf_mod
from ai_fixtures import FakeResp, folder
from conftest import login, make_user
from gamma import ai_web, fetch_handoff, net_guard
from gamma import publisher_sessions as sessions
from gamma.ai_tools import agent_system, run_agent_tool
from gamma.db import connect_users_db
from test_ai_tools_web import _text_pdf
from test_net_guard import transport  # noqa: F401 -- fake urllib transport fixture

USER, OTHER = "fh_alice", "fh_bob"
PDF_TEXT = "Delivered from the browser."
CHALLENGE = (b"<html><head><title>Just a moment...</title></head><body>"
             b"<script>window._cf_chl_opt={cvId:'3'}</script></body></html>")
ARTICLE = ("<html><head><title>Wall paper</title>"
           "<meta name=\"citation_pdf_url\" content=\"https://journals.example.org/pdf/wall\"></head>"
           "<body><h1>Wall paper</h1><p>Abstract: the abstract only.</p>"
           "<a href=\"/suppl/wall-si.pdf\">Supplementary PDF</a>"
           "<div class=\"g-recaptcha\"></div>" + "<p>Long enough article text.</p>" * 120
           + "</body></html>").encode()


@pytest.fixture(scope="module")
def accounts(client):
    for name in (USER, OTHER):
        make_user(name, "handoff-password")
    return login(USER, "handoff-password"), login(OTHER, "handoff-password")


@pytest.fixture
def web(transport, monkeypatch):
    routes, seen = transport
    monkeypatch.setattr(pdf_mod, "_open_access_pdf_candidates_for_doi", lambda doi: [])
    ai_web.clear_cache()
    fetch_handoff.clear()
    yield routes, seen
    ai_web.clear_cache()
    fetch_handoff.clear()


@pytest.fixture
def fetch(accounts):
    """fetch_paper in a chat of USER's (the scope a personal account's chat gets)."""
    ws = make_user(USER, "handoff-password")

    def run(source, user=USER, **scope):
        return run_agent_tool(ws, {**folder(""), "read_chars": 20000, "handoff_user": user, **scope},
                              "fetch_paper", {"source": source})
    return run


# ------------------------------------------------------------ walls

def test_cloudflare_challenge_opens_a_handoff_card_and_retries_reuse_it(web, fetch):
    routes, seen = web
    doi = "https://doi.org/10.5555/cf-test"
    landing = "https://www.science.example/doi/10.5555/cf-test"
    routes[doi] = (302, {"Location": landing}, b"")
    routes[landing] = (403, {"Content-Type": "text/html", "cf-mitigated": "challenge"}, CHALLENGE)

    text, action = fetch("doi:10.5555/cf-test")
    assert action["error"] and action["summary"] == "Needs your browser: www.science.example"
    handoff = action["handoff"]
    assert handoff["wall"] == "captcha" and handoff["host"] == "www.science.example"
    assert "a CAPTCHA or bot check at www.science.example" in text
    assert "end your reply" in text and "do not retry this source" in text
    assert 'fetch_paper(source="doi:10.5555/cf-test") returns it' in text
    req = fetch_handoff.get(USER, handoff["id"])
    assert req["url"] == landing and req["status"] == "waiting"

    # The model retrying (in another spelling) gets the same card, not a new one.
    _, again = fetch("10.5555/cf-test")
    assert again["handoff"]["id"] == handoff["id"]
    # Guests and share links have no one to hand it to: the Connector guidance.
    text, action = fetch("doi:10.5555/cf-test", user=None)
    assert "handoff" not in action and "Connect this publisher" in text


def test_pdf_behind_sign_in_redirect_opens_the_paper_not_the_login_page(web, fetch):
    routes, _ = web
    pdf = "https://pub.example.org/content/paper.pdf"
    login_page = "https://pub.example.org/action/showLogin?uri=%2Fcontent%2Fpaper.pdf"
    routes[pdf] = (302, {"Location": login_page}, b"")
    routes[login_page] = (200, {"Content-Type": "text/html"},
                          b"<html><title>Sign in</title><form><input type=password name=p></form></html>")
    text, action = fetch(pdf)
    assert action["handoff"]["wall"] == "login" and "a sign-in page" in text
    assert fetch_handoff.get(USER, action["handoff"]["id"])["url"] == pdf


def test_article_page_only_is_read_and_offers_the_full_text(web, fetch):
    routes, _ = web
    doi, landing = "https://doi.org/10.5555/wall", "https://journals.example.org/article/wall"
    pdf = "https://journals.example.org/pdf/wall"
    routes[doi] = (302, {"Location": landing}, b"")
    routes[landing] = (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE)
    routes[pdf] = (200, {"Content-Type": "text/html"}, CHALLENGE)

    text, action = fetch("doi:10.5555/wall")
    assert not action.get("error") and action["summary"] == "Fetched “Wall paper” (article page only)"
    assert "Abstract: the abstract only." in text  # the model still gets what was readable
    assert "Only the article page was readable" in text and "say so briefly and end your reply" in text
    # The page's other PDF links, for the model to try; the advertised one met the wall already.
    assert "PDF candidates ranked for the requested paper" in text
    assert "https://journals.example.org/suppl/wall-si.pdf" in text
    req = fetch_handoff.get(USER, action["handoff"]["id"])
    assert (req["wall"], req["url"], req["pdf_url"]) == ("abstract", landing, pdf)


def test_access_wall_ignores_a_widget_on_a_real_article():
    long_page = b"<html><body>" + b"<p>Real article text here.</p>" * 200 + b"<div class=g-recaptcha></div></body></html>"
    assert ai_web.access_wall("https://x.example/article", {}, long_page) == ""
    short = b"<html><body><div class=g-recaptcha></div>Please continue</body></html>"
    assert ai_web.access_wall("https://x.example/article", {}, short) == "captcha"
    assert ai_web.access_wall("https://idp.example.edu/profile", {}, b"<p>hi</p>") == "login"
    assert ai_web.access_wall("https://x.example/a", {"cf-mitigated": "challenge"}, b"") == "captcha"


# -------------------------------------------------------- endpoints

def test_card_endpoints_deliver_watch_dismiss_and_privacy(accounts, web, fetch):
    alice, bob = accounts
    req = fetch_handoff.open_request(USER, "doi:10.5555/deliver", wall="captcha",
                                     url="https://www.science.example/doi/10.5555/deliver",
                                     pdf_url="https://www.science.example/doi/pdf/10.5555/deliver")
    rid = req["id"]
    view = alice.get(f"/api/ai/handoffs/{rid}").json()
    assert view["status"] == "waiting" and not view["watched"] and "user" not in view
    # Another account can neither see nor fulfil it.
    assert bob.get(f"/api/ai/handoffs/{rid}").status_code == 404
    assert bob.post(f"/api/ai/handoffs/{rid}/pdf", files={"file": ("p.pdf", _text_pdf([PDF_TEXT]))}).status_code == 404

    assert alice.post(f"/api/ai/handoffs/{rid}/watch").json()["watched"] is True
    # What the Connector is doing in the tab shows on the card; unknown notes clear.
    assert alice.post(f"/api/ai/handoffs/{rid}/watch", json={"note": "opening"}).json()["note"] == "opening"
    assert alice.post(f"/api/ai/handoffs/{rid}/watch", json={"note": "<b>x</b>"}).json()["note"] == ""
    r = alice.post(f"/api/ai/handoffs/{rid}/pdf", files={"file": ("x.pdf", b"<html>login</html>")})
    assert r.status_code == 400 and "Not a PDF" in r.json()["detail"]
    r = alice.post(f"/api/ai/handoffs/{rid}/pdf", files={"file": ("p.pdf", _text_pdf([PDF_TEXT]))},
                   data={"url": "https://www.science.example/doi/pdf/10.5555/deliver?download=true"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["pages"]) == ("done", 1)
    # A second delivery (the Connector after a manual upload) is refused.
    r = alice.post(f"/api/ai/handoffs/{rid}/pdf", files={"file": ("p.pdf", _text_pdf(["Late copy."]))})
    assert r.status_code == 409 and "done" in r.json()["detail"]

    # Any spelling of the source reads the delivered PDF, for its owner only.
    for source in ("doi:10.5555/deliver", "https://doi.org/10.5555/deliver", "10.5555/DELIVER"):
        text, action = fetch(source)
        assert PDF_TEXT in text and action["summary"].endswith("from your browser")
        assert "fetched this PDF in their own browser" in text
    assert fetch_handoff.delivered(OTHER, "doi:10.5555/deliver") is None

    other = fetch_handoff.open_request(USER, "doi:10.5555/other", wall="denied", url="https://a.example/x")
    assert alice.delete(f"/api/ai/handoffs/{other['id']}").json()["status"] == "dismissed"
    r = alice.post(f"/api/ai/handoffs/{other['id']}/pdf", files={"file": ("p.pdf", _text_pdf([PDF_TEXT]))})
    assert r.status_code == 409
    assert alice.get("/api/ai/handoffs/not-a-request").status_code == 404
    with pytest.raises(ValueError):
        fetch_handoff.open_request(USER, "x", wall="denied", url="javascript:alert(1)")


def test_card_endpoints_need_a_personal_account(accounts, guest, anon):
    rid = fetch_handoff.open_request(USER, "doi:10.5555/g", wall="denied", url="https://a.example/g")["id"]
    assert anon.get(f"/api/ai/handoffs/{rid}").status_code == 401
    assert guest.get(f"/api/ai/handoffs/{rid}").status_code == 403


def test_go_page_redirects_its_owner_and_asks_everyone_else(accounts, anon):
    alice, bob = accounts
    rid = fetch_handoff.open_request(USER, "doi:10.5555/go", wall="login",
                                     url="https://journals.example.org/a?x=1&y=\"2\"")["id"]
    page = alice.get(f"/api/ai/handoffs/{rid}/go")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert '<meta http-equiv="refresh" content="1;url=https://journals.example.org/a?x=1&amp;y=&quot;2&quot;">' in page.text
    assert "Opening journals.example.org" in page.text
    for stranger in (bob, anon):
        page = stranger.get(f"/api/ai/handoffs/{rid}/go")
        assert "http-equiv" not in page.text and "Continue to journals.example.org?" in page.text
    assert anon.get("/api/ai/handoffs/nope/go").status_code == 404


def test_requests_expire_and_are_capped_per_account(accounts, monkeypatch):
    fetch_handoff.clear()
    ids = [fetch_handoff.open_request(USER, f"doi:10.5555/cap{i}", wall="denied",
                                      url="https://a.example/x")["id"]
           for i in range(fetch_handoff.MAX_PER_ACCOUNT + 2)]
    assert fetch_handoff.get(USER, ids[0]) is None and fetch_handoff.get(USER, ids[-1])
    now = time.time()
    monkeypatch.setattr(fetch_handoff.time, "time", lambda: now + fetch_handoff.TTL + 1)
    assert fetch_handoff.get(USER, ids[-1]) is None and fetch_handoff.target(ids[-1]) is None


# ------------------------------------------------------ the chat loop

def test_chat_streams_the_handoff_and_reads_the_delivery_next_turn(accounts, web, monkeypatch):
    import gamma.routers.ai as ai_mod

    alice, _ = accounts
    routes, _ = web
    source, landing = "https://doi.org/10.5555/loop", "https://www.science.example/doi/10.5555/loop"
    routes[source] = (302, {"Location": landing}, b"")
    routes[landing] = (403, {"Content-Type": "text/html", "cf-mitigated": "challenge"}, CHALLENGE)
    r = alice.post("/api/ai/providers", json={"protocol": "anthropic", "base_url": "https://ai-provider.invalid",
                                              "api_key": "sk-test-key-123", "models": "claude-solo"})
    assert r.status_code == 200, r.text

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kwargs):
        assert "a card lets the user get the PDF in their browser" in system
        if messages[-1]["role"] == "tool":
            return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}])
        return FakeResp([
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "f1", "name": "fetch_paper"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                                                      "partial_json": json.dumps({"source": source})}},
            {"type": "content_block_stop"},
        ])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    monkeypatch.setattr(ai_mod.ai_catalog, "context_window", lambda *args: (0, ""))

    def chat():
        r = alice.post("/api/ai/chat", json={"prompt": "Read it", "agent_scope": "folder", "stream": True})
        assert r.status_code == 200, r.text
        action, = [line["action"] for line in map(json.loads, r.text.splitlines()) if "action" in line]
        return action

    action = chat()
    rid = action["handoff"]["id"]
    assert action["handoff"]["host"] == "www.science.example"
    assert alice.post(f"/api/ai/handoffs/{rid}/pdf", files={"file": ("p.pdf", _text_pdf([PDF_TEXT]))}).status_code == 200
    action = chat()
    assert PDF_TEXT in action["result"] and "handoff" not in action


# ------------------------------------------------ cookies and agents

def test_browsing_session_keeps_a_landing_pages_cookie_for_its_pdf(transport):
    routes, seen = transport
    page, pdf = "https://pub.example/article", "https://pub.example/article.pdf"
    routes[page] = (200, {"Content-Type": "text/html", "Set-Cookie": "JSESSIONID=abc; Path=/; Secure"}, b"<p>x</p>")
    routes[pdf] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
    with net_guard.browsing_session():
        net_guard.guarded_urlopen(page).read()
        net_guard.guarded_urlopen(pdf).read()
    net_guard.guarded_urlopen(pdf).read()
    assert seen == [(page, None), (pdf, "JSESSIONID=abc"), (pdf, None)]


def test_connected_host_gets_its_browsers_user_agent(accounts, transport, monkeypatch):
    alice, _ = accounts
    routes, _ = transport
    agents = []
    host = "journals.aps.org"
    url, other = f"https://{host}/prl/pdf/10.1103/ua", "https://elsewhere.example/x.pdf"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36"
    with connect_users_db() as conn:
        conn.execute("DELETE FROM publisher_sessions WHERE username=?", (USER,))
        conn.commit()
    for u in (url, other):
        routes[u] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
    r = alice.post("/api/publisher-sessions", headers={"x-forwarded-proto": "https"}, json={
        "host": host, "user_agent": ua,
        "cookies": [{"name": "access", "value": "v", "domain": host, "hostOnly": True, "path": "/"}]})
    assert r.status_code == 200, r.text

    real = net_guard._BrowserAgent.https_request

    def spy(self, req):
        out = real(self, req)
        agents.append((req.full_url, req.get_header("User-agent")))
        return out

    monkeypatch.setattr(net_guard._BrowserAgent, "https_request", spy)
    token = sessions.current_user.set(USER)
    try:
        net_guard.guarded_urlopen(Request(url, headers=pdf_mod.BROWSER_HEADERS)).read()
        net_guard.guarded_urlopen(Request(other, headers=pdf_mod.BROWSER_HEADERS)).read()
    finally:
        sessions.current_user.reset(token)
    assert agents == [(url, ua), (other, pdf_mod.BROWSER_HEADERS["User-Agent"])]
    # An unusable agent is dropped, never a reason to refuse the cookies.
    assert sessions.browser_agent("bad\nagent") == "" and sessions.browser_agent("x" * 600) == ""


def test_resolver_sends_the_article_page_as_referer(transport, monkeypatch):
    routes, _ = transport
    referers = []
    page, pdf = "https://journals.example.org/article/r", "https://journals.example.org/pdf/r"
    routes[page] = (200, {"Content-Type": "text/html"},
                    b'<meta name="citation_pdf_url" content="/pdf/r">')
    routes[pdf] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
    real = net_guard._BrowserAgent.https_request

    def spy(self, req):
        referers.append((req.full_url, req.get_header("Referer")))
        return real(self, req)

    monkeypatch.setattr(net_guard._BrowserAgent, "https_request", spy)
    resolved = pdf_mod.resolve_source(page)
    assert resolved["source_url"] == pdf
    assert resolved["referer"] == page
    assert resolved["provenance"]["source_kind"] == "article_page"
    assert referers == [(page, None), (pdf, page)]


# ------------------------------------------------------------- search

def test_search_keeps_both_identifiers_and_ranks_the_exact_title_first(monkeypatch):
    title = "Bias-preserving gates with stabilized cat qubits"
    monkeypatch.setattr(metadata_mod, "_crossref_search", lambda q, rows=5: [
        {"title": "A survey of cat qubits", "doi": "10.1000/survey", "authors": []},
        {"title": title, "doi": "10.1126/sciadv.aay5901", "authors": []}])
    monkeypatch.setattr(metadata_mod, "_arxiv_search", lambda q, rows=5: [
        {"title": title.upper(), "arxiv_id": "1905.00450", "authors": []}])
    records = ai_web.search_papers(title)
    assert [r.get("doi") for r in records] == ["10.1126/sciadv.aay5901", "10.1000/survey"]
    assert records[0]["arxiv_id"] == "1905.00450"
    text = ai_web.format_records(records[:1])
    assert ('→ fetch_paper(source="arXiv:1905.00450") for the arXiv version, '
            'fetch_paper(source="doi:10.1126/sciadv.aay5901") for the publisher\'s') in text


def test_prompt_tells_the_model_to_stop_for_the_card():
    assert "end your reply instead of retrying" in agent_system(folder(""))
    assert "a card lets the user" not in agent_system(folder(""), {"web_read": False})


# ------------------------------------------- what real publishers do

def test_meta_refresh_landing_is_followed_to_the_refusing_publisher(web, fetch):
    """Elsevier's DOI landing (linkinghub) is a "Redirecting" page with a
    meta refresh and no text; its target, ScienceDirect, refuses. Before, the
    empty page ended the fetch with no card."""
    routes, _ = web
    doi, hub = "https://doi.org/10.1016/0031-9163(62)91369-0", "https://linkinghub.elsevier.com/retrieve/pii/0031916362913690"
    hop = "https://linkinghub.elsevier.com/retrieve/articleSelectSinglePerm?Redirect=x&key=k"
    article = "https://www.sciencedirect.com/science/article/pii/0031916362913690"
    routes[doi] = (302, {"Location": hub}, b"")
    routes[hub] = (200, {"Content-Type": "text/html"}, (
        b"<html><head><meta HTTP-EQUIV=\"REFRESH\" content=\"2; url='/retrieve/articleSelectSinglePerm"
        b"?Redirect=x&amp;key=k'\"/><title>Redirecting</title></head><body></body></html>"))
    routes[hop] = (302, {"Location": article}, b"")
    routes[article] = (403, {"Content-Type": "text/html"}, b"<html><title>Access denied</title></html>")
    text, action = fetch("doi:10.1016/0031-9163(62)91369-0")
    assert action["handoff"]["host"] == "www.sciencedirect.com"
    assert fetch_handoff.get(USER, action["handoff"]["id"])["url"] == article


def test_bot_check_host_opens_the_page_it_guards(web, fetch):
    """IOP sends server fetches to Radware's validate.perfdrive.com; the card
    opens the IOP article the check returns to (its ssc=), not the check."""
    routes, _ = web
    doi, article = "https://doi.org/10.1088/1361-6633/aa7e1a", "https://iopscience.iop.org/article/10.1088/1361-6633/aa7e1a"
    check = ("https://validate.perfdrive.com/fb80/?ssa=1&ssc=https%3A%2F%2Fiopscience.iop.org%2Farticle"
             "%2F10.1088%2F1361-6633%2Faa7e1a&ssk=botmanager_support@radware.com")
    routes[doi] = (302, {"Location": article}, b"")
    routes[article] = (302, {"Location": check}, b"")
    routes[check] = (200, {"Content-Type": "text/html"}, b"<html><body>" + b"<p>Please verify.</p>" * 200 + b"</body></html>")
    text, action = fetch("doi:10.1088/1361-6633/aa7e1a")
    assert action["handoff"]["wall"] == "captcha" and action["handoff"]["host"] == "iopscience.iop.org"
    assert fetch_handoff.get(USER, action["handoff"]["id"])["url"] == article


def test_arxiv_refusal_falls_back_to_its_export_host(web, fetch):
    """arxiv.org answers some PDFs with 406 to programs; export.arxiv.org,
    arXiv's host for automated clients, serves them."""
    routes, _ = web
    routes["https://arxiv.org/pdf/2401.04219"] = (406, {}, b"")
    routes["https://export.arxiv.org/pdf/2401.04219"] = (200, {"Content-Type": "application/pdf"}, _text_pdf([PDF_TEXT]))
    text, action = fetch("arXiv:2401.04219")
    assert PDF_TEXT in text and action["url"] == "https://export.arxiv.org/pdf/2401.04219"


def test_a_page_with_nothing_to_read_is_handed_to_the_browser(web, fetch):
    routes, _ = web
    page = "https://app.example.org/paper/42"
    routes[page] = (200, {"Content-Type": "text/html"}, b"<html><head><script src=app.js></script></head><body><div id=root></div></body></html>")
    text, action = fetch(page)
    assert action["handoff"]["wall"] == "script" and "only works in a browser" in text


def test_meta_refresh_parsing():
    from gamma.routers.pdf import meta_refresh
    base = "https://hub.example/retrieve/pii/1"
    assert meta_refresh("<meta http-equiv='refresh' content='0;URL=https://x.example/a'>", base) == "https://x.example/a"
    assert meta_refresh('<META content="5; url=/b?c=1&amp;d=2" HTTP-EQUIV="Refresh">', base) == "https://hub.example/b?c=1&d=2"
    assert meta_refresh('<meta http-equiv="refresh" content="30">', base) == ""  # a reload, not a redirect
    assert meta_refresh("<meta http-equiv='refresh' content='0;url=javascript:alert(1)'>", base) == ""
