"""FastAPI application assembly: middleware, routers, startup maintenance, SPA serving."""

import asyncio
import os
import ssl
import sys
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import backup_schedule, cloud_auth, cloud_deletions, cloud_sync, comments, config, db_maintenance, downloads
from . import email_invites, guests, hosted, import_staging, inbox, jobs, migrations, net_proxy, ocr_auto, ocr_engine, offsite
from . import ai_roles, reports
from . import spa, standby, sync_engine, trash, upload_gc, version, workspaces, ws_backup, zotero_stream
from .annotations import listeners as annotation_listeners
from .computers import channel as computer_channel
from . import local_agents
from .local_agents import runner as local_agents_runner
from .publish import check_config as check_publish_config
from .auth import session_middleware
from .compression import JsonGzip
from .db import connect_data_db, connect_pages_db, connect_users_db, connect_vaults_db, workspace_ids, ws_dir
from .logbuf import log, setup_logging
from .mcp_lazy import LazyMCP
from .mcp_oauth import router as mcp_oauth_router
from .routers import (
    admin,
    agent_changes as agent_changes_router,
    ai,
    ai_handoffs,
    annotations as annotations_router,
    auth as auth_router,
    blocks,
    backup_tasks,
    chats,
    citations as citations_router,
    clip,
    collab,
    comments as comments_router,
    export,
    folders,
    imports,
    inbox as inbox_router,
    integrations,
    ink,
    ink_ai,
    jobs as jobs_router,
    links,
    mentions,
    metadata,
    mirrors,
    notices,
    ocr as ocr_router,
    pages,
    pdf,
    prefs,
    publish,
    publisher_sessions,
    recordings,
    reports as reports_router,
    search,
    shares,
    skills as skills_router,
    sync,
    trash as trash_router,
    translate,
    uploads,
    workspaces as workspaces_router,
    ws_backups, cloud_auth as cloud_auth_router,
    zotero as zotero_router,
    zotero_sync as zotero_sync_router)
from . import folder_links
from .routers import computer_links as computer_links_api, computers as computers_api
from .routers import remote_servers as remote_servers_api
from .routers import folder_links as folder_links_api
from .routers import local_agents as local_agents_api
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


def _verify_https_with_certifi_when_no_cas():
    """Point OpenSSL at certifi's CA bundle when its default places hold none.

    The server's outbound HTTPS (urllib's default context: the AI providers,
    the ChatGPT sign-in, Gamma Cloud) is verified against the CA file and
    folder OpenSSL was built to look in; outside Windows Python reads no
    system trust store, so the macOS Keychain never counts. The desktop
    app's frozen server keeps the paths of the Python it was built with
    (python.org's, under /Library/Frameworks), which a Mac without that
    Python lacks, and every call then fails with CERTIFICATE_VERIFY_FAILED.
    A CA file or folder that exists, built in or named by SSL_CERT_FILE /
    SSL_CERT_DIR, is left alone. Setting the variable works from here on:
    OpenSSL reads it whenever a context loads its default CAs."""
    if sys.platform == "win32":  # Python loads the Windows store itself
        return
    paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath:
        return
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()
    log.info(f"[startup] no CA certificates at {paths.openssl_cafile}; verifying HTTPS with certifi's")


def _upgrade_data_directory() -> dict | None:
    """Bring the data directory to the current schema version
    (gamma/migrations.py), the first thing the server does. Returns None
    when it is current, else the guidance for the person running the
    server: the directory is newer than this build, older than it can
    upgrade, or a step failed. The server then serves that guidance
    (``standby.standby_app``) instead of exiting, so a container that restarts on
    its own shows the page at the usual address rather than looping on a
    message in its log."""
    try:
        done = migrations.ensure_current()
    except migrations.MigrationError as e:
        guide = migrations.guidance(e)
        log.error(f"[startup] {guide['title']}: {guide['summary']}")
        for n, step in enumerate(guide["steps"], 1):
            log.error(f"[startup]   {n}. {step}")
        for line in guide["details"]:
            log.error(f"[startup]   - {line}")
        return guide
    if done["applied"]:
        log.info(f"[startup] data directory upgraded from schema version {done['from']} "
                 f"to {done['to']} ({', '.join(done['applied'])}); snapshot: {done['backup']}")
    return None


