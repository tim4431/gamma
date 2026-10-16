"""Audio recordings (gamma/recording.py, routers/recordings.py): the audio
uploads and how they are served, the ``recording`` kind and its migration
step, the exports, the agent's label, and the ``transcribe`` job end to end
against a faked transcription API."""

import io
import json
import sqlite3
import threading
import urllib.error
import zipfile

import pytest

from conftest import login, make_page, make_user, workspace_of
from gamma import ai_catalog, jobs, migrations, recording
from gamma.db import BLOCK_HOT_INDEXES, PAGES_SCHEMA, connect_pages_db
from gamma.markdown_export import render_readable
from gamma.recording import fmt_duration, recording_label, transcript_paragraphs
from gamma.storage import is_audio

USER, PASSWORD = "rec_owner", "pw"
T0 = 1_760_000_000_000  # wall-clock ms the first segment began
# Two segments' files: an EBML header and bytes that tell them apart.
WEBM_A = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01" + b"A" * 300
WEBM_B = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01" + b"B" * 300


@pytest.fixture(scope="module")
def owner(client):
    make_user(USER, PASSWORD)
    c = login(USER, PASSWORD)
    r = c.post("/api/ai/providers", json={"protocol": "openai", "api_key": "sk-rec-key-1234"})
    assert r.status_code == 200, r.text
    return c


def _upload(c, name, data, mime="audio/webm"):
    r = c.post("/api/upload-file", files={"file": (name, data, mime)})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _recording(c, title="Lecture", segments=None):
    """A page holding a recording of two segments (or ``segments``) and a
    note of the user's under it; ``(page id, recording id, segments)``."""
    if segments is None:
        segments = [{"url": _upload(c, "a.webm", WEBM_A), "started": T0, "dur": 60_000, "bytes": len(WEBM_A),
                     "mime": "audio/webm"},
                    {"url": _upload(c, "b.webm", WEBM_B), "started": T0 + 65_000, "dur": 42_000,
                     "bytes": len(WEBM_B), "mime": "audio/webm"}]
    page = make_page(c, f"{title} page")["id"]
    rec_id = f"rec{title.replace(' ', '')}"
    props = {"recording": {"started": T0, "segments": segments, "state": "done",
                           "dur": sum(s["dur"] for s in segments)}, "t0": T0}
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": rec_id, "parent": page, "content": title, "props": props},
        {"op": "insert", "id": f"{rec_id}Note", "parent": rec_id, "content": "my own note", "props": {"t0": T0 + 5}}]})
    assert r.status_code == 200, r.text
    return page, rec_id, segments


def _children(block_id):
    with connect_pages_db(workspace_of(USER)) as conn:
        rows = conn.execute("SELECT id, content, properties FROM unified_blocks WHERE parent_id = ? ORDER BY position",
                            (block_id,)).fetchall()
    return [(i, content, json.loads(props)) for i, content, props in rows]


class _Answer:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


# Each file's verbose_json answer: segments in seconds into that file.
_SEGMENTS = {
    b"A" * 300: [{"id": 0, "start": 0.0, "end": 2.5, "text": " Welcome to the lecture."},
                 {"id": 1, "start": 2.5, "end": 6.0, "text": " Today: cavities."}],
    b"B" * 300: [{"id": 0, "start": 1.25, "end": 4.0, "text": " After the break."}],
}


def _fake_transcriptions(monkeypatch, seen, refuse=()):
    """The transcription API: verbose_json segments for the file sent, a
    404 for a model in ``refuse``; each request's body lands in ``seen``."""
    def urlopen(req, timeout=0):
        seen.append(req.data)
        model = next(m for m in ("whisper-1", "gpt-4o-transcribe-diarize", "gpt-4o-transcribe")
                     if f'name="model"\r\n\r\n{m}\r\n'.encode() in req.data)
        if model in refuse:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None,
                                         io.BytesIO(b'{"error":{"message":"model not found"}}'))
        segments = next(s for key, s in _SEGMENTS.items() if key in req.data)
        return _Answer({"text": "".join(s["text"] for s in segments), "segments": segments})

    monkeypatch.setattr(recording, "urlopen", urlopen)


def _listing(monkeypatch, *ids):
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: {"data": [{"id": i} for i in ids]})


def _transcribe(c, rec_id, **body):
    return c.post("/api/jobs/transcribe", json={"block_id": rec_id, **body})


# --- the files ---------------------------------------------------------------------------

