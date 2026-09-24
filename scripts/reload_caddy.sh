#!/usr/bin/env bash
# Used by acme.sh renewals; reloads Caddy without recreating containers.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if ((EUID != 0)) && command -v sudo >/dev/null; then DOCKER=(sudo docker); fi
fi
if [[ -f "$ROOT/deploy/runtime.env" ]]; then
  (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
   "${DOCKER[@]}" compose --project-directory "$ROOT" --env-file "$ROOT/.env" --env-file "$ROOT/deploy/runtime.env" -f "$ROOT/compose.yaml" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile)
else
  (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
   "${DOCKER[@]}" compose --project-directory "$ROOT" --env-file "$ROOT/.env" -f "$ROOT/compose.yaml" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile)
fi
