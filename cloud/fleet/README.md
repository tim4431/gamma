# gamma-fleet: the agent on a host of hosted Gamma servers

One small process per host. It holds the Docker socket, makes outbound
calls only, and works through the job queue the account server keeps for
its host: create, start, stop, restart, upgrade or resize, roll back and
delete a hosted Gamma container, and fetch its logs. Every five minutes it
reports the host's memory and disk and each container's state. It imports
nothing from `gammacloud`; the two share only the HTTP shape. The whole
picture, including the API and the job payloads:
[docs/dev/hosted.md](../../docs/dev/hosted.md).

```
fleet/
  gammafleet/agent.py   the loop, the job handlers, the HTTP client
  tests/                pytest with a fake Docker client (no Docker needed)
  Dockerfile            the image (python:3.12-slim + docker + boto3)
  requirements.txt
  deploy/               the agent's own compose project on a host (deploy/README.md)
```

## Running it

On every host it is its own compose project, `deploy/compose.yml`, apart
from the account server's: first deployment, updates, what `/srv/gamma`
holds and the security notes are in [deploy/README.md](deploy/README.md).

The data root must be mounted at the same path inside the agent as on the
host, because the agent hands that path to the Docker daemon as a bind
mount. The agent joins the fleet network so its health check reaches
`http://gamma-<label>:9001/api/health`.

Environment: `GAMMA_FLEET_ACCOUNT_URL` and `GAMMA_FLEET_HOST_TOKEN`
(required), `GAMMA_FLEET_NETWORK` (`gamma-fleet`), `GAMMA_FLEET_DATA_ROOT`
(`/srv/gamma`), `GAMMA_FLEET_MEMORY_MB` (768) and `GAMMA_FLEET_CPUS` (1),
and the optional off-site bucket `GAMMA_FLEET_S3_BUCKET`, `_ENDPOINT`,
`_REGION`, `_ACCESS_KEY`, `_SECRET_KEY`, `_PREFIX` (`hosted/`). The
docstring at the top of `gammafleet/agent.py` describes each, and
[deploy/.env.example](deploy/.env.example) lists them.

## Tests

```bash
cd cloud/fleet
python -m pytest -q
```

`.github/workflows/fleet.yml` publishes `ghcr.io/tim4431/gamma-fleet:latest`
and `:sha-<short>` on every push to `main` that touches this folder, and on
a manual dispatch from any branch (`gh workflow run fleet.yml --ref dev`).
To build by hand instead: `docker build -t ghcr.io/tim4431/gamma-fleet:latest cloud/fleet`.
