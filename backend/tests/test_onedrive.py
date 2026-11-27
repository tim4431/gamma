"""OneDrive as a backup destination (gamma/onedrive.py, gamma/cloud_account.py,
docs/dev/backups.md "OneDrive") against an in-memory Microsoft
(tests/onedrive_fake.py): what Microsoft does differently from Google — a
public client that sends no secret, the sign-in page coming back to
``localhost``, refresh tokens rotated on every renewal and none revoked on
Disconnect, folders reached by path, upload sessions in 320 KiB multiples
without the token, downloads redirected elsewhere, deletes that skip the
recycle bin — and a backup task copying there through the same routes and
jobs as every store. What every cloud does alike is tests/test_gdrive.py's."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import account_of, fresh_client, login, make_user
from gamma import backup_schedule as tasks, cloud_account, cloud_http, db, onedrive, ws_backup
from gamma.cloud_http import CloudConfigError, CloudError
from gamma.routers.backup_tasks import TaskInput
from onedrive_fake import CLIENT_ID, FakeMicrosoft

ACCOUNT = cloud_account.ACCOUNTS["onedrive"]


@pytest.fixture
def microsoft(client, monkeypatch):
    """The fake Microsoft, Gamma's own app registered there."""
    fake = FakeMicrosoft().install(monkeypatch)
    monkeypatch.setattr(cloud_account.config, "ONEDRIVE_CLIENT_ID", CLIENT_ID)
    yield fake
    with db.connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('onedrive', 'onedrive_client')")
        conn.commit()


def _connect(microsoft, folder=None):
    ACCOUNT.start(folder)
    microsoft.approve()
    ACCOUNT._pending["next_poll_at"] = 0
    done = ACCOUNT.poll()
    assert done["connected"] and not done["error"], done
    return done


@pytest.fixture
def drive(microsoft):
    _connect(microsoft)
    return ACCOUNT.client()


def _saved_token():
    with db.connect_users_db() as conn:
        raw = conn.execute("SELECT value FROM settings WHERE key = 'onedrive'").fetchone()[0]
    return ACCOUNT._saved()["refresh_token"], raw


# --- connecting --------------------------------------------------------------------------

def test_a_build_without_microsofts_app_cannot_connect(client, monkeypatch):
    monkeypatch.setattr(cloud_account.config, "ONEDRIVE_CLIENT_ID", "")
    view = ACCOUNT.status()
    assert view["available"] is False and view["connected"] is False
    with pytest.raises(CloudConfigError, match="no app to sign in to OneDrive"):
        ACCOUNT.start()


def test_the_device_flow_connects_with_a_public_client(microsoft):
    shown = ACCOUNT.start()
    assert shown["pending"]["user_code"] == "MS000001"
    assert shown["pending"]["verification_url"] == "https://microsoft.com/devicelogin"
    ACCOUNT._pending["next_poll_at"] = 0
    assert ACCOUNT.poll()["pending"]
    microsoft.deny()
    ACCOUNT._pending["next_poll_at"] = 0
    assert ACCOUNT.poll()["error"] == "OneDrive access was not allowed."  # Microsoft's authorization_declined
    done = _connect(microsoft)
    # the short scope names Microsoft answers are the scope asked for; the address is the sign-in name
    assert (done["email"], done["folder"], done["revokes"]) == ("someone@outlook.com", "Gamma backups", False)
    token, raw = _saved_token()
    assert token and token not in raw and json.loads(raw)["client_id"] == CLIENT_ID
    microsoft.granted = "User.Read openid"  # a grant without the files
    ACCOUNT.start()
    microsoft.approve()
    ACCOUNT._pending["next_poll_at"] = 0
    assert ACCOUNT.poll()["error"] == "OneDrive access was not allowed."


def test_microsofts_page_comes_back_to_localhost(microsoft):
    """Gamma opened at 127.0.0.1 on its own computer: Microsoft's page sends
    the browser back to the path its app registered at ``localhost``, on
    the port Gamma was opened at (Microsoft does not match the port)."""
    make_user("od_page", "od-page-pw", is_admin=1)
    here = fresh_client(base_url="http://127.0.0.1:9100")
    assert here.post("/api/login", json={"username": "od_page", "password": "od-page-pw"}).status_code == 200
    assert here.get("/api/admin/clouds/onedrive").json()["browser"] is True
    url = here.post("/api/admin/clouds/onedrive/authorize", json={"folder": "Backups/Server A"}).json()["url"]
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == ["http://localhost:9100/api/admin/clouds/onedrive/callback"]
    assert query["scope"] == ["Files.ReadWrite User.Read offline_access"] and query["prompt"] == ["select_account"]
    back = urlsplit(microsoft.authorize(url))
    page = fresh_client(base_url="http://localhost:9100").get(f"{back.path}?{back.query}")
    assert page.status_code == 200 and "OneDrive is connected" in page.text
    view = here.get("/api/admin/clouds/onedrive").json()
    assert view["connected"] and view["folder"] == "Backups/Server A"


