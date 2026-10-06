"""The fleet agent against a fake Docker client and a fake account server:
no Docker, no network."""

import json
import os
import threading
import time

import pytest

from gammafleet import VERSION, agent as agent_module
from gammafleet.agent import DATA_EVERY, REGISTRY_EVERY, Agent, Api, JobError, Settings, cpu_pct, split_image

MIB = 1024 * 1024
STARTED = "2026-10-04T09:00:00.123456789Z"
# 0.75 s of CPU over 2 s of the host's 4 CPUs: one and a half CPUs
STATS = {"memory_stats": {"usage": 300 * MIB},
         "cpu_stats": {"cpu_usage": {"total_usage": 2_750_000_000}, "system_cpu_usage": 20_000_000_000,
                       "online_cpus": 4},
         "precpu_stats": {"cpu_usage": {"total_usage": 2_000_000_000}, "system_cpu_usage": 18_000_000_000}}


class NotFound(Exception):
    pass


class FakeContainer:
    def __init__(self, docker, name, image, kwargs, status="running"):
        self.docker, self.name, self.image, self.kwargs = docker, name, image, kwargs
        self.status = status
        self.labels = kwargs.get("labels", {})
        self.stats_reply = STATS
        self.inspect = {}                                # replaces keys of attrs: what Docker left out

    @property
    def attrs(self):
        memory = int(str(self.kwargs.get("mem_limit", "0m")).rstrip("m")) * MIB
        return {"Image": f"sha256:{self.image}", "RestartCount": 0,
                "State": {"Health": {"Status": "healthy" if self.status == "running" else ""},
                          "StartedAt": STARTED, "OOMKilled": False},
                "Config": {"Image": self.image}, "HostConfig": {"Memory": memory}, **self.inspect}

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
        lines = [f"2026-10-04T09:00:{i:02d}.000000000Z line {i}" for i in range(250)][-tail:]
        return ("\n".join(lines) + "\n").encode()


class FakeContainers:
    def __init__(self, docker):
        self.docker, self.by_name = docker, {}

    def get(self, name):
        if name not in self.by_name:
            raise NotFound(name)
        return self.by_name[name]

    def run(self, image, name, **kwargs):
        return self.create(image, name, _verb="run", **kwargs)

    def create(self, image, name, _verb="create", **kwargs):
        if name in self.by_name:
            raise RuntimeError(f"name {name} in use")
        self.docker.log.append((_verb, name, image))
        c = FakeContainer(self.docker, name, image, kwargs, status="running" if _verb == "run" else "created")
        self.by_name[name] = c
        return c

    def list(self, all=False, filters=None):
        return [c for c in self.by_name.values() if "gamma.label" in c.labels]


class FakeImage:
    def __init__(self, attrs):
        self.attrs = attrs


class FakeRegistryData:
    def __init__(self, digest):
        self.id = digest


class FakeImages:
    """``digests``: a local image's RepoDigests by image ID; ``registry``: the
    digest the registry has for a reference (none: the registry fails)."""

    def __init__(self, docker):
        self.docker, self.fail = docker, False
        self.digests, self.registry, self.asked = {}, {}, []

    def pull(self, repo, tag=None):
        if self.fail:
            raise RuntimeError("manifest unknown")
        self.docker.log.append(("pull", f"{repo}:{tag}"))

    def get(self, image_id):
        return FakeImage({"Id": image_id, "RepoDigests": self.digests.get(image_id, [])})

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
    def __init__(self):
        self.log = []
        self.containers = FakeContainers(self)
        self.images = FakeImages(self)
        self.networks = FakeNetworks()


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
                                      "disk_used_mb": 5000}, s3=S3())
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
