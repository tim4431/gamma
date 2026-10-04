"""The fleet agent's loop: heartbeat every five minutes, long-poll the
account server for the next job, apply it with the Docker SDK, report the
result. Outbound calls only; the account server never reaches the host.

Environment:

- ``GAMMA_FLEET_ACCOUNT_URL`` (required): the account server, e.g.
  ``http://account:9002`` on the same compose network, or
  ``https://account.gammapdf.com`` from another host.
- ``GAMMA_FLEET_HOST_TOKEN`` (required): the host's token from Admin →
  Servers → Add host (or ``manage.py add-host``).
- ``GAMMA_FLEET_NETWORK`` (default ``gamma-fleet``): the Docker network the
  containers join, where Caddy reaches ``gamma-<label>:9001``. Its subnet
  becomes each container's ``FORWARDED_ALLOW_IPS``.
- ``GAMMA_FLEET_DATA_ROOT`` (default ``/srv/gamma``): a server's data is
  ``<root>/<label>/data``, mounted on ``/data``. The agent must see the
  host's path at the same place (mount it at the same path).
- ``GAMMA_FLEET_MEMORY_MB`` (default 768) and ``GAMMA_FLEET_CPUS``
  (default 1): a container's limits when the job names none.
- ``GAMMA_FLEET_S3_BUCKET``, ``_ENDPOINT``, ``_REGION``, ``_ACCESS_KEY``,
  ``_SECRET_KEY``, ``_PREFIX`` (default ``hosted/``): the off-site bucket.
  When the bucket is set every container gets the ``GAMMA_S3_*`` variables
  with the prefix ``<prefix><account id>/``, and a ``delete`` job that
  names the account removes that prefix.

Jobs (``kind`` → payload): ``create`` → ``{label, account_id, plan, image,
env, data_dir, memory_mb, cpus, network, public_url}``; ``start``,
``stop``, ``restart``, ``rollback``, ``logs`` → ``{label}``; ``delete`` →
``{label, account_id}``; ``upgrade`` → ``{label, image?, tag?, memory_mb?,
cpus?}`` (no image: a resize, on the image the container runs). A
container is ``gamma-<label>``; what it was created with (image,
environment, limits) is kept in ``<root>/<label>/container.json`` so
``start``, ``restart`` and ``upgrade`` can rebuild it. Every handler can be
run again after a crash midway and ends in the same place.
"""

import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import VERSION

log = logging.getLogger("gammafleet")

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
HEARTBEAT_EVERY = 300
POLL_WAIT = 25
HEALTH_TIMEOUT = 120
HEALTH_EVERY = 2
STOP_TIMEOUT = 30
CPU_PERIOD = 100_000           # microseconds; a container's CPU limit is a quota of this
DATA_EVERY = 1800              # a data directory is measured at most this often
LOG_LINES = 200
LOG_LINE_MAX = 400
MIB = 1024 * 1024
TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\S+$")


class JobError(Exception):
    """A job that cannot be done; the message goes back as its result."""


@dataclass
class Settings:
    account_url: str
    host_token: str
    network: str = "gamma-fleet"
    data_root: str = "/srv/gamma"
    memory_mb: int = 768
    cpus: float = 1.0
    s3: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        url = env.get("GAMMA_FLEET_ACCOUNT_URL", "").strip().rstrip("/")
        token = env.get("GAMMA_FLEET_HOST_TOKEN", "").strip()
        if not url or not token:
            raise SystemExit("GAMMA_FLEET_ACCOUNT_URL and GAMMA_FLEET_HOST_TOKEN are required")
        s3 = {k: env.get(f"GAMMA_FLEET_S3_{k.upper()}", "").strip()
              for k in ("bucket", "endpoint", "region", "access_key", "secret_key", "prefix")}
        s3["prefix"] = s3["prefix"] or "hosted/"
        return cls(account_url=url, host_token=token,
                   network=env.get("GAMMA_FLEET_NETWORK", "").strip() or "gamma-fleet",
                   data_root=(env.get("GAMMA_FLEET_DATA_ROOT", "").strip() or "/srv/gamma").rstrip("/"),
                   memory_mb=int(env.get("GAMMA_FLEET_MEMORY_MB", "") or 768),
                   cpus=float(env.get("GAMMA_FLEET_CPUS", "") or 1),
                   s3=s3 if s3["bucket"] else {})