def test_refresh_tokens_rotate_and_disconnect_forgets_without_revoking(drive, microsoft):
    first, _ = _saved_token()
    microsoft.expire_access()
    drive.put_bytes("a.txt", b"a")  # a 401 renews: Microsoft answers a new refresh token, saved in place
    second, raw = _saved_token()
    assert second != first and microsoft.rotations == 1 and second not in raw
    microsoft.expire_access()
    ACCOUNT.client().get_bytes("a.txt")  # renewed with the new one
    assert microsoft.rotations == 2 and microsoft.refresh[second] is True
    calls = len(microsoft.calls)
    assert ACCOUNT.disconnect()["connected"] is False
    assert len(microsoft.calls) == calls  # nothing to call: the person removes the app from their account
    _connect(microsoft)
    with pytest.raises(CloudConfigError, match="Microsoft no longer honours this connection"):
        microsoft.revoke_all()
        ACCOUNT.client().get_bytes("a.txt")


def test_a_server_may_use_a_microsoft_app_of_its_own(microsoft, capsys):
    import manage
    for bad in (["onedrive", "not-a-guid"], ["onedrive", "--desktop", CLIENT_ID]):
        with pytest.raises(SystemExit):
            manage.cloud_client(bad)
    own = "99999999-8888-4777-8666-555555555555"
    manage.cloud_client(["onedrive", own])  # no secret: a public client
    assert "OneDrive uses this server's own app" in capsys.readouterr().out
    assert ACCOUNT.oauth_client().id == own and ACCOUNT.browser_client().id == own
    with pytest.raises(CloudConfigError, match="refused the OAuth client"):  # the stand-in does not know it
        ACCOUNT.start()
    manage.cloud_client(["onedrive", "--remove"])
    assert ACCOUNT.oauth_client().id == CLIENT_ID


def test_a_folders_name_is_one_onedrive_takes(microsoft):
    for bad in ("a:b", "what?", "trailing.", "a/b|c", ""):
        with pytest.raises(ValueError):
            ACCOUNT.check_folder(bad)
    assert ACCOUNT.check_folder(" Backups / Gamma ") == "Backups/Gamma"


# --- the client --------------------------------------------------------------------------

def test_files_live_in_folders_made_by_path(drive, microsoft, tmp_path):
    path = tmp_path / "one.zip"
    path.write_bytes(b"first")
    drive.put_object("snapshots/ws1/one.zip", path)
    drive.put_bytes("snapshots/ws1/workspace.json", b"{}")
    path.write_bytes(b"second, longer")
    drive.put_object("snapshots/ws1/one.zip", path)  # replaced, not doubled
    assert microsoft.files() == {"Gamma backups/snapshots/ws1/one.zip": b"second, longer",
                                 "Gamma backups/snapshots/ws1/workspace.json": b"{}"}
    assert [(k, s) for k, s, _ in ACCOUNT.client().list_objects("snapshots/")] == [
        ("snapshots/ws1/one.zip", 14), ("snapshots/ws1/workspace.json", 2)]
    out = tmp_path / "out" / "one.zip"
    assert drive.get_object("snapshots/ws1/one.zip", out) and out.read_bytes() == b"second, longer"
    assert drive.get_object("snapshots/ws1/gone.zip", out) is False and drive.get_bytes("x/y") is None
    drive.delete_object("snapshots/ws1/one.zip")  # for good: not to the recycle bin
    drive.delete_object("snapshots/ws1/one.zip")
    assert "Gamma backups/snapshots/ws1/one.zip" not in microsoft.files() and microsoft.recycled == []
    microsoft.permanent = False  # a personal OneDrive takes no permanent delete: the recycle bin
    drive.put_bytes("snapshots/ws1/two.zip", b"2")
    for name in ("workspace.json", "two.zip"):
        drive.delete_object(f"snapshots/ws1/{name}")
    assert microsoft.recycled == ["Gamma backups/snapshots/ws1/two.zip", "Gamma backups/snapshots/ws1/workspace.json"]
    assert sum("permanentDelete" in call for call in microsoft.calls) == 2, "one.zip's, then refused once and not asked again"
    assert drive.check() == (1, False)


