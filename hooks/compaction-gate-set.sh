#!/bin/bash
# PreCompact hook — sets the compaction gate marker
# Fires right before context compacts. Writes a marker file to the project root.
# PreToolUse hooks check for this marker and block until compaction is saved.
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
[ -z "$CWD" ] && exit 0

# Auto-detect project root by walking up from CWD looking for _SESSION_LOG.md
PROJECT_ROOT=""
dir="$CWD"
while [ "$dir" != "/" ] && [ "$dir" != "$HOME" ]; do
  if [ -f "$dir/_SESSION_LOG.md" ]; then
    PROJECT_ROOT="$dir"
    break
  fi
  dir=$(dirname "$dir")
done
[ -z "$PROJECT_ROOT" ] && exit 0

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $PROJECT_ROOT" > "$PROJECT_ROOT/.compaction-gate"
exit 0
