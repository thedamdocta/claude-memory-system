#!/bin/bash
# PreToolUse hook — enforces Layer 3 leaf size cap with two-tier response
# Fires on: Write|Edit operations on leaf files in MyProject vault
# Exit 2 = block write over 100% cap (and growing)
# Exit 0 + JSON additionalContext = soft warning at 80-100% cap
# Exit 0 silent = pass (<80%)

set -euo pipefail

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ -z "$CWD" ] || [ -z "$FILE_PATH" ] && exit 0

# Project detection — only fire for MyProject vault
case "$CWD" in
  __VAULT_PATH__*) PROJECT_ROOT="__VAULT_PATH__" ;;
  *) exit 0 ;; # Not MyProject, silent pass
esac

# Layer detection — only fire on leaf files
# Leaf patterns: Characters/, Lore/, References/, Synthesis/, EP*.md at vault root
RELATIVE_PATH="${FILE_PATH#$PROJECT_ROOT/}"
case "$RELATIVE_PATH" in
  Characters/*|Lore/*|References/*|Synthesis/*) ;; # Leaf directories
  EP*.md) ;; # Episode docs at root
  compactions/*) ;; # Compaction files (leaf by nature)
  *) exit 0 ;; # Not a leaf file
esac

# Get proposed content
PROPOSED=""
if [ "$TOOL_NAME" = "Write" ]; then
  PROPOSED=$(echo "$INPUT" | jq -r '.tool_input.content // empty')
elif [ "$TOOL_NAME" = "Edit" ]; then
  # Simulate the edit: read current file, replace old_string with new_string
  OLD_STRING=$(echo "$INPUT" | jq -r '.tool_input.old_string // empty')
  NEW_STRING=$(echo "$INPUT" | jq -r '.tool_input.new_string // empty')
  REPLACE_ALL=$(echo "$INPUT" | jq -r '.tool_input.replace_all // false')

  if [ ! -f "$FILE_PATH" ]; then
    # New file via Edit (unusual but possible) — treat new_string as full content
    PROPOSED="$NEW_STRING"
  else
    # Simulate edit using Python for safety (handles multiline, special chars)
    PROPOSED=$(python3 -c "
import sys
content = open('$FILE_PATH', 'r', encoding='utf-8').read()
old = sys.argv[1]
new = sys.argv[2]
replace_all = sys.argv[3] == 'true'
if replace_all:
    result = content.replace(old, new)
else:
    result = content.replace(old, new, 1)
print(result, end='')
" "$OLD_STRING" "$NEW_STRING" "$REPLACE_ALL" 2>/dev/null || echo "$NEW_STRING")
  fi
else
  exit 0 # Unknown tool
fi

[ -z "$PROPOSED" ] && exit 0

# Pipe proposed content to validator
VALIDATOR="__CLAUDE_DIR__/scripts/vault-health-check.py"
if [ ! -x "$VALIDATOR" ]; then
  echo "WARNING: vault-health-check.py not found or not executable — degraded mode, allowing write" >&2
  exit 0
fi

RESULT=$(echo "$PROPOSED" | "$VALIDATOR" --path "$FILE_PATH" --proposed-content-stdin 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 2 ]; then
  # Validator blocked — hard block, propagate stderr and exit 2
  echo "$RESULT" >&2
  exit 2
elif [ $EXIT_CODE -eq 1 ]; then
  # Script error — log but don't block (degraded mode)
  echo "WARNING: vault-health-check.py script error (degraded mode, allowing write): $RESULT" >&2
  exit 0
fi

# Validator passed (exit 0) — check if we're in warning zone (80-100% of cap)
LEAF_CAP_BYTES=20480 # 20 KB
WARN_THRESHOLD_BYTES=16384 # 80% of 20KB

PROPOSED_SIZE=$(echo -n "$PROPOSED" | wc -c | tr -d ' ')

if [ "$PROPOSED_SIZE" -ge "$WARN_THRESHOLD_BYTES" ] && [ "$PROPOSED_SIZE" -lt "$LEAF_CAP_BYTES" ]; then
  # Warning zone — emit JSON additionalContext
  PERCENT=$(( PROPOSED_SIZE * 100 / LEAF_CAP_BYTES ))
  jq -n \
    --arg path "$FILE_PATH" \
    --argjson pct "$PERCENT" \
    '{
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": ("Leaf file " + $path + " at " + ($pct | tostring) + "% of 20KB cap. Consider splitting before adding more content.")
      }
    }'
fi

exit 0
