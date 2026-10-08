#!/usr/bin/env bash
# Copy Sparky's rendered config, .env and logs to the NAS.
# Cron (as root): 30 2 * * * /opt/sparky/repo/server/scripts/backup-to-nas.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
set -a
# shellcheck disable=SC1091
source "$REPO_DIR/server/.env"
set +a
: "${NAS_BACKUP_TARGET:?Set NAS_BACKUP_TARGET in server/.env}"

APP_DIR=/opt/sparky/xiaozhi-esp32-server/main/xiaozhi-server
DEST="$NAS_BACKUP_TARGET/$(hostname)/$(date +%F)"

mkdir -p "$DEST"
rsync -a "$REPO_DIR/server/.env" "$DEST/env"
rsync -a "$APP_DIR/data/" "$DEST/data/"
rsync -a --include='*.log*' --exclude='*' "$APP_DIR/tmp/" "$DEST/logs/" 2>/dev/null || true

# Keep 30 days.
find "$NAS_BACKUP_TARGET/$(hostname)" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
echo "Backed up to $DEST"
