#!/usr/bin/env bash
# Make room on a GitHub-hosted Ubuntu runner, then export WORK: a build
# directory on whichever filesystem has the most free space.
set -euo pipefail

df -h / /mnt 2>/dev/null || df -h /

sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /usr/local/.ghcup \
  /opt/hostedtoolcache /usr/local/share/boost /usr/share/swift /usr/local/share/powershell \
  /usr/local/share/chromium /usr/local/lib/node_modules /opt/microsoft /opt/google /opt/az \
  /usr/lib/jvm /usr/share/miniconda /usr/lib/google-cloud-sdk /opt/pipx || true
sudo docker system prune --all --force >/dev/null 2>&1 || true
sudo apt-get clean

best=/
if mountpoint -q /mnt && [ "$(df --output=avail /mnt | tail -1)" -gt "$(df --output=avail / | tail -1)" ]; then
  best=/mnt
fi
work="${best%/}/work"
sudo mkdir -p "$work"
sudo chown "$(id -u):$(id -g)" "$work"
echo "WORK=$work" >> "$GITHUB_ENV"

df -h / /mnt 2>/dev/null || df -h /
echo "build directory: $work"
