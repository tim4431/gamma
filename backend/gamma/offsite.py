"""Off-site copies: the databases and the uploaded files copied to an
S3-compatible bucket, so a lost disk costs at most one interval of work
(docs/dev/debugging.md "Off-site copies in a bucket"). The app always works
on its local files; the bucket is a backup target only, written by the
rounds and read by a restore.

The settings (``settings``, the one place that merges their two sources):
an admin saves them in Settings → Backups (``save``: users.db ``settings``
key ``offsite``, the secret key Fernet-encrypted with the data directory's
key like the shared AI provider keys), unless ``GAMMA_S3_BUCKET`` is set:
then every field comes from the environment (``config.offsite_env``) and
the pane shows them read-only (``from_env``).

``tick``, which the app's ``every()`` loop runs at startup and then after
each pause of ``wait_s``, runs one round while the copies are on;
``run_now`` starts one in a thread (the pane's Copy now). One round runs at
a time. A round reads the settings as it starts, and ``wait_s`` reads the
interval as it ends, so a change applies from the next round, and a new
interval from the pause after it. A round:

- Copies users.db and each workspace's pages.db and data.db that changed
  to ``<prefix>offsite/users/<stamp>.db`` and
  ``<prefix>offsite/<ws>/<stamp>-pages.db`` / ``-data.db``, ``<stamp>`` the
  round's UTC time (``20261003T140000Z``), one for every copy of a round.
  The newest ``keep`` copies of each database are kept.
- Each copy is taken with the backup API into a temp file under
  ``backups/.offsite/`` (consistent while the server writes:
  ``backups.snapshot_db``), quick-checked and uploaded only when it passes.
  One that fails is skipped (the check's warning and the admins'
  ``db-damage`` notice, gamma/integrity.py) and tried again next round.
- Only what changed is copied: a round compares each file's mtime and size,
  and its WAL's, with what they were when its last copy was taken (the
  state file, ``backups/offsite.json``), so an unchanged database costs two
  stats. Before a copy a WAL with frames in it is checkpointed and
  truncated when nothing holds it (``db_maintenance.truncate_wal``, which
  never waits): the server folding the WAL into the file later, as its last
  connection to it closes, then changes nothing.
- Then the uploads. A workspace whose ``uploads/`` directory changed since
  the last round (its mtime, in the state file), and every workspace in the
  first round, has ``<prefix>uploads/<ws>/`` listed once, and each local
  file the bucket lacks (or holds at another size) is streamed up under its
  name. Names are content hashes, so the next round uploads nothing. A file
  deleted here stays in the bucket, like a deleted workspace's copies. The
  databases go first: every file a database copy names is in the bucket
  once the same round's uploads are done.
- A copy or an upload that fails is logged and the round goes on with the
  others; a round logs one line when it copied or failed something.
- A data directory never prunes the copies older than its own first round
  (``since`` in the state file): a server started on an empty disk (a lost
  volume, before anyone restored it) copies its fresh users.db, and must
  not push the old copies out. A restore makes them its own.

``status`` is what the pane shows, from memory and the state file;
``restore`` (``manage.py offsite --restore``, the server stopped) puts
copies back in place; ``litestream_config`` writes a Litestream
configuration for the same bucket (``manage.py litestream-config``).
"""

import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken

from . import config, db, integrity, s3, storage
from .backups import snapshot_db
from .db_maintenance import truncate_wal
from .logbuf import log
from .publisher_sessions import cipher
from .server_settings import _set_raw

SETTINGS_KEY = "offsite"                 # users.db ``settings``: the saved settings, as JSON
FIELDS = ("enabled", "bucket", "endpoint", "region", "access_key", "secret_key", "prefix", "interval_s", "keep")
DEFAULT_INTERVAL_S, INTERVAL_MIN_S, INTERVAL_MAX_S = 3600, 60, 30 * 24 * 3600
DEFAULT_KEEP, KEEP_MAX = 7, 1000
SPACE = "offsite"                        # the databases' copies in the bucket, beside uploads/
UPLOADS = "uploads"                      # the uploaded files in the bucket: uploads/<ws>/<name>
STATE_FILE = "offsite.json"              # under backups/: what each copy saw, since when, the last round
WORK_DIR = ".offsite"                    # under backups/: the database copies on their way up
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
_COPY_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:-(pages|data))?\.db$")
KINDS = ("pages", "data")                # a workspace's databases; users.db is kind ""
_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]$")
_REGION_RE = re.compile(r"^[A-Za-z0-9_-]{0,64}$")
_UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,254}$")  # what a restore writes into uploads/

