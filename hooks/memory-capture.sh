#!/bin/bash
# memory-capture.sh — PostToolUse hook (Write matcher)
# Extracts facts from new compaction files and indexes them automatically.
#
# Fires after every Write tool use. Checks if the written file is a compaction
# file (matches compactions/session-*compaction* pattern). If so, calls
# memory-consolidate.py to extract and index facts from it.
#
# Runs the Python script in the background so the hook returns immediately
# and does not block the agent.

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ -z "$CWD" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

# Only act on compaction file writes
echo "$FILE_PATH" | grep -q "compactions/session-.*compaction" || exit 0

# Detect project from cwd
case "$CWD" in
  __VAULT_PATH__*) PROJECT="my-project"; PROJECT_ROOT="__VAULT_PATH__" ;;
  *) exit 0 ;; # Unknown project, no capture
esac

# Use the file that was just written (we know the exact path)
COMPACTION_FILE="$FILE_PATH"
[ -f "$COMPACTION_FILE" ] || exit 0

# Ensure logs directory exists
LOG_DIR="$HOME/.claude/memory-index/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/capture.log"
SCRIPT="$HOME/.claude/scripts/memory-consolidate.py"

# Run the consolidation script in the background so the hook returns immediately
(
  echo "--- $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
  echo "Project: $PROJECT"
  echo "File: $COMPACTION_FILE"
  python3 "$SCRIPT" --file "$COMPACTION_FILE" --project "$PROJECT" 2>&1
  echo ""
) >> "$LOG_FILE" 2>&1 &

# Reset nudge counter (facts were just captured)
NUDGE_STATE="$HOME/.claude/memory-index/nudge-state/$PROJECT.json"
if [ -f "$NUDGE_STATE" ]; then
  python3 -c "
import json, time
with open('$NUDGE_STATE') as f:
    state = json.load(f)
state['turns_since_memory_write'] = 0
state['last_memory_write_ts'] = time.time()
with open('$NUDGE_STATE', 'w') as f:
    json.dump(state, f)
" &
fi

exit 0
