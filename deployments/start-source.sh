#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f docker-compose.deploy.yml)
"${compose[@]}" up -d pg-egress plane-redis plane-mq
relay=$("${compose[@]}" ps -q pg-egress)
if ! docker inspect "$relay" --format '{{json .NetworkSettings.Networks}}' | grep -q '"plane-source_default"'; then
  docker network connect --alias pg-egress --gw-priority -1 plane-source_default "$relay"
fi
"${compose[@]}" up -d
# This host only permits Internet egress through Docker's built-in bridge.
# Keep the Plane network for service discovery and use bridge for SMTP egress.
for service in api worker beat-worker; do
  container=$("${compose[@]}" ps -q "$service")
  if ! docker inspect "$container" --format '{{json .NetworkSettings.Networks}}' | grep -q '"bridge"'; then
    docker network connect --gw-priority 1 bridge "$container"
  fi
done