_round_lock = threading.Lock()           # held by the round in progress
_save_lock = threading.Lock()
# The newest round this process ran, {where, stamp, copied, uploads_copied,
# failed, error}; the state file keeps the same (without where) across restarts.
_last_round: dict | None = None
_next_round_at: float | None = None      # when the every() loop runs the next round (``wait_s``)


class RestoreError(ValueError):
    """A restore that cannot be done; nothing was changed."""


class FromEnvError(ValueError):
    """The environment sets the bucket (``GAMMA_S3_BUCKET``): the saved
    settings cannot be changed."""


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _iso(stamp: str) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.strptime(stamp, "%Y%m%dT%H%M%SZ"))


# --- the settings --------------------------------------------------------------------

def _number(raw, default: int, least: int, most: int) -> int:
    """A stored or environment number within bounds; one that does not
    parse takes the default."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, least), most)


def _saved() -> dict:
    """The saved settings, the secret key decrypted ("" when it no longer
    decrypts: the data directory's key changed); {} when there are none.
    Read on a connection of its own that never creates users.db, so it
    works before the schema guard (manage.py) and on an empty data
    directory, and holds nothing a restore's in-use check would see."""
    path = Path(db.USERS_DB)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
        value = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    sealed = value.get("secret_key")
    try:
        value["secret_key"] = cipher().decrypt(sealed.encode("ascii")).decode("utf-8") \
            if isinstance(sealed, str) and sealed else ""
    except (InvalidToken, ValueError, OSError):
        log.warning("[offsite] the saved secret key cannot be decrypted (the data directory's key changed?)")
        value["secret_key"] = ""
    return value


def settings() -> dict:
    """The off-site settings in force: ``{enabled, bucket, endpoint, region,
    access_key, secret_key, prefix, interval_s, keep, from_env}``. With
    ``GAMMA_S3_BUCKET`` set, all from the environment (``GAMMA_S3_*``,
    ``GAMMA_OFFSITE`` on unless 0/false/no/off, ``GAMMA_OFFSITE_INTERVAL``,
    ``GAMMA_OFFSITE_KEEP``); else the saved ones, off until an admin turns
    them on. The interval is 3600 s and the copies kept 7 by default."""
    env = config.offsite_env()
    if env["bucket"]:
        flag = env["enabled"].lower()
        conf = {"enabled": flag not in ("0", "false", "no", "off"),
                **{k: env[k] for k in ("bucket", "endpoint", "region", "access_key", "secret_key", "prefix")},
                "interval_s": _number(env["interval"], DEFAULT_INTERVAL_S, INTERVAL_MIN_S, INTERVAL_MAX_S),
                "keep": _number(env["keep"], DEFAULT_KEEP, 1, KEEP_MAX), "from_env": True}
    else:
        saved = _saved()

        def text(name):
            value = saved.get(name)
            return value if isinstance(value, str) else ""

        conf = {"enabled": saved.get("enabled") is True and bool(text("bucket")),
                **{k: text(k) for k in ("bucket", "endpoint", "region", "access_key", "secret_key", "prefix")},
                "interval_s": _number(saved.get("interval_s"), DEFAULT_INTERVAL_S, INTERVAL_MIN_S, INTERVAL_MAX_S),
                "keep": _number(saved.get("keep"), DEFAULT_KEEP, 1, KEEP_MAX), "from_env": False}
    return conf


def public(conf: dict) -> dict:
    """The settings as the admin API answers them: the secret key never,
    only whether one is set (``secret_set``)."""
    return {**{k: conf[k] for k in FIELDS if k != "secret_key"}, "secret_set": bool(conf["secret_key"])}


def _checked(current: dict, fields: dict) -> dict:
    """``fields`` (any of FIELDS; ``secret_key`` absent or empty keeps
    ``current``'s, and goes with an access key cleared) laid over
    ``current`` and checked: ``{field: value}`` for every one of FIELDS.
    ValueError with the reason a person can act on."""
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(unknown)}.")
    conf = {**{k: current.get(k) for k in FIELDS},
            **{k: v for k, v in fields.items() if k != "secret_key" and v is not None}}

    def text(name: str, what: str, most: int = 256) -> str:
        value = conf.get(name) or ""
        if not isinstance(value, str):
            raise ValueError(f"{what} must be text.")
        value = value.strip()
        if len(value) > most or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"{what} must be one word of at most {most} characters.")
        return value

    if not isinstance(conf["enabled"], bool):
        raise ValueError("enabled must be true or false.")
    bucket = text("bucket", "The bucket")
    if bucket and not _BUCKET_RE.match(bucket):
        raise ValueError("The bucket's name must be 3 to 63 letters, digits, dots, hyphens or underscores, "
                         "starting and ending with a letter or digit.")
    if conf["enabled"] and not bucket:
        raise ValueError("Set a bucket before turning off-site copies on.")
    endpoint = text("endpoint", "The endpoint", 300).rstrip("/")
    if endpoint:
        try:
            url = urlsplit(endpoint)
            ok = url.scheme in ("http", "https") and bool(url.hostname) and url.port != 0
        except ValueError:
            ok = False
        if not ok or url.username is not None or url.query or url.fragment:
            raise ValueError("The endpoint must be an http or https address, like "
                             "https://<account>.r2.cloudflarestorage.com (leave it empty for AWS).")
    region = text("region", "The region", 64)
    if not _REGION_RE.match(region):
        raise ValueError("The region must be letters, digits and hyphens, like us-east-1 (R2: auto).")
    access_key = text("access_key", "The access key")
    secret = fields.get("secret_key")
    if secret is not None and not isinstance(secret, str):
        raise ValueError("The secret key must be text.")
    if secret and secret.strip():
        conf["secret_key"] = secret
        if not access_key:
            raise ValueError("Give the access key that goes with the secret key.")
    secret_key = text("secret_key", "The secret key") if access_key else ""
    if access_key and not secret_key:
        raise ValueError("Give the secret key that goes with the access key.")
    prefix = text("prefix", "The prefix", 200).strip("/")
    if prefix:
        try:
            s3.check_key(prefix)
        except ValueError:
            raise ValueError("The prefix must be names separated by /, without . or .. names or "
                             "backslashes.") from None
    interval, keep = conf["interval_s"], conf["keep"]
    if isinstance(interval, bool) or not isinstance(interval, int) or not INTERVAL_MIN_S <= interval <= INTERVAL_MAX_S:
        raise ValueError(f"The interval must be a whole number of seconds from {INTERVAL_MIN_S} to "
                         f"{INTERVAL_MAX_S}.")
    if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= KEEP_MAX:
        raise ValueError(f"The copies kept must be a whole number from 1 to {KEEP_MAX}.")
    return {"enabled": conf["enabled"], "bucket": bucket, "endpoint": endpoint, "region": region,
            "access_key": access_key, "secret_key": secret_key, "prefix": prefix, "interval_s": interval,
            "keep": keep}


