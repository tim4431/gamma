"""The citation styles an account adds (gamma/routers/citations.py): from
the CSL repository (its fetches stood in for), with a dependent style's
parent bundled or fetched and a renamed style kept under its own id, or
from a file; what is no style refused before it is kept; the limit, the
server option, a repository out of reach, and no guest or integration
token."""

import json
import urllib.error

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from gamma.db import connect_users_db, page_now
from gamma.routers import citations

USER = "cs_owner"


def csl(style_id, title="A style", kind="author-date", parent="", locale="", citation=True) -> bytes:
    """A CSL style's XML: an independent one (with a citation layout unless
    ``citation`` is False) or, with ``parent``, a dependent one linking it."""
    link = f'<link href="http://www.zotero.org/styles/{parent}" rel="independent-parent"/>' if parent else ""
    layout = '<citation><layout><text variable="title"/></layout></citation>' if citation and not parent else ""
    lang = f' default-locale="{locale}"' if locale else ""
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" '
            f'version="1.0"{lang}><info><title>{title}</title><id>http://www.zotero.org/styles/{style_id}</id>{link}'
            f'<category citation-format="{kind}"/><updated>2024-01-01T00:00:00+00:00</updated></info>{layout}'
            "</style>").encode()


@pytest.fixture(scope="module")
def owner(client):
    make_user(USER, "pw")
    return login(USER, "pw")


@pytest.fixture
def repository(monkeypatch, owner):
    """The repository as a dict of id → XML (a missing id answers 404),
    every address asked recorded; the account starts with no styles."""
    served, asked = {}, []

    def fetch(url, limit):
        asked.append(url)
        if url == citations.INDEX_URL:
            return json.dumps(served.get("index", [])).encode()
        slug = url.removeprefix("https://www.zotero.org/styles/")
        if slug not in served:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return served[slug][:limit + 1]
    monkeypatch.setattr(citations, "_fetch", fetch)
    monkeypatch.setattr(citations, "_index", {"until": 0.0, "styles": [], "error": ""})
    with connect_users_db() as conn:
        conn.execute("DELETE FROM citation_styles WHERE user_id = (SELECT id FROM users WHERE username = ?)", (USER,))
        conn.commit()
    return served, asked


def _upload(client, data: bytes, name="style.csl"):
    return client.post("/api/citations/styles/upload", files={"file": (name, data, "application/xml")})