def _startup_maintenance():
    """After the data directory is current: create users.db on a fresh
    install (and vaults.db on the desktop app's own server, the one server
    that keeps it, whose agents' turns a stopped server left fail there:
    gamma/local_agents/runner.py ``recover``), seed the first admin, then per workspace: apply the per-file
    schema statements (a restored backup gains page_ops, WAL, ...). A
    workspace behind on its own migration steps is skipped: the background
    walk the lifespan starts (``migrations.warming``) upgrades it, or the
    first request that opens it. A workspace whose files fail to open is
    logged and left out: the others are served. The stored files'
    reconciliation (gamma/upload_gc.py) runs in the background once the
    server is up, never here."""
    connect_users_db().close()
    if config.folder_links_enabled():
        connect_vaults_db().close()
    if local_agents.enabled():
        try:
            local_agents_runner.recover()  # the turns a stopped server left fail
        except Exception as e:  # noqa: BLE001 — the agents never stop the server
            log.error(f"[startup] the agents on this computer could not recover their runs: {e}")
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
    with 423 and the reason; a read sent as a POST (``hosted.POST_READS``)
    is no write. Inside the session middleware, so the refusal is logged
    like any answer. Collaboration needs nothing of its own: an op batch is
    a POST, and the page socket carries only carets. A hosted server also
    notes every answer here for the sync's report (``hosted.answered``: a
    5xx, an accepted write); any other server only passes the request on."""
    if not hosted.enabled():
        return await call_next(request)
    path = request.url.path
    write = request.method in hosted.WRITE_METHODS and path.startswith("/api/") and not hosted.is_post_read(path)
    if write and not hosted.allowed_when_read_only(path):
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




def sweep_temp() -> None:
    """The leftover temporary files' sweeps, at startup and hourly: the
    snapshots' work files a killed process left (gamma/ws_backup.py), the
    import reviews past their TTL (gamma/import_staging.py) and the
    downloads a killed process left (gamma/downloads.py). One timer for the
    three: the lifespan's ``async with`` is at Python's limit of nested
    blocks. Each runs whatever the one before
    did, and logs its own failure."""
    for fn, failed in ((ws_backup.sweep_stale_temp, "[backups] temp sweep failed"),
                       (import_staging.sweep, "[imports] staging sweep failed"),
                       (downloads.sweep, "[downloads] sweep failed")):
        try:
            fn()
        except Exception:
            log.exception(failed)


def sweep_jobs() -> None:
    """The jobs' sweep (gamma/jobs.py ``sweep``), then the automatic text
    recognitions left waiting started (gamma/ocr_auto.py ``resume``, which
    never raises): at startup, after ``jobs.recover`` failed the runs a
    restart cut short, then every ``jobs.SWEEP_INTERVAL_S``. One timer for
    the two, as ``sweep_temp`` says why."""
    jobs.sweep()
    ocr_auto.resume()


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


@asynccontextmanager
async def zotero_live():
    """While the app runs: Zotero's streaming API (gamma/zotero_stream.py),
    which starts the rounds of Live Zotero links as soon as their library
    changes. Off with the sync loops (``GAMMA_SYNC_INTERVAL=0``); the links
    keep in step without it, at their safety poll. At its end this
    computer's channels to other servers stop too (gamma/computers/channel.py,
    which ``folder_links.tick`` starts): the lifespan's ``async with`` is at
    Python's limit of nested blocks, so the two share it."""
    zotero_stream.start()
    try:
        yield
    finally:
        computer_channel.stop_all()
        await asyncio.to_thread(zotero_stream.stop)


