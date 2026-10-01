#!/usr/bin/env bash
# Update Gamma to the latest published image on ghcr and restart it.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f docker-compose.yml ]; then
  echo "No docker-compose.yml here. Copy docker-compose.yml.example to docker-compose.yml first." >&2
  exit 1
fi

docker compose pull
docker compose up -d --no-build
docker image prune -f
docker compose ps
