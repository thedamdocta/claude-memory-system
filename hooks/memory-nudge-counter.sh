#!/bin/bash
# UserPromptSubmit hook — Memory Nudge Counter
# Fires on every user prompt. After N turns without a memory write,
# gently reminds the agent to reflect and capture lessons.

NUDGE_THRESHOLD=8

# Read stdin JSON for cwd
INPUT=$(cat)
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
[ -z "$CWD" ] && CWD="$(pwd)"

# Project detection
case "$CWD" in
  __VAULT_PATH__*) PROJECT="my-project" ;;
  *) exit 0 ;; # Unknown project — silent pass
esac

STATE_DIR="$HOME/.claude/memory-index/nudge-state"
STATE_FILE="$STATE_DIR/$PROJECT.json"

# Atomic read-modify-write via Python
NUDGE_MSG=$(python3 -c "
import json, time, os, sys

state_file = '$STATE_FILE'
threshold = $NUDGE_THRESHOLD

# Read or initialize state
if os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)
else:
    state = {
        'turns_since_memory_write': 0,
        'last_memory_write_ts': 0.0,
        'last_nudge_ts': 0.0,
        'nudge_count': 0
    }

state['turns_since_memory_write'] += 1

if state['turns_since_memory_write'] >= threshold:
    state['turns_since_memory_write'] = 0
    state['last_nudge_ts'] = time.time()
    state['nudge_count'] += 1
    print(f'Memory nudge: {threshold} turns without recording lessons or decisions. Reflect briefly — any lore decisions, corrections, preferences, or patterns worth capturing? If yes, write a lesson fact. If not, carry on.')

with open(state_file, 'w') as f:
    json.dump(state, f)
" 2>/dev/null)

# Output nudge if threshold was hit (stdout = injected context for UserPromptSubmit)
[ -n "$NUDGE_MSG" ] && echo "$NUDGE_MSG"

exit 0
