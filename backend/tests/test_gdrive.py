"""Google Drive as a backup destination (gamma/gdrive.py,
gamma/cloud_account.py, docs/dev/debugging.md "Google Drive") against an
in-memory Google (tests/gdrive_fake.py), and through it what every cloud
connection does alike: connecting through the device flow or the sign-in
page with Gamma's own app (or a server's own), the client's files (folders
made as needed, a name replaced rather than doubled, chunked and resumed
uploads, renewed tokens, Google's refusals), a backup task copying its
snapshots there and pruning them, and the admin's routes: the connection,
the copies' listing, download, restore and delete; and the clouds'
transport itself (gamma/cloud_http.py)."""

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import account_of, fresh_client, login, make_page, make_user, workspace_of
from gamma import backup_schedule as tasks, cloud_account, cloud_http, db, gdrive, snapshot_copies, workspaces, ws_backup
from gamma.cloud_http import CloudConfigError, CloudError, CloudUnreachable
from gamma.publisher_sessions import seal
from gamma.server_settings import _set_raw
from gamma.routers.backup_tasks import TaskInput
from gdrive_fake import CLIENT_ID, CLIENT_SECRET, DESKTOP_ID, DESKTOP_SECRET, FakeGoogle


@pytest.fixture
def google(client, monkeypatch):
    """The fake Google, Gamma's own app (its two clients) registered there."""
    fake = FakeGoogle().install(monkeypatch)
    monkeypatch.setattr(cloud_account.config, "GOOGLE_DRIVE_CLIENT", (CLIENT_ID, CLIENT_SECRET))
    monkeypatch.setattr(cloud_account.config, "GOOGLE_DRIVE_DESKTOP_CLIENT", (DESKTOP_ID, DESKTOP_SECRET))
    yield fake
    with db.connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('gdrive', 'gdrive_client', 'gdrive_desktop_client')")
        conn.commit()


ACCOUNT = cloud_account.ACCOUNTS["drive"]
DEFAULT_FOLDER = "Gamma backups"


def _due():
    """The interval Google asked for has passed."""
    ACCOUNT._pending["next_poll_at"] = 0


def _connect(google, folder=DEFAULT_FOLDER):
    ACCOUNT.start(folder)
    google.approve()
    _due()
    done = ACCOUNT.poll()
    assert done["connected"] and not done["error"], done
    return done


@pytest.fixture
def drive(client, google):
    """A connected Drive and its client."""
    _connect(google)
    return ACCOUNT.client()


# --- connecting --------------------------------------------------------------------------

def test_the_device_flow_connects_and_keeps_the_token_sealed(client, google):
    assert ACCOUNT.status()["connected"] is False
    shown = ACCOUNT.start("My backups")
    assert shown["pending"]["user_code"] == "ABCD-0001"
    assert shown["pending"]["verification_url"] == "https://www.google.com/device"
    asked = len(google.calls)
    assert ACCOUNT.poll()["pending"]  # before Google's interval: nothing asked
    assert len(google.calls) == asked
    _due()
    assert ACCOUNT.poll()["pending"]  # asked: not entered yet
    assert google.calls[-1] == "POST /token"
    google.approve()
    _due()
    done = ACCOUNT.poll()
    assert done["connected"] and done["pending"] is None and done["error"] == ""
    assert (done["email"], done["folder"], done["problem"]) == ("someone@gmail.com", "My backups", "")
    with db.connect_users_db() as conn:
        raw = conn.execute("SELECT value FROM settings WHERE key = 'gdrive'").fetchone()[0]
    token = next(t for t in google.refresh)
    assert token not in raw and json.loads(raw)["client_id"] == "fake-client.apps.googleusercontent.com"
    assert ACCOUNT.ready()


def test_a_slow_down_waits_longer_and_a_refusal_or_expiry_ends_it(client, google):
    ACCOUNT.start()
    google.fail_next("POST /token", 429)
    _due()
    assert ACCOUNT.poll()["pending"]["interval"] == 10  # Google's 5, and 5 more
    google.fail_next("POST /token", 0)  # no answer: the code stays
    _due()
    assert ACCOUNT.poll()["pending"]
    google.deny()
    _due()
    ended = ACCOUNT.poll()
    assert ended["pending"] is None and ended["connected"] is False
    assert ended["error"] == "Google Drive access was not allowed."
    ACCOUNT.start()
    assert ACCOUNT.status()["error"] == ""  # a new connection clears it
    google.expire()
    _due()
    assert "expired" in ACCOUNT.poll()["error"]
    google.granted = "openid"  # the person unticked Drive on Google's page
    ACCOUNT.start()
    google.approve()
    _due()
    assert ACCOUNT.poll()["error"] == "Google Drive access was not allowed."


