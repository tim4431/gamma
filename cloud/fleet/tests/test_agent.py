"""The fleet agent against a fake Docker client and a fake account server:
no Docker, no network."""

import copy
import hashlib
import itertools
import json
import os
import threading
import time
import urllib.error

import pytest

from gammafleet import VERSION, agent as agent_module, selfupdate
from gammafleet.agent import (DATA_EVERY, HELPER_LABEL, REGISTRY_EVERY, Agent, Api, JobError, Settings, clone_config,
                              cpu_pct, published_ports, split_image)

MIB = 1024 * 1024
STARTED = "2026-10-04T09:00:00.123456789Z"
CREATED = "2026-10-04T08:59:58.000000000Z"
NEVER = "0001-01-01T00:00:00Z"
IDS = itertools.count(1)
# 0.75 s of CPU over 2 s of the host's 4 CPUs: one and a half CPUs
STATS = {"memory_stats": {"usage": 300 * MIB},
         "cpu_stats": {"cpu_usage": {"total_usage": 2_750_000_000}, "system_cpu_usage": 20_000_000_000,
                       "online_cpus": 4},
         "precpu_stats": {"cpu_usage": {"total_usage": 2_000_000_000}, "system_cpu_usage": 18_000_000_000}}


class NotFound(Exception):
    pass


class FakeContainer:
    """A container run with the SDK's keyword arguments (the hosted ones, the helper)."""

    def __init__(self, docker, name, image, kwargs, status="running"):
        self.docker, self.name, self.image, self.kwargs = docker, name, image, kwargs
        self.id = hashlib.sha256(f"{name}-{next(IDS)}".encode()).hexdigest()
        self.status = status
        self.stats_reply = STATS
        self.inspect = {}                                # replaces keys of attrs: what Docker left out

    @property
    def labels(self):
        return (self.attrs.get("Config") or {}).get("Labels") or {}

    @property
    def attrs(self):
        memory = int(str(self.kwargs.get("mem_limit", "0m")).rstrip("m")) * MIB
        return {"Id": self.id, "Name": "/" + self.name, "Created": CREATED, "Image": f"sha256:{self.image}",
                "RestartCount": 0,
                "State": {"Status": self.status, "Health": {"Status": "healthy" if self.status == "running" else ""},
                          "StartedAt": STARTED, "OOMKilled": False},
                "Config": {"Image": self.image, "Labels": self.kwargs.get("labels", {})},
                "HostConfig": {"Memory": memory, "RestartPolicy": self.kwargs.get("restart_policy") or {"Name": "no"}},
                "NetworkSettings": {"Ports": {}}, **self.inspect}

    @property
    def env(self):
        return self.kwargs["environment"]

    def start(self):
        self.docker.log.append(("start", self.name))
        self.status = "running"

    def stop(self, timeout=None):
        self.docker.log.append(("stop", self.name))
        self.status = "exited"

    def restart(self, timeout=None):
        self.docker.log.append(("restart", self.name))
        self.status = "running"

    def remove(self, force=False):
        self.docker.log.append(("remove", self.name))
        del self.docker.containers.by_name[self.name]

    def update(self, **limits):
        self.docker.log.append(("update", self.name, limits.get("mem_limit")))
        self.kwargs.update(limits)

    def rename(self, new):
        self.docker.log.append(("rename", self.name, new))
        del self.docker.containers.by_name[self.name]
        self.name = new
        self.docker.containers.by_name[new] = self

    def stats(self, stream=False):
        if isinstance(self.stats_reply, Exception):
            raise self.stats_reply
        return self.stats_reply

    def logs(self, tail=None, timestamps=False):
        self.docker.log.append(("logs", self.name, tail))
        lines = [f"2026-10-04T09:00:{i:02d}.000000000Z line {i}" for i in range(250)][-tail:]
        return ("\n".join(lines) + "\n").encode()


def merged(config, image, short):
    """A create body's Config with the image's defaults, as Docker merges them."""
    out = dict(config)
    names = {e.partition("=")[0] for e in config.get("Env") or []}
    out["Env"] = [e for e in image.get("Env") or [] if e.partition("=")[0] not in names] + list(config.get("Env") or [])
    out["Labels"] = {**(image.get("Labels") or {}), **(config.get("Labels") or {})}
    if not config.get("Entrypoint"):
        out["Entrypoint"] = image.get("Entrypoint")
        out["Cmd"] = config.get("Cmd") or image.get("Cmd")
    for key in ("WorkingDir", "User", "Healthcheck", "StopSignal"):
        out[key] = config.get(key) or image.get(key)
    out["ExposedPorts"] = {**(image.get("ExposedPorts") or {}), **(config.get("ExposedPorts") or {})}
    out["Hostname"] = config.get("Hostname") or short
    out["MacAddress"] = "02:42:ac:11:00:02"
    return out


RUNTIME_ENDPOINT = {"NetworkID": "n0", "EndpointID": "e0", "Gateway": "10.201.0.1", "IPAddress": "10.201.0.7",
                    "IPPrefixLen": 24, "MacAddress": "02:42:0a:c9:00:07", "DNSNames": ["x"]}


class RawContainer(FakeContainer):
    """A container made the way Compose or the create API makes one: its
    inspect data from a create body, the image's defaults merged in."""

    def __init__(self, docker, name, body):
        super().__init__(docker, name, body.get("Image", ""), {}, status="created")
        self.image_id = docker.images.tags.get(self.image, self.image)
        config = {k: v for k, v in body.items() if k not in ("HostConfig", "NetworkingConfig")}
        self.config = merged(config, docker.images.configs.get(self.image_id, {}), self.id[:12])
        self.host = body.get("HostConfig") or {}
        self.networks = {net: {**(ep or {}), **RUNTIME_ENDPOINT}
                         for net, ep in ((body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}).items()}
        self.mounts = [{"Type": "bind", "Source": b.split(":")[0], "Destination": b.split(":")[1]}
                       for b in self.host.get("Binds") or []]
        self.mounts += [{"Type": m["Type"], "Name": m["Source"], "Destination": m["Target"]}
                        for m in self.host.get("Mounts") or []]
        test = (self.config.get("Healthcheck") or {}).get("Test")
        self.health = None if not test or test == ["NONE"] else "starting"
        self.restarts, self.started, self.exit_code = 0, False, 0

    def start(self):
        super().start()
        self.started = True
        if self.health is not None:
            self.health = self.docker.new_health
        self.docker.on_start(self)

    @property
    def attrs(self):
        state = {"Status": self.status, "StartedAt": STARTED if self.started else NEVER, "OOMKilled": False,
                 "ExitCode": self.exit_code}
        if self.health is not None:
            state["Health"] = {"Status": self.health}
        ports = {}
        if self.status == "running":
            ports = {p: None for p in self.config.get("ExposedPorts") or {}}
            ports.update({p: [{"HostIp": b.get("HostIp") or "0.0.0.0", "HostPort": b["HostPort"]} for b in bindings]
                          for p, bindings in (self.host.get("PortBindings") or {}).items()})
        return {"Id": self.id, "Name": "/" + self.name, "Created": CREATED, "Image": self.image_id,
                "RestartCount": self.restarts, "State": state, "Config": self.config, "HostConfig": self.host,
                "NetworkSettings": {"Networks": self.networks, "Ports": ports}, "Mounts": self.mounts, **self.inspect}


class FakeContainers:
    def __init__(self, docker):
        self.docker, self.by_name = docker, {}

    def get(self, name):
        if name in self.by_name:
            return self.by_name[name]
        for c in self.by_name.values():
            if c.id == name:
                return c
        raise NotFound(name)

    def run(self, image, name, **kwargs):
        return self.create(image, name, _verb="run", **kwargs)

    def create(self, image, name, _verb="create", **kwargs):
        if name in self.by_name:
            raise RuntimeError(f"name {name} in use")
        self.docker.log.append((_verb, name, image))
        c = FakeContainer(self.docker, name, image, kwargs, status="running" if _verb == "run" else "created")
        self.by_name[name] = c
        return c

    def list(self, all=False, filters=None, ignore_removed=False):
        key, _, value = ((filters or {}).get("label") or "").partition("=")
        return [c for c in self.by_name.values() if (all or c.status == "running")
                and (not key or (key in c.labels and (not value or c.labels[key] == value)))]


class FakeLowLevel:
    """``docker.api``: what an update creates, connects and starts through;
    ``bodies`` holds each create body as it was sent."""

    def __init__(self, docker):
        self.docker, self.bodies = docker, []

    def create_container_from_config(self, config, name=None):
        if name in self.docker.containers.by_name:
            raise RuntimeError(f"name {name} in use")
        self.docker.log.append(("create", name, config.get("Image")))
        self.bodies.append(copy.deepcopy(config))
        c = RawContainer(self.docker, name, copy.deepcopy(config))
        self.docker.containers.by_name[name] = c
        return {"Id": c.id, "Warnings": []}

    def connect_container_to_network(self, container, net_id, ipv4_address=None, ipv6_address=None, aliases=None,
                                     links=None, link_local_ips=None, driver_opt=None, mac_address=None):
        c = self.docker.containers.get(container)
        self.docker.log.append(("connect", c.name, net_id))
        ipam = {k: v for k, v in (("IPv4Address", ipv4_address), ("IPv6Address", ipv6_address),
                                  ("LinkLocalIPs", link_local_ips)) if v}
        c.networks[net_id] = {"Aliases": aliases, "IPAMConfig": ipam or None, "DriverOpts": driver_opt,
                              "Links": [f"{k}:{v}" if v else k for k, v in sorted(links)] if links else None,
                              **RUNTIME_ENDPOINT}

    def start(self, container):
        self.docker.containers.get(container).start()


