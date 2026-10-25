#!/usr/bin/env bash
# Apply the Droidspaces kABI patch and gki_defconfig changes, and make the
# kernel trust the stock module-signing certificate so the stock system_dlkm
# modules (signed by the official build) keep verifying.
# Usage: SKIP_OPTIONS="..." apply_droidspaces.sh <common dir> <stock cert pem>
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
COMMON=$(realpath "$1")
CERT=$(realpath "$2")
OPTIONS="$HERE/config/droidspaces_options.txt"
DEFCONFIG=arch/arm64/configs/gki_defconfig
SKIP=" ${SKIP_OPTIONS:-} "
cd "$COMMON"

# 1. kABI patches: stop unless each one applies cleanly.
for p in "$HERE"/patches/*.patch; do
  echo "== $(basename "$p")"
  patch -p1 --forward --dry-run < "$p"
  patch -p1 --forward --no-backup-if-mismatch < "$p"
done

# 2. Edit gki_defconfig in place (no fragment).
while read -r opt category; do
  case "$opt" in ''|'#'*) continue ;; esac
  if [[ "$SKIP" == *" $opt "* ]]; then
    if [ "$category" != recommended ]; then
      echo "only recommended options can be skipped: $opt" >&2
      exit 1
    fi
    echo "skip    CONFIG_$opt"
  elif grep -qx "CONFIG_${opt}=y" "$DEFCONFIG"; then
    echo "keep    CONFIG_${opt}=y"
  elif grep -qx "# CONFIG_${opt} is not set" "$DEFCONFIG"; then
    sed -i "s/^# CONFIG_${opt} is not set\$/CONFIG_${opt}=y/" "$DEFCONFIG"
    echo "enable  CONFIG_${opt}=y"
  elif grep -q "^CONFIG_${opt}=" "$DEFCONFIG"; then
    echo "unexpected existing value: $(grep "^CONFIG_${opt}=" "$DEFCONFIG")" >&2
    exit 1
  else
    echo "CONFIG_${opt}=y" >> "$DEFCONFIG"
    echo "append  CONFIG_${opt}=y"
  fi
done < "$OPTIONS"

# 3. Trust the stock module-signing certificate.
cp "$CERT" certs/stock_gki_module_signing.pem
echo 'CONFIG_SYSTEM_TRUSTED_KEYS="certs/stock_gki_module_signing.pem"' >> "$DEFCONFIG"

# Guard: nothing but the listed options and SYSTEM_TRUSTED_KEYS may change,
# and never CFS_BANDWIDTH or CGROUP_PIDS.
changed=$(git diff -U0 -- "$DEFCONFIG" | grep -E '^[-+](# )?CONFIG_' \
  | sed -E 's/^[-+](# )?CONFIG_([A-Za-z0-9_]+).*/\2/' | sort -u)
allowed=$( { grep -vE '^[[:space:]]*(#|$)' "$OPTIONS" | awk '{print $1}'; echo SYSTEM_TRUSTED_KEYS; } | sort -u)
unexpected=$(comm -23 <(echo "$changed") <(echo "$allowed"))
if [ -n "$unexpected" ] || grep -qxE 'CFS_BANDWIDTH|CGROUP_PIDS' <<< "$changed"; then
  echo "unexpected gki_defconfig changes: $unexpected" >&2
  exit 1
fi

git diff --stat
git diff -- "$DEFCONFIG"
