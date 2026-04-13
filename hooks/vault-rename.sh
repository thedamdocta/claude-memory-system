#!/bin/bash
# Vault Rename Script
# Usage: vault-rename.sh <old_name> <new_name> [vault_dir]
#
# Replaces all occurrences of old_name with new_name across canonical vault files.
# Skips: compactions/, _SESSION_ARCHIVE.md, audits/, Synthesis/, References/, _workspace/
# Handles: wikilinks [[Old Name]], plain text, frontmatter related fields, aliases
#
# Examples:
#   vault-rename.sh "The Outsider Scientist" "Dr Hamick Maynewton" __VAULT_PATH__
#   vault-rename.sh "Furloon" "Furcrow" __VAULT_PATH__

set -euo pipefail

OLD_NAME="${1:?Usage: vault-rename.sh <old_name> <new_name> [vault_dir]}"
NEW_NAME="${2:?Usage: vault-rename.sh <old_name> <new_name> [vault_dir]}"
VAULT_DIR="${3:-$(pwd)}"

CHANGED=0

# Find canonical .md files, excluding historical/reference dirs
while IFS= read -r file; do
  if grep -q "$OLD_NAME" "$file" 2>/dev/null; then
    sed -i '' "s|$OLD_NAME|$NEW_NAME|g" "$file"
    CHANGED=$((CHANGED + 1))
    echo "  UPDATED: ${file#$VAULT_DIR/}"
  fi
done < <(find "$VAULT_DIR" -name '*.md' \
  -not -path '*/.git/*' \
  -not -path '*/compactions/*' \
  -not -path '*/audits/*' \
  -not -path '*/Synthesis/*' \
  -not -path '*/References/*' \
  -not -path '*/_workspace/*' \
  -not -name '_SESSION_ARCHIVE.md')

# Handle file rename — only .md files
OLD_FILE=$(find "$VAULT_DIR" -name "*.md" -path "*${OLD_NAME}*" -not -path '*/.git/*' 2>/dev/null | head -1)
if [ -n "$OLD_FILE" ]; then
  NEW_FILE=$(echo "$OLD_FILE" | sed "s|$OLD_NAME|$NEW_NAME|g")
  if [ "$OLD_FILE" != "$NEW_FILE" ]; then
    mv "$OLD_FILE" "$NEW_FILE"
    echo "  RENAMED: $(basename "$OLD_FILE") -> $(basename "$NEW_FILE")"
  fi
fi

echo ""
echo "Done. $CHANGED files updated. Skipped: compactions, audits, Synthesis, References, _workspace, _SESSION_ARCHIVE."
