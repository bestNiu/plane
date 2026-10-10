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
