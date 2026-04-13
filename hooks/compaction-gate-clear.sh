#!/bin/bash
# PostToolUse hook — clears gate after compaction file is written
# Fires on Write tool. If the written file matches compaction pattern, deletes the gate.
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ -z "$CWD" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

# Only act on compaction file writes
echo "$FILE_PATH" | grep -q "compactions/session-.*compaction" || exit 0

# Auto-detect project root by walking up from CWD looking for .compaction-gate
PROJECT_ROOT=""
dir="$CWD"
while [ "$dir" != "/" ] && [ "$dir" != "$HOME" ]; do
  if [ -f "$dir/.compaction-gate" ]; then
    PROJECT_ROOT="$dir"
    break
  fi
  dir=$(dirname "$dir")
done
[ -z "$PROJECT_ROOT" ] && exit 0

GATE_FILE="$PROJECT_ROOT/.compaction-gate"
[ -f "$GATE_FILE" ] && rm "$GATE_FILE"
exit 0