def create_app() -> FastAPI:
    config.shell_secret()  # read once and taken off the environment, before anything starts a process
    setup_logging()
    _silence_windows_connection_reset()
    _verify_https_with_certifi_when_no_cas()
    net_proxy.install()  # route every bare urlopen (its TLS context is made at the first https request)
    log.info(f"[startup] Gamma {version.label()}")
    try:
        check_publish_config()
    except ValueError as e:
        log.error(f"[startup] {e}")
        raise SystemExit(1)
    guide = _upgrade_data_directory()
    if guide:
        return standby.standby_app(guide)
    mcp = LazyMCP()
    @asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = THREAD_TOKENS
        # The MCP lifespan's yield is request state (its runtime, read by the
        # /mcp route from scope["state"]) — it must pass through here.
        async with mcp.lifespan(app) as state, backup_schedule.lifespan(), \
                every(cloud_sync.CHECK_INTERVAL, cloud_sync.check_all, "cloud: the grant check failed"), \
                every(cloud_deletions.TICK_S, cloud_deletions.tick, "cloud: the deleted accounts' round failed"), \
                every(hosted.SYNC_INTERVAL, hosted.tick, "[hosted] the plan sync failed"), \
                every(guests.SWEEP_INTERVAL_S, guests.delete_expired, "[guests] sweep failed"), \
                every(trash.SWEEP_INTERVAL_S, trash.sweep, "[trash] sweep failed"), \
                every(comments.SWEEP_INTERVAL_S, comments.sweep, "[comments] sweep failed"), \
                every(email_invites.SWEEP_INTERVAL_S, email_invites.sweep, "[invites] sweep failed"), \
                every(reports.SWEEP_INTERVAL_S, reports.tick, "[reports] round failed"), \
                every(inbox.SWEEP_INTERVAL_S, inbox.sweep, "[inbox] sweep failed"), \
                every(ws_backup.STALE_TEMP_S, sweep_temp, "[temp] sweep failed"), \
                every(jobs.SWEEP_INTERVAL_S, sweep_jobs, "[jobs] sweep failed"), \
                every(db_maintenance.EVERY_S, db_maintenance.tick, "[db] maintenance failed"), \
                every(offsite.wait_s, offsite.tick, "[offsite] round failed"), \
                every(folder_links.TICK_S, folder_links.tick, "[folders] round failed"), \
                every(folder_links.HOT_S, folder_links.hot_tick, "[folders] a page's round failed"), \
                every(workspaces.LEFTOVERS_EVERY_S, workspaces.remove_leftovers,
                      "[workspaces] leftover sweep failed"), \
                zotero_live():
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

    @app.exception_handler(cloud_auth.InvitationError)
    async def invitation_not_mailed(request: Request, exc: cloud_auth.InvitationError):
        # The account server did not mail an invitation by e-mail
        # (gamma/email_invites.py): whichever router sent or resent it
        # answers the inviter with the status the failure carries.
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(ai_roles.ModelUnavailable)
    async def model_unavailable(request: Request, exc: ai_roles.ModelUnavailable):
        # A request naming a model no longer offered (ai_roles.resolve_model),
        # from whichever route: the 400 with its kind beside the sentence, the
        # shape the chat's error card and the translator read.
        return JSONResponse({"detail": exc.detail, "kind": exc.kind}, status_code=exc.status_code)

    @app.exception_handler(workspaces.WorkspaceDeleting)
    async def workspace_deleting(request: Request, exc: workspaces.WorkspaceDeleting):
        # A change to a workspace a delete holds (workspaces.refuse_writes):
        # the 423 with a code a script can test beside its header, which
        # the web app's fetch wrapper turns the tab read-only on.
        return JSONResponse({"detail": exc.detail, "error": "workspace_deleting", "workspace": exc.ws},
                            status_code=exc.status_code, headers=exc.headers)

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
    app.include_router(workspaces_router.jobs_router)
    app.include_router(ws_backups.router)
    app.include_router(ws_backups.transfers)
    app.include_router(backup_tasks.router)
    app.include_router(jobs_router.router)
    app.include_router(ai.router)
    app.include_router(agent_changes_router.router)
    app.include_router(translate.router)
    app.include_router(ink_ai.router)
    app.include_router(ai_handoffs.router)
    app.include_router(chats.router)
    app.include_router(chats.history_router)
    app.include_router(skills_router.router)
    app.include_router(prefs.router)
    app.include_router(notices.router)
    app.include_router(inbox_router.router)
    app.include_router(mentions.router)
    app.include_router(integrations.router)
    app.include_router(mcp_oauth_router)
    app.router.routes.append(mcp.route())
    app.include_router(metadata.router)
    app.include_router(citations_router.router)
    app.include_router(search.router)
    app.include_router(shares.router)
    app.include_router(pdf.router)
    app.include_router(publisher_sessions.router)
    app.include_router(uploads.router)
    app.include_router(ink.router)
    app.include_router(recordings.router)
    app.include_router(blocks.router)
    app.include_router(pages.router)
    app.include_router(ocr_router.router)
    app.include_router(trash_router.router)
    app.include_router(imports.router)
    app.include_router(export.router)
    app.include_router(links.router)
    app.include_router(clip.router)
    app.include_router(folders.router)
    app.include_router(collab.router)
    app.include_router(comments_router.router)
    app.include_router(annotations_router.router)
    app.include_router(local_agents_api.router)
    annotation_listeners.register()  # what keeps the annotation files and kept versions current
    local_agents_runner.register()  # the agents' turns stop when the server does
    app.include_router(reports_router.router)
    app.include_router(sync.router)
    app.include_router(mirrors.router)
    app.include_router(folder_links_api.router)
    app.include_router(computers_api.router)        # a computer's channel to this server, every server
    app.include_router(computer_links_api.router)   # this computer's channels to others, the desktop app's
    app.include_router(remote_servers_api.router)   # the other servers' addresses and certificates, the desktop app's
    app.include_router(publish.router)
    app.include_router(zotero_router.router)
    app.include_router(zotero_sync_router.router)  # importing it registers the Zotero link's clock

    # The built frontend (GAMMA_STATIC_DIR), registered last so every /api
    # route takes precedence.
    spa.serve(app)

    _startup_maintenance()

    sync_engine.start_loop()
    upload_gc.start()
    ocr_engine.start_check()  # whether this server's engine starts, known before anyone asks for a run
    return app


app = create_app()
