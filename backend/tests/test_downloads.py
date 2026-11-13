"""A download made for one request (gamma/downloads.py): its temporary
file goes when its writer raises, and once its answer ends — sent, or cut
short by a send that fails or a cancelled request, after which a
FileResponse's background task never runs. One a killed process left goes
with the sweep."""

import os
import time

import anyio
import pytest

from gamma import downloads
from gamma.downloads import TempFileResponse, temp_file

SCOPE = {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}}


@pytest.fixture
def made(tmp_path, monkeypatch):
    """A 1 MB download in a temp dir of the test's own → its path."""
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with temp_file(".zip") as path:
        path.write_bytes(b"x" * (1 << 20))
    return path


async def _never(*_):
    await anyio.sleep_forever()


def test_temp_file_is_removed_when_its_writer_raises(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(OSError):
        with temp_file(".zip") as path:
            path.write_bytes(b"half")
            raise OSError("No space left on device")
    assert list(tmp_path.iterdir()) == []


def test_a_sent_download_is_removed(made):
    sent = []

    async def send(message):
        sent.append(len(message.get("body", b"")))
    anyio.run(TempFileResponse(made), SCOPE, _never, send)
    assert sum(sent) == 1 << 20 and not made.exists()


def test_a_send_that_fails_removes_it(made):
    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("Connection reset by peer")
    with pytest.raises(OSError):
        anyio.run(TempFileResponse(made), SCOPE, _never, send)
    assert not made.exists()


def test_a_cancelled_request_removes_it(made):
    async def send(message):
        if message["type"] == "http.response.body":
            await anyio.sleep_forever()  # a client that stopped reading

    async def main():
        with anyio.move_on_after(0.2):
            await TempFileResponse(made)(SCOPE, _never, send)
    anyio.run(main)
    assert not made.exists()


def test_the_sweep_takes_what_a_killed_process_left(made, tmp_path):
    """A download's file older than a day was left by a process killed
    mid-download; a younger one may be sending now, and other files are
    not the server's."""
    left, other = tmp_path / "gamma-download-left.zip", tmp_path / "tmp-left.zip"
    left.write_bytes(b"users.db")
    other.write_bytes(b"x")
    then = time.time() - downloads.STALE_S - 60
    for path in (left, other):
        os.utime(path, (then, then))
    downloads.sweep()
    assert not left.exists() and made.exists() and other.exists()
