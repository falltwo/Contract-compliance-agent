#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRANCH="${BRANCH:-main}"
API_SERVICE="${API_SERVICE:-contract-agent-api.service}"
API_PORT="${API_PORT:-8000}"
WEB_FRONT_SERVICE="${WEB_FRONT_SERVICE:-contract-agent-web-frontend.service}"
WEB_ADMIN_SERVICE="${WEB_ADMIN_SERVICE:-contract-agent-web-admin.service}"
WEB_FRONT_PORT="${WEB_FRONT_PORT:-4173}"
WEB_ADMIN_PORT="${WEB_ADMIN_PORT:-4174}"
UV_ENV_FILE="${UV_ENV_FILE:-$HOME/.local/bin/env}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-60}"

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
git fetch origin "${BRANCH}"
git reset --hard "origin/${BRANCH}"
uv sync

# ── 動態產生前端 env 檔（Tailscale IP 優先，否則 LAN IP）
# 需在 npm build 前執行，讓 Vite 能 inline 正確的 API URL
_lan_ip="$(hostname -I | awk '{print $1}')"
_tailscale_ip=""
if command -v tailscale >/dev/null 2>&1; then
  _tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
fi
_api_ip="${_tailscale_ip:-${_lan_ip}}"

printf 'VITE_APP_TARGET=frontend\nVITE_API_BASE_URL=http://%s:%s\n' \
  "${_api_ip}" "${API_PORT}" > "${PROJECT_DIR}/web/.env.frontend"
printf 'VITE_APP_TARGET=admin\nVITE_API_BASE_URL=http://%s:%s\n' \
  "${_api_ip}" "${API_PORT}" > "${PROJECT_DIR}/web/.env.admin"

echo "Generated web/.env.frontend  → http://${_api_ip}:${API_PORT}"
echo "Generated web/.env.admin     → http://${_api_ip}:${API_PORT}"

cd "${PROJECT_DIR}/web"
npm ci
npm run build

cd "${PROJECT_DIR}"
# Re-render and install systemd unit files so template changes take effect on every deploy.
START_NOW=0 bash "${SCRIPT_DIR}/install_dgx_services.sh"
sudo -n systemctl restart "${API_SERVICE}" "${WEB_FRONT_SERVICE}" "${WEB_ADMIN_SERVICE}"

# ── 健康檢查：等待 API 就緒（單次請求有 --max-time 保護；接受 ok 或 degraded）
health_url="http://127.0.0.1:${API_PORT}/health"
deadline=$((SECONDS + HEALTH_TIMEOUT_SEC))
until curl -fsS --max-time 5 "${health_url}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status') in ('ok','degraded') else 1)" \
  2>/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "API health check timeout after ${HEALTH_TIMEOUT_SEC}s: ${health_url}" >&2
    exit 1
  fi
  sleep 2
done

echo "Deployment complete."
echo "Frontend (LAN):       http://${_lan_ip}:${WEB_FRONT_PORT}"
echo "Admin (LAN):          http://${_lan_ip}:${WEB_ADMIN_PORT}"
echo "API health (LAN):     http://${_lan_ip}:${API_PORT}/health"
if [[ -n "${_tailscale_ip}" ]]; then
  echo "Frontend (Tailscale): http://${_tailscale_ip}:${WEB_FRONT_PORT}"
  echo "Admin (Tailscale):    http://${_tailscale_ip}:${WEB_ADMIN_PORT}"
  echo "API health (Tail):    http://${_tailscale_ip}:${API_PORT}/health"
fi