def test_reconnecting_and_disconnecting_revoke_the_old_token(client, google):
    _connect(google)
    first = next(t for t, ok in google.refresh.items() if ok)
    _connect(google, "Elsewhere")
    assert google.refresh[first] is False  # revoked
    assert ACCOUNT.status()["folder"] == "Elsewhere"
    second = next(t for t, ok in google.refresh.items() if ok)
    off = ACCOUNT.disconnect()
    assert off["connected"] is False and google.refresh[second] is False
    with pytest.raises(CloudConfigError, match="not connected"):
        ACCOUNT.client()


def test_a_connection_through_another_client_or_none_cannot_be_used(client, google, monkeypatch):
    _connect(google)
    # a code answered just as the client changed: the connection names the old one
    _set_raw("gdrive_client", json.dumps({
        "client_id": "another.apps.googleusercontent.com", "client_secret": seal("GOCSPX-o")}))
    assert "through another OAuth client" in ACCOUNT.status()["problem"]
    assert not ACCOUNT.ready()
    _set_raw("gdrive_client", "")
    monkeypatch.setattr(cloud_account.config, "GOOGLE_DRIVE_CLIENT", ("", ""))  # a build without Gamma's app
    monkeypatch.setattr(cloud_account.config, "GOOGLE_DRIVE_DESKTOP_CLIENT", ("", ""))
    view = ACCOUNT.status()
    assert view["available"] is False and "no app to sign in to Google Drive" in view["problem"]
    with pytest.raises(CloudConfigError, match="no app to sign in"):
        ACCOUNT.start()


def test_a_server_may_use_a_google_app_of_its_own(client, google, capsys):
    """Gamma's own app by default; one given with manage.py cloud-client
    drive in its place, its secret sealed; a change of app disconnects
    (revoking the token); --remove goes back to Gamma's."""
    import manage
    _connect(google)  # through Gamma's app
    token = next(t for t, ok in google.refresh.items() if ok)
    for bad in (["drive", "", "s"], ["drive", "123-abc", "s"], ["drive", CLIENT_ID, "a b"], ["drive", CLIENT_ID],
                ["dropbox", CLIENT_ID, "s"], []):
        with pytest.raises(SystemExit):
            manage.cloud_client(bad)
    own = "own.apps.googleusercontent.com"
    manage.cloud_client(["drive", own, "GOCSPX-own"])
    assert "this server's own app" in capsys.readouterr().out
    assert ACCOUNT.status()["connected"] is False and google.refresh[token] is False
    with db.connect_users_db() as conn:
        assert "GOCSPX-own" not in conn.execute("SELECT value FROM settings WHERE key = 'gdrive_client'").fetchone()[0]
    assert ACCOUNT.oauth_client().id == own
    with pytest.raises(CloudConfigError, match="refused"):  # the stand-in Google does not know it
        ACCOUNT.start()
    manage.cloud_client(["drive", "--remove"])
    assert ACCOUNT.oauth_client().id == CLIENT_ID  # Gamma's again
    assert ACCOUNT.start()["pending"]


