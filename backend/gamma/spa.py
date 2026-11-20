"""The built frontend (GAMMA_STATIC_DIR, ``config.STATIC_DIR``), served at
every address no route above it answers: a file of the build when the path
names one, else ``index.html``, the app's shell. The app registers it after
its routers (gamma/app.py); the standby app after its own few routes
(gamma/standby.py), so the page a server shows while it cannot open its
data directory is the app's own (``auth/StandbyPage.jsx``)."""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import config


def build_dir() -> Path | None:
    """The build's folder, None when GAMMA_STATIC_DIR is unset or names no
    folder (a checkout run by hand, whose frontend is Vite's)."""
    folder = Path(config.STATIC_DIR) if config.STATIC_DIR else None
    return folder if folder and folder.is_dir() else None


def serve(app: FastAPI) -> bool:
    """Register the build's catch-all GET route on ``app``; False (nothing
    registered) without a build."""
    static_dir = build_dir()
    if static_dir is None:
        return False
    index_html = static_dir / "index.html"
    # Pin browser-critical types instead of trusting the OS MIME table.
    # Windows registry entries can label .mjs as text/plain, preventing
    # Chromium from loading the PDF worker; slim images can lack types.
    media_types = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".webmanifest": "application/manifest+json",
        ".woff2": "font/woff2",
    }

    # The build writes a Brotli and a gzip copy beside every compressible
    # file (frontend/vite.config.js); the first the request accepts is
    # sent in the file's place.
    precompressed = (("br", ".br"), ("gzip", ".gz"))

    def accepted_codings(request: Request) -> set[str]:
        """The content codings the request's Accept-Encoding names, less
        those it refuses (``q=0``)."""
        codings = set()
        for item in request.headers.get("accept-encoding", "").lower().split(","):
            name, _, q = item.partition(";")
            try:
                if float(q.strip().removeprefix("q=") or 1) > 0:
                    codings.add(name.strip())
            except ValueError:
                pass
        return codings

    def static_file(file: Path, request: Request, cache_control: str):
        """``file``, or its precompressed copy the request accepts, sent
        with ``Content-Encoding`` and the file's own media type. A file
        with a copy answers ``Vary: Accept-Encoding`` whichever goes out,
        and each coding has an ETag of its own. A matching If-None-Match
        gets a 304: Starlette's FileResponse sets an ETag but never
        compares one, so without this every revalidation of an unhashed
        file (index.html, favicons) carried the whole body."""
        sent, coding, headers = file, "", {"Cache-Control": cache_control}
        codings = accepted_codings(request)
        for name, suffix in precompressed:
            copy = file.with_name(file.name + suffix)
            if copy.is_file():
                headers["Vary"] = "Accept-Encoding"
                if name in codings:
                    sent, coding = copy, name
                    break
        st = sent.stat()
        headers["ETag"] = f'"{st.st_mtime_ns:x}-{st.st_size:x}{"-" + coding if coding else ""}"'
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        if coding:
            headers["Content-Encoding"] = coding
        media_type = media_types.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or "text/plain"
        return FileResponse(sent, media_type=media_type, headers=headers)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str, request: Request):
        if "\x00" in path:  # a scanner's NUL byte: not a file, never a crash
            raise HTTPException(status_code=404)
        if request.method == "HEAD" and (path == "api" or path.startswith("api/")):
            # an API route without HEAD answers 405, not the app's page
            raise HTTPException(status_code=405, headers={"Allow": "GET"})
        candidate = (static_dir / path).resolve()
        # Path-traversal guard: only serve files inside the static dir
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            if path.startswith("assets/"):
                # Vite content-hashes these filenames (the pdf.js worker
                # among them) — safe to cache forever.
                return static_file(candidate, request, "public, max-age=31536000, immutable")
            # An unhashed file changes in place on upgrade: the browser
            # asks every time, and gets a 304 when it holds this version.
            return static_file(candidate, request, "no-cache")
        # index.html must revalidate every load, or clients keep referencing
        # deleted hashed assets after a deploy.
        return static_file(index_html, request, "no-cache")

    return True
