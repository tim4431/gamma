"""Small answers that used to be wrong: /api/clip's ``existed`` for a PDF
whose page the lookup-or-create found, a deleted backup task's lock file,
and ``auto`` of an old server backup's manifest in the admin listing."""

import json

from conftest import login, make_user
from gamma import backup_schedule, backups, config

PDF_BYTES = b"%PDF-1.4 small fixes\n" + b"z" * 5_000


def test_clip_of_a_pdf_that_has_a_page_says_it_existed():
    make_user("ssf_clip", "ssfclippw1")
    c = login("ssf_clip", "ssfclippw1")
    doc_id = c.post("/api/uploads", files={"file": ("p.pdf", PDF_BYTES, "application/pdf")}).json()["doc_id"]
    body = {"doc_id": doc_id, "title": "A clipped paper", "fetch_metadata": False}
    first = c.post("/api/clip", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["existed"] is False
    second = c.post("/api/clip", json=body).json()
    assert second["block_id"] == first.json()["block_id"] and second["existed"] is True


def test_deleting_a_backup_task_removes_its_lock_file(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_schedule.config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_schedule, "_wake", lambda: None)
    ws = make_user("ssf_tasks", "ssftaskspw1")
    owner = login("ssf_tasks", "ssftaskspw1")
    saved = owner.post("/api/backup-tasks", json={"name": "Nightly", "workspaces": [ws], "cron": "0 2 * * *"})
    assert saved.status_code == 200, saved.text
    task_id = saved.json()["id"]
    assert owner.post(f"/api/backup-tasks/{task_id}/run").status_code == 200  # takes the lock once
    assert (backup_schedule.root() / f"{task_id}.lock").exists()
    assert owner.delete(f"/api/backup-tasks/{task_id}").status_code == 200
    assert not (backup_schedule.root() / f"{task_id}.lock").exists()
    assert not (backup_schedule.root() / f"{task_id}.json").exists()


def test_an_old_manifest_reports_the_auto_flag_pruning_reads():
    make_user("ssf_admin", "ssfadminpw1", is_admin=1)
    admin = login("ssf_admin", "ssfadminpw1")
    old = config.BACKUPS_DIR / "20200101-000000-v3"  # a pre-flag migration snapshot
    old.mkdir(parents=True, exist_ok=True)
    (old / "manifest.json").write_text(json.dumps({"created_at": "2020-01-01T00:00:00Z", "label": "v3",
                                                   "files": [], "uploads": False}), encoding="utf-8")
    hand = config.BACKUPS_DIR / "20200101-000001-manual"
    hand.mkdir(parents=True, exist_ok=True)
    (hand / "manifest.json").write_text(json.dumps({"created_at": "2020-01-01T00:00:01Z", "label": "manual",
                                                    "files": [], "uploads": False}), encoding="utf-8")
    try:
        listed = {b["name"]: b for b in admin.get("/api/admin/backups").json()["backups"]}
        assert listed[old.name]["auto"] is True and listed[hand.name]["auto"] is False
        assert backups.is_auto(listed[old.name])
    finally:
        backups.delete(old.name)
        backups.delete(hand.name)