class FakeImage:
    def __init__(self, attrs):
        self.attrs = attrs

    @property
    def id(self):
        return self.attrs.get("Id", "")


class FakeRegistryData:
    def __init__(self, digest):
        self.id = digest


class FakeImages:
    """``digests``: a local image's RepoDigests by image ID; ``registry``: the
    digest the registry has for a reference (none: the registry fails);
    ``tags``: the image ID a reference names here; ``configs``: an image's
    Config by ID; ``newer``: the image ID a pull of a reference brings."""

    def __init__(self, docker):
        self.docker, self.fail = docker, False
        self.digests, self.registry, self.asked = {}, {}, []
        self.tags, self.configs, self.newer = {}, {}, {}

    def pull(self, repo, tag=None):
        if self.fail:
            raise RuntimeError("manifest unknown")
        ref = f"{repo}:{tag}"
        self.docker.log.append(("pull", ref))
        if ref in self.newer:
            self.tags[ref] = self.newer[ref]
        return self.get(ref)

    def get(self, name):
        image_id = self.tags.get(name, name)
        return FakeImage({"Id": image_id, "RepoDigests": self.digests.get(image_id, []),
                          "Config": self.configs.get(image_id, {})})

    def get_registry_data(self, ref):
        self.asked.append(ref)
        if ref not in self.registry:
            raise RuntimeError("unauthorized")
        return FakeRegistryData(self.registry[ref])


class FakeNetwork:
    attrs = {"IPAM": {"Config": [{"Subnet": "10.203.0.0/24", "Gateway": "10.203.0.1"}]}}


class FakeNetworks:
    def get(self, name):
        if name != "gamma-fleet":
            raise NotFound(name)
        return FakeNetwork()


class FakeDocker:
    """``new_health``: what the health check of a container started now says;
    ``on_start``: what else happens to it as it starts (a crash, say)."""

    def __init__(self):
        self.log = []
        self.containers = FakeContainers(self)
        self.images = FakeImages(self)
        self.networks = FakeNetworks()
        self.api = FakeLowLevel(self)
        self.new_health = "healthy"
        self.on_start = lambda c: None


class FakeApi:
    def __init__(self, jobs=()):
        self.jobs, self.finished, self.beats = list(jobs), [], []

    def next_job(self, wait=25):
        return self.jobs.pop(0) if self.jobs else None

    def finish(self, job_id, state, result):
        self.finished.append((job_id, state, result))

    def heartbeat(self, body):
        self.beats.append(body)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class S3:
    def __init__(self):
        self.deleted = []

    def delete_prefix(self, prefix):
        self.deleted.append(prefix)
        return 3


@pytest.fixture
def world(tmp_path):
    """An agent on a fake host: (agent, docker, api, health) where
    ``health["ok"]`` decides what the health check answers."""
    docker, api, clock, health = FakeDocker(), FakeApi(), Clock(), {"ok": True, "urls": []}

    def check(url):
        health["urls"].append(url)
        return health["ok"]
    settings = Settings(account_url="http://account:9002", host_token="gf_x", data_root=str(tmp_path),
                        s3={"bucket": "b", "endpoint": "https://s3.example", "region": "auto", "access_key": "AK",
                            "secret_key": "SK", "prefix": "hosted/"})
    agent = Agent(settings, docker, api, not_found=NotFound, health=check, sleep=clock.sleep, clock=clock,
                  stats=lambda root: {"memory_mb": 8000, "memory_used_mb": 2000, "disk_mb": 100000,
                                      "disk_used_mb": 5000}, s3=S3(), hostname="")
    return agent, docker, api, health


CREATE = {"label": "alice", "account_id": "acc123", "plan": "plus", "image": "ghcr.io/tim4431/gamma:sha-1",
          "env": {"GAMMA_HOSTED": "1", "GAMMA_CLOUD_CLIENT_SECRET": "s3cret", "GAMMA_PUBLIC_URL": "https://alice.example"},
          "data_dir": "alice", "memory_mb": None, "cpus": None, "network": None, "public_url": "https://alice.example"}


def create(agent):
    state, result = agent.apply({"id": "j1", "kind": "create", "payload": CREATE})
    assert state == "done", result
    return result


def test_create_runs_the_container_with_its_configuration(world, tmp_path):
    agent, docker, _, health = world
    assert create(agent) == {"container": "gamma-alice", "image": CREATE["image"], "health": "ok"}
    c = docker.containers.get("gamma-alice")
    kw = c.kwargs
    assert ("pull", "ghcr.io/tim4431/gamma:sha-1") in docker.log
    assert kw["environment"]["GAMMA_CLOUD_CLIENT_SECRET"] == "s3cret"
    assert kw["environment"]["FORWARDED_ALLOW_IPS"] == "10.203.0.0/24"
    assert kw["environment"]["GAMMA_S3_PREFIX"] == "hosted/acc123/" and kw["environment"]["GAMMA_S3_BUCKET"] == "b"
    assert kw["labels"] == {"gamma.label": "alice", "gamma.account": "acc123", "gamma.plan": "plus"}
    assert kw["restart_policy"] == {"Name": "unless-stopped"} and kw["network"] == "gamma-fleet"
    assert kw["mem_limit"] == "768m" and kw["cpu_quota"] == 100_000          # the agent's defaults
    data = os.path.join(str(tmp_path), "alice", "data")
    assert kw["volumes"] == {data: {"bind": "/data", "mode": "rw"}} and os.path.isdir(data)
    assert health["urls"][-1] == "http://gamma-alice:9001/api/health"
    spec = json.loads((tmp_path / "alice" / "container.json").read_text())
    assert spec["image"] == CREATE["image"] and spec["env"]["GAMMA_HOSTED"] == "1"
    # a retried create replaces the container (and a -prev of an earlier life) and keeps the data
    (tmp_path / "alice" / "data" / "users.db").write_text("x")
    docker.containers.run("old", name="gamma-alice-prev", labels={"gamma.label": "alice"})
    create(agent)
    assert ("remove", "gamma-alice") in docker.log and (tmp_path / "alice" / "data" / "users.db").exists()
    assert set(docker.containers.by_name) == {"gamma-alice"}


def test_create_takes_the_plans_size(world, tmp_path):
    agent, docker, _, _ = world
    state, _ = agent.apply({"id": "j", "kind": "create", "payload": {**CREATE, "memory_mb": 1536, "cpus": 2.0}})
    kw = docker.containers.get("gamma-alice").kwargs
    assert state == "done" and kw["mem_limit"] == "1536m" and kw["cpu_quota"] == 200_000
    spec = json.loads((tmp_path / "alice" / "container.json").read_text())
    assert (spec["memory_mb"], spec["cpus"]) == (1536, 2.0)


EXTRA = {"OPENAI_API_KEY": "sk-one", "TZ": "Europe/Berlin", "WORKERS": 2,
         # what the account server or the agent sets is never overridden
         "GAMMA_HOSTED": "0", "GAMMA_CLOUD_CLIENT_SECRET": "mine", "GAMMA_CLOUD_ISSUER": "https://evil",
         "GAMMA_PUBLIC_URL": "https://evil", "GAMMA_GUEST_MAX": "9", "FORWARDED_ALLOW_IPS": "*",
         "GAMMA_S3_BUCKET": "other", "GAMMA_S3_SECRET_KEY": "x",
         # nor passed off as another variable
         "": "x", "GAMMA_HOSTED=0": "x"}


def spec_of(tmp_path, label="alice"):
    return json.loads((tmp_path / label / "container.json").read_text())


def test_create_with_extra_environment(world, tmp_path):
    """The payload's env, then the agent's own variables, then extra_env,
    later winning; extra_env is kept apart in container.json."""
    agent, docker, _, _ = world
    payload = {**CREATE, "env": {**CREATE["env"], "TZ": "UTC", "GAMMA_S3_BUCKET": "payload"}, "extra_env": EXTRA}
    assert agent.apply({"id": "j", "kind": "create", "payload": payload})[0] == "done"
    env = docker.containers.get("gamma-alice").env
    assert env["OPENAI_API_KEY"] == "sk-one" and env["WORKERS"] == "2" and env["TZ"] == "Europe/Berlin"
    assert env["GAMMA_HOSTED"] == "1" and env["GAMMA_CLOUD_CLIENT_SECRET"] == "s3cret"
    assert env["GAMMA_PUBLIC_URL"] == "https://alice.example" and "GAMMA_CLOUD_ISSUER" not in env
    assert "GAMMA_GUEST_MAX" not in env and env["FORWARDED_ALLOW_IPS"] == "10.203.0.0/24"
    assert env["GAMMA_S3_BUCKET"] == "b" and env["GAMMA_S3_SECRET_KEY"] == "SK"
    assert "" not in env and "GAMMA_HOSTED=0" not in env
    spec = spec_of(tmp_path)
    assert spec["extra_env"] == {"OPENAI_API_KEY": "sk-one", "TZ": "Europe/Berlin", "WORKERS": "2"}
    assert "OPENAI_API_KEY" not in spec["env"] and spec["env"]["TZ"] == "UTC"
    # a container rebuilt by start, restart or upgrade has it too
    for kind in ("start", "restart"):
        docker.containers.get("gamma-alice").remove()
        assert agent.apply({"id": "j", "kind": kind, "payload": {"label": "alice"}})[0] == "done"
        assert docker.containers.get("gamma-alice").env["OPENAI_API_KEY"] == "sk-one"
    agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    assert docker.containers.get("gamma-alice").env["OPENAI_API_KEY"] == "sk-one"
    assert spec_of(tmp_path)["extra_env"]["OPENAI_API_KEY"] == "sk-one"


