#!/usr/bin/env bash
# Installs the Gamma fleet agent on this host, or brings an installed one up to
# date (cloud/fleet/deploy/README.md "On a fresh host"). Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/fleet/deploy/bootstrap.sh \
#     | bash -s -- --token gf_... [--edge] [--domain gammapdf.com] \
#       [--account-url https://account.gammapdf.com] [--dir ~/Container/gamma-fleet] [--ref main] [--auto-update]
#
# It installs Docker (get.docker.com) when it is missing, creates the network
# gamma-fleet with its pinned subnet and the data root /srv/gamma, downloads
# compose.yml, Caddyfile and .env.example of --ref into --dir, writes .env (an
# existing one is kept: only what the options name is set in it), and starts
# the project. --edge also starts the host's own Caddy (the compose profile
# `edge`), for a host with a public IP; never on the account server's VPS.
# --auto-update installs a daily systemd timer that pulls and restarts the
# project. Run as root. Everything is in main(), so bash has read the whole
# script before any of it runs.
set -euo pipefail

REPO="https://raw.githubusercontent.com/tim4431/Gamma"
NETWORK="gamma-fleet"
SUBNET="10.203.0.0/24"
DATA_ROOT="/srv/gamma"
UNIT="gamma-fleet-update"

say() { printf '%s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }

usage() {
	cat <<'EOF'
usage: bootstrap.sh --token gf_... [--edge] [--domain gammapdf.com]
                    [--account-url https://account.gammapdf.com] [--dir ~/Container/gamma-fleet]
                    [--ref main] [--auto-update]

  --token        this host's token, from Admin > Machines > Add machine (needed unless .env has one)
  --edge         also run this host's own Caddy on 80/443: a host with a public IP of its own
  --domain       the hosted servers' zone, for that Caddy (default gammapdf.com)
  --account-url  the account server (default https://account.gammapdf.com)
  --dir          the compose project's folder (default ~/Container/gamma-fleet)
  --ref          the branch or tag the files are downloaded from (default main)
  --auto-update  pull and restart the project once a day (a systemd timer)
EOF
}

value() {  # the value after option $1, or an error when there is none
	[ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
	printf '%s' "$2"
}

set_env() {  # KEY VALUE: set KEY in .env, in place of its line (or of the commented-out one), else at the end
	local tmp
	tmp="$(mktemp .env.XXXXXX)"
	awk -v k="$1" -v v="$2" '
		NR == FNR { if (index($0, k "=") == 1) live = 1; next }
		index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
		!live && !done && index($0, "#" k "=") == 1 { print k "=" v; done = 1; next }
		{ print }
		END { if (!done) print k "=" v }' .env .env > "$tmp"
	mv "$tmp" .env
}

install_timer() {  # a daily pull and restart of the project in $1
	command -v systemctl >/dev/null 2>&1 || die "--auto-update needs systemd"
	cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=Pull and restart the Gamma fleet agent in $1
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$1
ExecStart=/bin/sh -c 'docker compose pull --quiet && docker compose up -d'
EOF
	cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=Update the Gamma fleet agent once a day

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
	systemctl daemon-reload
	systemctl enable --now "$UNIT.timer" >/dev/null
	say "Updated daily: systemctl list-timers $UNIT.timer"
}

main() {
	local token="" edge=0 domain="" account_url="" dir="${HOME:-/root}/Container/gamma-fleet" ref="main"
	local auto_update=0 subnets base f
	while [ "$#" -gt 0 ]; do
		case "$1" in
			--token) token="$(value "$@")"; shift 2 ;;
			--edge) edge=1; shift ;;
			--domain) domain="$(value "$@")"; shift 2 ;;
			--account-url) account_url="$(value "$@")"; shift 2 ;;
			--dir) dir="$(value "$@")"; shift 2 ;;
			--ref) ref="$(value "$@")"; shift 2 ;;
			--auto-update) auto_update=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*) usage >&2; die "unknown option: $1" ;;
		esac
	done

	[ "$(id -u)" -eq 0 ] || die "run it as root (curl ... | sudo bash -s -- ...)"
	case "$token" in ""|gf_*) ;; *) die "--token is the gf_... token Add machine showed" ;; esac
	case "$account_url" in ""|https://*|http://*) ;; *) die "--account-url is an http(s) address" ;; esac
	command -v curl >/dev/null 2>&1 || die "curl is missing"

	# Docker, from get.docker.com, only when there is none.
	if ! command -v docker >/dev/null 2>&1; then
		say "Installing Docker (get.docker.com)..."
		curl -fsSL https://get.docker.com | sh
	fi
	docker compose version >/dev/null 2>&1 || die "docker compose (the Compose plugin) is missing"

	# The network every hosted container joins and trusts for X-Forwarded-For.
	if docker network inspect "$NETWORK" >/dev/null 2>&1; then
		subnets="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$NETWORK")"
		case " $subnets " in
			*" $SUBNET "*) ;;
			*) say "warning: $NETWORK has the subnet $subnets, not $SUBNET, which the hosted servers trust" ;;
		esac
	else
		docker network create --subnet "$SUBNET" "$NETWORK" >/dev/null
		say "Created the network $NETWORK ($SUBNET)."
	fi
	mkdir -p "$DATA_ROOT"

	# The project's files as they are on --ref: compose.yml and Caddyfile are
	# replaced, .env is only ever added to.
	mkdir -p "$dir"
	cd "$dir"
	dir="$(pwd)"
	base="$REPO/$ref/cloud/fleet/deploy"
	for f in compose.yml Caddyfile .env.example; do
		curl -fsSL -o "$f.new" "$base/$f" || die "could not download $base/$f"
		mv "$f.new" "$f"
	done

	# (if, not ||: set -e does not reach into a function called on the left of ||)
	umask 077
	if [ ! -f .env ]; then cp .env.example .env; fi
	if [ -n "$token" ]; then set_env GAMMA_FLEET_HOST_TOKEN "$token"; fi
	if [ -n "$account_url" ]; then set_env GAMMA_FLEET_ACCOUNT_URL "${account_url%/}"; fi
	if [ -n "$domain" ]; then set_env GAMMA_FLEET_DOMAIN "$domain"; fi
	if [ "$edge" -eq 1 ]; then set_env COMPOSE_PROFILES edge; fi
	chmod 600 .env
	umask 022
	grep -q '^GAMMA_FLEET_HOST_TOKEN=gf_' .env || die "no token in $dir/.env: pass --token gf_... (Admin > Machines > Add machine)"

	if [ "$auto_update" -eq 1 ]; then install_timer "$dir"; fi

	docker compose pull --quiet
	docker compose up -d
	sleep 5

	say ""
	say "The agent's log (docker compose logs --tail 20 fleet, in $dir):"
	docker compose logs --tail 20 fleet || true
	say ""
	say "It should start with 'gamma-fleet <version>: <account server>, network $NETWORK, data $DATA_ROOT'"
	say "and show no 'heartbeat failed' or 'job poll failed' after it. Admin > Servers then shows this"
	say "host seen just now, with its memory and disk."
	say ""
	say "Still to do by hand:"
	say "  - the off-site bucket: the GAMMA_FLEET_S3_* lines in $dir/.env (the same bucket as the"
	say "    other hosts), then docker compose up -d there;"
	if [ "$edge" -eq 1 ]; then
		say "  - this host's public IP on Admin > Servers (Public IP...), with GAMMA_CLOUD_CF_API_TOKEN and"
		say "    GAMMA_CLOUD_CF_ZONE_ID set on the account server: until both, the host takes no servers;"
		say "  - the firewall: 80 and 443 from Cloudflare's ranges only (https://www.cloudflare.com/ips/)."
	else
		say "  - for a host with a public IP of its own: run this again with --edge, set the IP on"
		say "    Admin > Servers, and allow 80 and 443 from Cloudflare's ranges only."
	fi
}

main "$@"
