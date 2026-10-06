"""FastAPI application assembly: middleware, routers, startup maintenance, SPA serving."""

import asyncio
import html
import mimetypes
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import backup_schedule, cloud_sync, config, db_maintenance, guests, hosted, jobs, migrations, offsite
from . import sync_engine, trash, upload_gc, version, workspaces, ws_backup
from .publish import check_config as check_publish_config
from .auth import session_middleware
from .compression import JsonGzip
from .db import connect_data_db, connect_pages_db, connect_users_db, workspace_ids, ws_dir
from .logbuf import log, setup_logging
from .mcp_lazy import LazyMCP
from .mcp_oauth import router as mcp_oauth_router
from .routers import (
    admin,
    ai,
    ai_handoffs,
    auth as auth_router,
    blocks,
    backup_tasks,
    chats,
    clip,
    collab,
    export,
    folders,
    imports,
    integrations,
    ink,
    jobs as jobs_router,
    links,
    metadata,
    mirrors,
    notices,
    pages,
    pdf,
    prefs,
    publish,
    publisher_sessions,
    search,
    shares,
    sync,
    trash as trash_router,
    uploads,
    workspaces as workspaces_router,
    ws_backups, cloud_auth as cloud_auth_router)
from .seed import ensure_admin_seed

# Worker threads for sync endpoints, streamed replies and file responses
# (AnyIO's default limiter holds 40). Slow outbound work — the PDF proxy, an
# AI stream — holds one for as long as the far side takes, and a PDF page
# read must not wait behind forty of them.
THREAD_TOKENS = 100


def _silence_windows_connection_reset():
    """Swallow the benign ConnectionResetError [WinError 10054] that the Windows
    Proactor event loop raises in _call_connection_lost when a client aborts an
    in-flight stream (e.g. a browser refresh cancelling a 206 range request for a
    PDF). The request has already completed; only the socket teardown fails, and
    stock asyncio logs it as an alarming unhandled-callback traceback.
    See https://github.com/python/cpython/issues/87643."""
    if sys.platform != "win32":
        return
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _orig = _ProactorBasePipeTransport._call_connection_lost

    def _quiet_call_connection_lost(self, exc):
        try:
            _orig(self, exc)
        except (ConnectionResetError, ConnectionAbortedError):
            pass

    _ProactorBasePipeTransport._call_connection_lost = _quiet_call_connection_lost


def _upgrade_data_directory() -> dict | None:
    """Bring the data directory to the current schema version
    (gamma/migrations.py), the first thing the server does. Returns None
    when it is current, else the guidance for the person running the
    server: the directory is newer than this build, older than it can
    upgrade, or a step failed. The server then serves that guidance
    (``_blocked_app``) instead of exiting, so a container that restarts on
    its own shows the page at the usual address rather than looping on a
    message in its log."""
    try:
        done = migrations.ensure_current()
    except migrations.MigrationError as e:
        guide = migrations.guidance(e)
        log.error(f"[startup] {guide['title']}: {guide['summary']}")
        for n, step in enumerate(guide["steps"], 1):
            log.error(f"[startup]   {n}. {step}")
        return guide
    if done["applied"]:
        log.info(f"[startup] data directory upgraded from schema version {done['from']} "
                 f"to {done['to']} ({', '.join(done['applied'])}); snapshot: {done['backup']}")
    return None


_BLOCKED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Gamma</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; box-sizing: border-box;
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: Canvas; color: CanvasText; }}
  main {{ max-width: 640px; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 12px; }}
  ol {{ padding-left: 1.3em; }} li {{ margin: 8px 0; }}
  code {{ font: 13px/1.4 ui-monospace, Consolas, monospace; padding: 1px 5px; border-radius: 4px;
          background: color-mix(in srgb, CanvasText 10%, Canvas); overflow-wrap: anywhere; }}
  p.meta {{ opacity: .7; font-size: .9rem; }}