def save(fields: dict) -> dict:
    """Store ``fields`` (any of FIELDS, ``_checked``) over the saved
    settings, the secret key encrypted; the new ``settings()``. FromEnvError
    while the environment sets the bucket, ValueError when a field is
    wrong (nothing saved). The rounds read them as each starts."""
    if config.offsite_env()["bucket"]:
        raise FromEnvError("The off-site copies are set by GAMMA_S3_BUCKET and the other GAMMA_S3_* and "
                           "GAMMA_OFFSITE* variables in the server's environment.")
    with _save_lock:
        conf = _checked(settings(), fields)
        secret = conf["secret_key"]
        _set_raw(SETTINGS_KEY, json.dumps({
            **conf, "secret_key": cipher().encrypt(secret.encode("utf-8")).decode("ascii") if secret else ""}))
    return settings()


def test(fields: dict | None = None) -> dict:
    """The pane's Test: one listing of the bucket (``s3.Client.check``),
    tried once. ``fields``: unsaved settings laid over the saved ones like
    ``save`` would (ignored while the environment sets the bucket).
    ``{ok: True, message: "<bucket>: <n> objects under <prefix>"}``, or
    ``{ok: False, message: <why>}``."""
    conf = settings()
    if fields and not conf["from_env"]:
        try:
            conf = _checked(conf, {**fields, "enabled": False})
        except ValueError as e:
            return {"ok": False, "message": str(e)}
    if not conf["bucket"]:
        return {"ok": False, "message": "No bucket is set."}
    try:
        bucket = s3.Client.from_settings(conf, attempts=1)
        count, more = bucket.check()
    except s3.S3ConfigError as e:
        return {"ok": False, "message": str(e)}
    held = f"{count} or more objects" if more else f"{count} object{'' if count == 1 else 's'}"
    return {"ok": True, "message": f"{conf['bucket']}: {held} " +
            (f"under {bucket.prefix}" if bucket.prefix else "in the bucket")}


# --- naming --------------------------------------------------------------------------

def key(group: str, kind: str, stamp: str) -> str:
    """The object of one copy: ``offsite/users/<stamp>.db`` for users.db
    (``kind`` ""), ``offsite/<ws>/<stamp>-<kind>.db`` for a workspace's."""
    return f"{SPACE}/{group}/{stamp}{'-' + kind if kind else ''}.db"