def test_a_container_json_from_before_extra_env(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    spec = spec_of(tmp_path)
    del spec["extra_env"]
    agent.save_spec("alice", spec)
    docker.containers.get("gamma-alice").remove()
    assert agent.apply({"id": "j", "kind": "start", "payload": {"label": "alice"}})[0] == "done"
    assert docker.containers.get("gamma-alice").env == spec["env"]
    state, result = agent.apply({"id": "j", "kind": "update", "payload": {"label": "alice", "extra_env": {"A": "1"}}})
    assert state == "done" and docker.containers.get("gamma-alice").env["A"] == "1"


def test_create_failures_are_reported(world):
    agent, docker, _, health = world
    assert agent.apply({"id": "j", "kind": "create", "payload": {**CREATE, "label": "../etc"}})[0] == "failed"
    assert agent.apply({"id": "j", "kind": "nuke", "payload": {}}) == ("failed", {"error": "unknown job kind 'nuke'"})
    docker.images.fail = True
    state, result = agent.apply({"id": "j", "kind": "create", "payload": CREATE})
    assert state == "failed" and "manifest unknown" in result["error"]
    docker.images.fail = False
    health["ok"] = False
    state, result = agent.apply({"id": "j", "kind": "create", "payload": CREATE})
    assert state == "failed" and "did not answer" in result["error"]


def test_start_stop_restart(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    c = docker.containers.get("gamma-alice")
    assert agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})[0] == "done"
    assert c.status == "exited"
    assert agent.apply({"id": "j", "kind": "start", "payload": {"label": "alice"}})[0] == "done"
    assert c.status == "running"
    assert agent.apply({"id": "j", "kind": "restart", "payload": {"label": "alice"}})[0] == "done"
    assert ("restart", "gamma-alice") in docker.log
    c.remove()                                          # gone: start rebuilds it from container.json
    assert agent.apply({"id": "j", "kind": "start", "payload": {"label": "alice"}})[0] == "done"
    assert docker.containers.get("gamma-alice").kwargs["environment"]["GAMMA_HOSTED"] == "1"
    assert agent.apply({"id": "j", "kind": "restart", "payload": {"label": "bob"}})[0] == "failed"
    assert agent.apply({"id": "j", "kind": "stop", "payload": {"label": "bob"}})[1]["note"] == "no such container"


def test_start_stop_restart_run_again(world):
    """A job retried after it already worked (or with its container gone)
    ends in the same place."""
    agent, docker, _, _ = world
    create(agent)
    docker.log.clear()
    assert agent.apply({"id": "j", "kind": "start", "payload": {"label": "alice"}})[0] == "done"
    assert ("start", "gamma-alice") not in docker.log                   # already running: left as it is
    agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})
    assert agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})[0] == "done"
    docker.containers.get("gamma-alice").remove()
    assert agent.apply({"id": "j", "kind": "restart", "payload": {"label": "alice"}})[0] == "done"
    assert docker.containers.get("gamma-alice").status == "running"     # rebuilt from container.json


def test_upgrade_replaces_the_container_and_keeps_its_configuration(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    docker.log.clear()
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    assert state == "done" and result["previous"] == "ghcr.io/tim4431/gamma:sha-1"
    assert docker.log[:4] == [("pull", "ghcr.io/tim4431/gamma:sha-2"), ("rename", "gamma-alice", "gamma-alice-prev"),
                              ("stop", "gamma-alice-prev"), ("run", "gamma-alice", "ghcr.io/tim4431/gamma:sha-2")]
    assert ("remove", "gamma-alice-prev") in docker.log
    new = docker.containers.get("gamma-alice")
    assert new.image.endswith("sha-2") and new.kwargs["environment"]["GAMMA_CLOUD_CLIENT_SECRET"] == "s3cret"
    assert set(docker.containers.by_name) == {"gamma-alice"}
    assert json.loads((tmp_path / "alice" / "container.json").read_text())["image"].endswith("sha-2")


def test_a_failed_upgrade_keeps_the_previous_container_stopped(world, tmp_path):
    agent, docker, _, health = world
    create(agent)
    health["ok"] = False
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    assert state == "failed" and "gamma-alice-prev" in result["error"]
    prev, new = docker.containers.get("gamma-alice-prev"), docker.containers.get("gamma-alice")
    assert prev.status == "exited" and prev.image.endswith("sha-1")
    assert new.status == "exited" and new.image.endswith("sha-2")
    assert json.loads((tmp_path / "alice" / "container.json").read_text())["image"].endswith("sha-1")
    assert agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "nobody", "image": "x:1"}})[0] == "failed"
    # retrying fails again: the known-good sha-1 container is still the -prev one
    state, _ = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    assert state == "failed" and docker.containers.get("gamma-alice-prev").image.endswith("sha-1")
    assert set(docker.containers.by_name) == {"gamma-alice", "gamma-alice-prev"}
    # a retry that works replaces the failed one and only then drops the fallback
    health["ok"] = True
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-3"}})
    assert state == "done" and result["previous"].endswith("sha-1")
    assert set(docker.containers.by_name) == {"gamma-alice"} and docker.containers.get("gamma-alice").image.endswith("sha-3")


def test_an_upgrade_run_again_after_a_crash_midway(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    docker.containers.get("gamma-alice").rename("gamma-alice-prev")    # died after the rename
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    assert state == "done" and result["previous"].endswith("sha-1")
    assert set(docker.containers.by_name) == {"gamma-alice"} and docker.containers.get("gamma-alice").image.endswith("sha-2")


def test_a_resize_keeps_the_image_and_changes_the_limits(world, tmp_path):
    agent, docker, _, health = world
    create(agent)
    docker.log.clear()
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "memory_mb": 1536, "cpus": 2}})
    assert state == "done" and result["image"] == result["previous"] == CREATE["image"]
    assert not [e for e in docker.log if e[0] == "pull"]                 # the image it runs, not pulled
    assert [e[0] for e in docker.log] == ["update"]                       # in place: no new container, no restart
    c = docker.containers.get("gamma-alice")
    assert c.image == CREATE["image"] and c.kwargs["mem_limit"] == "1536m" and c.kwargs["cpu_quota"] == 200_000
    assert c.kwargs["environment"]["GAMMA_CLOUD_CLIENT_SECRET"] == "s3cret"
    spec = json.loads((tmp_path / "alice" / "container.json").read_text())
    assert (spec["memory_mb"], spec["cpus"], spec["image"]) == (1536, 2.0, CREATE["image"])
    assert agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice"}}) == ("failed", {"error": "no image"})
    # while a failed upgrade to another image waits for its retry or rollback, a resize is refused
    health["ok"] = False
    agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    health["ok"] = True
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "memory_mb": 512}})
    assert state == "failed" and "roll it back" in result["error"]
    assert docker.containers.get("gamma-alice-prev").image == CREATE["image"]


def test_a_resize_leaves_a_stopped_container_stopped(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})
    docker.log.clear()
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "memory_mb": 512, "cpus": 1}})
    assert state == "done" and result["resized"] is True
    c = docker.containers.get("gamma-alice")
    assert c.status == "exited" and c.kwargs["mem_limit"] == "512m" and c.kwargs["memswap_limit"] == "1024m"
    assert [e[0] for e in docker.log] == ["update"]
    # with no container at all, only container.json changes: a later start runs it at that size
    c.remove(force=True)
    state, result = agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "memory_mb": 768}})
    assert state == "done" and result["resized"] is False
    assert json.loads((tmp_path / "alice" / "container.json").read_text())["memory_mb"] == 768


UPDATE = {"id": "j", "kind": "update",
          "payload": {"label": "alice", "extra_env": {"OPENAI_API_KEY": "sk-two", "MODEL": "big"}}}
UPDATED = {"container": "gamma-alice", "image": CREATE["image"], "extra_env": ["MODEL", "OPENAI_API_KEY"]}


def test_update_rebuilds_the_container_with_the_new_environment(world, tmp_path):
    agent, docker, _, _ = world
    agent.apply({"id": "j", "kind": "create",
                 "payload": {**CREATE, "memory_mb": 1536, "extra_env": {"OPENAI_API_KEY": "sk-one", "TZ": "UTC"}}})
    agent.settings.s3["secret_key"] = "SK-rotated"                       # the agent's .env changed since the create
    docker.log.clear()
    state, result = agent.apply(UPDATE)
    assert (state, result) == ("done", {**UPDATED, "health": "ok"}) and "sk-two" not in json.dumps(result)
    assert docker.log == [("rename", "gamma-alice", "gamma-alice-prev"), ("stop", "gamma-alice-prev"),
                          ("run", "gamma-alice", CREATE["image"]), ("remove", "gamma-alice-prev")]   # no pull
    c = docker.containers.get("gamma-alice")
    assert c.env["OPENAI_API_KEY"] == "sk-two" and c.env["MODEL"] == "big" and "TZ" not in c.env
    assert c.env["GAMMA_S3_SECRET_KEY"] == "SK-rotated" and c.env["GAMMA_CLOUD_CLIENT_SECRET"] == "s3cret"
    assert c.kwargs["mem_limit"] == "1536m" and set(docker.containers.by_name) == {"gamma-alice"}
    spec = spec_of(tmp_path)
    assert spec["extra_env"] == UPDATE["payload"]["extra_env"] and spec["env"]["GAMMA_S3_SECRET_KEY"] == "SK-rotated"
    # run again after it finished: the same place
    assert agent.apply(UPDATE) == (state, result)
    assert docker.containers.get("gamma-alice").env == c.env and spec_of(tmp_path) == spec
    assert set(docker.containers.by_name) == {"gamma-alice"}
    # an empty extra_env clears it; a bucket gone from the agent's settings takes its variables along
    agent.settings.s3 = {}
    state, result = agent.apply({"id": "j", "kind": "update", "payload": {"label": "alice", "extra_env": {}}})
    env = docker.containers.get("gamma-alice").env
    assert state == "done" and result["extra_env"] == [] and not [k for k in env if k.startswith("GAMMA_S3_")]
    assert "OPENAI_API_KEY" not in env and env["FORWARDED_ALLOW_IPS"] == "10.203.0.0/24" and env["GAMMA_HOSTED"] == "1"


