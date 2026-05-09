#!/usr/bin/env bash
# BizIntel MCP — local deploy script.
#
# Cowork's sandbox can't reach Railway/GitHub APIs, so this script is meant
# to run on Brett's Mac. It:
#   1) Pulls secrets from the shared workspace .deploy-secrets.env
#   2) Creates/updates the Railway project + env vars
#   3) Pushes code via `railway up`
#   4) (Optional) Pushes to GitHub if BIZINTEL_GH_REPO is set
#
# Usage:
#   cd ~/Projects/agentic-builds/Build\ Prompts\ from\ OpenClaw/mcp-bizintel
#   bash deploy.sh
#
# Requires: railway, gh, python3 already installed locally.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="${HERE}/../.deploy-secrets.env"
SERVICE_NAME="${SERVICE_NAME:-mcp-bizintel}"

if [[ ! -f "$SECRETS" ]]; then
  echo "❌ deploy-secrets.env not found at $SECRETS" >&2
  exit 1
fi

# Inherit shared secrets
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a

# Railway CLI uses RAILWAY_API_TOKEN, not RAILWAY_TOKEN
if [[ -n "${RAILWAY_TOKEN:-}" && -z "${RAILWAY_API_TOKEN:-}" ]]; then
  export RAILWAY_API_TOKEN="$RAILWAY_TOKEN"
fi
unset RAILWAY_TOKEN || true

echo "▶ Sanity-check tests before deploy"
python3 -m pip install --quiet --break-system-packages -r "$HERE/requirements.txt"
( cd "$HERE" && python3 -m pytest -q )

echo "▶ Linking Railway project"
cd "$HERE"
if ! railway status >/dev/null 2>&1; then
  railway init --name "$SERVICE_NAME"
fi

echo "▶ Pushing env vars"
railway variables \
  --set "BIZINTEL_DEV_KEY=${BIZINTEL_DEV_KEY:-bizintel-dev-key-001}" \
  --set "BIZINTEL_PRO_KEYS=${BIZINTEL_PRO_KEYS:-}" \
  --set "YELP_API_KEY=${YELP_API_KEY:-}" \
  --set "PYTHONUNBUFFERED=1" \
  >/dev/null

echo "▶ Deploying"
railway up --detach

echo "▶ Health check"
sleep 5
URL="$(railway domain 2>/dev/null | tail -1 || true)"
if [[ -n "$URL" ]]; then
  echo "  Service URL: https://$URL"
  echo "  Try:  curl https://$URL/health"
fi

if [[ -n "${BIZINTEL_GH_REPO:-}" && -n "${GITHUB_CLASSIC_PAT:-}" ]]; then
  echo "▶ Pushing to GitHub: $BIZINTEL_GH_REPO"
  cd "$HERE"
  if [[ ! -d .git ]]; then
    git init -q -b main
    git add .
    git commit -qm "Initial: BizIntel MCP scaffold"
  fi
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://${GITHUB_CLASSIC_PAT}@github.com/${BIZINTEL_GH_REPO}.git"
  git push -u origin main --force
fi

echo "✅ BizIntel MCP deploy complete"
