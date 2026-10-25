#!/usr/bin/env bash
# Check out exactly the source tree of an official GKI build, using the
# manifest that build was made from (every project pinned to a commit,
# including the clang and rust prebuilts).
# Usage: sync_source.sh <work dir> <manifest.xml>
set -euo pipefail

WORK=$1
MANIFEST=$(realpath "$2")

git config --global user.name "github-actions"
git config --global user.email "github-actions@users.noreply.github.com"
git config --global color.ui false

# <superproject revision="..."> names the manifest branch the build came from.
BRANCH=$(grep -m1 -o '<superproject [^>]*' "$MANIFEST" | sed -n 's/.*revision="\([^"]*\)".*/\1/p')
EXPECTED=$(grep -m1 -o '<project path="common" [^>]*' "$MANIFEST" | sed -n 's/.*revision="\([0-9a-f]\{40\}\)".*/\1/p')
echo "manifest branch: $BRANCH"
echo "common commit:   $EXPECTED"

mkdir -p "$WORK"
cd "$WORK"
# Any manifest branch will do to bootstrap; the build manifest replaces it.
repo init --depth=1 --no-use-superproject -u https://android.googlesource.com/kernel/manifest -b "$BRANCH" ||
  repo init --depth=1 --no-use-superproject -u https://android.googlesource.com/kernel/manifest -b common-android-mainline

# Branches named in upstream= get renamed later (android16-6.12-2025-06 is now
# deprecated/android16-6.12-2025-06), and repo fails fetching a missing one.
# Every project is pinned to a commit anyway, so fetch the commits directly.
python3 - "$MANIFEST" .repo/manifests/build_manifest.xml <<'EOF'
import re
import sys

src, dst = sys.argv[1:]

def unpin_branches(m):
    tag = m.group(0)
    if re.search(r'\srevision="[0-9a-f]{40}"', tag):
        tag = re.sub(r'\s(?:upstream|dest-branch)="[^"]*"', '', tag)
    return tag

with open(src, encoding='utf-8') as f:
    xml = f.read()
with open(dst, 'w', encoding='utf-8') as f:
    f.write(re.sub(r'<project\s[^>]*>', unpin_branches, xml))
EOF
repo init --depth=1 --no-use-superproject -m build_manifest.xml

sync() { repo sync -c --no-tags --no-clone-bundle --fail-fast -j8; }
sync || sync

ACTUAL=$(git -C common rev-parse HEAD)
echo "common HEAD:     $ACTUAL"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "common is not at the commit of the official build" >&2
  exit 1
fi
df -h "$WORK"