def test_googles_page_signs_in_where_gamma_is_opened_on_its_own_computer(client, google):
    """Gamma opened at a loopback address (the desktop app): Connect… opens
    Google's page, which sends the browser back to the callback with a code,
    traded with its PKCE verifier; the connection is made through Gamma's
    "Desktop app" client and renewed by it. Opened elsewhere, Gamma connects
    with a code. A used sign-in or a refusal says why."""
    make_user("gd_page", "gd-page-pw", is_admin=1)

    def admin_at(base):
        c = fresh_client(base_url=base)
        assert c.post("/api/login", json={"username": "gd_page", "password": "gd-page-pw"}).status_code == 200
        return c

    def browser(location):  # the system browser Google sends back: no Gamma session
        back = urlsplit(location)
        return fresh_client(base_url="http://127.0.0.1:9100").get(f"{back.path}?{back.query}")

    far, here = admin_at("http://nas.example:9001"), admin_at("http://127.0.0.1:9100")
    assert far.get("/api/admin/clouds/drive").json()["browser"] is False
    assert far.post("/api/admin/clouds/drive/authorize", json={}).status_code == 400
    assert here.get("/api/admin/clouds/drive").json()["browser"] is True
    url = here.post("/api/admin/clouds/drive/authorize", json={"folder": "From the page"}).json()["url"]
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{google.base}/o/oauth2/v2/auth?") and query["client_id"] == [DESKTOP_ID]
    assert query["redirect_uri"] == ["http://127.0.0.1:9100/api/admin/clouds/drive/callback"]
    assert query["scope"] == [gdrive.SCOPE] and query["access_type"] == ["offline"]
    location = google.authorize(url)  # the person allows
    page = browser(location)
    assert page.status_code == 200 and "Google Drive is connected" in page.text
    view = here.get("/api/admin/clouds/drive").json()
    assert view["connected"] and (view["email"], view["folder"], view["error"]) == ("someone@gmail.com", "From the page", "")
    google.expire_access()
    assert ACCOUNT.client().check() == (0, False)  # renewed through the "Desktop app" client
    assert "already used" in browser(location).text
    google.browser_deny = True
    assert "not allowed" in browser(google.authorize(here.post("/api/admin/clouds/drive/authorize", json={}).json()["url"])).text
    assert here.get("/api/admin/clouds/drive").json()["error"] == "Google Drive access was not allowed."


def test_a_folder_is_a_path_of_names_from_the_top(client, google):
    for bad in ("", "   ", " / ", "a\\b", "a/../b", "a\x7fb", "x" * 121, "/".join("a" * 17)):
        with pytest.raises(ValueError):
            ACCOUNT.start(bad)
    assert ACCOUNT.check_folder("  Gamma   backups ") == "Gamma backups"
    assert ACCOUNT.check_folder("/ Backups //  Gamma / ") == "Backups/Gamma"


# --- the client --------------------------------------------------------------------------

def test_files_live_in_folders_and_a_name_is_replaced_not_doubled(drive, google, tmp_path):
    path = tmp_path / "one.zip"
    path.write_bytes(b"first")
    drive.put_object("snapshots/ws1/one.zip", path)
    drive.put_bytes("snapshots/ws1/workspace.json", b"{}")
    path.write_bytes(b"second, longer")
    drive.put_object("snapshots/ws1/one.zip", path)
    drive.put_bytes("snapshots/ws2/two.zip", b"2")
    assert google.mine() == {"Gamma backups/snapshots/ws1/one.zip": b"second, longer",
                             "Gamma backups/snapshots/ws1/workspace.json": b"{}",
                             "Gamma backups/snapshots/ws2/two.zip": b"2"}
    folders = [f for f in google.files.values() if f["mimeType"] == gdrive.FOLDER_MIME]
    assert sorted(google.path(f["id"]) for f in folders) == [
        "Gamma backups", "Gamma backups/snapshots", "Gamma backups/snapshots/ws1", "Gamma backups/snapshots/ws2"]
    listed = ACCOUNT.client().list_objects("snapshots/")  # a new client finds the same folders
    assert [(k, s) for k, s, _ in listed] == [("snapshots/ws1/one.zip", 14), ("snapshots/ws1/workspace.json", 2),
                                              ("snapshots/ws2/two.zip", 1)]
    assert [k for k, *_ in drive.list_objects("snapshots/ws1/w")] == ["snapshots/ws1/workspace.json"]
    assert drive.list_objects("nothing/") == []
    out = tmp_path / "out" / "one.zip"
    assert drive.get_object("snapshots/ws1/one.zip", out) and out.read_bytes() == b"second, longer"
    assert drive.get_object("snapshots/ws1/gone.zip", out) is False
    assert drive.get_bytes("snapshots/ws1/workspace.json") == b"{}" and drive.get_bytes("x/y") is None
    drive.delete_object("snapshots/ws1/one.zip")
    drive.delete_object("snapshots/ws1/one.zip")  # already gone: no error
    assert "Gamma backups/snapshots/ws1/one.zip" not in google.mine()
    assert drive.check() == (1, False)  # snapshots/
    with pytest.raises(ValueError):
        drive.put_bytes("../escape", b"")
    with pytest.raises(FileNotFoundError):
        drive.put_object("snapshots/x.zip", tmp_path / "missing.zip")