def test_a_failed_update_keeps_the_previous_container(world, tmp_path, caplog):
    agent, docker, _, health = world
    agent.apply({"id": "j", "kind": "create", "payload": {**CREATE, "extra_env": {"OPENAI_API_KEY": "sk-one"}}})
    health["ok"] = False
    state, result = agent.apply(UPDATE)
    assert state == "failed" and "gamma-alice-prev" in result["error"]
    assert "sk-two" not in result["error"] and "sk-two" not in caplog.text
    prev, new = docker.containers.get("gamma-alice-prev"), docker.containers.get("gamma-alice")
    assert prev.status == "exited" and prev.env["OPENAI_API_KEY"] == "sk-one"
    assert new.status == "exited" and new.env["OPENAI_API_KEY"] == "sk-two"
    assert spec_of(tmp_path)["extra_env"] == {"OPENAI_API_KEY": "sk-one"}
    # a retry that fails again keeps the same fallback
    assert agent.apply(UPDATE)[0] == "failed" and docker.containers.get("gamma-alice-prev") is prev
    # rollback brings it back, as after a failed upgrade
    health["ok"] = True
    assert agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}})[0] == "done"
    assert set(docker.containers.by_name) == {"gamma-alice"} and docker.containers.get("gamma-alice") is prev
    assert prev.status == "running" and spec_of(tmp_path)["extra_env"] == {"OPENAI_API_KEY": "sk-one"}
    # a retry that works replaces only the failed container, and drops the fallback once healthy
    health["ok"] = False
    agent.apply(UPDATE)
    health["ok"] = True
    assert agent.apply(UPDATE) == ("done", {**UPDATED, "health": "ok"})
    assert set(docker.containers.by_name) == {"gamma-alice"}
    assert docker.containers.get("gamma-alice").env["OPENAI_API_KEY"] == "sk-two"


def test_an_update_run_again_after_a_crash_midway(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    c = docker.containers.get("gamma-alice")
    c.rename("gamma-alice-prev")                                          # died after the rename and the stop
    c.stop()
    assert agent.apply(UPDATE) == ("done", {**UPDATED, "health": "ok"})
    assert set(docker.containers.by_name) == {"gamma-alice"}
    c = docker.containers.get("gamma-alice")
    assert c.status == "running" and c.env["MODEL"] == "big"
    # died after saving container.json, before removing the previous container
    docker.containers.run(CREATE["image"], name="gamma-alice-prev", labels={"gamma.label": "alice"}).stop()
    assert agent.apply(UPDATE)[0] == "done"
    assert set(docker.containers.by_name) == {"gamma-alice"} and docker.containers.get("gamma-alice").env == c.env
    assert spec_of(tmp_path)["extra_env"] == UPDATE["payload"]["extra_env"]


def test_an_update_leaves_a_stopped_container_stopped(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})
    docker.log.clear()
    state, result = agent.apply(UPDATE)
    assert state == "done" and result == {**UPDATED, "health": "unchanged", "note": result["note"]}
    assert result["note"].startswith("stopped")
    assert docker.log == [("remove", "gamma-alice"), ("create", "gamma-alice", CREATE["image"])]
    c = docker.containers.get("gamma-alice")
    assert c.status == "created" and c.env["MODEL"] == "big"
    assert c.kwargs["restart_policy"] == {"Name": "unless-stopped"}
    assert spec_of(tmp_path)["extra_env"] == UPDATE["payload"]["extra_env"]
    assert agent.apply(UPDATE) == (state, result) and docker.containers.get("gamma-alice").status == "created"
    # died between removing it and creating its replacement: no container, and container.json already has it
    docker.containers.get("gamma-alice").remove()
    state, result = agent.apply(UPDATE)
    assert state == "done" and result["note"].startswith("no such container") and not docker.containers.by_name
    # the next start runs with it
    assert agent.apply({"id": "j", "kind": "start", "payload": {"label": "alice"}})[0] == "done"
    c = docker.containers.get("gamma-alice")
    assert c.status == "running" and c.env["MODEL"] == "big"
    # a crash-looping container is meant to run: it is rebuilt and checked like a running one
    c.status = "restarting"
    docker.log.clear()
    assert agent.apply(UPDATE) == ("done", {**UPDATED, "health": "ok"})
    assert ("run", "gamma-alice", CREATE["image"]) in docker.log


def test_an_update_with_no_container(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    docker.containers.get("gamma-alice").remove()
    docker.log.clear()
    state, result = agent.apply(UPDATE)
    assert state == "done" and result == {**UPDATED, "health": "unchanged",
                                          "note": "no such container: container.json alone changed"}
    assert docker.log == [] and spec_of(tmp_path)["extra_env"] == UPDATE["payload"]["extra_env"]
    state, result = agent.apply({"id": "j", "kind": "update", "payload": {"label": "bob", "extra_env": {}}})
    assert state == "failed" and result["error"].startswith("no container.json")


def test_an_update_is_refused_while_a_failed_upgrade_waits(world, tmp_path):
    agent, docker, _, health = world
    create(agent)
    health["ok"] = False
    agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    health["ok"] = True
    docker.log.clear()
    state, result = agent.apply(UPDATE)
    assert state == "failed" and "roll it back" in result["error"] and docker.log == []
    assert spec_of(tmp_path)["extra_env"] == {}
    # rolled back, it goes through
    agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}})
    assert agent.apply(UPDATE) == ("done", {**UPDATED, "health": "ok"})


def test_a_create_without_an_account_is_refused(world):
    agent, docker, _, _ = world
    state, result = agent.apply({"id": "j", "kind": "create", "payload": {**CREATE, "account_id": ""}})
    assert state == "failed" and result["error"] == "no account id"
    assert not docker.containers.by_name


def test_rollback_returns_to_the_kept_container(world, tmp_path):
    agent, docker, _, health = world
    create(agent)
    assert agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}}) == \
        ("failed", {"error": "nothing to roll back to"})
    health["ok"] = False
    agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-2"}})
    health["ok"] = True
    kept = docker.containers.get("gamma-alice-prev")
    docker.log.clear()
    state, result = agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}})
    assert state == "done" and result["image"] == CREATE["image"] and result["previous"].endswith("sha-2")
    assert docker.log[:4] == [("stop", "gamma-alice"), ("remove", "gamma-alice"),
                              ("rename", "gamma-alice-prev", "gamma-alice"), ("start", "gamma-alice")]
    assert set(docker.containers.by_name) == {"gamma-alice"} and docker.containers.get("gamma-alice") is kept
    assert kept.status == "running"
    spec = json.loads((tmp_path / "alice" / "container.json").read_text())
    assert spec["image"] == CREATE["image"] and spec["rolled_back"] is True
    # twice, or retried after a crash between the rename and the start: it only makes sure it is up
    kept.stop()
    state, result = agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}})
    assert state == "done" and result["note"] == "already rolled back" and kept.status == "running"
    # the next upgrade clears the mark, after which there is nothing to roll back to
    agent.apply({"id": "j", "kind": "upgrade", "payload": {"label": "alice", "image": "ghcr.io/tim4431/gamma:sha-3"}})
    assert "rolled_back" not in json.loads((tmp_path / "alice" / "container.json").read_text())
    assert agent.apply({"id": "j", "kind": "rollback", "payload": {"label": "alice"}})[0] == "failed"


def test_logs_are_the_last_lines(world):
    agent, docker, _, _ = world
    create(agent)
    state, result = agent.apply({"id": "j", "kind": "logs", "payload": {"label": "alice"}})
    assert state == "done" and result["container"] == "gamma-alice" and len(result["lines"]) == 200
    assert result["lines"][-1].endswith("line 249") and result["since"] == "2026-10-04T09:00:50.000000000Z"
    assert agent.apply({"id": "j", "kind": "logs", "payload": {"label": "bob"}})[0] == "failed"


