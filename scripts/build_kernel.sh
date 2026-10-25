#!/usr/bin/env bash
# Build //common:kernel_aarch64_dist.
# The kernel config step runs first. Only if its defconfig check really fails
# (expected once gki_defconfig is edited by hand) is check_defconfig turned off
# for kernel_aarch64 -- the 6.12 equivalent of dropping check_defconfig from
# build.config.gki -- and the config step retried.
# Usage: build_kernel.sh <work dir> <dist dir> <log dir>
set -euo pipefail

WORK=$1
DIST=$(realpath -m "$2")
LOGS=$(realpath -m "$3")
FLAGS=(--config=fast --config=stamp)
mkdir -p "$DIST" "$LOGS"
cd "$WORK"

if ! tools/bazel build "${FLAGS[@]}" //common:kernel_aarch64_config 2>&1 | tee "$LOGS/config.log"; then
  if ! grep -q 'savedefconfig does not match' "$LOGS/config.log"; then
    exit 1
  fi
  echo "::warning::defconfig check failed after editing gki_defconfig; disabling check_defconfig for kernel_aarch64"
  if [ "$(grep -c '^    name = "kernel_aarch64",$' common/BUILD.bazel)" != 1 ]; then
    echo "cannot locate the kernel_aarch64 target in common/BUILD.bazel" >&2
    exit 1
  fi
  sed -i 's/^    name = "kernel_aarch64",$/&\n    check_defconfig = "disabled",/' common/BUILD.bazel
  touch "$LOGS/check_defconfig_disabled"
  tools/bazel build "${FLAGS[@]}" //common:kernel_aarch64_config 2>&1 | tee "$LOGS/config_retry.log"
fi

tools/bazel run "${FLAGS[@]}" //common:kernel_aarch64_dist -- --destdir="$DIST" 2>&1 | tee "$LOGS/build.log"
df -h "$WORK"
