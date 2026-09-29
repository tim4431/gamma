"""FastAPI application assembly: middleware, routers, startup maintenance, SPA serving."""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import backup_schedule, cloud_sync, config, guests, migrations
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
    web_search,
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


def _startup_maintenance():
    """In this order: bring the data directory to the current schema version
    (gamma/migrations.py — refuses to serve a newer or unmigratable data
    directory), create users.db on a fresh install, seed the first admin,
    then per workspace: apply the per-file schema statements (a restored
    backup gains page_ops, WAL, ...). A workspace whose files fail to open
    is logged and left out: the others are served. The stored files'
    reconciliation (gamma/upload_gc.py) runs in the background once the
    server is up, never here."""
    log.info(f"[startup] Gamma {version.label()}")
    try:
        check_publish_config()
    except ValueError as e:
        log.error(f"[startup] {e}")
        raise SystemExit(1)
    try:
        done = migrations.ensure_current()
    except migrations.MigrationError as e:
        log.error(f"[startup] {e}")
        raise SystemExit(1)
    if done["applied"]:
        log.info(f"[startup] data directory upgraded from schema version {done['from']} "
                 f"to {done['to']} ({', '.join(done['applied'])}); snapshot: {done['backup']}")
    connect_users_db().close()
    ensure_admin_seed()
    for ws_id in workspace_ids():
        ws_root = ws_dir(ws_id)
        try:
            if (ws_root / "pages.db").exists():
                # connect_pages_db also switches the file to WAL and adds any
                # table an older file lacks (page_ops, ...).
                connect_pages_db(ws_id).close()
            if (ws_root / "data.db").exists():
                connect_data_db(ws_id).close()
        except Exception as e:  # noqa: BLE001 — one damaged library never stops the server
            log.error(f"[startup] workspace {ws_id} could not be opened, the others are served: {e}")


@asynccontextmanager
async def every(seconds: float, fn, failed: str):
    """While the app runs: ``fn`` in a worker thread at startup, then every
    ``seconds``. A round that raises is logged (``failed``) and the next
    one comes anyway."""
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.to_thread(fn)
            except Exception:
                log.exception(failed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=seconds)
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
    mcp = LazyMCP()
    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = THREAD_TOKENS
        # The MCP lifespan's yield is request state (its runtime, read by the
        # /mcp route from scope["state"]) — it must pass through here.
        async with mcp.lifespan(app) as state, backup_schedule.lifespan(), \
                every(cloud_sync.CHECK_INTERVAL, cloud_sync.check_all, "cloud: the grant check failed"), \
                every(guests.SWEEP_INTERVAL_S, guests.delete_expired, "[guests] sweep failed"), \
                every(trash.SWEEP_INTERVAL_S, trash.sweep, "[trash] sweep failed"), \
                every(ws_backup.STALE_TEMP_S, ws_backup.sweep_stale_temp, "[backups] temp sweep failed"), \
                every(workspaces.LEFTOVERS_EVERY_S, workspaces.remove_leftovers,
                      "[workspaces] leftover sweep failed"):
            yield state

    app = FastAPI(title="Gamma PDF Annotator", lifespan=lifespan)

    app.middleware("http")(session_middleware)
    app.add_middleware(JsonGzip)  # outermost: compresses what the rest answered

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(auth_router.router)
    app.include_router(cloud_auth_router.router)
    app.include_router(admin.router)
    app.include_router(workspaces_router.router)
    app.include_router(ws_backups.router)
    app.include_router(backup_tasks.router)
    app.include_router(ai.router)
    app.include_router(web_search.router)
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

        def revalidating(file: Path, request: Request):
            """An unhashed file (index.html, favicons) changes in place on
            upgrade, so it is sent ``no-cache``: the browser asks every time,
            and gets a 304 when it already holds this version. Starlette's
            FileResponse sets an ETag but never compares one, so without this
            every revalidation carried the whole body."""
            st = file.stat()
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            headers = {"Cache-Control": "no-cache", "ETag": etag}
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            return FileResponse(file, media_type=media_types.get(file.suffix.lower()), headers=headers)

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str, request: Request):
            if "\x00" in path:  # a scanner's NUL byte: not a file, never a crash
                raise HTTPException(status_code=404)
            candidate = (static_dir / path).resolve()
            # Path-traversal guard: only serve files inside the static dir
            if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
                if path.startswith("assets/"):
                    # Vite content-hashes these filenames (the pdf.js worker
                    # among them) — safe to cache forever.
                    return FileResponse(candidate, media_type=media_types.get(candidate.suffix.lower()),
                                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
                return revalidating(candidate, request)
            # index.html must revalidate every load, or clients keep referencing
            # deleted hashed assets after a deploy.
            return revalidating(index_html, request)

    _startup_maintenance()

    sync_engine.start_loop()
    upload_gc.start()
    return app


app = create_app()