def _label(group: str, kind: str) -> str:
    return f"{group}/{kind}.db" if kind else "users.db"


def _rel(group: str, kind: str) -> str:
    """The database's path in the data directory, as gamma/integrity.py
    names it."""
    return f"workspaces/{group}/{kind}.db" if kind else "users.db"


def _path(group: str, kind: str) -> Path:
    return Path(db.ws_db_path(group, f"{kind}.db")) if kind else Path(db.USERS_DB)


def _databases() -> list[tuple[str, str]]:
    """``(group, kind)`` of users.db and of every workspace's two databases."""
    return [("users", "")] + [(ws, kind) for ws in db.workspace_ids() for kind in KINDS]


def _signal(path: Path) -> list | None:
    """What says a database changed: the mtime and size of the file and of
    its WAL (an empty WAL counts as none); None when there is no file."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    seen = [st.st_mtime_ns, st.st_size]
    try:
        wal = os.stat(f"{path}-wal")
    except FileNotFoundError:
        return seen
    return seen + [wal.st_mtime_ns, wal.st_size] if wal.st_size else seen


def _some(names, n: int = 5) -> str:
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


# --- the state file ------------------------------------------------------------------

def _state_path() -> Path:
    return config.BACKUPS_DIR / STATE_FILE


def _load_state():
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_state(where: str, stamp: str) -> dict:
    """``{store, since, files: {label: signal}, uploads: {ws: mtime},
    last_round}``; a fresh one (nothing copied, ``since`` this round) when
    there is none, it is not of that shape or it was another bucket's."""
    state = _load_state()
    if not (isinstance(state, dict) and state.get("store") == where
            and isinstance(state.get("files"), dict) and isinstance(state.get("uploads"), dict)):
        return {"store": where, "since": stamp, "files": {}, "uploads": {}}
    return state


def _write_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


# --- the round -----------------------------------------------------------------------

def _generations(bucket: s3.Client, group: str | None = None) -> dict:
    """``{(group, kind): [(stamp, size), ...]}``, oldest first: the copies
    the bucket holds of one group (``users`` or a workspace) or of all."""
    held: dict = {}
    for object_key, size, _mtime in bucket.list_objects(f"{SPACE}/{group}/" if group else f"{SPACE}/"):
        parts = object_key.split("/")
        m = _COPY_RE.match(parts[2]) if len(parts) == 3 else None
        if not m or (parts[1] == "users") != (not m.group(2)):  # users.db's under users/, a workspace's two elsewhere
            continue
        held.setdefault((parts[1], m.group(2) or ""), []).append((m.group(1), size))
    for gens in held.values():
        gens.sort()
    return held


def _copy(bucket: s3.Client, path: Path, object_key: str, work: Path) -> str:
    """Copy one database to ``object_key``: a backup-API copy into
    ``work``, quick-checked, uploaded when the check passes. Returns the
    check's result."""
    tmp = work / f"{secrets.token_hex(8)}.db"
    try:
        check = snapshot_db(path, tmp)
        if check == "ok":
            bucket.put_object(object_key, tmp)
        return check
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{tmp}{suffix}").unlink(missing_ok=True)


def _prune(bucket: s3.Client, group: str, kinds, keep: int, since: str) -> int:
    """Remove all but the newest ``keep`` copies of the group's ``kinds``,
    never one from before ``since``; how many went."""
    removed = 0
    held = _generations(bucket, group)
    for kind in kinds:
        for stamp, _size in held.get((group, kind), [])[:-keep]:
            if stamp >= since:
                bucket.delete_object(key(group, kind, stamp))
                removed += 1
    return removed


def _copy_databases(bucket: s3.Client, state: dict, stamp: str, keep: int, done: dict) -> dict:
    """The round's databases (above); the copies' checks, for
    gamma/integrity.py."""
    work = config.BACKUPS_DIR / WORK_DIR
    shutil.rmtree(work, ignore_errors=True)  # what a killed round left
    work.mkdir(parents=True, exist_ok=True)
    checks, copied = {}, {}
    try:
        for group, kind in _databases():
            label, path = _label(group, kind), _path(group, kind)
            try:
                seen = _signal(path)
                if seen is None:
                    continue
                if state["files"].get(label) == seen:
                    done["unchanged"] += 1
                    continue
                if len(seen) > 2 and truncate_wal(str(path)):  # a WAL with frames, folded in now if nothing holds it
                    seen = _signal(path)
                    if seen is None:
                        continue
                checks[_rel(group, kind)] = check = _copy(bucket, path, key(group, kind, stamp), work)
            except (OSError, sqlite3.Error, ValueError) as e:  # a bucket's refusal is an S3Error, an OSError
                done["failed"][label] = str(e)
                continue
            if check != "ok":
                done["failed"][label] = f"the copy failed its check: {check}"
                continue
            state["files"][label] = seen
            done["copied"].append(label)
            copied.setdefault(group, []).append(kind)
        for group, kinds in copied.items():
            try:
                done["pruned"] += _prune(bucket, group, kinds, keep, state.get("since", ""))
            except (OSError, ValueError) as e:
                done["failed"][f"pruning {group}"] = str(e)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return checks


