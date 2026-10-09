#!/usr/bin/env bash
# Copy Ivee's settings and logs to BACKUP_TARGET (set it on the config page):
# a mounted NAS share or any local path. Keeps 30 days.
# Cron (as root): 30 2 * * * /opt/ivee/repo/server/scripts/backup.sh
set -euo pipefail

PREFIX="${IVEE_PREFIX:-/opt/ivee}"
CONFIG="$PREFIX/config"
APP_DIR="$PREFIX/ivee-esp32-server/main/xiaozhi-server"
target="$(sed -n 's/^BACKUP_TARGET=//p' "$CONFIG/ivee.env" | tail -1 | tr -d "\"'")"
[[ -n "$target" ]] || { echo "BACKUP_TARGET is empty in $CONFIG/ivee.env; nothing to do."; exit 0; }

dest="$target/$(hostname)/$(date +%F)"
mkdir -p "$dest"
# ivee.env has the Groq API key: keep the backup readable by root only.
umask 077
rsync -a --exclude 'apply.*' "$CONFIG/" "$dest/config/"
rsync -a --include='*.log*' --exclude='*' "$APP_DIR/tmp/" "$dest/logs/" 2>/dev/null || true

find "$target/$(hostname)" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
echo "Backed up to $dest"
