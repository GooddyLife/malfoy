#!/usr/bin/env bash
# Installs or updates the agentforce-* skills that Malfoy conducts, straight from
# https://github.com/forcedotcom/sf-skills (Apache-2.0). Nothing is redistributed here.
#
#   install-upstream.sh [TARGET_DIR]      default: ~/.claude/skills
#
# It replaces each agentforce-* folder in TARGET_DIR with the upstream copy, so local
# edits to those skills are lost. Needs git. Works in Git Bash on Windows.
set -euo pipefail

TARGET="${1:-$HOME/.claude/skills}"
CACHE="${MALFOY_CACHE:-$HOME/.cache/malfoy/sf-skills}"

if [ -d "$CACHE/.git" ]; then
  git -C "$CACHE" fetch -q --depth 1 origin HEAD
  git -C "$CACHE" reset -q --hard FETCH_HEAD
else
  # sparse: the full repo has paths too long for Windows and 250+ skills we do not need
  git clone -q --depth 1 --filter=blob:none --sparse -c core.autocrlf=false \
    https://github.com/forcedotcom/sf-skills.git "$CACHE"
  git -C "$CACHE" sparse-checkout set --no-cone '/skills/agentforce-*/'
fi

mkdir -p "$TARGET"
for src in "$CACHE"/skills/agentforce-*/; do
  name=$(basename "$src")
  rm -rf "${TARGET:?}/$name"
  cp -r "$src" "$TARGET/$name"
  echo "installed $name"
done
echo "sf-skills @ $(git -C "$CACHE" rev-parse --short HEAD) -> $TARGET"
