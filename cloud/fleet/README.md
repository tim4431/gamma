# gamma-fleet: the agent on a host of hosted Gamma servers

One small process per host. It holds the Docker socket, makes outbound
calls only, and works through the job queue the account server keeps for
its host: create, start, stop, restart, upgrade and delete a hosted
Gamma container. Every five minutes it reports the host's memory and disk
and each container's state. It imports nothing from `gammacloud`; the two
share only the HTTP shape. The whole picture, including the API and the
job payloads: [docs/dev/hosted.md](../../docs/dev/hosted.md).

```
fleet/
  gammafleet/agent.py   the loop, the job handlers, the HTTP client
  tests/                pytest with a fake Docker client (no Docker needed)
  Dockerfile            the image (python:3.12-slim + docker + boto3)
  requirements.txt
```

## Running it

On the account server's own host it is the `fleet` service of
`cloud/deploy/compose.yml` (profile `fleet`). Anywhere else:

```bash
docker network inspect gamma-fleet >/dev/null 2>&1 || docker network create --subnet 10.203.0.0/24 gamma-fleet
docker run -d --name gamma-fleet --restart unless-stopped --network gamma-fleet \
  -v /var/run/docker.sock:/var/run/docker.sock -v /srv/gamma:/srv/gamma \
  -e GAMMA_FLEET_ACCOUNT_URL=https://account.gammapdf.com \
  -e GAMMA_FLEET_HOST_TOKEN=gf_... \
  ghcr.io/tim4431/gamma-fleet:latest
```

The data root must be mounted at the same path inside the agent as on the
host, because the agent hands that path to the Docker daemon as a bind
mount. The agent joins the fleet network so its health check reaches
`http://gamma-<label>:9001/api/health`.

Environment: `GAMMA_FLEET_ACCOUNT_URL` and `GAMMA_FLEET_HOST_TOKEN`
(required), `GAMMA_FLEET_NETWORK` (`gamma-fleet`), `GAMMA_FLEET_DATA_ROOT`
(`/srv/gamma`), `GAMMA_FLEET_MEMORY_MB` (768) and `GAMMA_FLEET_CPUS` (1),
and the optional off-site bucket `GAMMA_FLEET_S3_BUCKET`, `_ENDPOINT`,
`_REGION`, `_ACCESS_KEY`, `_SECRET_KEY`, `_PREFIX` (`hosted/`). The
docstring at the top of `gammafleet/agent.py` describes each.

## Tests

```bash
cd cloud/fleet
python -m pytest -q
```

`.github/workflows/fleet.yml` publishes `ghcr.io/tim4431/gamma-fleet:latest`
and `:sha-<short>` on every push to `main` that touches this folder, and on
a manual dispatch from any branch (`gh workflow run fleet.yml --ref dev`).
To build by hand instead: `docker build -t ghcr.io/tim4431/gamma-fleet:latest cloud/fleet`.