def test_delete_removes_container_data_and_bucket_prefix(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    state, result = agent.apply({"id": "j", "kind": "delete", "payload": {"label": "alice", "account_id": "acc123"}})
    assert state == "done" and result["removed"] == ["gamma-alice"] and result["bucket_objects"] == 3
    assert agent.s3.deleted == ["hosted/acc123/"]
    assert not (tmp_path / "alice").exists() and docker.containers.by_name == {}
    # again, with nothing left: done
    state, result = agent.apply({"id": "j", "kind": "delete", "payload": {"label": "alice", "account_id": "acc123"}})
    assert state == "done" and result["removed"] == []


def test_an_orphans_delete_leaves_the_bucket_alone(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    state, result = agent.apply({"id": "j", "kind": "delete", "payload": {"label": "alice", "account_id": ""}})
    assert state == "done" and result["removed"] == ["gamma-alice"] and result["bucket_prefix"] == ""
    assert agent.s3.deleted == [] and not (tmp_path / "alice").exists()


def test_heartbeat_body(world, tmp_path):
    agent, docker, _, _ = world
    create(agent)
    (tmp_path / "alice" / "data" / "big").write_bytes(b"x" * (3 * MIB))
    docker.containers.run("other", name="unrelated")          # no gamma labels: not reported
    body = agent.heartbeat_body()
    assert body["agent_version"] == VERSION and body["memory_mb"] == 8000 and body["disk_used_mb"] == 5000
    assert body["containers"] == [{"label": "alice", "running": True, "health": "healthy", "memory_mb": 300,
                                   "memory_limit_mb": 768, "cpu_pct": 150.0, "restarts": 0, "started_at": STARTED,
                                   "oom_killed": False, "data_mb": 3, "image": CREATE["image"], "image_stale": None}]
    # the data directory is walked at most every DATA_EVERY seconds
    (tmp_path / "alice" / "data" / "more").write_bytes(b"x" * (2 * MIB))
    assert agent.heartbeat_body()["containers"][0]["data_mb"] == 3
    agent.sleep(DATA_EVERY)
    assert agent.heartbeat_body()["containers"][0]["data_mb"] == 5


def test_heartbeat_figures_docker_has_or_not(world):
    agent, docker, _, _ = world
    create(agent)
    c = docker.containers.get("gamma-alice")
    c.inspect = {"RestartCount": 3, "State": {"StartedAt": STARTED, "OOMKilled": True}}
    (row,) = agent.heartbeat_body()["containers"]
    assert (row["restarts"], row["started_at"], row["oom_killed"]) == (3, STARTED, True)
    c.inspect = {"RestartCount": None, "State": {}}                     # unknown: 0, "", false
    (row,) = agent.heartbeat_body()["containers"]
    assert (row["restarts"], row["started_at"], row["oom_killed"], row["health"]) == (0, "", False, "running")
    c.stats_reply = {"memory_stats": {"usage": 300 * MIB}, "cpu_stats": STATS["cpu_stats"], "precpu_stats": {}}
    (row,) = agent.heartbeat_body()["containers"]
    assert row["cpu_pct"] is None and row["memory_mb"] == 300               # the first sample: no CPU figure
    c.stats_reply = RuntimeError("daemon busy")
    (row,) = agent.heartbeat_body()["containers"]
    assert row["cpu_pct"] is None and row["memory_mb"] == 0
    c.stats_reply = STATS
    agent.apply({"id": "j", "kind": "stop", "payload": {"label": "alice"}})
    (row,) = agent.heartbeat_body()["containers"]
    assert row["running"] is False and row["cpu_pct"] is None and row["memory_mb"] == 0


def test_cpu_pct():
    assert cpu_pct(STATS) == 150.0
    cgroup1 = {"cpu_stats": {"cpu_usage": {"total_usage": 300, "percpu_usage": [1, 2]}, "system_cpu_usage": 2000},
               "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}}
    assert cpu_pct(cgroup1) == 40.0                                        # no online_cpus: the per-CPU list
    idle = {**STATS, "precpu_stats": {**STATS["precpu_stats"], "cpu_usage": STATS["cpu_stats"]["cpu_usage"]}}
    assert cpu_pct(idle) == 0.0
    assert cpu_pct({}) is None
    assert cpu_pct({**STATS, "precpu_stats": {**STATS["precpu_stats"], "system_cpu_usage": 20_000_000_000}}) is None
    assert cpu_pct({**STATS, "precpu_stats": {"cpu_usage": {"total_usage": 9e9}, "system_cpu_usage": 1}}) is None
    assert cpu_pct({**STATS, "cpu_stats": {**STATS["cpu_stats"], "system_cpu_usage": None}}) is None


def test_heartbeat_image_stale(world):
    """The registry's digest for the container's reference against the local
    image's, asked at most once an hour per reference."""
    agent, docker, _, _ = world
    create(agent)
    image, ref = f"sha256:{CREATE['image']}", CREATE["image"]

    def row():
        return agent.heartbeat_body()["containers"][0]
    assert row()["image_stale"] is None and docker.images.asked == []     # built here: no digest, no ask
    docker.images.digests[image] = ["other.io/gamma@sha256:aaa"]
    assert row()["image_stale"] is None and docker.images.asked == []     # a digest of another repository only
    docker.images.digests[image] = ["ghcr.io/tim4431/gamma@sha256:aaa"]
    docker.images.registry[ref] = "sha256:aaa"
    assert row()["image_stale"] is False and docker.images.asked == [ref]
    docker.images.registry[ref] = "sha256:bbb"                           # pushed again: told within the hour
    assert row()["image_stale"] is False and docker.images.asked == [ref]
    agent.sleep(REGISTRY_EVERY)
    assert row()["image_stale"] is True and docker.images.asked == [ref] * 2
    del docker.images.registry[ref]                                       # private, offline: unknown, kept an hour
    agent.sleep(REGISTRY_EVERY)
    assert row()["image_stale"] is None and row()["image_stale"] is None and docker.images.asked == [ref] * 3
    # two containers on one reference: one ask
    agent.apply({"id": "j", "kind": "create", "payload": {**CREATE, "label": "bob"}})
    docker.images.registry[ref] = "sha256:aaa"
    agent.sleep(REGISTRY_EVERY)
    rows = agent.heartbeat_body()["containers"]
    assert [r["image_stale"] for r in rows] == [False, False] and docker.images.asked == [ref] * 4
    # any error is unknown, never a failed heartbeat
    docker.images.get = lambda image_id: 1 / 0
    assert row()["image_stale"] is None


def test_a_registry_that_hangs_does_not_hold_the_heartbeat(world, monkeypatch):
    agent, docker, _, _ = world
    create(agent)
    docker.images.digests[f"sha256:{CREATE['image']}"] = ["ghcr.io/tim4431/gamma@sha256:aaa"]
    release = threading.Event()

    def hang(ref):
        release.wait(10)
        return FakeRegistryData("sha256:aaa")
    docker.images.get_registry_data = hang
    monkeypatch.setattr(agent_module, "REGISTRY_WAIT", 0.05)
    started = time.monotonic()
    assert agent.heartbeat_body()["containers"][0]["image_stale"] is None
    assert time.monotonic() - started < 2
    release.set()
    # the late answer is not waited for again within the hour
    assert agent.heartbeat_body()["containers"][0]["image_stale"] is None


def test_the_loop_beats_then_polls_and_reports(world):
    agent, docker, api, _ = world
    api.jobs = [{"id": "j1", "kind": "create", "payload": CREATE}, {"id": "j2", "kind": "stop", "payload": {"label": "zz"}}]
    beat = agent.step(0)
    assert len(api.beats) == 1 and api.finished[0][:2] == ("j1", "done")
    beat = agent.step(beat)
    assert len(api.beats) == 1 and api.finished[1][:2] == ("j2", "done")
    t = agent.clock()
    agent.step(beat)                                           # nothing queued, answered at once: back off
    assert agent.clock() - t >= 10


def test_a_lost_result_is_retried(world):
    agent, _, api, _ = world
    calls = []

    def flaky(job_id, state, result):
        calls.append(job_id)
        if len(calls) < 3:
            raise OSError("connection reset")
    api.finish = flaky
    agent.report("j1", "done", {})
    assert calls == ["j1"] * 3


def test_settings_and_the_http_api():
    with pytest.raises(SystemExit):
        Settings.from_env({})
    s = Settings.from_env({"GAMMA_FLEET_ACCOUNT_URL": "http://account:9002/", "GAMMA_FLEET_HOST_TOKEN": "gf_t",
                           "GAMMA_FLEET_MEMORY_MB": "1024"})
    assert s.account_url == "http://account:9002" and s.memory_mb == 1024 and s.s3 == {} and s.network == "gamma-fleet"
    sent = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def opener(req, timeout):
        sent.append((req.get_method(), req.full_url, req.get_header("Authorization"), req.data, timeout))
        return Resp(b'{"job": null}')
    api = Api(s.account_url, s.host_token, opener=opener)
    assert api.next_job(25) is None
    api.finish("j1", "done", {"a": 1})
    assert sent[0][:3] == ("GET", "http://account:9002/api/fleet/jobs?wait=25", "Bearer gf_t") and sent[0][4] == 40
    assert sent[1][0] == "POST" and json.loads(sent[1][3]) == {"state": "done", "result": {"a": 1}}


def test_split_image():
    assert split_image("ghcr.io/tim4431/gamma:sha-1") == ("ghcr.io/tim4431/gamma", "sha-1")
    assert split_image("localhost:5000/gamma") == ("localhost:5000/gamma", "latest")
    assert split_image("gamma") == ("gamma", "latest")
    with pytest.raises(JobError):
        Agent.label({"label": "Bad_Label"})


# --- every container on the host ------------------------------------------------

CLOUD = "ghcr.io/tim4431/gamma-cloud:latest"
CLOUD_1, CLOUD_2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64
CLOUD_IMAGE = {"Env": ["PATH=/usr/local/bin:/usr/bin", "GAMMA_CLOUD_BUILD=1"], "Cmd": ["python", "app.py"],
               "WorkingDir": "/app", "ExposedPorts": {"9002/tcp": {}},
               "Labels": {"org.opencontainers.image.revision": "aaa"},
               "Healthcheck": {"Test": ["CMD", "python", "-c", "import urllib.request"], "Interval": 30_000_000_000}}
FLEET = "ghcr.io/tim4431/gamma-fleet:latest"
FLEET_1, FLEET_2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64
FLEET_IMAGE = {"Env": ["PATH=/usr/local/bin:/usr/bin", "PYTHONUNBUFFERED=1"],
               "Cmd": ["python", "-m", "gammafleet.agent"], "WorkingDir": "/app"}
CADDY, LOCAL = "sha256:" + "c" * 64, "sha256:" + "d" * 64
ANONYMOUS = {"Type": "volume", "Name": "f" * 64, "Source": f"/var/lib/docker/volumes/{'f' * 64}/_data",
             "Destination": "/cache", "Driver": "local", "RW": True}
ACCOUNT, FLEET_NAME = "gamma-account-account-1", "gamma-fleet-fleet-1"


def images(docker):
    """The account server's image (1 here, 2 in the registry), the agent's (a
    here, b in the registry), Caddy's from Docker Hub and a local build."""
    im = docker.images
    im.tags.update({CLOUD: CLOUD_1, FLEET: FLEET_1, "caddy:2-alpine": CADDY, "gamma-cloud:local": LOCAL})
    im.newer.update({CLOUD: CLOUD_2, FLEET: FLEET_2})
    im.configs.update({CLOUD_1: CLOUD_IMAGE, FLEET_1: FLEET_IMAGE, FLEET_2: FLEET_IMAGE,
                       CLOUD_2: {**CLOUD_IMAGE, "Env": ["PATH=/usr/local/bin:/usr/bin", "GAMMA_CLOUD_BUILD=2"],
                                 "Cmd": ["python", "-m", "gammacloud"],
                                 "Labels": {"org.opencontainers.image.revision": "bbb"}}})
    im.digests.update({CLOUD_1: ["ghcr.io/tim4431/gamma-cloud@sha256:c1"], CADDY: ["caddy@sha256:old"],
                       FLEET_1: ["ghcr.io/tim4431/gamma-fleet@sha256:f1"]})


def compose_up(docker, project, service, image, *, networks=None, env=(), ports=None, binds=(), start=True):
    """A container as ``docker compose up -d`` makes it."""
    images(docker)
    name = f"{project}-{service}-1"
    networks = networks or [f"{project}_default"]
    body = {"Image": image, "Env": list(env),
            "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service,
                       "com.docker.compose.container-number": "1", "com.docker.compose.config-hash": f"hash-{service}",
                       "com.docker.compose.image": docker.images.tags.get(image, image)},
            "HostConfig": {"Binds": list(binds), "NetworkMode": networks[0], "PortBindings": ports or {},
                           "RestartPolicy": {"Name": "unless-stopped"}, "Memory": 0},
            "NetworkingConfig": {"EndpointsConfig": {n: {"Aliases": [name, service]} for n in networks}}}
    docker.api.create_container_from_config(body, name=name)
    c = docker.containers.get(name)
    for ep in c.networks.values():
        ep["Aliases"] = ep["Aliases"] + [c.id[:12]]                   # an older daemon lists the short id too
    if start:
        c.start()
    docker.log.clear()
    return c


def account(docker, start=True):
    """The account server's container: on its project's network and the fleet's, a published port, its data
    directory, and an anonymous volume."""
    c = compose_up(docker, "gamma-account", "account", CLOUD, networks=["gamma-account_default", "gamma-fleet"],
                   env=["GAMMA_CLOUD_ISSUER=https://account.gammapdf.com", "GAMMA_CLOUD_SECRET=s3cret"],
                   ports={"9002/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9002"}]},
                   binds=["/root/Container/gamma-account/data:/data:rw"], start=start)
    c.mounts.append(ANONYMOUS)
    return c