class Api:
    """The account server's fleet API (``/api/fleet``) over urllib."""

    def __init__(self, base: str, token: str, opener=None):
        self.base, self.token = base.rstrip("/"), token
        self.opener = opener or urllib.request.urlopen

    def call(self, method: str, path: str, body=None, timeout: float = 30) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": f"gamma-fleet/{VERSION}"})
        with self.opener(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw or b"{}")

    def next_job(self, wait: float = POLL_WAIT) -> dict | None:
        return self.call("GET", f"/api/fleet/jobs?wait={int(wait)}", timeout=wait + 15).get("job")

    def finish(self, job_id: str, state: str, result: dict) -> None:
        self.call("POST", f"/api/fleet/jobs/{job_id}", {"state": state, "result": result})

    def heartbeat(self, body: dict) -> None:
        self.call("POST", "/api/fleet/heartbeat", body)


def http_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def host_stats(data_root: str) -> dict:
    """Memory from /proc/meminfo (total, and used = total − available) and
    the disk the data root is on, in MB."""
    memory = used = 0
    try:
        with open("/proc/meminfo") as f:
            info = {line.split(":")[0]: int(line.split()[1]) for line in f if line.split()[1:2]}
        memory = info.get("MemTotal", 0) // 1024
        used = memory - info.get("MemAvailable", info.get("MemFree", 0)) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        disk = shutil.disk_usage(data_root)
        disk_mb, disk_used = disk.total // MIB, disk.used // MIB
    except OSError:
        disk_mb = disk_used = 0
    return {"memory_mb": memory, "memory_used_mb": max(0, used), "disk_mb": disk_mb, "disk_used_mb": disk_used}


def dir_mb(path: str) -> int:
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total // MIB


def split_image(image: str) -> tuple[str, str]:
    """``repo:tag`` → (repo, tag); no tag is ``latest``."""
    repo, _, tag = image.rpartition(":")
    if not repo or "/" in tag:
        return image, "latest"
    return repo, tag


