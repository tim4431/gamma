"""What a path of this computer is to Gamma (``folder_links.where_is``,
``POST /api/folder-links/where``, docs/research/open-with-plan.md 3.1), for
the desktop app's *Open with Gamma*: a work folder's directories (with a
folder of their own or the nearest one's) and its files (tracked, not
tracked, not read yet, their page in Recently deleted), what its scan never
tracks and why (a dot directory, node_modules, two names alike but for
case, a link inside it), a link elsewhere to a file inside one, a vault
choosing or paused, a kept folder and a file not its own, a directory
around vaults, a vault of one file, another account's vault, a path that
names nothing, and the places no vault may have said as ``none``'s
refusal. And the page a work folder's file has (``GET
/api/folder-links/{id}/page?path=``), which the tracking dialog lands on."""

import os
from pathlib import Path

import pytest

from conftest import account_of, login, make_page, make_user, workspace_of
from gamma import config, folder_links, workspaces
from gamma.db import connect_pages_db
from gamma.ops import SYNC_CLIENT, trash_page
from gamma.vault import store as vault_store
from gamma.work_folders import selection
from test_vault_routes import _create, _page_of, _pdf, _put, _vault, disk  # noqa: F401: the disk fixture

OWNER = "ow_owner"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(scope="module")
def other(client):
    make_user("ow_other", "pw")
    return login("ow_other", "pw")


@pytest.fixture(scope="module")
def lab(owner):
    """A shared workspace of the owner's with an editor: ``(workspace,
    owner, editor)``, each asking in it."""
    info = workspaces.create("OW lab", account_of(OWNER), kind="shared")
    make_user("ow_bob", "pw")
    workspaces.set_member(info["id"], account_of("ow_bob"), "editor", by=account_of(OWNER))
    clients = [login(OWNER, "pw"), login("ow_bob", "pw")]
    for c in clients:
        c.params = {"ws": info["id"]}
    return info["id"], *clients


def _where(c, path) -> dict:
    r = c.post("/api/folder-links/where", json={"path": str(path)})
    assert r.status_code == 200, r.text
    return r.json()


def _word(link, name, **over) -> dict:
    """A vault as ``where`` names it, from the link the API answered."""
    return {"id": link["id"], "kind": "folder", "workspace": link["workspace_id"], "folder": link["folder_id"],
            "remote_url": link["remote_url"], "work": link["work"], "choosing": False, "paused": False, "mine": True,
            "name": name, **over}


