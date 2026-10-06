"""The self-update helper. The fleet agent cannot update, restart or roll
back its own container: stopping it would stop the agent midway. For those
jobs on its own container it starts this one-shot container instead, from
the image pulled (an update) or the one it runs, with the Docker socket, on
no network, removed when it exits:

    python -m gammafleet.selfupdate <container> [update|restart|rollback]

It does to the agent's container what the agent does to any other, with the
same code: ``Agent.recreate`` stops it, keeps it as ``<container>-prev``,
creates the new one under the name with the same configuration, waits for it
and removes ``-prev``; ``Agent.restore`` brings ``-prev`` back. A failed
update stops the new one (its logs stay) and keeps ``-prev``, which is then
started again under that name, so the host keeps an agent that reports: a
``container_rollback`` brings it back under its own name, a
``container_update`` tries again. The agent the helper stops finishes
reporting its job first (it exits on SIGTERM once the job is done), and the
new one reports with its first heartbeat. Each step goes to the helper's log.
"""

import logging
import sys

from .agent import STOP_TIMEOUT, Agent, JobError, Settings

log = logging.getLogger("gammafleet.selfupdate")

ACTIONS = ("update", "restart", "rollback")


def run(agent: Agent, name: str, action: str) -> dict:
    """One action on the agent's container ``name``; raises JobError."""
    if action == "restart":
        agent.need(name).restart(timeout=STOP_TIMEOUT)
        return {"container": name, **agent.state_of(name)}
    if action == "rollback":
        return agent.restore(name)
    try:
        return {"container": name, **agent.recreate(name)}
    except JobError:
        prev = agent.get(name + "-prev")
        if prev is not None and prev.status != "running":
            log.warning("starting the previous agent %s again, so the host keeps one", prev.name)
            prev.start()
        raise


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:] if argv is None else list(argv)
    action = args[1] if len(args) > 1 else "update"
    if len(args) not in (1, 2) or action not in ACTIONS:
        log.error("usage: python -m gammafleet.selfupdate <container> [%s]", "|".join(ACTIONS))
        return 2
    import docker
    agent = Agent(Settings(account_url="", host_token=""), docker.from_env(), None)
    log.info("%s of %s", action, args[0])
    try:
        result = run(agent, args[0], action)
    except Exception as e:  # noqa: BLE001 — what failed is the helper's last log line
        log.error("%s of %s failed: %s", action, args[0], e)
        return 1
    log.info("%s of %s done: %s", action, args[0], result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