def test_audio_is_served_inline_with_its_type_and_by_range(owner):
    url = _upload(owner, "segment.webm", WEBM_A)
    assert url.endswith(".webm")
    r = owner.get(url)
    assert r.status_code == 200 and r.content == WEBM_A
    assert r.headers["content-type"] == "audio/webm"
    assert "content-disposition" not in r.headers, "an <audio> element plays it"
    r = owner.get(url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == WEBM_A[:100]

    m4a = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00" + b"m" * 64
    url = _upload(owner, "segment.m4a", m4a, "audio/mp4")
    r = owner.get(url)
    assert r.headers["content-type"] == "audio/mp4" and "content-disposition" not in r.headers
    url = _upload(owner, "voice.ogg", b"OggS\x00\x02" + b"o" * 64, "audio/ogg")
    assert owner.get(url).headers["content-type"] == "audio/ogg"


def test_an_audio_upload_must_be_its_container(owner):
    for name in ("fake.webm", "fake.m4a", "fake.ogg", "fake.opus"):
        r = owner.post("/api/upload-file", files={"file": (name, b"<html>not audio</html>", "audio/webm")})
        assert r.status_code == 400 and r.json()["detail"] == "not a valid audio file", name
    assert is_audio(".mp3", b"ID3\x04\x00") and is_audio(".mp3", b"\xff\xfb\x90\x00")
    assert is_audio(".wav", b"RIFF\x24\x00\x00\x00WAVEfmt ") and not is_audio(".wav", b"RIFF\x24\x00\x00\x00AVI ")
    assert is_audio(".opus", b"OggS\x00") and not is_audio(".mp3", b"\x00\x00") and not is_audio(".txt", b"OggS")


# --- the block ---------------------------------------------------------------------------

def test_a_recording_is_its_own_kind(owner):
    _, rec_id, _ = _recording(owner, "Kind check")
    got = owner.get(f"/api/blocks/{rec_id}").json()
    assert got["kind"] == "recording" and got["content"] == "Kind check"
    assert owner.get(f"/api/blocks/{rec_id}Note").json()["kind"] == "note"


def test_step_38_rebuilds_the_kind_column():
    """A pages.db as step 37 left it: ``kind`` without its recording case."""
    old = next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks"))
    line = "WHEN json_type(properties, '$.recording') = 'object' THEN 'recording'"
    assert line in old
    conn = sqlite3.connect(":memory:")
    conn.execute(old.replace(line, ""))
    for stmt in BLOCK_HOT_INDEXES:
        conn.execute(stmt)
    conn.execute("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, page_id) "
                 "VALUES ('p', 'root', 'a0', '{\"doc_id\": \"d1\"}', 't', 't', 'p'), "
                 "('r', 'p', 'a0', '{\"recording\": {\"segments\": []}}', 't', 't', 'p')")
    conn.commit()
    assert conn.execute("SELECT kind FROM unified_blocks WHERE id = 'r'").fetchone() == ("note",)
    migrations._v38_recording_kind("ws", conn, None)
    assert conn.execute("SELECT id, kind, doc_id FROM unified_blocks ORDER BY id").fetchall() == [
        ("p", "page", "d1"), ("r", "recording", None)]
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_ub_kind", "idx_ub_doc"} <= indexes
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
    migrations._v38_recording_kind("ws", conn, None)  # re-runnable: nothing to do
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0] == ddl
    migrations._v38_recording_kind("", sqlite3.connect(":memory:"), None)  # a copy without the table
    assert (38, "recording_kind", migrations._v38_recording_kind) in migrations.WORKSPACE_STEPS
    assert migrations.workspace_format() == 38


# --- what it reads as --------------------------------------------------------------------

def test_the_label_and_the_duration():
    assert fmt_duration(754_000) == "12:34" and fmt_duration(3_723_400) == "1:02:03"
    assert fmt_duration(999) == "0:00" and fmt_duration(None) == "0:00"
    props = {"recording": {"segments": [{"url": "/api/uploads/ab12.webm", "dur": 1000}], "state": "recording"}}
    assert recording_label(props) == (
        "(a recording, 0:01, 1 segment, still being recorded; the text is its title, and the notes "
        "under it marked (transcript) are what was said)")
    assert recording_label({"transcript": True, "t0": 5}) == "(transcript)"
    assert recording_label({"recording": "no"}) == "" and recording_label({}) == ""
    from gamma.ai_context import handwriting_label
    assert handwriting_label({"recording": {"dur": 754_000, "segments": []}}).startswith("(a recording, 12:34, 0 segments;")


def test_transcript_paragraphs_join_segments_and_break_at_sentence_ends():
    long = "word " * 90  # 450 characters without a sentence end
    segments = [{"start": 0.0, "text": " First part,"}, {"start": 1.5, "text": " then the rest."},
                {"start": 3.0, "text": long + "ends here."}, {"start": 9.0, "text": " "},
                {"start": 10.0, "text": "A new paragraph"}, {"start": 12.0, "text": "runs on"}]
    paragraphs = transcript_paragraphs(segments)
    assert [p["start"] for p in paragraphs] == [0.0, 10.0]
    assert paragraphs[0]["text"].startswith("First part, then the rest. word word")
    assert paragraphs[0]["text"].endswith("word ends here.") and "  " not in paragraphs[0]["text"]
    assert paragraphs[1]["text"] == "A new paragraph runs on"
    # short paragraphs never break; CJK segments join without a space
    assert transcript_paragraphs([{"start": 0, "text": "一。"}, {"start": 1, "text": "二。"}]) == [
        {"start": 0, "text": "一。二。"}]
    assert transcript_paragraphs([]) == []


