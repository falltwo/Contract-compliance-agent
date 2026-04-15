#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRANCH="${BRANCH:-main}"
API_SERVICE="${API_SERVICE:-contract-agent-api.service}"
WEB_SERVICE="${WEB_SERVICE:-contract-agent-web.service}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-4173}"
UV_ENV_FILE="${UV_ENV_FILE:-$HOME/.local/bin/env}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-30}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd git
require_cmd npm
require_cmd curl
require_cmd systemctl

if [[ ! -f "${UV_ENV_FILE}" ]]; then
  echo "Missing uv env file: ${UV_ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "Missing ${PROJECT_DIR}/.env" >&2
  exit 1
fi

source "${UV_ENV_FILE}"

cd "${PROJECT_DIR}"
git pull --ff-only origin "${BRANCH}"
uv sync

cd "${PROJECT_DIR}/web"
npm ci
npm run build

cd "${PROJECT_DIR}"
sudo -n systemctl daemon-reload
sudo -n systemctl restart "${API_SERVICE}" "${WEB_SERVICE}"

health_url="http://127.0.0.1:${API_PORT}/health"
deadline=$((SECONDS + HEALTH_TIMEOUT_SEC))
until curl -fsS "${health_url}" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "API health check timeout after ${HEALTH_TIMEOUT_SEC}s: ${health_url}" >&2
    exit 1
  fi
  sleep 1
done

lan_ip="$(hostname -I | awk '{print $1}')"
tailscale_ip=""
if command -v tailscale >/dev/null 2>&1; then
  tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
fi

echo "Deployment complete."
echo "Frontend (LAN):       http://${lan_ip}:${WEB_PORT}"
echo "API health (LAN):     http://${lan_ip}:${API_PORT}/health"
if [[ -n "${tailscale_ip}" ]]; then
  echo "Frontend (Tailscale): http://${tailscale_ip}:${WEB_PORT}"
  echo "API health (Tail):    http://${tailscale_ip}:${API_PORT}/health"
fi
