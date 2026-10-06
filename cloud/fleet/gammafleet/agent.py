"""The fleet agent's loop: heartbeat every five minutes, long-poll the
account server for the next job, apply it with the Docker SDK, report the
result. Outbound calls only; the account server never reaches the host.

Environment:

- ``GAMMA_FLEET_ACCOUNT_URL`` (required): the account server, e.g.
  ``http://account:9002`` on the same compose network, or
  ``https://account.gammapdf.com`` from another host.
- ``GAMMA_FLEET_HOST_TOKEN`` (required): the host's token from Admin →
  Machines → Add machine (or ``manage.py add-host``).
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
env, extra_env?, data_dir, memory_mb, cpus, network, public_url}``;
``start``, ``stop``, ``restart``, ``rollback``, ``logs`` → ``{label}``;
``delete`` → ``{label, account_id}``; ``upgrade`` → ``{label, image?, tag?,
memory_mb?, cpus?}`` (no image: a resize, on the image the container
runs); ``update`` → ``{label, extra_env}`` (the whole extra environment
from now on). A container is ``gamma-<label>``; what it was created with
(image, environment, extra environment, limits) is kept in
``<root>/<label>/container.json`` so ``start``, ``restart``, ``upgrade`` and
``update`` can rebuild it. Its environment is the payload's ``env``, then
the agent's own variables, then ``extra_env``, later winning; ``extra_env``
never sets a name the account server or the agent sets (``RESERVED_ENV``).

Any container on the host, the hosted ones included, is reached by its name:
``container_restart``, ``container_start``, ``container_stop``,
``container_update``, ``container_rollback`` → ``{container}``;
``container_logs`` → ``{container, lines?}``. ``container_update`` pulls the
reference the container was created with and recreates it under its name
with the configuration cloned from Docker's inspect data (``clone_config``),
as safely as an upgrade (``swap_container``); ``container_rollback`` brings
back the ``-prev`` a failed one kept. Both refuse a hosted server, which its
own jobs upgrade. The agent cannot stop its own container, so it updates,
restarts or rolls itself back through a one-shot helper container
(``selfupdate.py``).

Every handler can be run again after a crash midway and ends in the same
place.
"""

import json
import logging
import os
import re
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
REGISTRY_EVERY = 3600          # the registry is asked about an image reference at most this often
REGISTRY_WAIT = 5              # and its answer waited for at most this long
SETTLE = 10                    # a container with no health check is up once it has run this long
STATS_THREADS = 4              # stats() calls at once in a heartbeat
LOG_LINES = 200
LOG_LINES_MAX = 5000           # the most a container_logs job may ask for
LOG_LINE_MAX = 400
REPORT_FOR = 180               # a result is offered this long: the account server may be restarting
REPORT_WAIT_MAX = 30           # the waits between tries double from 2 s up to this
MIB = 1024 * 1024
TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\S+$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,254}$")         # Docker's container names
IMAGE_ID_RE = re.compile(r"^(sha256:)?[0-9a-f]{12,64}$")
HOSTNAME_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
DOCKER_SOCK = "/var/run/docker.sock"
HELPER_LABEL = "gamma.fleet.selfupdate"   # on the self-update helper; its value is the agent's container
FLEET_SERVICE = ("gamma-fleet", "fleet")  # the agent's Compose project and service (deploy/compose.yml)
# what of an inspected Config the create API takes again; MacAddress, the old container's own, is left out
CREATE_CONFIG = ("Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr", "ExposedPorts",
                 "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck", "ArgsEscaped", "Image", "Volumes",
                 "WorkingDir", "Entrypoint", "NetworkDisabled", "OnBuild", "Labels", "StopSignal", "StopTimeout",
                 "Shell")
FROM_IMAGE = ("WorkingDir", "User", "Healthcheck", "StopSignal", "Shell")
ENDPOINT_CONFIG = ("Aliases", "IPAMConfig", "Links", "DriverOpts")
# what the account server (create's env) or the agent sets: extra_env never overrides it
RESERVED_ENV = frozenset({"GAMMA_HOSTED", "GAMMA_PUBLIC_URL", "GAMMA_GUEST_MAX", "FORWARDED_ALLOW_IPS"})
RESERVED_ENV_PREFIXES = ("GAMMA_CLOUD_", "GAMMA_S3_")


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


def read_proc_stat() -> str:
    with open("/proc/stat") as f:
        return f.read()


def cpu_times(text: str) -> tuple[int, int]:
    """The host's CPU time as (busy, total) clock ticks, summed over every
    core, from the ``cpu`` line of /proc/stat: total is user, nice, system,
    idle, iowait, irq, softirq and steal (guest time is in user already),
    busy all of it but idle and iowait. ValueError when there is no such
    line."""
    for line in text.splitlines():
        fields = line.split()
        if fields[:1] == ["cpu"] and len(fields) >= 6:
            ticks = [int(v) for v in fields[1:9]]
            return sum(ticks) - ticks[3] - ticks[4], sum(ticks)
    raise ValueError("no cpu line in /proc/stat")


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


def image_ref(image: str) -> tuple[str, str, str]:
    """``repo:tag@digest`` → (repo, tag, digest), "" for a part it lacks."""
    name, _, digest = image.partition("@")
    repo, colon, tag = name.rpartition(":")
    if not colon or "/" in tag:
        return name, "", digest
    return repo, tag, digest


def split_image(image: str) -> tuple[str, str]:
    """``repo:tag`` → (repo, tag); no tag is ``latest``."""
    repo, tag, _ = image_ref(image)
    return repo, tag or "latest"