def _copy_uploads(bucket: s3.Client, state: dict, done: dict) -> None:
    """The round's uploads (above). A workspace's directory mtime is taken
    before its files are listed and recorded once all of them are up, so a
    file added meanwhile, or one that failed, makes the next round look
    again."""
    seen, present = state["uploads"], set()
    for ws in db.workspace_ids():
        folder = db.ws_uploads_dir(ws)
        try:
            mtime = os.stat(folder).st_mtime_ns
        except OSError:
            continue
        present.add(ws)
        if seen.get(ws) == mtime:
            continue
        prefix = f"{UPLOADS}/{ws}/"
        try:
            local = {name: size for name, size, _ in storage.list(ws)}
            held = {}
            if local:  # one listing of the workspace's objects, never a HEAD per file
                held = {k[len(prefix):]: size for k, size, _ in bucket.list_objects(prefix)}
            for name, size in sorted(local.items()):
                if held.get(name) == size:
                    continue
                try:
                    bucket.put_object(prefix + name, folder / name)
                except FileNotFoundError:
                    continue  # deleted since the listing (the upload GC's purge)
                done["uploads_copied"] += 1
        except (OSError, ValueError) as e:
            done["failed"][f"{ws}/uploads"] = str(e)
            continue
        seen[ws] = mtime
    for ws in [w for w in seen if w not in present]:
        del seen[ws]  # a deleted workspace: its files stay in the bucket


def _round() -> dict:
    """One round with the round lock held; see ``tick``."""
    global _last_round
    done = {"copied": [], "uploads_copied": 0, "failed": {}, "unchanged": 0, "pruned": 0}
    conf = settings()
    if not conf["enabled"]:
        return done
    started, stamp = time.monotonic(), _stamp()
    try:
        bucket = s3.Client.from_settings(conf)
    except s3.S3ConfigError as e:
        log.warning(f"[offsite] no round: {e}")
        _last_round = {"where": s3.where(conf["bucket"], conf["endpoint"], conf["prefix"]), "stamp": stamp,
                       "copied": 0, "uploads_copied": 0, "failed": 0, "error": str(e)}
        return {**done, "error": str(e)}
    state = _read_state(bucket.where, stamp)
    checks = _copy_databases(bucket, state, stamp, conf["keep"], done)
    _copy_uploads(bucket, state, done)

    def summary():
        error = next((f"{label}: {why}" for label, why in done["failed"].items()), None)
        return {"stamp": stamp, "copied": len(done["copied"]), "uploads_copied": done["uploads_copied"],
                "failed": len(done["failed"]), "error": error}

    state["last_round"] = summary()
    try:
        _write_state(state)
    except OSError as e:  # the next round copies these again
        done["failed"][str(_state_path())] = str(e)
    if checks:
        integrity.record(checks, "copy to the bucket")
    if done["copied"] or done["uploads_copied"] or done["failed"]:
        parts = []
        if done["copied"] or done["uploads_copied"]:
            what = ([_some(done["copied"])] if done["copied"] else []) + \
                   ([f"{done['uploads_copied']} upload(s)"] if done["uploads_copied"] else [])
            pruned = f"{done['pruned']} old cop{'y' if done['pruned'] == 1 else 'ies'} removed"
            parts.append(f"copied {' and '.join(what)} to {bucket.where} in {time.monotonic() - started:.1f} s "
                         f"({done['unchanged']} database(s) unchanged, {pruned})")
        if done["failed"]:
            failed = [f"{label} ({why})" for label, why in done["failed"].items()]
            parts.append(f"failed: {_some(failed, 3)}")
        (log.warning if done["failed"] else log.info)(f"[offsite] {'; '.join(parts)}")
    _last_round = {"where": bucket.where, **summary()}
    return done