def fleet_agent(agent, docker):
    """The agent's own container, whose short id is the agent's hostname."""
    c = compose_up(docker, "gamma-fleet", "fleet", FLEET, networks=["gamma-fleet"],
                   binds=["/run/docker.sock:/var/run/docker.sock", "/srv/gamma:/srv/gamma"])
    agent.hostname = c.id[:12]
    return c


def job(kind, **payload):
    return {"id": "j", "kind": kind, "payload": payload}


def test_heartbeat_lists_every_container(world):
    agent, docker, _, _ = world
    create(agent)
    docker.containers.run("old", name="gamma-alice-prev", labels={"gamma.label": "alice"}).stop()
    acc = account(docker)
    docker.images.registry.update({CLOUD: "sha256:c1", "caddy:2-alpine": "sha256:new"})
    caddy = compose_up(docker, "gamma-account", "caddy", "caddy:2-alpine",
                       ports={"443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"},
                                          {"HostIp": "::", "HostPort": "443"}],
                              "80/tcp": [{"HostIp": "", "HostPort": "80"}]})
    caddy.stats_reply = RuntimeError("daemon busy")                     # one container's stats fail: the rest stand
    compose_up(docker, "gamma-share", "share", "gamma-cloud:local", start=False)   # built here, never started
    me = fleet_agent(agent, docker)
    body = agent.heartbeat_body()
    assert [row["label"] for row in body["containers"]] == ["alice"]    # as before: the hosted servers only
    rows = {row["name"]: row for row in body["docker"]}
    assert list(rows) == [ACCOUNT, "gamma-account-caddy-1", "gamma-alice", "gamma-alice-prev", FLEET_NAME,
                          "gamma-share-share-1"]
    assert rows[ACCOUNT] == {
        "id": acc.id[:12], "name": ACCOUNT, "image": CLOUD, "image_id": "sha256:111111111111", "status": "running",
        "health": "healthy", "created_at": CREATED, "started_at": STARTED, "restarts": 0,
        "restart_policy": "unless-stopped", "memory_mb": 300, "memory_limit_mb": 0, "cpu_pct": 150.0,
        "image_stale": False, "managed": False, "self": False,
        "compose": {"project": "gamma-account", "service": "account"}, "ports": ["127.0.0.1:9002->9002/tcp"]}
    row = rows["gamma-account-caddy-1"]                                    # Docker Hub, no health check, pushed again
    assert row["ports"] == ["0.0.0.0:80->80/tcp", "0.0.0.0:443->443/tcp", "[::]:443->443/tcp"]
    assert (row["memory_mb"], row["cpu_pct"], row["health"], row["image_stale"]) == (0, None, "", True)
    row = rows["gamma-share-share-1"]
    assert (row["status"], row["started_at"], row["image_stale"], row["ports"]) == ("created", "", None, [])
    assert (row["memory_mb"], row["cpu_pct"], row["image_id"]) == (0, None, "sha256:dddddddddddd")
    assert "gamma-cloud:local" not in docker.images.asked                 # a local build: the registry is not asked
    row = rows["gamma-alice"]
    assert (row["managed"], row["compose"], row["restart_policy"], row["memory_limit_mb"]) == \
        (True, None, "unless-stopped", 768)
    row = rows["gamma-alice-prev"]
    assert (row["status"], row["managed"], row["restart_policy"]) == ("exited", True, "")
    assert [name for name, row in rows.items() if row["self"]] == [FLEET_NAME] and rows[FLEET_NAME]["id"] == me.id[:12]


def test_the_agent_finds_its_own_container(world):
    agent, docker, _, _ = world
    me = fleet_agent(agent, docker)
    acc = account(docker)

    def own():
        return [row["name"] for row in agent.heartbeat_body()["docker"] if row["self"]]
    assert own() == [FLEET_NAME]
    agent.hostname = acc.id[:12]                                           # the hostname decides
    assert own() == [ACCOUNT]
    for hostname in ("vps-1", "0" * 12):                                   # given another, or none of these
        agent.hostname = hostname
        assert own() == [FLEET_NAME]                                       # the running fleet service of gamma-fleet
    me.stop()
    assert own() == []


def test_heartbeat_reads_stats_side_by_side(world):
    agent, docker, _, _ = world
    together = threading.Barrier(4, timeout=5)

    def stats(stream=False):
        together.wait()                                                    # passes only with four calls at once
        return STATS
    for i in range(4):
        docker.containers.run("img", name=f"c{i}").stats = stats
    assert [row["memory_mb"] for row in agent.heartbeat_body()["docker"]] == [300] * 4


def test_image_stale_needs_a_tag_and_a_registry(world):
    agent, docker, _, _ = world
    image = "sha256:" + "e" * 64
    docker.images.digests[image] = ["ghcr.io/x/y@sha256:e1"]
    docker.images.registry.update({"ghcr.io/x/y": "sha256:e2", "ghcr.io/x/y:1": "sha256:e1"})
    assert agent.image_stale("ghcr.io/x/y:1", image) is False
    assert agent.image_stale("ghcr.io/x/y", image) is None                 # no tag
    assert agent.image_stale("ghcr.io/x/y@sha256:e1", image) is False      # pinned: no need to ask
    assert agent.image_stale(image, image) is None and agent.image_stale("e" * 12, image) is None
    assert docker.images.asked == ["ghcr.io/x/y:1"]
    assert published_ports(None) == [] and published_ports({"80/tcp": None}) == []