# --- exports -----------------------------------------------------------------------------

def test_the_markdown_export_bundles_the_audio(owner):
    page, rec_id, segments = _recording(owner, "Bundled")
    names = [s["url"].rsplit("/", 1)[1] for s in segments]
    r = owner.get(f"/api/pages/{page}/export")
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {f"assets/{n}" for n in names} <= set(z.namelist())
    assert z.read(f"assets/{names[0]}") == WEBM_A
    md = next(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))
    assert f"- **Recording · 1:42** [part 1](assets/{names[0]}) [part 2](assets/{names[1]})\n  Bundled\n" in md
    assert "  - my own note" in md and "/api/uploads/" not in md

    one = {"id": "p", "content": "One", "properties": {}, "children": [
        {"id": "r", "content": "Talk", "children": [], "properties": {"recording": {
            "dur": 5000, "segments": [{"url": "/api/uploads/ab12.webm", "dur": 5000}]}}}]}
    assert "- **Recording · 0:05** [audio](/api/uploads/ab12.webm)\n  Talk" in render_readable(one)
    off = render_readable(one, highlights=False)  # treated as ink: the caption stays
    assert "- Talk" in off and "Recording" not in off and "ab12" not in off


def test_the_obsidian_export_embeds_the_audio(owner):
    page, _, segments = _recording(owner, "Vaulted")
    names = [s["url"].rsplit("/", 1)[1] for s in segments]
    r = owner.get(f"/api/pages/{page}/export", params={"mode": "obsidian"})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {f"attachments/{n}" for n in names} <= set(z.namelist())
    md = next(z.read(n).decode() for n in z.namelist() if n.endswith(".md") and not n.startswith("."))
    assert (f"**Recording · 1:42**\n![[attachments/{names[0]}]]\n![[attachments/{names[1]}]]\nVaulted\n\n"
            "- my own note") in md


def test_the_pdf_exports_take_a_recording(owner):
    import pypdfium2 as pdfium
    from PyPDF2 import PdfWriter

    page, rec_id, _ = _recording(owner, "Typeset")
    r = owner.get(f"/api/pages/{page}/export", params={"mode": "notes-pdf"})
    assert r.status_code == 200, r.text
    doc = pdfium.PdfDocument(r.content)
    try:
        text = doc[0].get_textpage().get_text_bounded()
    finally:
        doc.close()
    assert "Recording" in text and "1:42 (audio not included in a PDF)" in text
    assert "Typeset" in text and "my own note" in text

    # the annotated PDF leaves a recording out
    w, blank = PdfWriter(), io.BytesIO()
    w.add_blank_page(width=600, height=780)
    w.write(blank)
    up = owner.post("/api/uploads", files={"file": ("rec.pdf", blank.getvalue(), "application/pdf")}).json()
    r = owner.put(f"/api/blocks/{page}", json={"properties": {"doc_id": up["doc_id"]}})
    assert r.status_code == 200, r.text
    r = owner.get(f"/api/pages/{page}/export", params={"mode": "annotated-pdf"})
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


# --- the transcribe job ------------------------------------------------------------------

def test_transcription_writes_the_paragraphs_and_a_rerun_replaces_them(owner, monkeypatch):
    seen = []
    _fake_transcriptions(monkeypatch, seen)
    _listing(monkeypatch, "gpt-4o-transcribe", "whisper-1")
    _, rec_id, segments = _recording(owner, "Transcribed")

    r = _transcribe(owner, rec_id, language="en")
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["kind"] == "transcribe" and started["title"] == "Transcribe “Transcribed”"
    assert started["params"]["block_id"] == rec_id
    job = jobs.wait(started["id"])
    assert job["state"] == "done", job["error"]
    assert job["result"] == {"paragraphs": 2, "model": "whisper-1"}
    assert job["progress"] == {"done": 2, "total": 2, "unit": "segments", "phase": "transcribing"}
    assert len(seen) == 2 and all(b"verbose_json" in body and b"timestamp_granularities[]" in body for body in seen)
    assert all(b'name="language"\r\n\r\nen' in body for body in seen)

    kids = _children(rec_id)
    assert kids[0][1] == "my own note", "the user's notes stay first"
    transcript = [(content, props) for _, content, props in kids[1:]]
    # t0: the segment's own wall-clock start plus the paragraph's start in it
    assert transcript == [("Welcome to the lecture. Today: cavities.", {"transcript": True, "t0": T0}),
                          ("After the break.", {"transcript": True, "t0": segments[1]["started"] + 1250})]
    first = {i for i, _, _ in kids[1:]}

    r = _transcribe(owner, rec_id)
    job = jobs.wait(r.json()["id"])
    assert job["state"] == "done", job["error"]
    again = _children(rec_id)
    assert [(c, p) for _, c, p in again[1:]] == transcript and again[0][1] == "my own note"
    assert not first & {i for i, _, _ in again}, "the old paragraphs were replaced, not kept"