def registry_host(repo: str) -> str:
    """The registry a repository names (``ghcr.io``, ``localhost:5000``), or
    "" when it names none: Docker Hub, or an image built on the host."""
    first, slash, _ = repo.partition("/")
    return first if slash and ("." in first or ":" in first or first == "localhost") else ""


def short_id(image_id: str) -> str:
    """``sha256:<64 hex>`` → ``sha256:<first 12>``."""
    algo, colon, rest = image_id.partition(":")
    return f"{algo}:{rest[:12]}" if colon else image_id[:12]


def docker_time(value) -> str:
    """One of Docker's times as it has it, or "" when unknown or never (its
    zero time, the start of a container that never ran)."""
    value = str(value or "")
    return "" if value.startswith("0001-01-01") else value


def published_ports(ports) -> list[str]:
    """``NetworkSettings.Ports`` as ``docker ps`` shows what is published:
    ``0.0.0.0:443->443/tcp``, ``[::]:443->443/tcp``."""
    shown = []
    for port, bindings in sorted((ports or {}).items(), key=lambda p: (p[0].split("/")[0].zfill(5), p[0])):
        for b in bindings or []:
            ip = b.get("HostIp") or "0.0.0.0"
            shown.append(f"{f'[{ip}]' if ':' in ip else ip}:{b.get('HostPort', '')}->{port}")
    return shown


def health_of(c) -> str:
    """Docker's health of a container: ``healthy``, ``unhealthy``,
    ``starting``, or "" when its image defines no HEALTHCHECK."""
    return str(((((c.attrs or {}).get("State") or {}).get("Health") or {}).get("Status")) or "")


def clone_config(attrs: dict, image_config: dict, image_id: str = "") -> tuple[dict, dict]:
    """The create body (``Config`` with ``HostConfig`` and
    ``NetworkingConfig``) for a container like the one ``attrs``, Docker's
    inspect data, describes, on the image its ``Config.Image`` names now; and
    the networks it joins after the first, ``{network: endpoint}``, since an
    older daemon takes one network at create.

    ``Config`` is taken less what the old image supplied (``image_config``,
    that image's own ``Config``) where the container has it unchanged: its
    environment entries and labels, its entrypoint and command, working
    directory, user, health check, stop signal and shell, so the new image's
    own apply. Less what Docker made for the old container too: a
    ``Hostname`` that is its short id, and ``MacAddress``. ``HostConfig`` is
    taken as it is, and an anonymous volume (one Docker made for an image's
    VOLUME) is mounted again, as Compose does, so its data stays. Each network
    keeps ``Aliases`` (less the old short id), ``IPAMConfig``, ``Links`` and
    ``DriverOpts``. Compose's labels stay, so a later ``docker compose up -d``
    finds its configuration hash unchanged; ``com.docker.compose.image``, the
    image id Compose made the container on, becomes ``image_id`` so it finds
    no new image either."""
    short = str(attrs.get("Id") or "")[:12]
    old, image_config = attrs.get("Config") or {}, image_config or {}
    config = {k: old[k] for k in CREATE_CONFIG if k in old}
    if short and config.get("Hostname") == short:
        del config["Hostname"]
    from_image = set(image_config.get("Env") or [])
    config["Env"] = [e for e in config.get("Env") or [] if e not in from_image]
    image_labels = image_config.get("Labels") or {}
    labels = {k: v for k, v in (config.get("Labels") or {}).items() if image_labels.get(k) != v}
    if image_id and "com.docker.compose.image" in labels:
        labels["com.docker.compose.image"] = image_id
    config["Labels"] = labels
    if config.get("Entrypoint") == image_config.get("Entrypoint"):
        config.pop("Entrypoint", None)
        if config.get("Cmd") == image_config.get("Cmd"):
            config.pop("Cmd", None)
    for key in FROM_IMAGE:
        if key in config and config[key] == image_config.get(key):
            del config[key]

    host = dict(attrs.get("HostConfig") or {})
    covered = {m.get("Target") for m in host.get("Mounts") or []}
    covered |= {b.split(":")[1] for b in host.get("Binds") or [] if ":" in b}
    anonymous = [{"Type": "volume", "Source": m["Name"], "Target": m["Destination"]}
                 for m in attrs.get("Mounts") or []
                 if m.get("Type") == "volume" and m.get("Name") and m.get("Destination") not in covered]
    if anonymous:
        host["Mounts"] = list(host.get("Mounts") or []) + anonymous
    config["HostConfig"] = host

    endpoints = {}
    for net, ep in ((attrs.get("NetworkSettings") or {}).get("Networks") or {}).items():
        ep = {k: ep[k] for k in ENDPOINT_CONFIG if (ep or {}).get(k)}
        aliases = [a for a in ep.pop("Aliases", []) if a != short]
        endpoints[net] = {**ep, "Aliases": aliases} if aliases else ep
    mode = str(host.get("NetworkMode") or "")
    first = (mode if mode in endpoints else "bridge" if mode == "default" and "bridge" in endpoints
             else next(iter(endpoints), ""))
    if first:
        config["NetworkingConfig"] = {"EndpointsConfig": {first: endpoints.pop(first)}}
    return config, endpoints


def own_hostname() -> str:
    """The agent's hostname: in a container Docker's short id of it, unless
    the container was given another."""
    try:
        with open("/etc/hostname") as f:
            return f.read().strip() or socket.gethostname()
    except OSError:
        return socket.gethostname()