def test_clone_config_keeps_the_configuration_and_drops_what_docker_made(world):
    agent, docker, _, _ = world
    attrs = account(docker).attrs
    body, more = clone_config(attrs, CLOUD_IMAGE, CLOUD_2)
    # the image's own environment, labels, command, working directory and health check go: the new image's apply
    assert body["Image"] == CLOUD
    assert body["Env"] == ["GAMMA_CLOUD_ISSUER=https://account.gammapdf.com", "GAMMA_CLOUD_SECRET=s3cret"]
    assert body["Labels"] == {"com.docker.compose.project": "gamma-account", "com.docker.compose.service": "account",
                              "com.docker.compose.container-number": "1",
                              "com.docker.compose.config-hash": "hash-account", "com.docker.compose.image": CLOUD_2}
    assert not {"Cmd", "Entrypoint", "WorkingDir", "Healthcheck", "User", "StopSignal"} & set(body)
    # what Docker made for the old container goes
    assert "Hostname" not in body and "MacAddress" not in body
    assert body["ExposedPorts"] == {"9002/tcp": {}}
    # HostConfig as it is, with the anonymous volume mounted again
    assert body["HostConfig"] == {**attrs["HostConfig"], "Mounts": [{"Type": "volume", "Source": "f" * 64,
                                                                     "Target": "/cache"}]}
    # the first network at create, the other after; aliases less the old short id, no runtime fields
    assert body["NetworkingConfig"] == {"EndpointsConfig": {"gamma-account_default": {"Aliases": [ACCOUNT, "account"]}}}
    assert more == {"gamma-fleet": {"Aliases": [ACCOUNT, "account"]}}
    # what the container set itself stays, though the image has a value of its own
    mine = {**attrs, "Config": {**attrs["Config"], "Cmd": ["python", "app.py", "--debug"], "WorkingDir": "/srv",
                                "Hostname": "account", "User": "1000"}}
    body, _ = clone_config(mine, CLOUD_IMAGE)
    assert (body["Cmd"], body["WorkingDir"], body["Hostname"], body["User"]) == \
        (["python", "app.py", "--debug"], "/srv", "account", "1000")
    assert body["Labels"]["com.docker.compose.image"] == CLOUD_1             # no new image id given: as it was
    # Docker's default network, a static address; a container on another one's network has none of its own
    bridge = {"Id": attrs["Id"], "Config": {"Image": "x:1"}, "HostConfig": {"NetworkMode": "default"},
              "NetworkSettings": {"Networks": {"bridge": {"Aliases": None, "IPAMConfig": {"IPv4Address": "172.17.0.9"},
                                                          **RUNTIME_ENDPOINT}}}}
    body, more = clone_config(bridge, {})
    assert body["NetworkingConfig"] == {"EndpointsConfig": {"bridge": {"IPAMConfig": {"IPv4Address": "172.17.0.9"}}}}
    body, more = clone_config({**bridge, "HostConfig": {"NetworkMode": "container:abc"},
                               "NetworkSettings": {"Networks": {}}}, {})
    assert "NetworkingConfig" not in body and more == {}


def test_container_update_recreates_it_on_the_new_image(world):
    agent, docker, _, _ = world
    old = account(docker)
    old.networks["gamma-fleet"].update({"IPAMConfig": {"IPv4Address": "10.203.0.5"}, "Links": ["gamma-share:share"],
                                        "DriverOpts": {"com.example.opt": "1"}})
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert (state, result) == ("done", {"container": ACCOUNT, "image": CLOUD,
                                        "previous_image_id": "sha256:111111111111",
                                        "image_id": "sha256:222222222222", "health": "healthy"})
    assert docker.log == [("pull", CLOUD), ("rename", ACCOUNT, ACCOUNT + "-prev"), ("stop", ACCOUNT + "-prev"),
                          ("create", ACCOUNT, CLOUD), ("connect", ACCOUNT, "gamma-fleet"), ("start", ACCOUNT),
                          ("remove", ACCOUNT + "-prev")]
    new = docker.containers.get(ACCOUNT)
    assert set(docker.containers.by_name) == {ACCOUNT} and new is not old and new.image_id == CLOUD_2
    config = new.attrs["Config"]
    assert config["Env"] == ["PATH=/usr/local/bin:/usr/bin", "GAMMA_CLOUD_BUILD=2",
                             "GAMMA_CLOUD_ISSUER=https://account.gammapdf.com", "GAMMA_CLOUD_SECRET=s3cret"]
    assert config["Cmd"] == ["python", "-m", "gammacloud"] and config["Hostname"] == new.id[:12]
    assert config["Labels"]["org.opencontainers.image.revision"] == "bbb"
    assert config["Labels"]["com.docker.compose.config-hash"] == "hash-account"
    assert config["Labels"]["com.docker.compose.image"] == CLOUD_2
    assert new.attrs["HostConfig"]["PortBindings"] == old.attrs["HostConfig"]["PortBindings"]
    assert new.attrs["HostConfig"]["RestartPolicy"] == {"Name": "unless-stopped"}
    assert {"Type": "volume", "Name": "f" * 64, "Destination": "/cache"} in new.mounts
    networks = new.attrs["NetworkSettings"]["Networks"]
    assert networks["gamma-account_default"]["Aliases"] == [ACCOUNT, "account"]
    fleet_net = networks["gamma-fleet"]
    assert (fleet_net["Aliases"], fleet_net["IPAMConfig"], fleet_net["Links"], fleet_net["DriverOpts"]) == \
        ([ACCOUNT, "account"], {"IPv4Address": "10.203.0.5"}, ["gamma-share:share"], {"com.example.opt": "1"})
    assert "s3cret" not in json.dumps(result)
    # run again after it finished: the newest image already, nothing recreated
    docker.log.clear()
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "done" and result["note"] == "already runs the newest image"
    assert result["image_id"] == result["previous_image_id"] == "sha256:222222222222" and result["health"] == "healthy"
    assert docker.log == [("pull", CLOUD)] and docker.containers.get(ACCOUNT) is new


def test_a_failed_container_update_keeps_the_previous_one(world):
    agent, docker, _, _ = world
    old = account(docker)
    docker.new_health = "unhealthy"
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert (state, result) == ("failed", {"error": f"update of {ACCOUNT} failed: {ACCOUNT} is unhealthy; the previous "
                                                   f"container is kept, stopped, as {ACCOUNT}-prev"})
    prev, new = docker.containers.get(ACCOUNT + "-prev"), docker.containers.get(ACCOUNT)
    assert prev is old and old.status == "exited" and new.status == "exited" and new.image_id == CLOUD_2
    # a retry that fails again keeps the same fallback: one that exits, one never healthy
    docker.new_health = "healthy"

    def crash(c):
        c.status, c.exit_code = "exited", 1
    docker.on_start = crash
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "failed" and f"{ACCOUNT} did not keep running (exited, exit code 1)" in result["error"]
    docker.on_start, docker.new_health = (lambda c: None), "starting"
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "failed" and "was not healthy within 120 s" in result["error"]
    assert docker.containers.get(ACCOUNT + "-prev") is old and old.status == "exited"
    assert set(docker.containers.by_name) == {ACCOUNT, ACCOUNT + "-prev"}
    # a retry that works replaces only the failed container, then drops the fallback
    docker.new_health = "healthy"
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "done" and result["previous_image_id"] == "sha256:111111111111" and result["health"] == "healthy"
    assert set(docker.containers.by_name) == {ACCOUNT} and docker.containers.get(ACCOUNT).image_id == CLOUD_2


def test_a_container_update_run_again_after_a_crash_midway(world):
    agent, docker, _, _ = world
    old = account(docker)
    old.rename(ACCOUNT + "-prev")                                          # died after the rename and the stop
    old.stop()
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "done" and result["previous_image_id"] == "sha256:111111111111"
    new = docker.containers.get(ACCOUNT)
    assert set(docker.containers.by_name) == {ACCOUNT} and new.status == "running" and new.image_id == CLOUD_2
    assert new.attrs["Config"]["Labels"]["com.docker.compose.service"] == "account"
    # died once the new one was up, before the previous one was removed
    docker.containers.by_name[old.name] = old
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "done" and result["health"] == "healthy"
    assert set(docker.containers.by_name) == {ACCOUNT} and docker.containers.get(ACCOUNT).image_id == CLOUD_2


def test_a_stopped_container_is_updated_stopped(world):
    agent, docker, _, _ = world
    account(docker).stop()
    docker.log.clear()
    state, result = agent.apply(job("container_update", container=ACCOUNT))
    assert state == "done" and result["note"] == "stopped: it starts on the new image" and result["health"] == ""
    assert ("start", ACCOUNT) not in docker.log
    new = docker.containers.get(ACCOUNT)
    assert new.status == "created" and new.image_id == CLOUD_2 and set(docker.containers.by_name) == {ACCOUNT}


def test_container_update_refuses_what_it_cannot_pull(world):
    agent, docker, _, _ = world
    create(agent)
    images(docker)
    docker.log.clear()

    def refused(name):
        state, result = agent.apply(job("container_update", container=name))
        assert state == "failed"
        return result["error"]
    assert refused("gamma-alice") == "gamma-alice is a hosted server: a hosted server is upgraded by its own jobs"
    for i, (ref, why) in enumerate((("gamma-cloud:local", "a local build"), ("ghcr.io/tim4431/gamma-cloud", "no tag"),
                                    ("ghcr.io/tim4431/gamma-cloud@sha256:c1", "pinned to a digest"),
                                    ("1" * 12, "by its id"))):
        docker.containers.run(ref, name=f"c{i}")
        assert why in refused(f"c{i}")
    docker.containers.run(CLOUD, name="once").inspect = {"HostConfig": {"AutoRemove": True}}
    assert refused("once") == "once is removed as it stops (--rm): there would be no -prev to fall back to"
    assert refused("nobody") == "no container nobody" and refused("../etc").startswith("bad container name")
    assert not [e for e in docker.log if e[0] in ("pull", "rename", "stop")]
    # Docker Hub has no host in the name, but an image pulled from it has its digest: that one is pulled
    compose_up(docker, "gamma-account", "caddy", "caddy:2-alpine")
    assert agent.apply(job("container_update", container="gamma-account-caddy-1"))[0] == "done"
    assert docker.log[0] == ("pull", "caddy:2-alpine")