def test_what_the_person_made_or_trashed_is_not_used(drive, google):
    google.add("Gamma backups", folder=True, mine=False)  # theirs: drive.file does not reach it
    drive.put_bytes("a.txt", b"a")
    ours = [f for f in google.files.values() if f["name"] == "Gamma backups" and f["mine"]]
    assert len(ours) == 1
    google.trash(ours[0]["id"])  # the person trashed the folder
    fresh = ACCOUNT.client()
    assert fresh.list_objects("") == [] and fresh.check() == (0, False)
    fresh.put_bytes("a.txt", b"again")
    assert google.mine() == {"Gamma backups/a.txt": b"again"}


def test_a_large_file_goes_in_chunks_and_resumes(drive, google, tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "CHUNK", 256 * 1024)
    data = bytes(range(256)) * 2600  # 665,600 bytes: three chunks
    path = tmp_path / "big.zip"
    path.write_bytes(data)
    ticks = []
    google.fail_next("PUT /upload/session", 0)        # the first chunk gets no answer
    google.fail_next("PUT /upload/session", 503)      # its resend a 503
    drive.put_object("big.zip", path, tick=ticks.append)
    assert google.mine()["Gamma backups/big.zip"] == data
    assert sum(ticks) == len(data)
    assert len(google.sleeps) == 2
    # a session Drive forgot starts over once
    real = google._chunk
    forgot = {"n": 0}

    def forgetful(upload, headers, body):
        if forgot["n"] == 0:
            forgot["n"] += 1
            google.sessions.pop(upload)
        return real(upload, headers, body)
    monkeypatch.setattr(google, "_chunk", forgetful)
    drive.put_object("big.zip", path)
    assert google.mine()["Gamma backups/big.zip"] == data


def test_tokens_are_renewed_and_refusals_say_why(drive, google, tmp_path):
    google.expire_access()
    drive.put_bytes("a.txt", b"a")  # a 401 renews the access token once
    assert google.calls.count("POST /token") >= 2
    google.fail_next("GET /drive/v3/files", 429, times=2)
    assert drive.get_bytes("a.txt") == b"a"
    google.fail_next("GET /drive/v3/files", 500, times=cloud_http.ATTEMPTS)
    with pytest.raises(CloudError, match="500"):
        drive.get_bytes("a.txt")
    google.quota = 10
    with pytest.raises(CloudError, match="storage is full"):
        drive.put_bytes("b.txt", b"0123456789")
    google.quota = 0
    google.revoke_all()
    with pytest.raises(CloudConfigError, match="no longer honours"):
        ACCOUNT.client().get_bytes("a.txt")


# --- backup tasks ------------------------------------------------------------------------

@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks.config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(tasks, "_wake", lambda: None)


def _task(owner, ws, **changes):
    data = TaskInput(**{"name": "To Drive", "workspaces": [ws], "uploads": False, "drive": True,
                        **changes}).model_dump()
    return tasks.save(owner, data)


def _run(owner, task):
    tasks.mutate(owner, task["id"], "run")
    tasks.run_due()
    return tasks.read(task["id"])


def _copies(google, ws):
    prefix = f"Gamma backups/snapshots/{ws}/"
    return sorted(p[len(prefix):] for p in google.mine() if p.startswith(prefix))


def test_a_task_copies_its_snapshots_to_drive_and_prunes_them(drive, google, scheduler):
    ws = make_user("gd_admin", "gd-admin-pw", is_admin=1)
    owner = account_of("gd_admin")
    task = _task(owner, ws, retention_mode="count", retention_value=2)
    for _ in range(3):
        done = _run(owner, task)
        assert done["state"] == "finished", done["last_error"]
        assert done["last_result"]["drive"] == 1 and "bucket" not in done["last_result"]
    mine = sorted(b["name"] + ".zip" for b in ws_backup.list_backups(ws) if b["task_id"] == task["id"])
    assert len(mine) == 2 and _copies(google, ws) == sorted(mine + ["workspace.json"])
    about = json.loads(google.mine()[f"Gamma backups/snapshots/{ws}/workspace.json"])
    assert about == {"id": ws, "name": workspaces.get(ws)["name"], "kind": "personal"}
    assert google.mine()[f"Gamma backups/snapshots/{ws}/{mine[0]}"] == (ws_backup.store_dir(ws) / mine[0]).read_bytes()