def test_a_repository_style_is_added_with_its_parent_served_and_removed(owner, repository):
    """A dependent style whose parent is bundled is kept alone, naming it;
    one whose parent is not is kept with that parent fetched, which is what
    its .csl serves, its own default locale beside it, as a download no
    browser renders in this origin (an uploaded style is any XML). The
    listing has no XML; a removed style is gone."""
    served, asked = repository
    served["nature-physics"] = csl("nature-physics", "Nature Physics", "numeric", parent="nature", locale="en-GB")
    served["journal-of-x"] = csl("journal-of-x", "Journal of X", parent="x-society", locale="de-DE")
    served["x-society"] = csl("x-society", "X Society", "note")
    r = owner.post("/api/citations/styles", json={"id": "nature-physics"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": "nature-physics", "title": "Nature Physics", "short_title": "", "kind": "numeric",
                        "locale": "en-GB", "parent": "nature", "source": "repository", "added_at": r.json()["added_at"]}
    assert owner.post("/api/citations/styles", json={"id": "journal-of-x"}).json()["parent"] == ""
    assert asked == ["https://www.zotero.org/styles/nature-physics", "https://www.zotero.org/styles/journal-of-x",
                     "https://www.zotero.org/styles/x-society"]
    listed = owner.get("/api/citations/styles").json()
    assert [s["id"] for s in listed["styles"]] == ["journal-of-x", "nature-physics"]  # by title
    assert listed["limit"] == 50 and listed["downloads"] is True and "xml" not in listed["styles"][0]
    alone = owner.get("/api/citations/styles/nature-physics.csl")
    assert alone.status_code == 200 and alone.content == served["nature-physics"]
    assert alone.headers["content-type"].startswith("application/vnd.citationstyles.style+xml")
    assert (alone.headers["x-csl-parent"], alone.headers["x-csl-locale"]) == ("nature", "en-GB")
    assert alone.headers["content-disposition"] == "attachment"
    assert alone.headers["content-security-policy"] == "default-src 'none'; sandbox"
    fetched = owner.get("/api/citations/styles/journal-of-x.csl")
    assert fetched.content == served["x-society"]
    assert (fetched.headers["x-csl-parent"], fetched.headers["x-csl-locale"]) == ("", "de-DE")
    assert owner.delete("/api/citations/styles/nature-physics").json() == {"ok": True}
    assert owner.get("/api/citations/styles/nature-physics.csl").status_code == 404
    assert owner.delete("/api/citations/styles/nature-physics").status_code == 404


def test_a_renamed_style_is_kept_under_the_id_it_has_now(owner, repository):
    """The repository redirects a renamed style: it is kept under its own
    id. One that turns out to be bundled is refused, as is one the
    repository has not."""
    served, _ = repository
    served["old-name"] = csl("new-name", "Renamed")
    served["vancouver"] = csl("nlm-citation-sequence", "NLM/Vancouver", "numeric")
    assert owner.post("/api/citations/styles", json={"id": "old-name"}).json()["id"] == "new-name"
    assert owner.get("/api/citations/styles/new-name.csl").status_code == 200
    bundled = owner.post("/api/citations/styles", json={"id": "vancouver"})
    assert bundled.status_code == 400 and "carries already" in bundled.json()["detail"]
    assert owner.post("/api/citations/styles", json={"id": "no-such-style"}).status_code == 404
    assert owner.post("/api/citations/styles", json={"id": "../etc"}).status_code == 400


@pytest.mark.parametrize("data, why", [
    (b"not xml at all", "not well-formed"),
    (b'<?xml version="1.0"?><!DOCTYPE style [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
     b'<style xmlns="http://purl.org/net/xbiblio/csl"><info><title>&b;</title></info></style>', "DOCTYPE"),
    (csl("plain").replace(b"http://purl.org/net/xbiblio/csl", b"http://example.org/other"), "root"),
    (csl("no-layout", citation=False), "citation layout"),
    (csl("elsewhere", parent="x").replace(b"http://www.zotero.org/styles/x", b"https://example.org/styles/x"),
     "not a style of the CSL repository"),
    (csl("big").replace(b"</style>", b"<!--" + b"x" * (512 * 1024) + b"--></style>"), "512 KB"),
], ids=["not-xml", "entities", "namespace", "no-layout", "foreign-parent", "too-large"])
def test_a_file_that_is_no_style_is_refused(owner, repository, data, why):
    r = _upload(owner, data)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert owner.get("/api/citations/styles").json()["styles"] == []


def test_a_file_is_added_and_the_fiftieth_style_is_the_last(owner, repository):
    """A style from a file (no fetch for a bundled parent, whose class it
    takes when it names none); one more past 50 is refused until one goes,
    and adding a style the account has again replaces it."""
    served, asked = repository
    r = _upload(owner, csl("my-style", "My style", "numeric", locale="fr-FR"))
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["source"], r.json()["locale"]) == ("my-style", "file", "fr-FR")
    r = _upload(owner, csl("my-ieee", "My IEEE", kind="", parent="ieee"))
    assert (r.json()["parent"], r.json()["kind"]) == ("ieee", "numeric") and asked == []
    with connect_users_db() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE username = ?", (USER,)).fetchone()[0]
        conn.executemany("INSERT INTO citation_styles (user_id, style_id, title, xml, added_at) "
                         "VALUES (?, ?, ?, '', ?)", [(user_id, f"filler-{i}", f"Filler {i}", page_now())
                                                     for i in range(48)])
        conn.commit()
    full = _upload(owner, csl("one-more"))
    assert full.status_code == 409 and "remove a style" in full.json()["detail"]
    assert _upload(owner, csl("my-style", "My style, edited")).json()["title"] == "My style, edited"
    owner.delete("/api/citations/styles/filler-0")
    assert _upload(owner, csl("one-more")).status_code == 200


