#!/bin/bash
# PreToolUse hook — blocks tools when compaction gate is active
# Allows only Write calls targeting compaction files (compactions/session-*compaction*).
# Exit 2 = block. stderr = reason shown to agent.
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

[ -z "$CWD" ] && exit 0

# Auto-detect project root by walking up from CWD looking for .compaction-gate or _SESSION_LOG.md
PROJECT_ROOT=""
dir="$CWD"
while [ "$dir" != "/" ] && [ "$dir" != "$HOME" ]; do
  if [ -f "$dir/.compaction-gate" ] || [ -f "$dir/_SESSION_LOG.md" ]; then
    PROJECT_ROOT="$dir"
    break
  fi
  dir=$(dirname "$dir")
done
[ -z "$PROJECT_ROOT" ] && exit 0

GATE_FILE="$PROJECT_ROOT/.compaction-gate"
[ ! -f "$GATE_FILE" ] && exit 0

# Gate is active — check if this is a compaction save (allowed)
if [ "$TOOL" = "Write" ]; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
  if echo "$FILE_PATH" | grep -q "compactions/session-.*compaction"; then
    # WALL: block Write to existing compaction files (forces one-file-per-compaction rule)
    # See MEMORY.md Session Start Protocol step 1 — the HARD RULE
    if [ -e "$FILE_PATH" ]; then
      echo "COMPACTION GATE: cannot Write to existing compaction file ($FILE_PATH). Create a NEW file with -cont1/-cont2/-cont3 suffix per the one-file-per-compaction rule. Re-writing existing compaction files burns 20k+ tokens per round-trip. See MEMORY.md Session Start Protocol step 1." >&2
      exit 2
    fi
    exit 0 # Allow NEW compaction saves only
  fi
fi

# Block everything else
echo "COMPACTION GATE ACTIVE: A context compaction occurred. You MUST save the compaction summary to compactions/session-XX-compaction.md BEFORE doing any other work. All Edit/Write/Bash/Task tools are blocked until the compaction file is saved." >&2
exit 2