def tick() -> dict:
    """One round (above), none with the copies off or while another round
    runs (``skipped`` says so). Returns ``copied`` (database labels:
    ``users.db``, ``<ws>/pages.db``), ``uploads_copied`` (files uploaded),
    ``failed`` ({label: why}; ``<ws>/uploads`` for a workspace's files),
    ``unchanged`` (databases passed by) and ``pruned`` (old copies
    removed), plus ``error`` when the settings name no bucket that can
    work."""
    if not _round_lock.acquire(blocking=False):
        return {"copied": [], "uploads_copied": 0, "failed": {}, "unchanged": 0, "pruned": 0,
                "skipped": "a round is running"}
    try:
        return _round()
    finally:
        _round_lock.release()


def _run_held() -> None:
    try:
        _round()
    except Exception:  # noqa: BLE001 — a thread of its own: logged, like the every() loop's
        log.exception("[offsite] round failed")
    finally:
        _round_lock.release()


def run_now() -> dict:
    """The pane's Copy now: a round in a thread of its own, now.
    ``{"started": True}``, or ``{"started": False, "message": why}`` with
    the copies off or a round running."""
    if not settings()["enabled"]:
        return {"started": False, "message": "off-site copies are off"}
    if not _round_lock.acquire(blocking=False):
        return {"started": False, "message": "a round is running"}
    try:
        threading.Thread(target=_run_held, name="offsite-round", daemon=True).start()
    except BaseException:
        _round_lock.release()
        raise
    return {"started": True}


def wait_s() -> float:
    """The pause after a round, which the app's ``every()`` loop asks for as
    each round ends: the interval in force, which also dates ``status``'s
    ``next_round_at``. Never raises."""
    global _next_round_at
    try:
        seconds = settings()["interval_s"]
    except Exception:  # noqa: BLE001 — the loop must go on
        log.exception("[offsite] the settings could not be read")
        seconds = DEFAULT_INTERVAL_S
    _next_round_at = time.time() + seconds
    return seconds


def status(conf: dict) -> dict:
    """What the pane shows of the rounds of ``conf`` (the settings as
    ``settings`` resolved them), opening no database: ``{enabled, running,
    interval_s, keep, last_round_at, copied, uploads_copied, failed, error,
    next_round_at}``. The last round is this process's newest, or after a
    restart the one the state file records, of the bucket the settings
    name: its UTC time, how many databases and files it copied, how many
    failed, and why the first did (or why no round could run); all None
    before the first."""
    here = s3.where(conf["bucket"], conf["endpoint"], conf["prefix"]) if conf["bucket"] else ""
    last = _last_round if _last_round and _last_round.get("where") == here else None
    if last is None and here:
        state = _load_state()
        if isinstance(state, dict) and state.get("store") == here and isinstance(state.get("last_round"), dict):
            last = state["last_round"]
    if last and not (isinstance(last.get("stamp"), str) and STAMP_RE.match(last["stamp"])):
        last = None
    last = last or {}
    on = bool(conf["enabled"])
    return {"enabled": on, "running": _round_lock.locked(), "interval_s": conf["interval_s"], "keep": conf["keep"],
            "last_round_at": _iso(last["stamp"]) if last else None, "copied": last.get("copied"),
            "uploads_copied": last.get("uploads_copied"), "failed": last.get("failed"), "error": last.get("error"),
            "next_round_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_next_round_at))
            if on and _next_round_at else None}


# --- listing and restoring (manage.py offsite) ---------------------------------------

def _bucket() -> s3.Client:
    """The bucket the settings name, on or off. S3ConfigError without one."""
    conf = settings()
    if not conf["bucket"]:
        raise s3.S3ConfigError("no bucket is set: save one in Settings → Backups, or set GAMMA_S3_BUCKET and the "
                               "other GAMMA_S3_* variables")
    return s3.Client.from_settings(conf)


def listing(group: str | None = None) -> dict:
    """``{label: [(stamp, size), ...]}``, oldest first: the copies the bucket
    holds of ``group`` (``users``, a workspace id) or of every database."""
    if group and group != "users":
        db.safe_ws_id(group)
    held = _generations(_bucket(), group)
    return {_label(g, k): gens for (g, k), gens in sorted(held.items())}


def uploads_held(ws: str) -> tuple[int, int]:
    """How many of the workspace's files the bucket holds, and their bytes."""
    prefix = f"{UPLOADS}/{db.safe_ws_id(ws)}/"
    files = [size for k, size, _ in _bucket().list_objects(prefix) if "/" not in k[len(prefix):]]
    return len(files), sum(files)


def _in_use(path: Path) -> str:
    """Why the database at ``path`` cannot be had alone, "" when it can: an
    exclusive open that never waits, which fails while any other
    connection, in this process or another, has the file open and has
    read it (a running server's do)."""
    try:
        uri = path.resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)) as conn:
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.execute("ROLLBACK")
    except sqlite3.OperationalError as e:
        return str(e)
    except sqlite3.DatabaseError:
        return ""  # not a database SQLite can read: nothing uses it as one
    return ""