def extra_env(raw) -> dict:
    """A payload's ``extra_env`` as strings, less what it may not set: a
    reserved name (``RESERVED_ENV``, ``RESERVED_ENV_PREFIXES``), and an empty
    name or one with ``=``, which would pass for another variable."""
    env = {}
    for name, value in (raw or {}).items():
        name = str(name)
        if name and "=" not in name and name not in RESERVED_ENV and not name.startswith(RESERVED_ENV_PREFIXES):
            env[name] = str(value)
    return env


def cpu_pct(stats: dict) -> float | None:
    """CPU use in percent of one CPU between the two samples a
    ``stats(stream=False)`` holds, as ``docker stats`` works it out: the
    container's CPU time over the host's, times the online CPUs. None when a
    figure is missing."""
    try:
        cpu, pre = stats["cpu_stats"], stats["precpu_stats"]
        used = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or ())
    except (KeyError, TypeError, AttributeError):
        return None
    if system <= 0 or used < 0 or not cpus:
        return None
    return round(used / system * cpus * 100, 1)


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
    (or anything shaped like one); ``not_found`` its NotFound error;
    ``hostname`` the agent's own (``own_hostname``), which finds its
    container."""

    def __init__(self, settings: Settings, docker, api, *, not_found=None, health=http_health, sleep=time.sleep,
                 clock=time.monotonic, stats=host_stats, proc_stat=read_proc_stat, s3=None, hostname=None):
        self.settings, self.docker, self.api = settings, docker, api
        if not_found is None:
            from docker.errors import NotFound as not_found
        self.not_found = not_found
        self.health, self.sleep, self.clock, self.stats, self.proc_stat = health, sleep, clock, stats, proc_stat
        self.hostname = own_hostname() if hostname is None else hostname
        self.s3 = s3 if s3 is not None else (S3Prefix(settings.s3) if settings.s3 else None)
        self.busy = False
        self.stopping = False
        self._subnet = None
        self._data_mb: dict[str, tuple[float, int]] = {}   # label -> (when measured, MB)
        self._registry: dict[str, tuple[float, str]] = {}  # image reference -> (when asked, digest or "")
        self._cpu: tuple[int, int] | None = None           # the host's cpu_times at the last heartbeat

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
        """The container of exactly that name, or None (Docker's lookup would
        also take an id, or the start of one)."""
        try:
            c = self.docker.containers.get(name)
        except self.not_found:
            return None
        return c if c.name == name else None

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

    def agent_env(self, env: dict, account_id: str) -> dict:
        """``env`` with the agent's own variables, from its settings as they
        are now: ``FORWARDED_ALLOW_IPS`` (the fleet network's subnet) unless
        ``env`` has it, and with a bucket the ``GAMMA_S3_*`` variables under
        the account's prefix."""
        env = dict(env)
        env.setdefault("FORWARDED_ALLOW_IPS", self.subnet() or "127.0.0.1")
        if self.settings.s3:
            s3 = self.settings.s3
            env.update({"GAMMA_S3_BUCKET": s3["bucket"], "GAMMA_S3_ENDPOINT": s3.get("endpoint", ""),
                        "GAMMA_S3_REGION": s3.get("region", ""), "GAMMA_S3_ACCESS_KEY": s3.get("access_key", ""),
                        "GAMMA_S3_SECRET_KEY": s3.get("secret_key", ""),
                        "GAMMA_S3_PREFIX": self.bucket_prefix(account_id)})
        return env

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

    def run(self, label: str, spec: dict, start: bool = True):
        """Run the container from a spec, or with ``start`` false create it
        stopped. Its environment is the spec's ``env`` with ``extra_env``
        over it."""
        data = os.path.join(self.server_dir(label), "data")
        os.makedirs(data, exist_ok=True)
        make = self.docker.containers.run if start else self.docker.containers.create
        return make(
            spec["image"], name=self.name(label), detach=True,
            environment={**spec["env"], **extra_env(spec.get("extra_env"))},
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

    def swap_container(self, name: str, make, wait, what: str, commit=None):
        """Replace the container ``name`` safely; ``upgrade``, ``update`` and
        ``container_update`` share it. Keep the one there as ``<name>-prev``
        and stop it, ``make()`` the new one under the name, ``wait()`` for it;
        then ``commit()`` and remove the previous one. A failure stops the new
        one (its logs stay), keeps the previous one stopped and says so;
        ``what`` names the job in that error. Returns the previous container
        (removed now, or None) and what ``wait()`` returned."""
        prev_name = name + "-prev"
        old, live = self.get(prev_name), self.get(name)
        if old is not None:
            # A kept -prev is the last known-good container of a job that
            # failed (or of this one, run again): it stays the fallback, and
            # the container under the live name is what goes.
            if live is not None:
                live.remove(force=True)
            old.stop(timeout=STOP_TIMEOUT)
        elif live is not None:
            live.rename(prev_name)
            live.stop(timeout=STOP_TIMEOUT)
            old = live
        if old is not None:
            log.info("%s: the previous container is %s, stopped", what, prev_name)
        try:
            make()
            waited = wait()
        except Exception as e:
            new = self.get(name)
            if new is not None:
                try:
                    new.stop(timeout=STOP_TIMEOUT)
                except Exception:  # noqa: BLE001
                    pass
            kept = f"; the previous container is kept, stopped, as {prev_name}" if old is not None else ""
            raise JobError(f"{what} failed: {e}{kept}") from e
        if commit is not None:
            commit()
        if old is not None:
            old.remove(force=True)
        log.info("%s: %s is up%s", what, name, f", {prev_name} removed" if old is not None else "")
        return old, waited

    def swap(self, label: str, new_spec: dict, what: str) -> str:
        """Replace a hosted container with one run from ``new_spec``, as an
        upgrade does (``swap_container``): health is its ``/api/health``, and
        ``new_spec`` is saved once it answers. Returns the image the previous
        container ran, or ""."""
        old, _ = self.swap_container(self.name(label), lambda: self.run(label, new_spec),
                                     lambda: self.wait_healthy(label), what,
                                     commit=lambda: self.save_spec(label, new_spec))
        return self.image_of(old) if old is not None else ""

    def wait_up(self, name: str) -> str:
        """Wait for a container started just now: Docker's own health when its
        image defines a HEALTHCHECK, healthy within HEALTH_TIMEOUT seconds;
        else still running, not restarted, after SETTLE seconds. Returns its
        health ("healthy", or "" with no health check)."""
        started, restarts = self.clock(), None
        while True:
            c = self.get(name)
            attrs = (c.attrs or {}) if c is not None else {}
            count = int(attrs.get("RestartCount") or 0)
            restarts = count if restarts is None else restarts
            if c is None or c.status != "running" or count > restarts:
                code = (attrs.get("State") or {}).get("ExitCode")
                raise JobError(f"{name} did not keep running ({c.status if c is not None else 'gone'}"
                               f"{f', exit code {code}' if code else ''})")
            health = health_of(c)
            if health == "healthy":
                return health
            if health == "unhealthy":
                raise JobError(f"{name} is unhealthy")
            if not health and self.clock() - started >= SETTLE:
                return ""
            if self.clock() - started >= HEALTH_TIMEOUT:
                raise JobError(f"{name} was not healthy within {HEALTH_TIMEOUT} s")
            self.sleep(HEALTH_EVERY)

    def create_from(self, name: str, body: dict, networks: dict, start: bool = True) -> None:
        """Create a container from a ``clone_config`` body through the
        low-level API, connect it to the networks after the first, start it."""
        cid = self.docker.api.create_container_from_config(body, name=name)["Id"]
        for net, ep in networks.items():
            ipam = ep.get("IPAMConfig") or {}
            self.docker.api.connect_container_to_network(
                cid, net, aliases=ep.get("Aliases") or None, driver_opt=ep.get("DriverOpts") or None,
                links=[(link.partition(":")[0], link.partition(":")[2]) for link in ep.get("Links") or []] or None,
                ipv4_address=ipam.get("IPv4Address") or None, ipv6_address=ipam.get("IPv6Address") or None,
                link_local_ips=ipam.get("LinkLocalIPs") or None)
        if start:
            self.docker.api.start(cid)
        log.info("created %s%s", name, " and started it" if start else "")

    def recreate(self, name: str, start: bool = True) -> dict:
        """Replace the container ``name`` (``swap_container``) with one made
        from the image its ``Config.Image`` names now and its configuration
        (``clone_config``), then wait for it (``wait_up``). The clone is taken
        from a kept ``-prev`` when there is one, the last known-good container
        of an update that failed or died midway, else from the container under
        the name. With ``start`` false the new one is created stopped. Returns
        ``{previous_image_id, image_id, health}``."""
        source = self.get(name + "-prev") or self.get(name)
        if source is None:
            raise JobError(f"no container {name}")
        attrs = source.attrs or {}
        try:
            image_config = self.docker.images.get(str(attrs.get("Image") or "")).attrs.get("Config") or {}
            image_id = str(self.docker.images.get(self.image_of(source)).id or "")
        except Exception as e:  # noqa: BLE001
            raise JobError(f"cannot read the images of {name}: {e}") from e
        body, networks = clone_config(attrs, image_config, image_id)
        _, health = self.swap_container(name, lambda: self.create_from(name, body, networks, start),
                                        (lambda: self.wait_up(name)) if start else (lambda: ""), f"update of {name}")
        new = self.get(name)
        return {"previous_image_id": short_id(str(attrs.get("Image") or "")),
                "image_id": short_id(str(((new.attrs or {}) if new is not None else {}).get("Image") or "")),
                "health": health}

    def restore(self, name: str) -> dict:
        """Back to the container a failed update kept as ``<name>-prev``: the
        one under the name is stopped and removed, ``-prev`` starts and takes
        the name, and is waited for (``wait_up``). Started before it is
        renamed, so a run again after a crash midway finds it under either
        name, running."""
        prev = self.get(name + "-prev")
        if prev is None:
            raise JobError("nothing to roll back to")
        live = self.get(name)
        if live is not None:
            live.stop(timeout=STOP_TIMEOUT)
            live.remove(force=True)
        if prev.status != "running":
            prev.start()
        prev.rename(name)
        self.wait_up(name)
        log.info("rolled %s back", name)
        return {"container": name, "image_id": short_id(str((prev.attrs or {}).get("Image") or ""))}

    def refuse_while_upgrade_fails(self, label: str, spec: dict, live) -> None:
        """A failed upgrade to another image waits for its retry or rollback
        (a ``-prev``, and a live container on another image than
        ``container.json``): a change made now would belong to the container
        that goes, so it is refused."""
        name, prev_name = self.name(label), self.name(label) + "-prev"
        if self.get(prev_name) is not None and live is not None and self.image_of(live) != spec["image"]:
            raise JobError(f"the upgrade of {name} to {self.image_of(live)} failed and {prev_name} is kept: "
                           "retry that upgrade or roll it back first")

    # --- jobs -------------------------------------------------------------------

    def create(self, payload: dict) -> dict:
        """Start a new container. One left by an earlier attempt is replaced,
        and so is a ``-prev`` left by an earlier life of the label; the data
        directory stays. ``extra_env`` is kept in ``container.json`` under
        its own key, apart from ``env``."""
        label = self.label(payload)
        if not payload.get("image"):
            raise JobError("no image")
        account_id = str(payload.get("account_id") or "")
        if not account_id:
            # the bucket prefix and the container's labels are the account's;
            # without one, a later delete could not find its copies
            raise JobError("no account id")
        env = self.agent_env({str(k): str(v) for k, v in (payload.get("env") or {}).items()}, account_id)
        spec = {"image": payload["image"], "env": env, "extra_env": extra_env(payload.get("extra_env")),
                "account_id": account_id, "plan": str(payload.get("plan") or ""),
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
        self.pull(image)
        new_spec = {**spec, **size, "image": image}
        new_spec.pop("rolled_back", None)
        previous = self.swap(label, new_spec, f"upgrade to {image}") or spec["image"]
        return {"container": self.name(label), "image": image, "previous": previous,
                "memory_mb": new_spec["memory_mb"], "cpus": new_spec["cpus"], "health": "ok"}

    def resize(self, label: str, spec: dict, size: dict) -> dict:
        """New memory and CPU limits on the container as it is, in place
        (Docker's update): the same container and image, no pull and no
        restart, and a stopped container stays stopped. ``container.json``
        takes the size, so a container run from it later has it too. With no
        container the spec alone changes. Refused while a failed upgrade to
        another image waits for its retry or rollback, since the size would
        then belong to the container that goes."""
        name = self.name(label)
        live = self.get(name)
        self.refuse_while_upgrade_fails(label, spec, live)
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

    def update(self, payload: dict) -> dict:
        """A new extra environment: ``extra_env`` is the whole of it from now
        on (a name left out goes). Docker cannot change a container's
        environment, so the container is rebuilt on the image it runs, with
        no pull, the way an upgrade rebuilds it (``swap``): the running one
        is kept as ``-prev`` until the new one passed its health check, and a
        failure has the same recovery, a retry or ``rollback``. The agent's
        own variables are worked out again from its settings as they are
        now, so a rotated bucket key reaches the container. ``container.json``
        takes the new environment once the new container is healthy.

        A stopped container is replaced by a stopped one that has the new
        environment (``container.json`` first, so an agent that dies between
        the removal and the creation leaves the spec a start runs from).
        With no container only ``container.json`` changes. Refused while a
        failed upgrade waits, as a resize is. Results name the variables,
        never their values."""
        label = self.label(payload)
        spec = self.load_spec(label)
        extra = extra_env(payload.get("extra_env"))
        name = self.name(label)
        live, prev = self.get(name), self.get(name + "-prev")
        self.refuse_while_upgrade_fails(label, spec, live)
        env = {k: v for k, v in spec["env"].items() if k != "FORWARDED_ALLOW_IPS" and not k.startswith("GAMMA_S3_")}
        new_spec = {**spec, "env": self.agent_env(env, spec.get("account_id", "")), "extra_env": extra,
                    "image": (self.image_of(live) if live is not None else "") or spec["image"]}
        result = {"container": name, "image": new_spec["image"], "extra_env": sorted(extra)}
        if live is None and prev is None:
            self.save_spec(label, new_spec)
            return {**result, "health": "unchanged", "note": "no such container: container.json alone changed"}
        # a crash-looping container ("restarting") is meant to run; with a -prev
        # kept, the live one is a failed attempt and this is its retry
        if prev is None and live.status not in ("running", "restarting"):
            self.save_spec(label, new_spec)
            live.remove(force=True)
            self.run(label, new_spec, start=False)
            return {**result, "health": "unchanged", "note": "stopped: it starts with the new environment"}
        self.swap(label, new_spec, f"update of {name}")
        return {**result, "health": "ok"}

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

    @staticmethod
    def tail(c, n: int) -> dict:
        """A container's last ``n`` lines, each with its timestamp and cut to
        LOG_LINE_MAX characters; ``since`` is the oldest one's."""
        raw = c.logs(tail=n, timestamps=True)
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        lines = [line[:LOG_LINE_MAX] for line in text.splitlines()][-n:]
        first = lines[0].split(" ", 1)[0] if lines else ""
        return {"lines": lines, "since": first if TIMESTAMP_RE.match(first) else ""}

    def logs(self, payload: dict) -> dict:
        """The container's last LOG_LINES lines (``tail``)."""
        label = self.label(payload)
        c = self.get(self.name(label))
        if c is None:
            raise JobError(f"no container {self.name(label)}")
        return {"container": self.name(label), **self.tail(c, LOG_LINES)}

    # --- any container on the host ---------------------------------------------

    @staticmethod
    def container_name(payload: dict) -> str:
        name = str(payload.get("container") or "")
        if not NAME_RE.match(name):
            raise JobError(f"bad container name {name!r}")
        return name

    def need(self, name: str):
        c = self.get(name)
        if c is None:
            raise JobError(f"no container {name}")
        return c

    def state_of(self, name: str) -> dict:
        """A container's status and health as Docker has them now."""
        c = self.get(name)
        return {"status": c.status if c is not None else "", "health": health_of(c) if c is not None else ""}

    def own_id(self, containers=None) -> str:
        """The id of the agent's own container: the one whose id starts with
        the agent's hostname (Docker makes a container's hostname its short
        id), else the running service ``fleet`` of the Compose project
        ``gamma-fleet``; "" when neither is found."""
        if containers is None:
            containers = self.docker.containers.list(all=True, ignore_removed=True)
        if HOSTNAME_ID_RE.match(self.hostname or ""):
            for c in containers:
                if str(c.id).startswith(self.hostname):
                    return c.id
        for c in containers:
            labels = c.labels or {}
            if c.status == "running" and (labels.get("com.docker.compose.project"),
                                          labels.get("com.docker.compose.service")) == FLEET_SERVICE:
                return c.id
        return ""

    @staticmethod
    def refuse_managed(name: str, c, verb: str) -> None:
        """A hosted server (``gamma.label``) is upgraded and rolled back by its
        own jobs, which keep ``container.json``; the account server never
        sends it here, and this is the backstop."""
        if "gamma.label" in (c.labels or {}):
            raise JobError(f"{name} is a hosted server: a hosted server is {verb} by its own jobs")

    def update_ref(self, name: str, c) -> str:
        """The reference ``container_update`` pulls: the container's
        ``Config.Image``, which must name a tag in a registry. Refused: an
        image id, a reference pinned to a digest or with no tag, and a local
        build (no registry in the name, and no digest from one on the image,
        as an image pulled from Docker Hub has)."""
        ref = self.image_of(c)
        repo, tag, digest = image_ref(ref)
        if not ref or IMAGE_ID_RE.match(ref):
            raise JobError(f"{name} runs an image by its id ({ref or '?'}): there is no reference to pull")
        if digest:
            raise JobError(f"{name} runs {ref}, pinned to a digest: there is nothing newer to pull")
        if not tag:
            raise JobError(f"{name} runs {ref}, which names no tag to pull")
        if not registry_host(repo) and not self.repo_digests(str((c.attrs or {}).get("Image") or ""), repo):
            raise JobError(f"{name} runs {ref}, a local build: there is no registry to pull it from")
        return ref

    def helper_at_work(self) -> str:
        """The name of a self-update helper at work, or "". One that is no
        longer running (it removes itself when it exits) is removed."""
        for c in self.docker.containers.list(all=True, filters={"label": HELPER_LABEL}, ignore_removed=True):
            if c.status in ("created", "running", "restarting"):
                return c.name
            c.remove(force=True)
        return ""

    def start_helper(self, own, name: str, image: str, action: str) -> str:
        """Start the self-update helper (``selfupdate.py``) for the agent's own
        container ``own``, under ``name``: one-shot from ``image``, removed when
        it exits, on no network, with the Docker socket the agent has."""
        mounts = (own.attrs or {}).get("Mounts") or []
        sock = next((m.get("Source") for m in mounts if m.get("Destination") == DOCKER_SOCK), "") or DOCKER_SOCK
        helper = f"{name}-selfupdate"
        self.docker.containers.run(image, command=["python", "-m", "gammafleet.selfupdate", name, action],
                                   name=helper, detach=True, remove=True, network_mode="none",
                                   labels={HELPER_LABEL: name}, volumes={sock: {"bind": DOCKER_SOCK, "mode": "rw"}})
        log.info("started %s to %s %s", helper, action, name)
        return helper

    def via_helper(self, own, name: str, image: str, action: str) -> dict:
        """A job on the agent's own container, handed to the helper: done as
        soon as it started (one at a time). The new agent reports with its
        first heartbeat."""
        busy = self.helper_at_work()
        if busy:
            return {"container": name, "image": image, "note": f"{busy} is already at work: no second helper"}
        self.start_helper(own, name, image, action)
        return {"container": name, "image": image,
                "note": "helper started; the new agent reports with its first heartbeat"}

    def container_restart(self, payload: dict) -> dict:
        """Docker's restart. The agent's own container is restarted by the
        helper, since the agent would stop midway."""
        name = self.container_name(payload)
        c = self.need(name)
        if c.id == self.own_id():
            return self.via_helper(c, name, str((c.attrs or {}).get("Image") or ""), "restart")
        c.restart(timeout=STOP_TIMEOUT)
        return {"container": name, **self.state_of(name)}

    def container_start(self, payload: dict) -> dict:
        name = self.container_name(payload)
        c = self.need(name)
        if c.status == "running":
            return {"container": name, "status": c.status, "note": "already running"}
        c.start()
        return {"container": name, "status": self.state_of(name)["status"]}

    def container_stop(self, payload: dict) -> dict:
        """Docker's stop. Not the agent's own container: nothing on the host
        could start it again."""
        name = self.container_name(payload)
        c = self.need(name)
        if c.status in ("exited", "created", "dead"):
            return {"container": name, "status": c.status, "note": "already stopped"}
        if c.id == self.own_id():
            raise JobError(f"{name} is this agent: stopped, nothing could start it again from here")
        c.stop(timeout=STOP_TIMEOUT)
        return {"container": name, "status": self.state_of(name)["status"]}

    def container_logs(self, payload: dict) -> dict:
        """The last ``lines`` lines (LOG_LINES unless named, at most
        LOG_LINES_MAX), as ``logs`` gives them."""
        name = self.container_name(payload)
        try:
            n = int(payload.get("lines") or LOG_LINES)
        except (TypeError, ValueError):
            raise JobError(f"bad lines {payload.get('lines')!r}") from None
        return {"container": name, **self.tail(self.need(name), max(1, min(n, LOG_LINES_MAX)))}

    def container_update(self, payload: dict) -> dict:
        """Pull the reference the container was created with (``update_ref``)
        and recreate it on the image pulled, under the same name with the same
        configuration (``recreate``). A container already on that image, with
        no ``-prev`` kept, is left alone. A stopped one is replaced by a
        stopped one, as ``update`` does; with a ``-prev`` kept the job is a
        retry and the new one runs. Refused: a hosted server
        (``refuse_managed``), and a container Docker removes as it stops
        (``--rm``), which would leave no ``-prev``. The agent's own container
        goes to the helper (``via_helper``) from the image pulled."""
        name = self.container_name(payload)
        live, prev = self.get(name), self.get(name + "-prev")
        source = prev or live
        if source is None:
            raise JobError(f"no container {name}")
        self.refuse_managed(name, source, "upgraded")
        if ((source.attrs or {}).get("HostConfig") or {}).get("AutoRemove"):
            raise JobError(f"{name} is removed as it stops (--rm): there would be no -prev to fall back to")
        ref = self.update_ref(name, source)
        own_id = self.own_id()
        own = next((c for c in (live, prev) if c is not None and own_id and c.id == own_id), None)
        if own is not None and self.helper_at_work():
            return self.via_helper(own, name, ref, "update")
        self.pull(ref)
        before = str((source.attrs or {}).get("Image") or "")
        result = {"container": name, "image": ref, "previous_image_id": short_id(before)}
        if prev is None and before == str(self.docker.images.get(ref).id or ""):
            return {**result, "image_id": short_id(before), "health": health_of(live),
                    "note": "already runs the newest image"}
        if own is not None:
            return self.via_helper(own, name, ref, "update")
        start = prev is not None or live.status in ("running", "restarting")
        result.update(self.recreate(name, start))
        return result if start else {**result, "note": "stopped: it starts on the new image"}

    def container_rollback(self, payload: dict) -> dict:
        """Back to the ``-prev`` a failed ``container_update`` kept
        (``restore``); "nothing to roll back to" without one. A hosted server
        is refused. When the agent runs under the name, its rollback goes to
        the helper; when it runs as the ``-prev`` (a failed self-update started
        it again), it rolls itself back, since nothing stops it."""
        name = self.container_name(payload)
        prev = self.get(name + "-prev")
        if prev is None:
            raise JobError("nothing to roll back to")
        live = self.get(name)
        for c in (prev, live):
            if c is not None:
                self.refuse_managed(name, c, "rolled back")
        if live is not None and live.id == self.own_id():
            return self.via_helper(live, name, str((live.attrs or {}).get("Image") or ""), "rollback")
        return self.restore(name)

    HANDLERS = ("create", "start", "stop", "restart", "delete", "upgrade", "update", "rollback", "logs",
                "container_restart", "container_start", "container_stop", "container_logs", "container_update",
                "container_rollback")

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

    def report(self, job_id: str, state: str, result: dict) -> None:
        """Send a result. A lost one is sent again for REPORT_FOR seconds, the
        waits doubling from 2 s up to REPORT_WAIT_MAX: an update of the account
        server's own container restarts it while its result is due. A refusal
        (a 4xx: not this host's job, or no longer running) is final. A result
        given up on is failed by the account server after an hour."""
        deadline, wait = self.clock() + REPORT_FOR, 2
        while True:
            try:
                self.api.finish(job_id, state, result)
                return
            except Exception as e:  # noqa: BLE001
                refused = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code not in (408, 429)
                if refused or self.clock() + wait > deadline:
                    log.warning("could not report job %s (%s), giving up", job_id, e)
                    return
                log.warning("could not report job %s (%s), trying again in %s s", job_id, e, wait)
                self.sleep(wait)
                wait = min(2 * wait, REPORT_WAIT_MAX)

    def run_job(self, job: dict) -> str:
        self.busy = True
        try:
            payload = job.get("payload") or {}
            log.info("job %s: %s %s", job.get("id"), job.get("kind"),
                     payload.get("label") or payload.get("container", ""))
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

    def registry_digest(self, ref: str) -> str:
        """The digest the registry has for an image reference now
        (``images.get_registry_data``), or "" when it failed or did not answer
        within REGISTRY_WAIT seconds. The ask runs on a thread so a registry
        that hangs holds the heartbeat no longer than that; any answer is
        kept for REGISTRY_EVERY seconds."""
        cached = self._registry.get(ref)
        if cached is None or self.clock() - cached[0] >= REGISTRY_EVERY:
            answer = []

            def ask():
                try:
                    answer.append(str(self.docker.images.get_registry_data(ref).id or ""))
                except Exception:  # noqa: BLE001 — no network, a private registry, an unknown tag
                    pass
            asking = threading.Thread(target=ask, name="registry", daemon=True)
            asking.start()
            asking.join(REGISTRY_WAIT)
            cached = (self.clock(), answer[0] if answer else "")
            self._registry[ref] = cached
        return cached[1]

    def repo_digests(self, image_id: str, repo: str) -> set[str]:
        """The digests a local image has from a repository's registry (its
        ``RepoDigests`` for that name): none for an image built here."""
        return {d.partition("@")[2] for d in self.docker.images.get(image_id).attrs.get("RepoDigests") or []
                if d.partition("@")[0] == repo}

    def image_stale(self, ref: str, image_id: str) -> bool | None:
        """Whether the registry's image for ``ref`` is another than the one
        the container runs (``image_id``): the registry's digest against the
        local image's ``RepoDigests`` for that repository. None when that
        cannot be told: an image id or a reference with no tag and no digest,
        an image with no digest there (built here), no answer from the
        registry, any error."""
        try:
            repo, tag, digest = image_ref(ref)
            if not ref or not image_id or IMAGE_ID_RE.match(ref) or not (tag or digest):
                return None
            digests = self.repo_digests(image_id, repo)
            current = (digest or self.registry_digest(ref)) if digests else ""
            return current not in digests if current else None
        except Exception:  # noqa: BLE001 — never fails the heartbeat
            return None

    @staticmethod
    def usage(c) -> tuple[int, float | None]:
        """A running container's memory use in MB and CPU use (``cpu_pct``)
        from one ``stats(stream=False)``, a second or two; (0, None) when it
        fails."""
        try:
            stats = c.stats(stream=False) or {}
            return int((stats.get("memory_stats") or {}).get("usage") or 0) // MIB, cpu_pct(stats)
        except Exception:  # noqa: BLE001
            return 0, None

    def usage_of(self, containers) -> dict:
        """``usage`` of each running container by id, STATS_THREADS at a time,
        so ten containers take a few seconds rather than twenty."""
        running = [c for c in containers if c.status == "running"]
        if not running:
            return {}
        with ThreadPoolExecutor(min(STATS_THREADS, len(running)), thread_name_prefix="stats") as pool:
            return dict(zip([c.id for c in running], pool.map(self.usage, running)))

    def host_cpu_pct(self) -> float | None:
        """The host's CPU use since the last heartbeat, in percent of all its
        cores (100.0: every core busy all along), to one decimal: the busy
        share of the CPU time /proc/stat counted in between (``cpu_times``).
        None on the first heartbeat, when /proc/stat cannot be read (the
        heartbeat after it is then a first one again), and when no time was
        counted."""
        try:
            now = cpu_times(self.proc_stat())
        except (OSError, ValueError):
            now = None
        before, self._cpu = self._cpu, now
        if before is None or now is None or now[1] <= before[1]:
            return None
        # iowait, counted as idle, can go back on some kernels: the share is kept within 0 to 100
        return round(min(100.0, max(0.0, (now[0] - before[0]) / (now[1] - before[1]) * 100)), 1)

    def docker_entry(self, c, usage: tuple, stale, own_id: str) -> dict:
        """One container of the host as the heartbeat's ``docker`` list has it."""
        attrs = c.attrs or {}
        state, host, labels = attrs.get("State") or {}, attrs.get("HostConfig") or {}, c.labels or {}
        policy = str((host.get("RestartPolicy") or {}).get("Name") or "")
        project = labels.get("com.docker.compose.project")
        return {
            "id": str(c.id)[:12], "name": c.name, "image": self.image_of(c),
            "image_id": short_id(str(attrs.get("Image") or "")), "status": c.status, "health": health_of(c),
            "created_at": docker_time(attrs.get("Created")), "started_at": docker_time(state.get("StartedAt")),
            "restarts": int(attrs.get("RestartCount") or 0), "restart_policy": "" if policy == "no" else policy,
            "memory_mb": usage[0], "memory_limit_mb": int(host.get("Memory") or 0) // MIB, "cpu_pct": usage[1],
            "image_stale": stale, "managed": "gamma.label" in labels, "self": bool(own_id) and c.id == own_id,
            "compose": {"project": project, "service": labels.get("com.docker.compose.service", "")}
            if project else None,
            "ports": published_ports((attrs.get("NetworkSettings") or {}).get("Ports"))}

    def heartbeat_body(self) -> dict:
        """The host's memory and disk, and its CPU use since the last
        heartbeat (``host_cpu_pct``); ``containers``, the hosted servers;
        ``docker``, every container on the host (``docker_entry``). Each
        running container's stats are read once (``usage_of``) for both. A
        container whose entry fails is left out, and logged."""
        body = {"agent_version": VERSION, **self.stats(self.settings.data_root), "cpu_pct": self.host_cpu_pct(),
                "containers": [], "docker": []}
        containers = sorted(self.docker.containers.list(all=True, ignore_removed=True), key=lambda c: c.name or "")
        usage, own_id = self.usage_of(containers), self.own_id(containers)
        seen, refs = set(), set()
        for c in containers:
            try:
                attrs = c.attrs or {}
                image = self.image_of(c)
                refs.add(image)
                stale = self.image_stale(image, str(attrs.get("Image") or ""))
                entry = self.docker_entry(c, usage.get(c.id, (0, None)), stale, own_id)
                label, hosted = (c.labels or {}).get("gamma.label", ""), None
                if label and c.name == self.name(label):       # not a kept -prev
                    state = attrs.get("State") or {}
                    hosted = {
                        "label": label, "running": c.status == "running", "health": entry["health"] or c.status,
                        "memory_mb": entry["memory_mb"], "memory_limit_mb": entry["memory_limit_mb"],
                        "cpu_pct": entry["cpu_pct"], "restarts": entry["restarts"],
                        "started_at": str(state.get("StartedAt") or ""), "oom_killed": bool(state.get("OOMKilled")),
                        "data_mb": self.data_mb(label), "image": image, "image_stale": stale}
            except Exception as e:  # noqa: BLE001 — one container never spoils the list
                log.warning("heartbeat: %s left out: %s", getattr(c, "name", "?"), e)
                continue
            body["docker"].append(entry)
            if hosted is not None:
                seen.add(label)
                body["containers"].append(hosted)
        self._data_mb = {k: v for k, v in self._data_mb.items() if k in seen}
        self._registry = {k: v for k, v in self._registry.items() if k in refs}
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
