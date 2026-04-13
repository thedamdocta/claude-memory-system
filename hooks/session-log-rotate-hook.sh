#!/usr/bin/env bash
# PostToolUse hook — auto-rotates session log when compaction files are written
# Triggered on Write operations matching compaction file patterns

set -euo pipefail

# Read stdin for hook data
INPUT=$(cat)

# Extract the file path from the Write tool input
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
# PostToolUse provides tool_input with file_path for Write operations
tool_input = data.get('tool_input', {})
print(tool_input.get('file_path', ''))
" 2>/dev/null || echo "")

# Only act on compaction files
if [[ "$FILE_PATH" == *"/compactions/session-"*"-compaction.md" ]]; then
    # Detect project root from compaction file path (parent of compactions/)
    PROJECT_ROOT=$(dirname "$(dirname "$FILE_PATH")")
    
    # Run rotate in background so we don't block the tool
    python3 ~/.claude/scripts/session-log-rotate.py \
        --project "$PROJECT_ROOT" \
        --compaction-file "$FILE_PATH" &
fi

exit 0
