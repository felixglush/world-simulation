#!/bin/sh
# Cloud sessions skip the marketplace and plugin declared in settings.json,
# so install pstack here on startup. Local sessions already load it.
set -u

[ "${CLAUDE_CODE_REMOTE:-}" = true ] || exit 0
command -v claude >/dev/null 2>&1 || exit 0

plugins_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins"
if ! grep -q '"pstack@pstack-claude"' "$plugins_dir/installed_plugins.json" 2>/dev/null; then
  # User scope keeps the repo's .claude/settings.json untouched.
  claude plugin marketplace add michael-denyer/pstack-claude >/dev/null 2>&1 || exit 0
  claude plugin install pstack@pstack-claude --scope user >/dev/null 2>&1 || exit 0
  fresh=1
else
  fresh=0
fi

# A plugin installed mid-startup may not have its own SessionStart hook run,
# so print its routing context here. Skip when the plugin's hook already ran.
[ "$fresh" = 1 ] || exit 0
root=$(ls -d "$plugins_dir"/cache/pstack-claude/pstack/*/ 2>/dev/null | tail -n 1)
[ -n "$root" ] && [ -f "$root/hooks/session-start.sh" ] || exit 0
CLAUDE_PLUGIN_ROOT="${root%/}" sh "$root/hooks/session-start.sh" claude 2>/dev/null || true
printf '\nIf the pstack skills are not listed yet, read them from %sskills/<name>/SKILL.md.\n' "$root"