def test_who_may_copy_to_drive_and_a_copy_that_fails(client, google, scheduler):
    ws = make_user("gd_admin2", "gd-admin2-pw", is_admin=1)
    owner = account_of("gd_admin2")
    with pytest.raises(tasks.TaskError, match="Google Drive is not connected"):
        _task(owner, ws)
    _connect(google)
    member = make_user("gd_member", "gd-member-pw")
    with pytest.raises(tasks.TaskError, match="Only a server admin's backup tasks can copy to Google Drive"):
        _task(account_of("gd_member"), member)
    with pytest.raises(tasks.TaskError, match="No bucket is set up"):  # both: each must be set up
        _task(owner, ws, bucket=True)
    task = _task(owner, ws)
    google.quota = 1
    done = _run(owner, task)
    assert done["state"] == "failed" and done["next_run"] == task["next_run"]
    assert done["last_error"].startswith("The snapshots were taken, but copying them to Google Drive failed")
    assert "storage is full" in done["last_error"]
    assert [b for b in ws_backup.list_backups(ws) if b["task_id"] == task["id"]]


# --- the admin's routes ------------------------------------------------------------------

def test_the_connection_through_the_api(client, google):
    make_user("gd_boss", "gd-boss-pw", is_admin=1)
    make_user("gd_user", "gd-user-pw")
    boss, user = login("gd_boss", "gd-boss-pw"), login("gd_user", "gd-user-pw")
    assert user.get("/api/admin/clouds/drive").status_code == 403
    assert boss.get("/api/admin/clouds/drive").json()["available"] is True
    assert boss.post("/api/admin/clouds/drive/connect", json={"folder": "a/../b"}).status_code == 400
    shown = boss.post("/api/admin/clouds/drive/connect", json={}).json()
    assert shown["pending"]["user_code"] and boss.get("/api/admin/clouds/drive").json()["pending"]
    assert boss.delete("/api/admin/clouds/drive/connect").json()["pending"] is None
    boss.post("/api/admin/clouds/drive/connect", json={"folder": "Server A"})
    google.approve()
    _due()
    done = boss.post("/api/admin/clouds/drive/poll").json()
    assert done["connected"] and done["folder"] == "Server A"
    assert "refresh" not in json.dumps(done)
    tested = boss.post("/api/admin/clouds/drive/test").json()
    assert tested == {"ok": True, "message": "someone@gmail.com: 0 items in Server A"}
    assert boss.put("/api/admin/clouds/drive", json={"folder": "Server B"}).json()["folder"] == "Server B"
    assert boss.put("/api/admin/clouds/drive", json={"folder": ""}).status_code == 400
    assert boss.delete("/api/admin/clouds/drive").json()["connected"] is False
    assert boss.put("/api/admin/clouds/drive", json={"folder": "Server C"}).status_code == 400


