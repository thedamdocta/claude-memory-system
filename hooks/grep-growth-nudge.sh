#!/bin/bash
# PreToolUse hook — Growth Tools Nudge
# Fires on Grep tool calls for MyProject project.
# When Grep targets the vault root (broad search), nudges agent to try
# memory-query.py or vault-query.py first.
# Targeted Grep on a specific file passes silently.
# Never blocks (exit 0 only) — behavioral nudge, not enforcement.

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Project detection — only fire for MyProject
case "$CWD" in
  __VAULT_PATH__*) ;;
  *) exit 0 ;; # Non-MyProject — silent pass
esac

GREP_PATH=$(echo "$INPUT" | jq -r '.tool_input.path // empty')

# If path is a specific file (has an extension), this is a targeted search — allow silently
case "$GREP_PATH" in
  *.md|*.py|*.sh|*.json|*.yaml|*.yml|*.txt|*.ts|*.js)
    exit 0 ;;
esac

# If path is a specific subdirectory (not vault root), allow silently
# Only nudge when searching vault root or no path specified
case "$GREP_PATH" in
  "") ;; # No path = defaults to cwd (vault root) — nudge
  __VAULT_PATH__) ;; # Vault root — nudge
  __VAULT_PATH__/) ;; # Vault root with trailing slash — nudge
  *)
    # Check if it's a deep subdirectory (2+ levels under vault root)
    # e.g., __VAULT_PATH__/characters/Cosmic/ = targeted, allow
    # e.g., __VAULT_PATH__/characters/ = still fairly broad
    DEPTH=$(echo "$GREP_PATH" | sed "s|__VAULT_PATH__/||" | tr '/' '\n' | wc -l)
    if [ "$DEPTH" -ge 2 ]; then
      exit 0 # Deep subdirectory — targeted enough
    fi
    ;; # Top-level subdirectory — still broad, nudge
esac

# Check if memory-query.py or vault-query.py was already used recently
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  if tail -n 30 "$TRANSCRIPT_PATH" 2>/dev/null | grep -qE 'memory-query|vault-query|--read-section' 2>/dev/null; then
    exit 0 # Already tried growth tools recently — allow Grep fallback
  fi
fi

# Broad vault Grep without trying growth tools first — nudge
jq -n '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "Growth tools nudge: You are about to Grep the vault broadly. Try these first:\n  - memory-query.py --query \"your search\" (indexed facts, ranked results)\n  - vault-query.py --read-section \"file.md\" \"Section\" (targeted section read)\n  - vault-query.py --type/--tag/--status (frontmatter search)\nIf these fail, Grep is fine as fallback — then note what the tool could not do so you can improve it."
  }
}'

exit 0
