"""A download made for one request: written to a temporary file, which
goes as soon as the answer ends, whatever ends it.

``temp_file`` gives the file and removes it when its writer raises;
``TempFileResponse`` sends it and removes it once the send is over. A
FileResponse's ``BackgroundTask`` is not enough: it runs only after a
complete send, so a refused Range (416), a malformed one (400), a send that
fails or a cancelled request left the file behind, a copy of the library
(``/export-all`` holds every personal workspace). One a killed process
left goes with ``sweep``, which the app lifespan runs at startup and
hourly (``app.sweep_temp``).

Docs: docs/dev/import_export.md "Temporary downloads".
"""

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi.responses import FileResponse

PREFIX = "gamma-download-"
STALE_S = 24 * 60 * 60  # a download's file this old was left by a killed process


@contextmanager
def temp_file(suffix: str):
    """A new, empty temporary file's path, removed if the block raises. It
    is closed: the block writes it by name, and Windows deletes no file
    that is open, so every writer has closed it by the time it goes."""
    fd, name = tempfile.mkstemp(prefix=PREFIX, suffix=suffix)
    os.close(fd)
    path = Path(name)
    try:
        yield path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class TempFileResponse(FileResponse):
    """A FileResponse that removes its file once the answer ends: sent,
    refused or cut short. FileResponse has closed the file by then (its
    close is shielded from a cancel, which is why requirements.txt pins
    starlette), so Windows lets it go."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def sweep():
    """Remove the download files a killed process left (quit or crashed
    mid-download). Only those older than STALE_S: the OS temp dir may be
    another instance's too, writing one now. One still open (Windows) or
    another account's stays."""
    cutoff = time.time() - STALE_S
    for path in Path(tempfile.gettempdir()).glob(f"{PREFIX}*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