class S3Prefix:
    """Removes a hosted server's off-site copies (boto3, imported on use)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def delete_prefix(self, prefix: str) -> int:
        import boto3
        s3 = boto3.client("s3", endpoint_url=self.cfg.get("endpoint") or None,
                          region_name=self.cfg.get("region") or None,
                          aws_access_key_id=self.cfg.get("access_key") or None,
                          aws_secret_access_key=self.cfg.get("secret_key") or None)
        n = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=self.cfg["bucket"], Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=self.cfg["bucket"], Delete={"Objects": keys, "Quiet": True})
                n += len(keys)
        return n


class Agent:
    """Applies jobs to one Docker host. ``docker`` is a ``docker.DockerClient``
    (or anything shaped like one); ``not_found`` its NotFound error."""

    def __init__(self, settings: Settings, docker, api, *, not_found=None, health=http_health, sleep=time.sleep,
                 clock=time.monotonic, stats=host_stats, s3=None):
        self.settings, self.docker, self.api = settings, docker, api
        if not_found is None:
            from docker.errors import NotFound as not_found
        self.not_found = not_found
        self.health, self.sleep, self.clock, self.stats = health, sleep, clock, stats
        self.s3 = s3 if s3 is not None else (S3Prefix(settings.s3) if settings.s3 else None)
        self.busy = False
        self.stopping = False
        self._subnet = None
        self._data_mb: dict[str, tuple[float, int]] = {}   # label -> (when measured, MB)

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def name(label: str) -> str:
        return f"gamma-{label}"

    @staticmethod
    def label(payload: dict) -> str:
        label = str(payload.get("label") or "")
        if not LABEL_RE.match(label):
            raise JobError(f"bad label {label!r}")
        return label

    def server_dir(self, label: str) -> str:
        return os.path.join(self.settings.data_root, label)

    def spec_path(self, label: str) -> str:
        return os.path.join(self.server_dir(label), "container.json")

    def get(self, name: str):
        try:
            return self.docker.containers.get(name)
        except self.not_found:
            return None

    @staticmethod
    def image_of(c) -> str:
        """The image a container was run from, as it was named."""
        return ((c.attrs or {}).get("Config") or {}).get("Image", "")

    def bucket_prefix(self, account_id: str) -> str:
        return f"{self.settings.s3.get('prefix') or 'hosted/'}{account_id}/"

    def subnet(self) -> str:
        if self._subnet is None:
            try:
                ipam = self.docker.networks.get(self.settings.network).attrs.get("IPAM") or {}
                self._subnet = ",".join(c["Subnet"] for c in ipam.get("Config") or [] if c.get("Subnet"))
            except self.not_found:
                raise JobError(f"the Docker network {self.settings.network} does not exist") from None
        return self._subnet

    def load_spec(self, label: str) -> dict:
        try:
            with open(self.spec_path(label)) as f:
                return json.load(f)
        except (OSError, ValueError):
            raise JobError(f"no container.json for {label}: create it first") from None

    def save_spec(self, label: str, spec: dict) -> None:
        path = self.spec_path(label)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(spec, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def pull(self, image: str) -> None:
        repo, tag = split_image(image)
        try:
            self.docker.images.pull(repo, tag=tag)
        except Exception as e:  # noqa: BLE001 — any registry error fails the job
            raise JobError(f"pull {image} failed: {e}") from e

    @staticmethod
    def limits(spec: dict) -> dict:
        """A spec's memory and CPU as Docker's limits. CPUs are a quota per
        ``CPU_PERIOD`` rather than ``nano_cpus``, because a container's
        update (a resize in place) takes only the quota. Swap stays at
        Docker's default for ``--memory``, twice the memory."""
        memory = int(spec["memory_mb"])
        return {"mem_limit": f"{memory}m", "memswap_limit": f"{2 * memory}m",
                "cpu_period": CPU_PERIOD, "cpu_quota": int(float(spec["cpus"]) * CPU_PERIOD)}

    def run(self, label: str, spec: dict):
        data = os.path.join(self.server_dir(label), "data")
        os.makedirs(data, exist_ok=True)
        return self.docker.containers.run(
            spec["image"], name=self.name(label), detach=True, environment=spec["env"],
            labels={"gamma.label": label, "gamma.account": spec.get("account_id", ""), "gamma.plan": spec.get("plan", "")},
            restart_policy={"Name": "unless-stopped"}, volumes={data: {"bind": "/data", "mode": "rw"}},
            network=spec["network"], **self.limits(spec))

    def wait_healthy(self, label: str) -> None:
        url = f"http://{self.name(label)}:9001/api/health"
        deadline = self.clock() + HEALTH_TIMEOUT
        while True:
            if self.health(url):
                return
            if self.clock() >= deadline:
                raise JobError(f"{self.name(label)} did not answer {url} within {HEALTH_TIMEOUT} s")
            self.sleep(HEALTH_EVERY)

    # --- jobs -------------------------------------------------------------------

    def create(self, payload: dict) -> dict:
        """Start a new container. One left by an earlier attempt is replaced,
        and so is a ``-prev`` left by an earlier life of the label; the data
        directory stays."""
        label = self.label(payload)
        if not payload.get("image"):
            raise JobError("no image")
        account_id = str(payload.get("account_id") or "")
        if not account_id:
            # the bucket prefix and the container's labels are the account's;
            # without one, a later delete could not find its copies
            raise JobError("no account id")
        env = {str(k): str(v) for k, v in (payload.get("env") or {}).items()}
        env.setdefault("FORWARDED_ALLOW_IPS", self.subnet() or "127.0.0.1")
        if self.settings.s3:
            s3 = self.settings.s3
            env.update({"GAMMA_S3_BUCKET": s3["bucket"], "GAMMA_S3_ENDPOINT": s3.get("endpoint", ""),
                        "GAMMA_S3_REGION": s3.get("region", ""), "GAMMA_S3_ACCESS_KEY": s3.get("access_key", ""),
                        "GAMMA_S3_SECRET_KEY": s3.get("secret_key", ""),
                        "GAMMA_S3_PREFIX": self.bucket_prefix(account_id)})
        spec = {"image": payload["image"], "env": env, "account_id": account_id, "plan": str(payload.get("plan") or ""),
                "memory_mb": int(payload.get("memory_mb") or self.settings.memory_mb),
                "cpus": float(payload.get("cpus") or self.settings.cpus),
                "network": payload.get("network") or self.settings.network}
        os.makedirs(self.server_dir(label), exist_ok=True)
        self.save_spec(label, spec)
        self.pull(spec["image"])
        for name in (self.name(label), self.name(label) + "-prev"):
            old = self.get(name)
            if old is not None:
                old.remove(force=True)
        self._data_mb.pop(label, None)
        self.run(label, spec)
        self.wait_healthy(label)
        return {"container": self.name(label), "image": spec["image"], "health": "ok"}

    def start(self, payload: dict) -> dict:
        """Start it (a running one is left as it is), or run it from
        ``container.json`` when it is gone."""
        label = self.label(payload)
        c = self.get(self.name(label))
        if c is None:
            self.run(label, self.load_spec(label))
        elif c.status != "running":
            c.start()
        self.wait_healthy(label)
        return {"container": self.name(label), "health": "ok"}

    def stop(self, payload: dict) -> dict:
        label = self.label(payload)
        c = self.get(self.name(label))
        if c is None:
            return {"container": self.name(label), "note": "no such container"}
        c.stop(timeout=STOP_TIMEOUT)
        return {"container": self.name(label), "stopped": True}

    def restart(self, payload: dict) -> dict:
        """Restart it, or run it from ``container.json`` when it is gone."""
        label = self.label(payload)
        c = self.get(self.name(label))
        if c is None:
            self.run(label, self.load_spec(label))
        else:
            c.restart(timeout=STOP_TIMEOUT)
        self.wait_healthy(label)
        return {"container": self.name(label), "health": "ok"}

    def delete(self, payload: dict) -> dict:
        """The container (and a kept previous one), the data directory, and
        the account's bucket prefix. A payload with no account (an orphan
        container's removal) leaves the bucket alone: the prefix may belong
        to the account's live server elsewhere. Nothing there is no error."""
        label = self.label(payload)
        removed = []
        for name in (self.name(label), self.name(label) + "-prev"):
            c = self.get(name)
            if c is not None:
                c.remove(force=True)
                removed.append(name)
        shutil.rmtree(self.server_dir(label), ignore_errors=True)
        self._data_mb.pop(label, None)
        result = {"removed": removed, "data": self.server_dir(label)}
        account_id = str(payload.get("account_id") or "")
        if self.s3 is not None and account_id:
            prefix = self.bucket_prefix(account_id)
            result["bucket_objects"] = self.s3.delete_prefix(prefix)
            result["bucket_prefix"] = prefix
        elif self.s3 is not None:
            result["bucket_prefix"] = ""                   # no account named: left alone
        return result

    def upgrade(self, payload: dict) -> dict:
        """Recreate the container on a new image, with new limits, or both.
        Pull, keep the running container as ``gamma-<label>-prev`` and stop
        it, start the new one with the same configuration, wait for health.
        Success removes the previous container. Failure stops the new one
        (its logs stay) and leaves the previous one stopped, not removed:
        Gamma may already have migrated the data, so starting the old image
        again is an operator's decision (``rollback``). A retry after such a
        failure keeps that ``-prev`` as the fallback and replaces only the
        failed container, so the one known-good container is never lost.

        A payload with no image is a resize (``resize``)."""
        label = self.label(payload)
        spec = self.load_spec(label)
        image = str(payload.get("image") or "")
        size = {}
        if payload.get("memory_mb"):
            size["memory_mb"] = int(payload["memory_mb"])
        if payload.get("cpus"):
            size["cpus"] = float(payload["cpus"])
        if not image and not size:
            raise JobError("no image")
        if not image:
            return self.resize(label, spec, size)
        name, prev_name = self.name(label), self.name(label) + "-prev"
        old, live = self.get(prev_name), self.get(name)
        self.pull(image)
        if old is not None:
            # A kept -prev is the last known-good container of an upgrade
            # that failed (or of this job, run again): it stays the
            # fallback, and the container under the live name is what goes.
            if live is not None:
                live.remove(force=True)
            old.stop(timeout=STOP_TIMEOUT)
        elif live is not None:
            live.rename(prev_name)
            live.stop(timeout=STOP_TIMEOUT)
            old = live
        new_spec = {**spec, **size, "image": image}
        new_spec.pop("rolled_back", None)
        try:
            self.run(label, new_spec)
            self.wait_healthy(label)
        except Exception as e:
            new = self.get(name)
            if new is not None:
                try:
                    new.stop(timeout=STOP_TIMEOUT)
                except Exception:  # noqa: BLE001
                    pass
            kept = f"; the previous container is kept, stopped, as {prev_name}" if old is not None else ""
            raise JobError(f"upgrade to {image} failed: {e}{kept}") from e
        self.save_spec(label, new_spec)
        previous = (self.image_of(old) if old is not None else "") or spec["image"]
        if old is not None:
            old.remove(force=True)
        return {"container": name, "image": image, "previous": previous, "memory_mb": new_spec["memory_mb"],
                "cpus": new_spec["cpus"], "health": "ok"}

    def resize(self, label: str, spec: dict, size: dict) -> dict:
        """New memory and CPU limits on the container as it is, in place
        (Docker's update): the same container and image, no pull and no
        restart, and a stopped container stays stopped. ``container.json``
        takes the size, so a container run from it later has it too. With no
        container the spec alone changes. Refused while a failed upgrade to
        another image waits for its retry or rollback, since the size would
        then belong to the container that goes."""
        name, prev_name = self.name(label), self.name(label) + "-prev"
        live = self.get(name)
        if self.get(prev_name) is not None and live is not None and self.image_of(live) != spec["image"]:
            raise JobError(f"the upgrade of {name} to {self.image_of(live)} failed and {prev_name} is kept: "
                           "retry that upgrade or roll it back first")
        new_spec = {**spec, **size}
        if live is not None:
            try:
                live.update(**self.limits(new_spec))
            except Exception as e:  # noqa: BLE001 — the daemon's refusal is the job's result
                raise JobError(f"resizing {name} failed: {e}") from e
        self.save_spec(label, new_spec)
        image = (self.image_of(live) if live is not None else "") or spec["image"]
        return {"container": name, "image": image, "previous": image, "memory_mb": new_spec["memory_mb"],
                "cpus": new_spec["cpus"], "resized": live is not None, "health": "unchanged"}

    def rollback(self, payload: dict) -> dict:
        """Back to the container an upgrade kept as ``gamma-<label>-prev``:
        the live one is stopped and removed, ``-prev`` takes the live name
        and starts, and ``container.json`` names its image again (marked
        ``rolled_back`` until the next upgrade). Run again, after it
        finished or after a crash midway, it finds the container already
        back and only makes sure it is up."""
        label = self.label(payload)
        spec = self.load_spec(label)
        name, prev_name = self.name(label), self.name(label) + "-prev"
        prev = self.get(prev_name)
        if prev is None:
            live = self.get(name)
            if live is None or not spec.get("rolled_back") or self.image_of(live) != spec["image"]:
                raise JobError("nothing to roll back to")
            if live.status != "running":
                live.start()
            self.wait_healthy(label)
            return {"container": name, "image": spec["image"], "health": "ok", "note": "already rolled back"}
        image = self.image_of(prev) or spec["image"]
        self.save_spec(label, {**spec, "image": image, "rolled_back": True})
        live = self.get(name)
        previous = self.image_of(live) if live is not None else ""
        if live is not None:
            live.stop(timeout=STOP_TIMEOUT)
            live.remove(force=True)
        prev.rename(name)
        prev.start()
        self.wait_healthy(label)
        return {"container": name, "image": image, "previous": previous, "health": "ok"}

    def logs(self, payload: dict) -> dict:
        """The container's last LOG_LINES lines, each with its timestamp;
        ``since`` is the oldest one's."""
        label = self.label(payload)
        c = self.get(self.name(label))
        if c is None:
            raise JobError(f"no container {self.name(label)}")
        raw = c.logs(tail=LOG_LINES, timestamps=True)
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        lines = [line[:LOG_LINE_MAX] for line in text.splitlines()][-LOG_LINES:]
        first = lines[0].split(" ", 1)[0] if lines else ""
        return {"container": self.name(label), "lines": lines, "since": first if TIMESTAMP_RE.match(first) else ""}

    HANDLERS = ("create", "start", "stop", "restart", "delete", "upgrade", "rollback", "logs")

    def apply(self, job: dict) -> tuple[str, dict]:
        """Run one job; ``("done", result)`` or ``("failed", {"error": …})``."""
        kind = job.get("kind")
        if kind not in self.HANDLERS:
            return "failed", {"error": f"unknown job kind {kind!r}"}
        try:
            return "done", getattr(self, kind)(job.get("payload") or {})
        except Exception as e:  # noqa: BLE001 — every failure is reported, never raised
            log.warning("job %s (%s) failed: %s", job.get("id"), kind, e)
            return "failed", {"error": str(e)[:2000]}

    def report(self, job_id: str, state: str, result: dict, tries: int = 5) -> None:
        """Send a result; a lost one is retried (else the account server
        fails the job after an hour)."""
        for attempt in range(tries):
            try:
                self.api.finish(job_id, state, result)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("could not report job %s (%s), retrying", job_id, e)
                self.sleep(5 * (attempt + 1))

    def run_job(self, job: dict) -> str:
        self.busy = True
        try:
            log.info("job %s: %s %s", job.get("id"), job.get("kind"), (job.get("payload") or {}).get("label", ""))
            state, result = self.apply(job)
            self.report(job["id"], state, result)
            return state
        finally:
            self.busy = False

    # --- heartbeat --------------------------------------------------------------

    def data_mb(self, label: str) -> int:
        """The size of a server's data directory, measured at most every
        DATA_EVERY seconds (a walk of every file)."""
        cached = self._data_mb.get(label)
        if cached is None or self.clock() - cached[0] >= DATA_EVERY:
            cached = (self.clock(), dir_mb(os.path.join(self.server_dir(label), "data")))
            self._data_mb[label] = cached
        return cached[1]

    def heartbeat_body(self) -> dict:
        body = {"agent_version": VERSION, **self.stats(self.settings.data_root), "containers": []}
        seen = set()
        for c in self.docker.containers.list(all=True, filters={"label": "gamma.label"}):
            label = (c.labels or {}).get("gamma.label", "")
            if c.name != self.name(label):
                continue                                   # a kept -prev container
            seen.add(label)
            running = c.status == "running"
            attrs = c.attrs or {}
            memory = 0
            if running:                                    # stats() takes a second or two per container
                try:
                    memory = int((c.stats(stream=False).get("memory_stats") or {}).get("usage") or 0) // MIB
                except Exception:  # noqa: BLE001
                    memory = 0
            body["containers"].append({
                "label": label, "running": running,
                "health": ((attrs.get("State") or {}).get("Health") or {}).get("Status") or c.status,
                "memory_mb": memory, "memory_limit_mb": int((attrs.get("HostConfig") or {}).get("Memory") or 0) // MIB,
                "data_mb": self.data_mb(label), "image": self.image_of(c)})
        self._data_mb = {k: v for k, v in self._data_mb.items() if k in seen}
        return body

    # --- the loop ---------------------------------------------------------------

    def step(self, next_beat: float) -> float:
        """One round: a heartbeat when due, one poll, one job. Returns when
        the next heartbeat is due."""
        if self.clock() >= next_beat:
            try:
                self.api.heartbeat(self.heartbeat_body())
                next_beat = self.clock() + HEARTBEAT_EVERY
            except Exception as e:  # noqa: BLE001
                log.warning("heartbeat failed: %s", e)
                next_beat = self.clock() + 60
        started = self.clock()
        try:
            job = self.api.next_job(POLL_WAIT)
        except Exception as e:  # noqa: BLE001
            log.warning("job poll failed: %s", e)
            self.sleep(10)
            return next_beat
        if job:
            self.run_job(job)
        elif self.clock() - started < 2:
            self.sleep(10)                                 # an account server that does not long-poll
        return next_beat

    def run_forever(self) -> None:
        next_beat = 0.0
        while not self.stopping:
            next_beat = self.step(next_beat)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    import docker
    agent = Agent(settings, docker.from_env(), Api(settings.account_url, settings.host_token))

    def on_term(signum, frame):
        agent.stopping = True
        if not agent.busy:
            sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    log.info("gamma-fleet %s: %s, network %s, data %s", VERSION, settings.account_url, settings.network,
             settings.data_root)
    agent.run_forever()


if __name__ == "__main__":
    main()