def _move_aside(path: Path, now: str) -> Path | None:
    """Move the database at ``path``, with its WAL or rollback journal, to
    ``<name>.pre-restore-<now>`` (it opens there as it was) and drop its
    ``-shm``; the moved file, None when there was none."""
    aside = path.with_name(f"{path.name}.pre-restore-{now}")
    had = path.exists()
    for suffix in ("", "-wal", "-journal"):
        if Path(f"{path}{suffix}").exists():
            os.replace(f"{path}{suffix}", f"{aside}{suffix}")
    Path(f"{path}-shm").unlink(missing_ok=True)
    return aside if had else None


def _fetch_uploads(bucket: s3.Client, ws: str) -> tuple[int, int]:
    """Download each of the workspace's files the bucket holds that its
    ``uploads/`` lacks, each stored through the store (downloaded into its
    ``.partial/``, then renamed into place); how many, and their bytes. The
    files there stay as they are."""
    partial = storage.partial_dir(ws)
    partial.mkdir(parents=True, exist_ok=True)
    have, prefix, count, size = {name for name, _, _ in storage.list(ws)}, f"{UPLOADS}/{ws}/", 0, 0
    for object_key, n, _mtime in bucket.list_objects(prefix):
        name = object_key[len(prefix):]
        if name in have or not _UPLOAD_NAME_RE.match(name):
            continue  # here already, or nothing an upload is named (in a folder, a dot file)
        tmp = partial / f"offsite-{secrets.token_hex(4)}"
        if bucket.get_object(object_key, tmp):
            storage.put_path(ws, name, tmp)
            count, size = count + 1, size + n
    return count, size


def restore(target: str, at: str | None = None, *, uploads: bool = False) -> list[dict]:
    """Put copies back in place, the server STOPPED. ``target``: ``users``
    (users.db), a workspace id (its pages.db and data.db) or ``all``
    (users.db and every workspace the bucket holds copies of). Each
    database gets its newest copy or, with ``at`` (a stamp as ``listing``
    shows it), its newest at or before it: what it held at that round,
    since a database is copied whenever it changed. A workspace without a
    pages.db copy that early is left out of ``all``; its data.db without
    one stays as it is (all of it can be rebuilt). ``uploads``: also each
    restored workspace's files the bucket holds that its ``uploads/``
    lacks (``_fetch_uploads``), downloaded before any database moves.

    Refused (RestoreError) with nothing changed when users.db or a
    database to replace is in use (``_in_use``: a running server holds
    the files it used lately), when there is no copy to restore, when a
    download fails its quick check (every copy is downloaded beside its
    database first), and without ``at`` when a database has copies from
    both sides of this data directory's first round: a server started on
    an empty disk copied its fresh databases, which are the newest now,
    and the operator must say which to take. Then each database in place
    is moved aside, with its WAL, as ``<name>.pre-restore-<time>``, and the
    copy takes its name; a restored pages.db's unreferenced-file clocks
    start over (``upload_gc.restart_clocks``). The data directory takes the
    bucket's copies for its own: its rounds may prune the older ones again.
    Returns ``[{"label", "stamp", "aside"}]`` for the databases, then
    ``{"label": "<ws>/uploads", "files", "bytes"}`` for each workspace's
    files with ``uploads``."""
    from . import upload_gc  # local: only a restore needs it

    if at is not None and not STAMP_RE.match(at):
        raise RestoreError(f"--at takes a stamp as --list shows it (like 20261003T140000Z), not {at!r}")
    if target not in ("users", "all"):
        try:
            db.safe_ws_id(target)
        except ValueError:
            raise RestoreError(f"{target!r} is no workspace id") from None
    bucket = _bucket()
    held = _generations(bucket, None if target == "all" else target)
    since = _read_state(bucket.where, "").get("since", "")
    if at is None and since:
        mixed = [gens for gens in held.values() if gens[0][0] < since <= gens[-1][0]]
        if mixed:
            before = max(stamp for gens in mixed for stamp, _size in gens if stamp < since)
            raise RestoreError(f"the bucket holds copies from before this data directory's first round ({since}): "
                               f"it may have started on an empty disk, so its own are the newest. Pass --at {before} "
                               f"for the newest from before, or --at {since} or later for its own")
    plan = []
    for (group, kind), gens in sorted(held.items()):
        picked = [stamp for stamp, _size in gens if at is None or stamp <= at]
        if picked:
            plan.append((group, kind, picked[-1]))
    primary = {group for group, kind, _ in plan if kind in ("", "pages")}
    plan = [step for step in plan if step[0] in primary]  # a data.db comes back with its pages.db only
    if not plan:
        raise RestoreError(f"{bucket.where} holds no copy of {target}" + (f" from {at} or before" if at else ""))

    checked = sorted({("users", "")} | {(group, kind) for group, kind, _ in plan})
    busy = [f"{_rel(group, kind)} ({why})" for group, kind in checked
            if _path(group, kind).exists() and (why := _in_use(_path(group, kind)))]
    if busy:
        raise RestoreError(f"in use, so the server is running: stop it first ({_some(busy, 3)})")

    fetched, files = [], []
    try:
        for group, kind, stamp in plan:
            path = _path(group, kind)
            tmp = path.with_name(f".{path.name}.restoring")
            if not bucket.get_object(key(group, kind, stamp), tmp):
                raise RestoreError(f"{key(group, kind, stamp)} is gone from {bucket.where}")
            fetched.append(tmp)
            check = integrity.quick_check(tmp)
            if check != "ok":
                raise RestoreError(f"the copy of {_label(group, kind)} from {stamp} failed its check: {check}")
        if uploads:
            for ws in sorted({group for group, kind, _ in plan if kind}):
                count, size = _fetch_uploads(bucket, ws)
                files.append({"label": f"{ws}/{UPLOADS}", "files": count, "bytes": size})
    except BaseException:
        for tmp in fetched:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{tmp}{suffix}").unlink(missing_ok=True)
        raise

    db.close_connections()  # this process's cached handles, before files move under them
    now, done = _stamp(), []
    for (group, kind, stamp), tmp in zip(plan, fetched):
        path = _path(group, kind)
        aside = _move_aside(path, now)
        os.replace(tmp, path)
        if kind == "pages":
            with closing(sqlite3.connect(str(path))) as conn:
                upload_gc.restart_clocks(conn)
        done.append({"label": _label(group, kind), "stamp": stamp, "aside": aside.name if aside else ""})
    try:
        state = _read_state(bucket.where, "")
        state["since"] = ""  # the copies are this data directory's now: older ones may be pruned
        _write_state(state)
    except OSError:
        pass  # the state stays as it was: the older copies are not pruned
    return done + files