</style></head>
<body><main>
<h1>{title}</h1>
<p>{summary}</p>
<ol>{steps}</ol>
<p class="meta">Gamma {build} · data directory <code>{data_dir}</code> · snapshots in <code>{backups_dir}</code> ·
<a href="https://github.com/tim4431/gamma/blob/main/docs/dev/migrations.md">how upgrades work</a></p>
</main></body></html>
"""


def _blocked_app(guide: dict) -> FastAPI:
    """The app served while the data directory cannot be upgraded by this
    build: one page with the guidance at every address, and a 503 with the
    same text as JSON under /api, so a browser, a script and the desktop
    shell all learn what to do. Nothing else runs: no routers, no
    background rounds, nothing that would open the databases."""
    app = FastAPI(title="Gamma PDF Annotator", docs_url=None, redoc_url=None, openapi_url=None)

    def step_html(text: str) -> str:
        parts = html.escape(text).split("`")  # `code` spans in the guidance
        return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))

    page = _BLOCKED_PAGE.format(
        title=html.escape(guide["title"]), summary=step_html(guide["summary"]),
        steps="".join(f"<li>{step_html(s)}</li>" for s in guide["steps"]),
        build=html.escape(version.label()), data_dir=html.escape(guide["data_dir"]),
        backups_dir=html.escape(guide["backups_dir"]))
    body = {"error": "data_directory_not_upgradable", "build": version.label(), **guide}

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def blocked(path: str):  # awaits nothing: a def, off the event loop
        if path == "api" or path.startswith("api/"):
            return JSONResponse(body, status_code=503, headers={"Retry-After": "60"})
        return HTMLResponse(page, status_code=503, headers={"Retry-After": "60"})

    return app


def _startup_maintenance():
    """After the data directory is current: create users.db on a fresh
    install, seed the first admin, then per workspace: apply the per-file
    schema statements (a restored backup gains page_ops, WAL, ...). A
    workspace behind on its own migration steps is skipped: the background
    walk the lifespan starts (``migrations.warming``) upgrades it, or the
    first request that opens it. A workspace whose files fail to open is
    logged and left out: the others are served. The stored files'
    reconciliation (gamma/upload_gc.py) runs in the background once the
    server is up, never here."""
    connect_users_db().close()
    jobs.recover()
    ensure_admin_seed()
    for ws_id in workspace_ids():
        ws_root = ws_dir(ws_id)
        try:
            if migrations.is_behind(ws_id):
                continue
            if (ws_root / "pages.db").exists():
                # connect_pages_db also switches the file to WAL and adds any
                # table an older file lacks (page_ops, ...).
                connect_pages_db(ws_id).close()
            if (ws_root / "data.db").exists():
                connect_data_db(ws_id).close()
        except Exception as e:  # noqa: BLE001 — one damaged library never stops the server
            log.error(f"[startup] workspace {ws_id} could not be opened, the others are served: {e}")


async def read_only_gate(request: Request, call_next):
    """A hosted server its plan made read-only (gamma/hosted.py): every
    state-changing /api request but ``hosted.READ_ONLY_ALLOWED`` is refused
    with 423 and the reason. Inside the session middleware, so the refusal
    is logged like any answer. Collaboration needs nothing of its own: an
    op batch is a POST, and the page socket carries only carets. A hosted
    server also notes every answer here for the sync's report
    (``hosted.answered``: a 5xx, an accepted write); any other server only
    passes the request on."""
    if not hosted.enabled():
        return await call_next(request)
    write = request.method in hosted.WRITE_METHODS and request.url.path.startswith("/api/")
    if write and not hosted.allowed_when_read_only(request.url.path):
        refusal = hosted.read_only()  # from memory: tick loaded it at startup
        if refusal:
            return JSONResponse({"detail": refusal, "read_only": True}, status_code=423)
    try:
        response = await call_next(request)
    except Exception:
        hosted.answered(500, write)  # the server's error handler, outside this one, answers it with a 500
        raise
    hosted.answered(response.status_code, write)
    return response


@asynccontextmanager
async def every(seconds, fn, failed: str):
    """While the app runs: ``fn`` in a worker thread at startup, then every
    ``seconds``: a number, or a callable asked after each round, in a
    worker thread too, for the pause before the next (the off-site copies'
    interval; it must not raise). A round that raises is logged
    (``failed``) and the next one comes anyway."""
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.to_thread(fn)
            except Exception:
                log.exception(failed)
            wait = await asyncio.to_thread(seconds) if callable(seconds) else seconds
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await task


def create_app() -> FastAPI:
    setup_logging()
    _silence_windows_connection_reset()
    log.info(f"[startup] Gamma {version.label()}")
    try:
        check_publish_config()
    except ValueError as e:
        log.error(f"[startup] {e}")
        raise SystemExit(1)
    guide = _upgrade_data_directory()
    if guide:
        return _blocked_app(guide)
    mcp = LazyMCP()
    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = THREAD_TOKENS
        # The MCP lifespan's yield is request state (its runtime, read by the
        # /mcp route from scope["state"]) — it must pass through here.
        async with mcp.lifespan(app) as state, backup_schedule.lifespan(), \
                every(cloud_sync.CHECK_INTERVAL, cloud_sync.check_all, "cloud: the grant check failed"), \
                every(hosted.SYNC_INTERVAL, hosted.tick, "[hosted] the plan sync failed"), \
                every(guests.SWEEP_INTERVAL_S, guests.delete_expired, "[guests] sweep failed"), \
                every(trash.SWEEP_INTERVAL_S, trash.sweep, "[trash] sweep failed"), \
                every(ws_backup.STALE_TEMP_S, ws_backup.sweep_stale_temp, "[backups] temp sweep failed"), \
                every(jobs.SWEEP_INTERVAL_S, jobs.sweep, "[jobs] sweep failed"), \
                every(db_maintenance.EVERY_S, db_maintenance.tick, "[db] maintenance failed"), \
                every(offsite.wait_s, offsite.tick, "[offsite] round failed"), \
                every(workspaces.LEFTOVERS_EVERY_S, workspaces.remove_leftovers,
                      "[workspaces] leftover sweep failed"):
            with migrations.warming():  # the workspaces still behind on their steps, one by one
                yield state

    app = FastAPI(title="Gamma PDF Annotator", lifespan=lifespan)

    @app.exception_handler(migrations.MigrationError)
    async def workspace_not_upgraded(request: Request, exc: migrations.MigrationError):
        # A workspace whose own migration steps failed as it opened
        # (migrations.upgrade_workspace): it answers the startup page's
        # guidance as a 503, and every other workspace is served.
        return JSONResponse({"error": "workspace_not_upgradable", "build": version.label(),
                             **migrations.guidance(exc)}, status_code=503, headers={"Retry-After": "60"})

    app.middleware("http")(read_only_gate)  # added first: runs inside the session middleware
    app.middleware("http")(session_middleware)
    app.add_middleware(JsonGzip)  # outermost: compresses what the rest answered

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(auth_router.router)
    app.include_router(cloud_auth_router.router)
    app.include_router(admin.router)
    app.include_router(admin.jobs_router)
    app.include_router(workspaces_router.router)
    app.include_router(ws_backups.router)
    app.include_router(ws_backups.transfers)
    app.include_router(backup_tasks.router)
    app.include_router(jobs_router.router)
    app.include_router(ai.router)
    app.include_router(ai_handoffs.router)
    app.include_router(chats.router)
    app.include_router(chats.history_router)
    app.include_router(prefs.router)
    app.include_router(notices.router)
    app.include_router(integrations.router)
    app.include_router(mcp_oauth_router)
    app.router.routes.append(mcp.route())
    app.include_router(metadata.router)
    app.include_router(search.router)
    app.include_router(shares.router)
    app.include_router(pdf.router)
    app.include_router(publisher_sessions.router)
    app.include_router(uploads.router)
    app.include_router(ink.router)
    app.include_router(blocks.router)
    app.include_router(pages.router)
    app.include_router(trash_router.router)
    app.include_router(imports.router)
    app.include_router(export.router)
    app.include_router(links.router)
    app.include_router(clip.router)
    app.include_router(folders.router)
    app.include_router(collab.router)
    app.include_router(sync.router)
    app.include_router(mirrors.router)
    app.include_router(publish.router)

    # Serve the built frontend (SPA) when GAMMA_STATIC_DIR is set.
    # Registered last so all /api routes take precedence.
    static_dir = Path(config.STATIC_DIR) if config.STATIC_DIR else None
    if static_dir and static_dir.is_dir():
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

    _startup_maintenance()

    sync_engine.start_loop()
    upload_gc.start()
    return app


app = create_app()
