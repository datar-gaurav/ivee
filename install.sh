#!/usr/bin/env bash
# One-line install of Ivee's server on Debian, Ubuntu or Raspberry Pi OS (64-bit):
#
#   curl -fsSL https://raw.githubusercontent.com/datar-gaurav/ivee/main/install.sh | sudo bash
#
# Clones (or updates) this repo in /opt/ivee/repo and runs server/install.sh.
# Already cloned? Run `sudo ./server/install.sh` instead. On a Mac: ./server/run-mac.sh.
set -euo pipefail

REPO_URL="${IVEE_REPO_URL:-https://github.com/datar-gaurav/ivee.git}"
BRANCH="${IVEE_BRANCH:-main}"
DEST=/opt/ivee/repo

if [[ "$(uname -s)" == Darwin ]]; then
  echo "On a Mac, clone the repo and run ./server/run-mac.sh (see README)." >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: curl -fsSL <this url> | sudo bash" >&2
  exit 1
fi

command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null; }
if [[ -d "$DEST/.git" ]]; then
  git -C "$DEST" pull --quiet --ff-only
else
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST"
fi
exec "$DEST/server/install.sh" "$@"