def test_the_folder_browser_sees_every_folder_and_makes_them(drive, microsoft):
    microsoft.add("Documents/Taxes", folder=True)  # the person's own: Files.ReadWrite reaches it
    microsoft.add("Documents/notes.txt", b"hi")
    top = ACCOUNT.browse("")
    assert [f["name"] for f in top["folders"]] == ["Documents"] and not top["more"]
    held = ACCOUNT.browse("Documents")
    assert [f["name"] for f in held["folders"]] == ["Taxes"] and [(f["name"], f["size"]) for f in held["files"]] == [
        ("notes.txt", 2)]
    assert ACCOUNT.make_folder(" Documents / Backups / Gamma ") == {"path": "Documents/Backups/Gamma"}
    ACCOUNT.make_folder("Documents/Backups/Gamma")  # there already: kept
    ACCOUNT.set_folder("Documents/Backups/Gamma")
    ACCOUNT.client().put_bytes("snapshots/x.zip", b"x")
    assert "Documents/Backups/Gamma/snapshots/x.zip" in microsoft.files()
    microsoft.add("Blocked")  # a file where a folder should go
    with pytest.raises(CloudError, match="in the way"):
        ACCOUNT.make_folder("Blocked/inner")


def test_a_large_file_goes_in_an_upload_session_and_resumes(drive, microsoft, tmp_path, monkeypatch):
    monkeypatch.setattr(onedrive, "CHUNK", 2 * 320 * 1024)  # 640 KiB chunks
    data = bytes(range(256)) * 20000  # 5,120,000 bytes: past the one-PUT size, eight chunks
    path = tmp_path / "big.zip"
    path.write_bytes(data)
    ticks = []
    microsoft.fail_next("PUT /upload/", 0)       # a chunk gets no answer
    microsoft.fail_next("GET /upload/", 503)     # nor does asking where it stands, at first
    drive.put_object("big.zip", path, tick=ticks.append)
    assert microsoft.files()["Gamma backups/big.zip"] == data and sum(ticks) == len(data)
    assert len(microsoft.sleeps) == 2
    # a session Graph forgot starts over once
    real = microsoft._chunk
    forgot = {"n": 0}

    def forgetful(method, upload, headers, body):
        if forgot["n"] == 0 and method == "PUT":
            forgot["n"] += 1
            microsoft.sessions.pop(upload)
        return real(method, upload, headers, body)
    monkeypatch.setattr(microsoft, "_chunk", forgetful)
    path.write_bytes(data[::-1])
    drive.put_object("big.zip", path)
    assert microsoft.files()["Gamma backups/big.zip"] == data[::-1]


def test_throttling_waits_as_asked_and_refusals_say_why(drive, microsoft):
    microsoft.fail_next("GET /v1.0/me/drive/root", 429, retry_after=7)
    assert drive.check() == (0, False)
    assert microsoft.sleeps == [7]  # Graph's Retry-After, not the exponential pause
    microsoft.quota = 10
    with pytest.raises(CloudError, match="storage is full"):
        drive.put_bytes("b.txt", b"0123456789A")
    microsoft.quota = 0
    microsoft.fail_next("GET /v1.0/me/drive/root", 500, times=cloud_http.ATTEMPTS)
    with pytest.raises(CloudError, match="500"):
        drive.check()
    tested = ACCOUNT.test()
    assert tested["ok"] and tested["message"].startswith("someone@outlook.com: 0 items in Gamma backups")


# --- backup tasks and the routes ------------------------------------------------------------

@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks.config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(tasks, "_wake", lambda: None)


def test_a_task_copies_to_onedrive_and_the_copies_are_read_through_the_routes(drive, microsoft, scheduler):
    ws = make_user("od_admin", "od-admin-pw", is_admin=1)
    owner = account_of("od_admin")
    task = tasks.save(owner, TaskInput(name="To OneDrive", workspaces=[ws], uploads=False, onedrive=True,
                                       retention_mode="count", retention_value=1).model_dump())
    for _ in range(2):
        tasks.mutate(owner, task["id"], "run")
        tasks.run_due()
    done = tasks.read(task["id"])
    assert done["state"] == "finished", done["last_error"]
    assert done["last_result"]["onedrive"] == 1 and "drive" not in done["last_result"]
    mine = [b["name"] for b in ws_backup.list_backups(ws) if b["task_id"] == task["id"]]
    assert sorted(p.rsplit("/", 1)[1] for p in microsoft.files() if f"/{ws}/" in p) == sorted(
        [f"{mine[0]}.zip", "workspace.json"])

    boss = login("od_admin", "od-admin-pw")
    assert boss.get("/api/admin/clouds/dropbox").status_code == 404
    groups = boss.get("/api/admin/offsite/snapshots?store=onedrive").json()["groups"]
    assert [(g["workspace"], len(g["snapshots"])) for g in groups] == [(ws, 1)]
    assert boss.delete(f"/api/admin/offsite/snapshots/{ws}/{mine[0]}?store=onedrive").json() == {"ok": True}
    assert not [p for p in microsoft.files() if p.endswith(f"{mine[0]}.zip")]
    assert boss.put("/api/admin/clouds/onedrive", json={"folder": "a:b"}).status_code == 400
    assert boss.delete("/api/admin/clouds/onedrive").json()["connected"] is False
    with pytest.raises(tasks.TaskError, match="OneDrive is not connected"):
        tasks.save(owner, {**TaskInput(name="Again", workspaces=[ws], onedrive=True).model_dump()})
