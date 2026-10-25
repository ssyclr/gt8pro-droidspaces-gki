#!/usr/bin/env bash
# Gather what the report job and the person flashing need from one build.
# Usage: collect_outputs.sh <work dir> <dist dir> <log dir> <out dir>
set -euo pipefail

WORK=$1
DIST=$2
LOGS=$3
OUT=$4
HERE=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$OUT"

for f in Image Image.lz4 Image.gz vmlinux.symvers System.map; do
  if [ -f "$DIST/$f" ]; then
    cp "$DIST/$f" "$OUT/"
  fi
done
"$WORK/common/scripts/extract-ikconfig" "$DIST/Image" > "$OUT/config"
python3 "$HERE/tools/extract.py" btf "$DIST/vmlinux" "$OUT/vmlinux.btf"
grep -a -o -m1 'Linux version [[:print:]]*' "$DIST/Image" > "$OUT/linux_version.txt"
git -C "$WORK/common" rev-parse HEAD > "$OUT/common_head.txt"
git -C "$WORK/common" status --porcelain > "$OUT/source_status.txt"
git -C "$WORK/common" diff > "$OUT/source.diff"
if [ -f "$LOGS/check_defconfig_disabled" ]; then
  cp "$LOGS/check_defconfig_disabled" "$OUT/"
fi

# Listed options that are not Kconfig symbols in this tree at all.
: > "$OUT/kconfig_missing.txt"
while read -r opt _; do
  case "$opt" in ''|'#'*) continue ;; esac
  if ! grep -rqsE --include='Kconfig*' "^[[:space:]]*(menu)?config[[:space:]]+${opt}[[:space:]]*$" "$WORK/common"; then
    echo "$opt" >> "$OUT/kconfig_missing.txt"
  fi
done < "$HERE/config/droidspaces_options.txt"

ls -la "$DIST" > "$OUT/dist_listing.txt"
ls -la "$OUT"
cat "$OUT/linux_version.txt"