# --- Litestream ----------------------------------------------------------------------

def litestream_config() -> tuple[str, int]:
    """A ``litestream.yml`` (Litestream 0.5: one ``replica`` per database)
    for the off-site bucket (``settings``: saved or from the environment),
    with an entry for users.db and for each workspace's pages.db and
    data.db as the data directory holds them now, replicated to
    ``<prefix>litestream/users.db`` and ``<prefix>litestream/<ws>/<name>``.
    The keys are never written: with an access key the file names
    ``${GAMMA_S3_ACCESS_KEY}`` and ``${GAMMA_S3_SECRET_KEY}``, which
    Litestream expands from its own environment as it reads the file.
    Returns the text and how many databases it names; ValueError without a
    bucket."""
    conf = settings()
    if not conf["bucket"]:
        raise ValueError("no bucket is set: save one in Settings → Backups, or set GAMMA_S3_BUCKET (and the other "
                         "GAMMA_S3_* variables) to the bucket to replicate to")
    prefix = s3.key_prefix(conf["prefix"])  # the copies' own, so both live under one prefix
    q = json.dumps  # a JSON string is a YAML double-quoted one
    lines = ["# Litestream's configuration for Gamma's databases, written by `manage.py litestream-config`",
             f"# at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} with an entry for each database there was.",
             "# Litestream does not watch this file: write it again and restart Litestream whenever a",
             "# workspace is added or deleted. docs/dev/debugging.md \"Off-site copies in a bucket\"."]
    if conf["access_key"]:
        lines += ["access-key-id: '${GAMMA_S3_ACCESS_KEY}'", "secret-access-key: '${GAMMA_S3_SECRET_KEY}'"]
    lines.append("dbs:")
    count = 0
    for group, kind in _databases():
        path = _path(group, kind)
        if not path.is_file():
            continue
        lines += [f"  - path: {q(str(path.absolute()))}",
                  "    replica:",
                  "      type: s3",
                  f"      bucket: {q(conf['bucket'])}",
                  f"      path: {q(prefix + 'litestream/' + (f'{group}/{kind}.db' if kind else 'users.db'))}"]
        if conf["endpoint"]:
            lines += [f"      endpoint: {q(conf['endpoint'])}", "      force-path-style: true"]
        if conf["region"]:
            lines.append(f"      region: {q(conf['region'])}")
        count += 1
    if not count:
        lines[-1] = "dbs: []"
    return "\n".join(lines) + "\n", count