def _link(root: Path, at: Path) -> None:
    """``root`` made a link to ``at``, or the test skipped where this
    platform makes none (Windows without the right)."""
    try:
        os.symlink(at, root, target_is_directory=at.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this platform makes no symbolic link here")


# --- a work folder ------------------------------------------------------------------------------

def test_a_work_folders_directories_are_its_folders_or_the_nearest_ones(owner, disk):
    root = disk / "OW work"
    link = _vault(owner, root, {"a.md": "a\n", "sub/b.md": "b\n"})
    _put(root / "sub" / "later" / "c.md", "c\n")                  # no round has read it: no folder of its own

    assert _where(owner, root) == {"is": "vault", "dir": True, "name": "OW work", "link": _word(link, "OW work"),
                                   "rel": "", "never": "", "folder": link["folder_id"], "exact": True}
    sub = _where(owner, root / "sub")
    assert sub["rel"] == "sub" and sub["exact"] is True and sub["never"] == ""
    assert sub["folder"] == vault_store.dirs(link["id"])["sub"]["folder_id"] != link["folder_id"]
    later = _where(owner, root / "sub" / "later")
    assert (later["rel"], later["folder"], later["exact"]) == ("sub/later", sub["folder"], False)
    assert folder_links.dir_folder(folder_links.get_link(link["id"]), "sub/later/deeper") == (sub["folder"], False)


def test_a_work_folders_files_say_their_page_and_whether_it_tracks_them(owner, disk):
    root = disk / "OW files"
    link = _vault(owner, root, {"a.md": "a\n", "paper.pdf": _pdf("OW a paper")})
    page = _page_of(link, "a.md")
    assert _where(owner, root / "a.md") == {"is": "vault", "dir": False, "name": "a.md", "link": _word(link, "OW files"),
                                            "rel": "a.md", "never": "", "page": page, "trashed": False, "tracked": True}
    assert _where(owner, root / "paper.pdf")["page"] == _page_of(link, "paper.pdf")
    _put(root / "later.md", "later\n")                              # tracked, not read yet
    later = _where(owner, root / "later.md")
    assert (later["page"], later["trashed"], later["tracked"]) == ("", False, True)
    _put(root / "data.csv", "a,b\n")                                # the default .gammaignore tracks no CSV
    csv = _where(owner, root / "data.csv")
    assert (csv["page"], csv["tracked"], csv["never"]) == ("", False, "")

    ws = link["workspace_id"]
    with connect_pages_db(ws) as conn:
        trash_page(ws, conn, page, actor=link["created_by"], client=SYNC_CLIENT)
    gone = _where(owner, root / "a.md")
    assert (gone["page"], gone["trashed"]) == ("", True)


def test_what_the_scan_never_tracks_is_said_with_why(owner, disk):
    root = disk / "OW never"
    _vault(owner, root, {"a.md": "a\n"})
    for rel in (".cache/x.md", "node_modules/m.md", "Notes.md", "notes.md", "~$lock.md"):
        _put(root / rel, "x\n")
    said = _where(owner, root / ".cache" / "x.md")
    assert said["never"] == "it is inside a dot directory" and said["tracked"] is False and said["page"] == ""
    assert _where(owner, root / ".cache")["never"] == "it is inside a dot directory"
    assert _where(owner, root / "node_modules" / "m.md")["never"] == "it is inside node_modules"
    assert selection.never_reason(root, "a.md") == ("", "")
    assert selection.never_reason(root, "~$lock.md") == ("unlisted", "Gamma never lists such a file")
    if {"Notes.md", "notes.md"} <= set(os.listdir(root)):         # two files only where names keep their case
        case = _where(owner, root / "Notes.md")
        assert case["never"] == "another name there differs from it only in case" and case["tracked"] is False
        assert selection.never_reason(root, "notes.md")[0] == "case"


def test_a_link_inside_a_work_folder_is_never_tracked_and_one_outside_to_its_file_is_that_file(owner, disk):
    root = disk / "OW linked"
    link = _vault(owner, root, {"a.md": "a\n"})
    elsewhere = disk / "OW elsewhere"
    _put(elsewhere / "e.md", "e\n")
    _link(root / "linked", elsewhere)
    said = _where(owner, root / "linked" / "e.md")
    assert (said["is"], said["rel"], said["never"]) == ("vault", "linked/e.md", "linked is a link or junction")
    assert said["tracked"] is False and said["page"] == ""
    assert _where(owner, root / "linked")["never"] == "linked is a link or junction"

    alias = disk / "OW desk" / "alias.md"
    alias.parent.mkdir(parents=True)
    _link(alias, root / "a.md")
    said = _where(owner, alias)
    assert (said["is"], said["rel"], said["page"], said["name"]) == ("vault", "a.md", _page_of(link, "a.md"), "alias.md")


def test_a_vault_choosing_or_paused_says_so(owner, disk):
    root = disk / "OW choosing"
    _put(root / "a.md", "a\n")
    choosing = _create(owner, path=str(root), new_folder=True, push="everything", choose=True)
    said = _where(owner, root)
    assert said["link"] == _word(choosing, "OW choosing", choosing=True, paused=True)
    assert _where(owner, root / "a.md")["tracked"] is True

    proot = disk / "OW paused"
    paused = _vault(owner, proot, {"a.md": "a\n"})
    owner.patch(f"/api/folder-links/{paused['id']}", json={"paused": True}).raise_for_status()
    _put(proot / "new.md", "new\n")
    said = _where(owner, proot / "new.md")
    assert said["link"]["paused"] is True and said["link"]["choosing"] is False
    assert (said["page"], said["tracked"]) == ("", True)
    r = owner.post("/api/folder-links/open", json={"path": str(proot / "new.md")})
    assert r.status_code == 400 and r.json()["detail"] == (
        f"{(proot / 'new.md').resolve()} is in “OW paused”, which is paused: resume it in the sync panel, then open "
        "the file again.")
    assert "new.md" not in vault_store.files(paused["id"])


def test_a_kept_folder_and_a_file_not_its_own(owner, disk):
    root = disk / "OW kept"
    link = _vault(owner, root, {"k.md": "k\n"}, push="off")
    said = _where(owner, root)
    assert said["link"]["work"] is False and (said["folder"], said["exact"]) == (link["folder_id"], True)
    loose = _put(root / "loose.tex", "\\section{Loose}\n")
    said = _where(owner, loose)
    assert (said["is"], said["page"], said["tracked"], said["never"]) == ("vault", "", False, "")
    r = owner.post("/api/folder-links/open", json={"path": str(loose)})
    assert r.status_code == 200 and r.json()["created"] is True and r.json()["format"] == "text"


# --- around vaults, a vault of one file, another account's -------------------------------------

def test_a_directory_around_vaults_lists_those_it_holds(owner, disk):
    one = _vault(owner, disk / "OW around one" / "inner", {"a.md": "a\n"})
    assert _where(owner, disk / "OW around one") == {"is": "around", "vaults": [
        {"id": one["id"], "workspace": one["workspace_id"], "folder": one["folder_id"], "remote_url": "", "work": True,
         "choosing": False, "mine": True, "name": "inner", "rel": "inner"}]}
    b = _vault(owner, disk / "OW around two" / "deep" / "b", {"b.md": "b\n"})
    a = _vault(owner, disk / "OW around two" / "a", {"a.md": "a\n"})
    said = _where(owner, disk / "OW around two")
    assert [(v["id"], v["rel"]) for v in said["vaults"]] == [(a["id"], "a"), (b["id"], "deep/b")]

    # a vault of one file is no vault around: making a work folder takes it in
    f = _put(disk / "OW around a file" / "f.pdf", _pdf("OW followed"))
    page = make_page(owner, "OW followed")["id"]
    _create(owner, kind="file", page=page, path=str(f))
    assert _where(owner, disk / "OW around a file") == {"is": "none", "dir": True, "name": "OW around a file",
                                                        "refusal": ""}
    assert _where(owner, f) == {"is": "file_vault", "workspace": workspace_of(OWNER), "page": page}


def test_another_accounts_vault_is_hidden(owner, other, disk):
    _vault(other, disk / "OW theirs" / "v", {"t.md": "t\n"})
    for path in (disk / "OW theirs" / "v", disk / "OW theirs" / "v" / "t.md", disk / "OW theirs"):
        assert _where(owner, path) == {"is": "hidden"}, path
    assert _where(other, disk / "OW theirs" / "v")["is"] == "vault"
    assert _where(other, disk / "OW theirs")["is"] == "around"


# --- what is refused ---------------------------------------------------------------------------

def test_a_path_that_names_nothing_is_refused_and_a_refused_place_is_none(owner, disk, monkeypatch):
    disk.mkdir(parents=True)
    for path, said in ((disk / "nothing here", "There is no file or directory"), ("relative/dir", "full path")):
        r = owner.post("/api/folder-links/where", json={"path": str(path)})
        assert r.status_code == 400 and said in r.json()["detail"], r.text

    monkeypatch.setattr(folder_links, "all_links", lambda: [])          # no vault anywhere, around none of them
    home = disk / "home" / "u"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    for place, said in ((config.DATA_DIR, folder_links.DATA_REFUSED), (Path(disk.anchor), folder_links.ROOT_REFUSED),
                        (home, folder_links.HOME_REFUSED), (home.parent, folder_links.HOME_REFUSED)):
        answer = _where(owner, place)
        assert (answer["is"], answer["dir"], answer["refusal"]) == ("none", True, said), place
    assert _where(owner, config.DATA_DIR / "users.db") == {"is": "none", "dir": False, "name": "users.db",
                                                          "refusal": folder_links.DATA_FILE_REFUSED}
    paper = _put(home / "paper" / "x.tex", "x\n")
    assert _where(owner, paper)["refusal"] == "" and _where(owner, paper.parent)["refusal"] == ""


# --- the page of a work folder's file ----------------------------------------------------------

def test_the_page_of_a_work_folders_file_is_its_rows(owner, lab, disk):
    root = disk / "OW landing"
    link = _vault(owner, root, {"a.md": "a\n"})
    url = f"/api/folder-links/{link['id']}/page"
    assert owner.get(url, params={"path": "a.md"}).json() == {"page": _page_of(link, "a.md")}
    _put(root / "later.md", "later\n")
    assert owner.get(url, params={"path": "later.md"}).json() == {"page": ""}      # before a round has read it
    for bad in ("../x.md", "a/../../x.md", "C:/x.md"):
        r = owner.get(url, params={"path": bad})
        assert r.status_code == 400, bad
    ws = link["workspace_id"]
    with connect_pages_db(ws) as conn:
        trash_page(ws, conn, _page_of(link, "a.md"), actor=link["created_by"], client=SYNC_CLIENT)
    assert owner.get(url, params={"path": "a.md"}).json() == {"page": ""}

    _ws, alice, bob = lab
    shared = _vault(alice, disk / "OW lab work", {"s.md": "s\n"})
    url = f"/api/folder-links/{shared['id']}/page"
    assert bob.get(url, params={"path": "s.md"}).status_code == 403             # its maker's alone, as the dialog
    assert alice.get(url, params={"path": "s.md"}).json() == {"page": _page_of(shared, "s.md")}
