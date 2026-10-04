"""The fleet agent against a fake Docker client and a fake account server:
no Docker, no network."""

import json
import os

import pytest

from gammafleet import VERSION
from gammafleet.agent import DATA_EVERY, Agent, Api, JobError, Settings, split_image

MIB = 1024 * 1024


class NotFound(Exception):
    pass


class FakeContainer:
    def __init__(self, docker, name, image, kwargs):
        self.docker, self.name, self.image, self.kwargs = docker, name, image, kwargs
        self.status = "running"
        self.labels = kwargs.get("labels", {})

    @property
    def attrs(self):
        memory = int(str(self.kwargs.get("mem_limit", "0m")).rstrip("m")) * MIB
        return {"State": {"Health": {"Status": "healthy" if self.status == "running" else ""}},
                "Config": {"Image": self.image}, "HostConfig": {"Memory": memory}}

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
        return {"memory_stats": {"usage": 300 * MIB}}

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
        if name in self.by_name:
            raise RuntimeError(f"name {name} in use")
        self.docker.log.append(("run", name, image))
        c = FakeContainer(self.docker, name, image, kwargs)
        self.by_name[name] = c
        return c

    def list(self, all=False, filters=None):
        return [c for c in self.by_name.values() if "gamma.label" in c.labels]


class FakeImages:
    def __init__(self, docker):
        self.docker, self.fail = docker, False

    def pull(self, repo, tag=None):
        if self.fail:
            raise RuntimeError("manifest unknown")
        self.docker.log.append(("pull", f"{repo}:{tag}"))


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
                                   "memory_limit_mb": 768, "data_mb": 3, "image": CREATE["image"]}]
    # the data directory is walked at most every DATA_EVERY seconds
    (tmp_path / "alice" / "data" / "more").write_bytes(b"x" * (2 * MIB))
    assert agent.heartbeat_body()["containers"][0]["data_mb"] == 3
    agent.sleep(DATA_EVERY)
    assert agent.heartbeat_body()["containers"][0]["data_mb"] == 5


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
