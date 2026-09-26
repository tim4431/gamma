# Deploying the public demo

How `demo.gammapdf.com` runs: a Gamma (`ghcr.io/tim4431/gamma`) in demo
mode, where anyone can try Gamma without an account. **Try the demo** on
its login page makes a throwaway guest account that is deleted with its
workspace after `GAMMA_GUEST_TTL_HOURS`; the admin's shared AI connection
is metered per guest. What demo mode changes and how guests work:
[docs/dev/guests.md](../../../docs/dev/guests.md).

```
deploy/demo/
  compose.yml        the demo alone: the image by GAMMA_TAG, ./data, the gamma-edge network
  .env.example       → .env: GAMMA_TAG, the sha-<short> tag to run (the skill writes it)
  demo.env.example   → demo.env: demo mode, guest lifetime and cap, first admin, seed library
```

## Where it runs

`root@69.63.206.178`, folder `/root/Container/gamma-demo/`: its own compose
project, next to the account server's `/root/Container/gamma-account/`
([../README.md](../README.md)) and independent of it. Starting, stopping,
updating or wiping the demo never touches the account server or the share
host, and their updates never touch the demo.

The one thing the two share is the way in. The account project's Caddy
owns the host's ports 80/443 and TLS, and its Caddyfile's `*.gammapdf.com`
site sends `demo.gammapdf.com` to `gamma-demo:9001`. That name is the demo
container's alias on `gamma-edge`, an external Docker network both projects
join. Caddy resolves the name per request, so the demo can restart or be
absent (a 502 for its name only) without Caddy noticing anything else.

The image is pinned to the `sha-<short>` tag of a branch build (`docker.yml`
dispatched on the branch, which never moves `:latest`), named by
`GAMMA_TAG` in the folder's `.env`. The `update-demo-server` skill
(`.claude/skills/`) builds, writes that line and restarts the demo.

## First deployment

1. **DNS.** At Cloudflare, `A demo → <the VPS address>`, proxied, SSL mode
   "Full". The share host's `*` record already routes the name to the host;
   the named record keeps the demo up if the wildcard ever changes.
2. **The network, once per host.** The account project's `compose.yml`
   refuses to start without it too:

   ```bash
   docker network inspect gamma-edge >/dev/null 2>&1 || docker network create gamma-edge
   ```

3. **The folder.** Copy this folder's files and fill in the settings:

   ```bash
   mkdir -p ~/Container/gamma-demo && cd ~/Container/gamma-demo
   B=https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/deploy/demo
   curl -o compose.yml $B/compose.yml
   curl -o .env $B/.env.example        # then set GAMMA_TAG to the build to run
   curl -o demo.env $B/demo.env.example
   chmod 600 demo.env
   # optional in demo.env: GAMMA_ADMIN_PASSWORD, GAMMA_GUEST_MAX, GAMMA_GUEST_SEED
   docker compose up -d
   ```

4. **The route.** The account project's `compose.yml` puts Caddy on
   `gamma-edge` and its `Caddyfile` has the `@demo` handle; both come from
   `cloud/deploy/` through the `update-account-server` skill.
5. **Check.** `curl -s https://demo.gammapdf.com/api/server-config` contains
   `"demo":true`, and the page opens on **Try the demo**.
6. **The admin, once.** With `GAMMA_ADMIN_PASSWORD` empty the container
   prints a random password for `admin` once:
   `docker compose logs demo | grep -A2 "created the admin account"`. Sign
   in through **Admin sign-in** on the login page and change the password
   (Settings → Users). Then, under Settings → Server → Shared AI provider,
   add the shared connection (an API key or a ChatGPT subscription), turn
   **Guests may use it** on and set the per-guest allowance. AI keys are
   never environment variables.
7. **Optional sample library.** Prepare a workspace (the admin's own here
   will do) and export it (Settings → Workspaces → Export, or download a
   snapshot from Settings → Backups). Copy the zip to
   `data/guest-seed.zip`, uncomment `GAMMA_GUEST_SEED` in `demo.env` and
   run `docker compose up -d`. Every new guest starts from a copy.

## Resetting

`data/` is the demo's whole state, and it is disposable: no backup. Wiping
it resets the demo to a fresh instance:

```bash
cd ~/Container/gamma-demo && docker compose down && rm -rf data && docker compose up -d
```

That means a new admin password in the log, the shared connection and the
allowance to enter again, and the seed zip to copy back if one was used.
