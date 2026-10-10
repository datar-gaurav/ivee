#!/usr/bin/env bash
# Publish this server as ivee.local on the home network (mDNS), so devices with
# the release firmware find it with no setup: their default OTA URL is
# http://ivee.local:8003/xiaozhi/ota/. Run by ivee-mdns.service (avahi).
set -euo pipefail

ENV_FILE="${1:-/opt/ivee/config/ivee.env}"
ip="$(sed -n 's/^SERVER_LAN_IP=//p' "$ENV_FILE" | tail -1 | tr -d "\"'")"
[[ -n "$ip" ]] || { echo "SERVER_LAN_IP is empty in $ENV_FILE" >&2; exit 1; }
if [[ "$(hostname -s)" == ivee ]]; then
  echo "This machine is already called ivee; avahi publishes ivee.local by itself."
  exit 0
fi
# -R: don't also publish the reverse (address -> name) record.
exec avahi-publish -a -R ivee.local "$ip"