def test_the_folder_browser_lists_what_gamma_made_and_makes_folders(drive, google):
    make_user("gd_picker", "gd-picker-pw", is_admin=1)
    make_user("gd_member", "gd-member-pw")
    boss = login("gd_picker", "gd-picker-pw")
    assert login("gd_member", "gd-member-pw").get("/api/admin/clouds/drive/browse").status_code == 403
    drive.put_bytes("snapshots/ws1/one.zip", b"12345")
    google.add("Gamma backups", folder=True)  # a second of one name: the first is the one used
    top = boss.get("/api/admin/clouds/drive/browse").json()
    assert [f["name"] for f in top["folders"]] == ["Gamma backups"] and top["files"] == [] and not top["more"]
    held = boss.get("/api/admin/clouds/drive/browse", params={"path": "Gamma backups/snapshots/ws1"}).json()
    assert [(f["name"], f["size"]) for f in held["files"]] == [("one.zip", 5)] and held["files"][0]["modified"]
    assert boss.get("/api/admin/clouds/drive/browse", params={"path": "Not there"}).json() == {
        "folders": [], "files": [], "more": False}
    assert boss.get("/api/admin/clouds/drive/browse", params={"path": "a/../b"}).status_code == 400

    # New folder, then the folder copies go to: a folder in a folder.
    assert boss.post("/api/admin/clouds/drive/folders", json={"path": " Backups / Server A "}).json() == {
        "path": "Backups/Server A"}
    boss.post("/api/admin/clouds/drive/folders", json={"path": "Backups/Server A"})  # there already: kept, not doubled
    assert boss.post("/api/admin/clouds/drive/folders", json={"path": "/"}).status_code == 400  # never My Drive itself
    made = [google.path(f["id"]) for f in google.files.values() if f["mimeType"] == gdrive.FOLDER_MIME and f["mine"]]
    assert made.count("Backups/Server A") == 1
    assert boss.put("/api/admin/clouds/drive", json={"folder": "Backups/Server A"}).json()["folder"] == "Backups/Server A"
    assert boss.post("/api/admin/clouds/drive/test").json()["message"] == "someone@gmail.com: 0 items in Backups › Server A"
    ACCOUNT.client().put_bytes("snapshots/ws1/two.zip", b"2")
    assert "Backups/Server A/snapshots/ws1/two.zip" in google.mine()
    assert [f["name"] for f in boss.get("/api/admin/clouds/drive/browse", params={"path": "Backups"}).json()["folders"]] == [
        "Server A"]
    ACCOUNT.disconnect()
    assert boss.get("/api/admin/clouds/drive/browse").status_code == 400


def _job(c, route, body):
    from gamma import jobs
    r = c.post(f"/api/jobs/{route}", json=body)
    assert r.status_code == 200, r.text
    return jobs.wait(r.json()["id"])


def test_the_copies_in_drive_through_the_api(drive, google):
    make_user("gd_chief", "gd-chief-pw", is_admin=1)
    boss = login("gd_chief", "gd-chief-pw")
    ws = workspace_of("gd_chief")
    make_page(boss, "In the Drive copy")
    snap = ws_backup.create(ws, label="task-0123456789ab", uploads=False, scheduled=True,
                            task_id="0123456789ab" + "0" * 20)
    snapshot_copies.upload(snapshot_copies.client("drive"), ws, snap["name"])
    zipped = (ws_backup.store_dir(ws) / f"{snap['name']}.zip").read_bytes()
    gone = workspaces.new_workspace_id()  # a workspace this server lost
    drive.put_bytes(f"snapshots/{gone}/{snap['name']}.zip", zipped)
    drive.put_bytes(f"snapshots/{gone}/workspace.json", b'{"name": "Gone"}')

    groups = boss.get("/api/admin/offsite/snapshots?store=drive").json()["groups"]
    assert [(g["workspace"], g["here"]) for g in groups] == [(ws, True), (gone, False)]
    assert groups[1]["name"] == "Gone" and groups[0]["snapshots"][0]["task"] == "0123456789ab"
    assert boss.get("/api/admin/offsite/snapshots?store=elsewhere").status_code == 404
    assert boss.get("/api/admin/offsite/snapshots").status_code == 400  # no bucket set up

    job = _job(boss, "bucket-download", {"ws": ws, "name": snap["name"], "store": "drive"})
    assert job["state"] == "done", job["error"]
    assert job["params"]["store"] == "drive"
    assert boss.get(f"/api/jobs/{job['id']}/download").content == zipped
    assert boss.post("/api/jobs/bucket-download", json={"ws": ws, "name": snap["name"],
                                                         "store": "elsewhere"}).status_code == 404

    job = _job(boss, "bucket-restore", {"ws": gone, "name": snap["name"], "into": "new", "store": "drive"})
    assert job["state"] == "done", job["error"]
    assert job["params"]["source"] == "drive" and job["result"]["workspace"] == gone
    assert workspaces.role_of(gone, account_of("gd_chief")) == "owner"

    assert boss.delete(f"/api/admin/offsite/snapshots/{ws}/{snap['name']}?store=drive").json() == {"ok": True}
    assert _copies(google, ws) == ["workspace.json"]
    ACCOUNT.disconnect()
    assert boss.get("/api/admin/offsite/snapshots?store=drive").status_code == 400
    assert boss.post("/api/jobs/bucket-download", json={"ws": ws, "name": snap["name"],
                                                         "store": "drive"}).status_code == 400