def test_container_restart_start_stop_and_logs(world):
    agent, docker, _, _ = world
    account(docker)
    assert agent.apply(job("container_restart", container=ACCOUNT)) == \
        ("done", {"container": ACCOUNT, "status": "running", "health": "healthy"})
    assert docker.log == [("restart", ACCOUNT)]
    assert agent.apply(job("container_start", container=ACCOUNT)) == \
        ("done", {"container": ACCOUNT, "status": "running", "note": "already running"})
    assert agent.apply(job("container_stop", container=ACCOUNT)) == ("done", {"container": ACCOUNT, "status": "exited"})
    assert agent.apply(job("container_stop", container=ACCOUNT)) == \
        ("done", {"container": ACCOUNT, "status": "exited", "note": "already stopped"})
    assert agent.apply(job("container_start", container=ACCOUNT)) == \
        ("done", {"container": ACCOUNT, "status": "running"})
    state, result = agent.apply(job("container_logs", container=ACCOUNT))
    assert state == "done" and len(result["lines"]) == 200 and result["since"] == "2026-10-04T09:00:50.000000000Z"
    assert agent.apply(job("container_logs", container=ACCOUNT, lines=10))[1]["lines"][0].endswith("line 240")
    agent.apply(job("container_logs", container=ACCOUNT, lines=99999))
    assert docker.log[-1] == ("logs", ACCOUNT, 5000)
    assert agent.apply(job("container_logs", container=ACCOUNT, lines="many")) == \
        ("failed", {"error": "bad lines 'many'"})
    for kind in ("container_restart", "container_start", "container_stop", "container_logs", "container_rollback"):
        assert agent.apply(job(kind, container="-x"))[1]["error"].startswith("bad container name")
        if kind != "container_rollback":
            assert agent.apply(job(kind, container="nobody")) == ("failed", {"error": "no container nobody"})


def test_container_rollback(world):
    agent, docker, _, _ = world
    old = account(docker)
    rollback = job("container_rollback", container=ACCOUNT)
    assert agent.apply(rollback) == ("failed", {"error": "nothing to roll back to"})

    def fail_an_update():
        docker.new_health = "unhealthy"
        assert agent.apply(job("container_update", container=ACCOUNT))[0] == "failed"
        docker.new_health = "healthy"
    fail_an_update()
    docker.log.clear()
    assert agent.apply(rollback) == ("done", {"container": ACCOUNT, "image_id": "sha256:111111111111"})
    assert docker.log == [("stop", ACCOUNT), ("remove", ACCOUNT), ("start", ACCOUNT + "-prev"),
                          ("rename", ACCOUNT + "-prev", ACCOUNT)]
    assert set(docker.containers.by_name) == {ACCOUNT} and docker.containers.get(ACCOUNT) is old
    assert old.status == "running"
    assert agent.apply(rollback) == ("failed", {"error": "nothing to roll back to"})       # nothing kept any more
    # run again after a crash midway: once the failed one was removed, or once -prev started
    for started in (False, True):
        fail_an_update()
        docker.containers.get(ACCOUNT).remove()
        if started:
            old.start()
        assert agent.apply(rollback) == ("done", {"container": ACCOUNT, "image_id": "sha256:111111111111"})
        assert set(docker.containers.by_name) == {ACCOUNT} and docker.containers.get(ACCOUNT) is old
        assert old.status == "running"
    # a hosted server's -prev is its own jobs'
    create(agent)
    docker.containers.run("old", name="gamma-alice-prev", labels={"gamma.label": "alice"}).stop()
    state, result = agent.apply(job("container_rollback", container="gamma-alice"))
    assert state == "failed" and result["error"].endswith("a hosted server is rolled back by its own jobs")
    assert docker.containers.get("gamma-alice-prev").status == "exited"


def test_the_agent_updates_itself_through_one_helper(world):
    agent, docker, _, _ = world
    me = fleet_agent(agent, docker)
    state, result = agent.apply(job("container_update", container=FLEET_NAME))
    assert (state, result) == ("done", {"container": FLEET_NAME, "image": FLEET,
                                        "note": "helper started; the new agent reports with its first heartbeat"})
    helper = docker.containers.get(FLEET_NAME + "-selfupdate")
    assert docker.log == [("pull", FLEET), ("run", helper.name, FLEET)]
    assert helper.kwargs == {"command": ["python", "-m", "gammafleet.selfupdate", FLEET_NAME, "update"],
                             "detach": True, "remove": True, "network_mode": "none",
                             "labels": {HELPER_LABEL: FLEET_NAME},
                             "volumes": {"/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}}}
    assert docker.containers.get(FLEET_NAME) is me and me.status == "running"        # it never touches itself
    # run again while the helper works: no second helper
    docker.log.clear()
    state, result = agent.apply(job("container_update", container=FLEET_NAME))
    assert state == "done" and result["note"] == f"{helper.name} is already at work: no second helper"
    assert docker.log == []
    # the helper does the work with the same code, and is removed as it exits
    helper.remove()
    docker.log.clear()
    assert selfupdate.run(agent, FLEET_NAME, "update") == {
        "container": FLEET_NAME, "previous_image_id": "sha256:aaaaaaaaaaaa", "image_id": "sha256:bbbbbbbbbbbb",
        "health": ""}                                                      # no health check: running after 10 s
    assert docker.log == [("rename", FLEET_NAME, FLEET_NAME + "-prev"), ("stop", FLEET_NAME + "-prev"),
                          ("create", FLEET_NAME, FLEET), ("start", FLEET_NAME), ("remove", FLEET_NAME + "-prev")]
    new = docker.containers.get(FLEET_NAME)
    assert new.attrs["HostConfig"]["Binds"][0] == "/run/docker.sock:/var/run/docker.sock"
    assert new.labels["com.docker.compose.service"] == "fleet" and new.image_id == FLEET_2
    # the new agent, handed the same job again: nothing to do, no helper
    agent.hostname = new.id[:12]
    docker.log.clear()
    state, result = agent.apply(job("container_update", container=FLEET_NAME))
    assert state == "done" and result["note"] == "already runs the newest image" and docker.log == [("pull", FLEET)]


def test_the_agent_restarts_and_rolls_back_itself_through_the_helper_and_never_stops(world):
    agent, docker, _, _ = world
    me = fleet_agent(agent, docker)
    state, result = agent.apply(job("container_restart", container=FLEET_NAME))
    helper = docker.containers.get(FLEET_NAME + "-selfupdate")
    assert state == "done" and result["image"] == FLEET_1 and helper.image == FLEET_1
    assert helper.kwargs["command"][-2:] == [FLEET_NAME, "restart"] and ("restart", FLEET_NAME) not in docker.log
    # a helper that exited without being removed is removed before the next one starts
    helper.stop()
    docker.containers.run(FLEET, name=FLEET_NAME + "-prev").stop()       # a -prev an interrupted self-update kept
    state, result = agent.apply(job("container_rollback", container=FLEET_NAME))
    assert state == "done" and result["note"].startswith("helper started")
    assert docker.containers.get(FLEET_NAME + "-selfupdate").kwargs["command"][-1] == "rollback"
    assert docker.containers.get(FLEET_NAME) is me
    state, result = agent.apply(job("container_stop", container=FLEET_NAME))
    assert state == "failed" and "is this agent" in result["error"] and me.status == "running"
    # what the helper does for a restart
    docker.log.clear()
    assert selfupdate.run(agent, FLEET_NAME, "restart")["status"] == "running"
    assert docker.log == [("restart", FLEET_NAME)]


def test_a_failed_self_update_starts_the_previous_agent_again(world):
    agent, docker, _, _ = world
    me = fleet_agent(agent, docker)
    docker.images.pull("ghcr.io/tim4431/gamma-fleet", tag="latest")      # what the agent pulled for the helper

    def crash(c):
        if c.image_id == FLEET_2:
            c.status, c.exit_code = "exited", 1
    docker.on_start = crash
    with pytest.raises(JobError, match="did not keep running"):
        selfupdate.run(agent, FLEET_NAME, "update")
    failed = docker.containers.get(FLEET_NAME)
    assert failed.status == "exited" and failed.image_id == FLEET_2
    assert docker.containers.get(FLEET_NAME + "-prev") is me and me.status == "running"   # the host keeps an agent
    # the previous agent, running as -prev, rolls itself back: nothing there stops it
    docker.log.clear()
    assert agent.apply(job("container_rollback", container=FLEET_NAME)) == \
        ("done", {"container": FLEET_NAME, "image_id": "sha256:aaaaaaaaaaaa"})
    assert docker.log == [("stop", FLEET_NAME), ("remove", FLEET_NAME), ("rename", FLEET_NAME + "-prev", FLEET_NAME)]
    assert set(docker.containers.by_name) == {FLEET_NAME} and docker.containers.get(FLEET_NAME) is me


def test_the_helper_refuses_what_it_does_not_know():
    assert selfupdate.main([]) == 2 and selfupdate.main([FLEET_NAME, "delete"]) == 2


def test_a_result_is_offered_for_three_minutes(world):
    agent, _, api, _ = world
    calls = []

    def down(job_id, state, result):
        calls.append(agent.clock())
        raise urllib.error.URLError("connection refused")
    api.finish = down
    agent.report("j1", "done", {})
    assert [b - a for a, b in zip(calls, calls[1:])] == [2, 4, 8, 16, 30, 30, 30, 30, 30]   # 180 s, growing waits
    # a proxy's 502 while the account server restarts is tried again; a refusal is final
    answers = [502, 502, None]

    def restarting(job_id, state, result):
        code = answers.pop(0)
        if code:
            raise urllib.error.HTTPError("http://account/api/fleet/jobs/j1", code, "Bad Gateway", {}, None)
    api.finish = restarting
    agent.report("j1", "done", {})
    assert answers == []
    calls.clear()

    def refused(job_id, state, result):
        calls.append(job_id)
        raise urllib.error.HTTPError("http://account/api/fleet/jobs/j1", 409, "Conflict", {}, None)
    api.finish = refused
    agent.report("j1", "done", {})
    assert calls == ["j1"]
