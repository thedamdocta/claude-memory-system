#!/bin/bash
# PreToolUse hook — Search Discipline Drift Check
# Fires on Read tool calls for MyProject project only.
# Nudges agent if last 10 tool calls contain no Grep/Glob.
# Never blocks (exit 0 only) — behavioral nudge, not enforcement.

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Project detection — only fire for MyProject
case "$CWD" in
  __VAULT_PATH__*) ;;
  *) exit 0 ;; # Non-MyProject — silent pass
esac

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Degraded mode if transcript missing — never block
[ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ] && exit 0

# Whitelist (always allowed without warning)
# Basename matches or path contains:
BASENAME=$(basename "$FILE_PATH" 2>/dev/null || echo "")
case "$BASENAME" in
  MEMORY.md|_SESSION_LOG.md|working-profile.md|my-project.md) exit 0 ;;
esac
case "$FILE_PATH" in
  */compactions/*) exit 0 ;;
  */Planning/research/*) exit 0 ;;
esac

# Check last 10 tool calls for Grep, Glob, or Skill (vault-query etc.)
# JSONL format: each line is a JSON record. Tool calls have "tool_use" or "tool_name".
# Performance: tail -n 50 (grab last 50 lines, should contain 10+ tool calls), grep for tool names.
RECENT_TOOLS=$(tail -n 50 "$TRANSCRIPT_PATH" 2>/dev/null | \
  grep -o '"tool_name":"[^"]*"' 2>/dev/null | \
  cut -d'"' -f4 | \
  tail -n 10)

# Check if Grep, Glob, or Skill present
# Skill included because vault-query skill is a frontmatter search tool
if echo "$RECENT_TOOLS" | grep -qE '^(Grep|Glob|Skill)$'; then
  exit 0 # Search present — no warning needed
fi

# Also check if vault-query.py was run via Bash in recent transcript
if tail -n 50 "$TRANSCRIPT_PATH" 2>/dev/null | grep -q 'vault-query' 2>/dev/null; then
  exit 0 # vault-query.py run counts as a search
fi

# No search in last 10 — emit JSON additionalContext
# PreToolUse requires JSON output for context injection (plain stdout discarded on exit 0)
jq -n '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "Drift check: no Grep/Glob in last 10 tool calls. Consider searching frontmatter before reading the full file. See _SEARCH_PLAYBOOK.md for canonical patterns."
  }
}'

exit 0