def test_several_copies_come_back_as_new_workspaces_in_one_job(drive, google):
    """Cloud copies' Restore all: the workspaces this server lacks, each
    from its copy, in one job; a copy that cannot be restored is reported,
    its workspace goes again, and the rest go on."""
    make_user("gd_many", "gd-many-pw", is_admin=1)
    make_user("gd_many_member", "gd-many-member-pw")
    boss, member = login("gd_many", "gd-many-pw"), login("gd_many_member", "gd-many-member-pw")
    ws = workspace_of("gd_many")
    make_page(boss, "Brought back")
    snap = ws_backup.create(ws, label="manual", uploads=False)
    zipped = (ws_backup.store_dir(ws) / f"{snap['name']}.zip").read_bytes()
    lost = [workspaces.new_workspace_id() for _ in range(2)]
    broken = workspaces.new_workspace_id()
    for gone in lost:
        drive.put_bytes(f"snapshots/{gone}/{snap['name']}.zip", zipped)
    drive.put_bytes(f"snapshots/{broken}/{snap['name']}.zip", b"not a zip")
    items = [{"ws": w, "name": snap["name"], "store": "drive", "label": f"Lost {i}"}
             for i, w in enumerate(lost + [broken])]
    assert member.post("/api/jobs/bucket-restore-many", json={"items": items}).status_code == 403
    assert boss.post("/api/jobs/bucket-restore-many", json={"items": items + items[:1]}).status_code == 400

    job = _job(boss, "bucket-restore-many", {"items": items})
    assert job["state"] == "done", job["error"]
    assert job["kind"] == "copies-restore" and job["params"]["count"] == 3
    assert [r["workspace"] for r in job["result"]["restored"]] == lost
    assert [(f["ws"], f["name"]) for f in job["result"]["failed"]] == [(broken, "Lost 2")]
    assert all(workspaces.role_of(w, account_of("gd_many")) == "owner" for w in lost)
    assert not workspaces.get(broken)  # its workspace went again
    job = _job(boss, "bucket-restore-many", {"items": [items[2]]})
    assert job["state"] == "failed" and "no copy could be restored: Lost 2" in job["error"]


# --- the transport ---------------------------------------------------------------------

def test_the_transport_answers_streams_and_drops_the_token_on_another_host(monkeypatch):
    """``cloud_http._request`` itself, against a local server: an answer, a body
    streamed into a sink, an error answer and a 308 as answers, a redirect
    to another host without the Authorization header, no server at all."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen[self.path] = self.headers.get("Authorization")
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/landed")
                self.end_headers()
                return
            body = {"/big": b"x" * 3_000_000, "/missing": b'{"error": {"errors": [{"reason": "notFound"}]}}'}.get(
                self.path, b"hello")
            self.send_response(404 if self.path == "/missing" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(308)
            self.send_header("Range", "bytes=0-9")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        auth = {"Authorization": "Bearer secret"}
        resp = cloud_http._request("GET", f"{base}/hello", auth)
        assert (resp.status, resp.body) == (200, b"hello")
        sink = io.BytesIO()
        assert cloud_http._request("GET", f"{base}/big", auth, sink=sink).body == b"" and len(sink.getvalue()) == 3_000_000
        missing = cloud_http._request("GET", f"{base}/missing", auth)
        assert missing.status == 404 and cloud_http.error(missing)[0] == "notFound"
        held = cloud_http._request("PUT", f"{base}/session", {"Content-Range": "bytes 0-9/20"}, b"0123456789")
        assert (held.status, held.headers["range"]) == (308, "bytes=0-9")
        assert cloud_http._request("GET", f"{base}/hop", auth).body == b"hello"
        assert seen["/hop"] == "Bearer secret" and seen["/landed"] is None  # 127.0.0.1 → localhost: another host
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(CloudUnreachable, match="could not be reached"):
        cloud_http._request("GET", f"http://127.0.0.1:{port}/", {}, timeout=5)
