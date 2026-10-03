#!/bin/sh
# Gamma container entrypoint: prepare the data volume, then start the server.
set -e

# Optional PUID/PGID (linuxserver.io convention): when either is set, own the
# data volume as that uid:gid and drop root before starting, so files Gamma
# creates on a bind mount belong to the host user instead of root. Unset →
# runs as root, exactly as before. setpriv ships in the base image; the
# dropped uid needs no passwd entry, so HOME moves somewhere writable.
AS_USER=""
if [ -n "${PUID}${PGID}" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    # Non-fatal: some mounts (Docker Desktop bind mounts, NFS with squash)
    # refuse chown but already present the files as the mounting user.
    chown -R "${PUID}:${PGID}" "${GAMMA_DATA_DIR:-/data}" \
        || echo "[entrypoint] warning: could not chown ${GAMMA_DATA_DIR:-/data}"
    export HOME=/tmp
    AS_USER="setpriv --reuid=${PUID} --regid=${PGID} --clear-groups"
fi

# Bring the data directory to this Gamma's schema version first (a
# snapshot of the databases is taken before any step; nothing to do on a
# fresh volume or an up-to-date one). Every other manage.py command, `setup`
# below included, refuses an outdated data directory. A directory this
# release cannot upgrade (older than its floor, newer than it, a failed
# step) is left as it is: `migrate` prints what to do, `setup` is skipped,
# and the server below starts anyway to show the same guidance at its
# address, instead of this container exiting into a restart loop.
# --global-only: the users.db steps; each workspace's own steps run when it
# is first opened after the upgrade (docs/dev/migrations.md).
if $AS_USER python manage.py migrate --global-only; then
    # Idempotent: gives every account a personal workspace and repairs missing workspace files.
    # First-run accounts are the app's own job: an empty instance seeds an
    # "admin" account with a random password printed once to the container log
    # (override via GAMMA_ADMIN_USER/GAMMA_ADMIN_PASSWORD) at startup and
    # never touches accounts again. Locked out? Use:
    #   docker exec <container> python manage.py set-password <user> <new>
    $AS_USER python manage.py setup
else
    echo "[entrypoint] the data directory was not upgraded; the server starts to show what to do"
fi

# --proxy-headers: the client address (and scheme) come from X-Forwarded-For
# / -Proto when the peer is one FORWARDED_ALLOW_IPS lists (the Dockerfile's
# default: loopback only — behind a reverse proxy, set it to the proxy).
exec $AS_USER uvicorn app:app --host 0.0.0.0 --port "${GAMMA_PORT:-9001}" --proxy-headers