def test_a_diarizing_model_is_preferred_and_a_refused_model_falls_back(owner, monkeypatch):
    seen = []
    _fake_transcriptions(monkeypatch, seen)
    _listing(monkeypatch, "gpt-4o-transcribe", "gpt-4o-transcribe-diarize", "whisper-1")
    _, rec_id, _ = _recording(owner, "Diarized")
    job = jobs.wait(_transcribe(owner, rec_id).json()["id"])
    assert job["state"] == "done" and job["result"]["model"] == "gpt-4o-transcribe-diarize"
    assert all(b"diarized_json" in body and b'name="chunking_strategy"\r\n\r\nauto' in body for body in seen)

    # a model asked for that the provider refuses: whisper-1 instead, for every segment
    seen.clear()
    _fake_transcriptions(monkeypatch, seen, refuse=("gpt-4o-transcribe",))
    job = jobs.wait(_transcribe(owner, rec_id, model="gpt-4o-transcribe").json()["id"])
    assert job["state"] == "done" and job["result"] == {"paragraphs": 2, "model": "whisper-1"}
    assert len(seen) == 3  # the refusal, then whisper-1 twice


def test_what_the_route_refuses(owner, monkeypatch):
    page = make_page(owner, "Not a recording")["id"]
    owner.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "recPlain", "parent": page, "content": "just a note"}]})
    assert _transcribe(owner, "recPlain").status_code == 404
    assert _transcribe(owner, "recNoSuchBlock").status_code == 404
    _, empty, _ = _recording(owner, "Empty", segments=[])
    assert _transcribe(owner, empty).status_code == 400
    _, rec_id, _ = _recording(owner, "Refused")
    assert _transcribe(owner, rec_id, model="bad model\r\n").status_code == 422
    assert _transcribe(owner, rec_id, language="not a language").status_code == 422
    assert owner.post("/api/jobs/transcribe", json={}).status_code == 422  # which recording

    make_user("rec_nokey", "pw-rec-2")
    other = login("rec_nokey", "pw-rec-2")
    _, theirs, _ = _recording(other, "No key")
    r = _transcribe(other, theirs)
    assert r.status_code == 503 and "OpenAI" in r.json()["detail"]
    assert _transcribe(other, rec_id).status_code == 404, "another account's recording is not in reach"

    # one run per recording: a second start while it runs answers 409 with that job
    gate, entered = threading.Event(), threading.Event()

    def held(req, timeout=0):
        entered.set()
        gate.wait(10)
        return _Answer({"text": "held"})

    monkeypatch.setattr(recording, "urlopen", held)
    _listing(monkeypatch, "whisper-1")
    first = _transcribe(owner, rec_id).json()
    try:
        assert entered.wait(10)
        busy = _transcribe(owner, rec_id)
        assert busy.status_code == 409 and busy.json()["job"]["id"] == first["id"]
    finally:
        gate.set()
    job = jobs.wait(first["id"])
    assert job["state"] == "done" and job["result"]["paragraphs"] == 2  # one segment-less answer per file


def test_a_job_that_cannot_transcribe_fails_and_keeps_the_transcript(owner, monkeypatch):
    seen = []
    _fake_transcriptions(monkeypatch, seen)
    _listing(monkeypatch, "whisper-1")
    _, rec_id, _ = _recording(owner, "Kept")
    assert jobs.wait(_transcribe(owner, rec_id).json()["id"])["state"] == "done"
    before = _children(rec_id)

    monkeypatch.setattr(recording, "TRANSCRIBE_MAX_BYTES", 10)
    job = jobs.wait(_transcribe(owner, rec_id).json()["id"])
    assert job["state"] == "failed" and "part 1 of the recording is over 25 MB" in job["error"]
    monkeypatch.undo()

    def unauthorized(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None,
                                     io.BytesIO(b'{"error":{"message":"bad key"}}'))

    monkeypatch.setattr(recording, "urlopen", unauthorized)
    _listing(monkeypatch, "whisper-1")
    job = jobs.wait(_transcribe(owner, rec_id).json()["id"])
    assert job["state"] == "failed" and job["error"].startswith("transcription failed") and "bad key" in job["error"]
    assert _children(rec_id) == before, "nothing is written before every segment is done"
