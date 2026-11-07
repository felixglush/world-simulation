#!/usr/bin/env bash
# Vendor pstack's Claude Code skills and agents into .claude/ from michael-denyer/pstack-claude.
# Usage: scripts/sync-pstack.sh [git-ref]   (default: main)
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ref="${1:-main}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone -q https://github.com/michael-denyer/pstack-claude "$work/src"
git -C "$work/src" checkout -q "$ref"
sha="$(git -C "$work/src" rev-parse HEAD)"
plugin="$work/src/plugins/pstack"
manifest="$project_root/.claude/pstack/skills.txt"

# Remove the skills and agents a previous sync added, so upstream deletions carry over.
if [[ -f "$manifest" ]]; then
    while read -r name; do
        [[ -n "$name" ]] && rm -rf "$project_root/.claude/skills/$name"
    done < "$manifest"
fi
rm -rf "$project_root/.claude/pstack"
mkdir -p "$project_root/.claude/skills" "$project_root/.claude/agents" "$project_root/.claude/pstack"

for dir in "$plugin"/skills/*/; do
    name="$(basename "$dir")"
    cp -R "$dir" "$project_root/.claude/skills/$name"
    echo "$name" >> "$manifest"
done
# The statechart test corpus is 3.8 MB of fixtures the skill does not need at runtime.
rm -rf "$project_root/.claude/skills/statechart/scripts/test"

cp "$plugin"/agents/*.md "$project_root/.claude/agents/"
cp "$work/src"/LICENSE* "$work/src"/NOTICE*.md "$project_root/.claude/pstack/"
printf 'Source: https://github.com/michael-denyer/pstack-claude\nCommit: %s\nVersion: %s\n' \
    "$sha" "$(cat "$work/src/VERSION")" > "$project_root/.claude/pstack/SOURCE"

echo "Synced $(wc -l < "$manifest") pstack skills at $sha"
