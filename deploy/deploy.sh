#!/usr/bin/env bash
# Pull latest code, install deps, restart the service.
# Run on the VPS (invoked by GitHub Actions over SSH).
set -euo pipefail

APP_DIR="/root/claude_cryptosignalbot"
BRANCH="${1:-claude/trading-signal-aggregator-mvp-DPRwx}"

cd "$APP_DIR"

echo "==> Fetching $BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

echo "==> Installing dependencies"
.venv/bin/pip install -q -r requirements.txt

echo "==> Restarting service"
systemctl restart signalbot

echo "==> Done. Status:"
systemctl is-active signalbot
