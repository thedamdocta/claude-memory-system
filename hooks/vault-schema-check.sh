#!/bin/bash
# PreToolUse hook — validates vault file frontmatter against vault-schema.json.
# Only activates if vault-schema.json exists in the file's directory tree.
# No schema = silent pass. Exit 2 = block write. Exit 0 = allow.
#
# Fires on: Write operations on .md files
# Edit validation deferred to v1 (would need to simulate the edit first).

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only validate Write (not Edit — can't easily get result content at PreToolUse)
[ "$TOOL_NAME" != "Write" ] && exit 0

# Only validate .md files
[ -z "$FILE_PATH" ] && exit 0
[[ ! "$FILE_PATH" == *.md ]] && exit 0

# Get proposed content
PROPOSED=$(echo "$INPUT" | jq -r '.tool_input.content // empty')
[ -z "$PROPOSED" ] && exit 0

# Check if proposed content has frontmatter
[[ ! "$PROPOSED" == ---* ]] && exit 0

# Run the Python validator
VALIDATOR="$HOME/.claude/scripts/vault-schema-check.py"
if [ ! -f "$VALIDATOR" ]; then
    exit 0  # Validator missing = pass
fi

RESULT=$(echo "$PROPOSED" | python3 "$VALIDATOR" "$FILE_PATH" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 2 ]; then
    echo "$RESULT" >&2
    exit 2
fi

exit 0