def test_with_downloads_off_only_files_are_added(owner, repository, server_option):
    """The server option off: no search, no repository style, no file whose
    parent would have to be fetched; a file standing alone, or on a bundled
    parent, is added still."""
    served, asked = repository
    served["index"] = [{"name": "nature", "title": "Nature", "dependent": 0}]
    server_option("citation_styles", False)
    assert owner.get("/api/citations/styles").json()["downloads"] is False
    assert owner.get("/api/citations/repository", params={"q": "nature"}).status_code == 403
    assert owner.post("/api/citations/styles", json={"id": "nature-physics"}).status_code == 403
    assert _upload(owner, csl("journal-of-x", parent="x-society")).status_code == 403
    assert _upload(owner, csl("on-ieee", parent="ieee")).status_code == 200
    assert _upload(owner, csl("standalone")).status_code == 200
    assert asked == []


def test_the_repository_is_searched_in_its_index_fetched_once(owner, repository):
    """Every word of the query in a style's title, short title or id; the id
    or short title that is the query first, then titles starting with it,
    independent styles before dependent ones. The index is read once."""
    served, asked = repository
    served["index"] = [
        {"name": "nature-physics", "title": "Nature Physics", "dependent": 1, "categories": {"format": "numeric"},
         "updated": "2024-01-01 00:00:00"},
        {"name": "physical-review-a", "title": "Physical Review A", "titleShort": "PRA", "dependent": 1,
         "categories": {"format": "numeric"}},
        {"name": "nature", "title": "Nature", "dependent": 0, "categories": {"format": "numeric"}},
        {"name": "annals-of-nature-physics", "title": "Annals of nature physics", "dependent": 0},
        {"name": "Bad Name", "title": "Not an id"},
    ]
    found = owner.get("/api/citations/repository", params={"q": "Nature  physics"}).json()["results"]
    assert [s["id"] for s in found] == ["nature-physics", "annals-of-nature-physics"]
    assert found[0] == {"id": "nature-physics", "title": "Nature Physics", "short_title": "", "kind": "numeric",
                        "dependent": True, "updated": "2024-01-01 00:00:00"}
    assert [s["id"] for s in owner.get("/api/citations/repository", params={"q": "pra"}).json()["results"]] == [
        "physical-review-a"]
    assert owner.get("/api/citations/repository", params={"q": " "}).json()["results"] == []
    assert asked == [citations.INDEX_URL]


def test_a_chain_of_dependents_and_an_unreachable_repository_are_refused(owner, repository, monkeypatch):
    """A dependent style whose parent is dependent too has no layout to
    render with: refused, nothing kept. A repository out of reach is 502,
    and a failed read of its index is the answer for a while, so the
    searches queued behind it do not each wait out a fetch."""
    served, asked = repository
    served["a"] = csl("a", parent="b")
    served["b"] = csl("b", parent="c")
    r = owner.post("/api/citations/styles", json={"id": "a"})
    assert r.status_code == 400 and "dependent style too" in r.json()["detail"]
    assert owner.get("/api/citations/styles").json()["styles"] == []

    def unreachable(url, limit):
        asked.append(url)
        raise urllib.error.URLError("down")
    monkeypatch.setattr(citations, "_fetch", unreachable)
    asked.clear()
    assert owner.post("/api/citations/styles", json={"id": "x"}).status_code == 502
    assert [owner.get("/api/citations/repository", params={"q": "x"}).status_code for _ in range(2)] == [502, 502]
    assert asked.count(citations.INDEX_URL) == 1


def test_guests_and_integration_tokens_are_refused(owner, guest, repository):
    assert guest.get("/api/citations/styles").status_code == 403
    assert guest.post("/api/citations/styles/upload", files={"file": ("a.csl", csl("g"), "application/xml")}
                      ).status_code == 403
    token = token_for(owner, "write")
    script = fresh_client()
    script.headers["Authorization"] = f"Bearer {token}"
    assert script.get("/api/citations/styles").status_code == 403


def test_a_deleted_account_takes_its_styles(owner, repository):
    """The styles an account added go with it; another account's stay."""
    from gamma.workspaces import delete_account
    make_user("cs_gone", "pw")
    gone = login("cs_gone", "pw")
    assert _upload(gone, csl("gone-style", "Gone")).status_code == 200
    assert _upload(owner, csl("kept-style", "Kept")).status_code == 200
    delete_account(account_of("cs_gone"))
    with connect_users_db() as conn:
        left = conn.execute("SELECT style_id FROM citation_styles WHERE style_id IN ('gone-style', 'kept-style')")
        assert [row[0] for row in left] == ["kept-style"]
