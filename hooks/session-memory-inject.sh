#!/bin/bash
# Session Memory Inject Hook
# Fires on SessionStart — pre-loads critical memory files so the agent
# starts oriented without spending context on Read tool calls.
#
# Output goes to stdout → injected as system context.
# Keep output lean — only the essentials for immediate orientation.

VAULT="__CLAUDE_DIR__/memory-vault"
PWD_DIR="$(pwd)"

# Auto-prune session log: keep newest 10 entries, move oldest to archive
prune_session_log() {
  local session_log="$1"
  local archive_file="$(dirname "$session_log")/_SESSION_ARCHIVE.md"

  [ ! -f "$session_log" ] && return 0
  [ ! -f "$archive_file" ] && return 0

  # Count data rows (lines starting with "| <number")
  local count
  count=$(grep -c '^| [0-9]' "$session_log" 2>/dev/null)
  [ "$count" -le 10 ] && return 0

  local excess=$((count - 10))

  # Oldest rows are at the bottom of the table (newest at top). Extract them.
  # Duplicate guard: only append rows whose session number isn't already in archive
  grep '^| [0-9]' "$session_log" | tail -n "$excess" | while IFS= read -r row; do
    local sess_num
    sess_num=$(echo "$row" | sed 's/^| *\([0-9a-z]*\) .*/\1/')
    if ! grep -q "^| *${sess_num} " "$archive_file" 2>/dev/null; then
      echo "$row" >> "$archive_file"
    fi
  done

  # Remove from session log: delete from bottom up so line numbers stay stable
  grep -n '^| [0-9]' "$session_log" | tail -n "$excess" | cut -d: -f1 | sort -rn | while read -r ln; do
    sed -i '' "${ln}d" "$session_log"
  done
}

# Always inject working profile (communication style, rules, corrections)
if [ -f "$VAULT/working-profile.md" ]; then
  echo "=== WORKING PROFILE (auto-injected) ==="
  cat "$VAULT/working-profile.md"
  echo ""
fi

# --- Auto-detect project from working directory ---
# Walk up from PWD looking for _SESSION_LOG.md to find the project root.
# Then look for a matching vault file in memory-vault/.

PROJECT_ROOT=""
SESSION_LOG=""
PROJECT_VAULT=""
COMPACTIONS_DIR=""

detect_project() {
  local dir="$1"

  # Walk up the directory tree looking for _SESSION_LOG.md
  while [ "$dir" != "/" ] && [ "$dir" != "$HOME" ]; do
    if [ -f "$dir/_SESSION_LOG.md" ]; then
      PROJECT_ROOT="$dir"
      SESSION_LOG="$dir/_SESSION_LOG.md"
      COMPACTIONS_DIR="$dir/compactions"

      # Try to find a matching vault file by directory basename
      local basename
      basename=$(basename "$dir")
      if [ -f "$VAULT/$basename.md" ]; then
        PROJECT_VAULT="$VAULT/$basename.md"
      fi
      return 0
    fi
    dir=$(dirname "$dir")
  done
  return 1
}

detect_project "$PWD_DIR"

# If no project detected, just exit (working profile was already injected)
if [ -z "$PROJECT_ROOT" ]; then
  exit 0
fi

# Auto-prune before injection (so injected log is already clean)
if [ -n "$SESSION_LOG" ]; then
  prune_session_log "$SESSION_LOG"
fi

# Compaction gate warning — if gate is active, warn the agent prominently
if [ -f "$PROJECT_ROOT/.compaction-gate" ]; then
  echo ""
  echo "=============================================="
  echo "COMPACTION GATE ACTIVE"
  echo "=============================================="
  echo "Previous session compacted. A continuation summary exists in your context."
  echo "You MUST save it to: ${COMPACTIONS_DIR}/session-XX-compaction.md"
  echo "BEFORE doing ANY other work. All Edit/Write/Bash/Task tools are BLOCKED"
  echo "until the compaction is saved."
  echo "=============================================="
  echo ""
fi

# Inject project vault file
if [ -n "$PROJECT_VAULT" ] && [ -f "$PROJECT_VAULT" ]; then
  echo "=== PROJECT VAULT (auto-injected) ==="
  cat "$PROJECT_VAULT"
  echo ""
fi

# Inject session log (Current State section is the most critical part)
if [ -f "$SESSION_LOG" ]; then
  echo "=== SESSION LOG (auto-injected) ==="
  cat "$SESSION_LOG"
  echo ""
fi

# Inject Conversations & Nuance from the most recent compaction
# Size-gated: skip injection if section is bloated (>6KB)
if [ -d "$COMPACTIONS_DIR" ]; then
  LATEST_COMPACTION=$(ls -t "$COMPACTIONS_DIR"/session-*-compaction.md 2>/dev/null | head -1)
  if [ -n "$LATEST_COMPACTION" ]; then
    # Extract only the Conversations & Nuance section
    NUANCE=$(awk '/^## Conversations & Nuance/{found=1; next} /^## /{if(found) exit} found{print}' "$LATEST_COMPACTION")
    NUANCE_BYTES=$(printf '%s' "$NUANCE" | wc -c | tr -d ' ')
    if [ "$NUANCE_BYTES" -gt 6000 ]; then
      echo "=== CONVERSATIONS & NUANCE — SKIPPED (bloated: ${NUANCE_BYTES} bytes from $(basename "$LATEST_COMPACTION")) ==="
      echo "C&N section in latest compaction exceeds 6KB threshold. Read the file directly only if needed."
      echo ""
    elif [ -n "$NUANCE" ]; then
      echo "=== CONVERSATIONS & NUANCE (from $(basename "$LATEST_COMPACTION"), auto-injected) ==="
      echo "$NUANCE"
      echo ""
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Inject procedure index — scan ~/.claude/procedures/ for active procedures
# ---------------------------------------------------------------------------

PROCEDURES_BASE="$HOME/.claude/procedures"

# Scan shared procedures + any project-specific procedures matching the project name
PROJECT_BASENAME=$(basename "$PROJECT_ROOT")
PROC_DIRS="shared"
if [ -d "$PROCEDURES_BASE/$PROJECT_BASENAME" ]; then
  PROC_DIRS="shared $PROJECT_BASENAME"
fi

# Build the procedure index by scanning frontmatter
PROC_ENTRIES=""
for proc_scope in $PROC_DIRS; do
  proc_dir="$PROCEDURES_BASE/$proc_scope"
  [ ! -d "$proc_dir" ] && continue

  for proc_file in "$proc_dir"/*.md; do
    [ ! -f "$proc_file" ] && continue

    # Extract status and trigger from frontmatter (fast awk scan)
    status=$(awk '/^---$/{if(n++)exit} /^status:/{print $2}' "$proc_file")
    [ "$status" != "active" ] && continue

    trigger=$(awk '/^trigger:/{found=1; next} found && /^[a-z]/{exit} found{gsub(/^[[:space:]]+/,""); printf "%s ", $0}' "$proc_file")
    trigger=$(echo "$trigger" | sed 's/[[:space:]]*$//')

    rel_path="$proc_scope/$(basename "$proc_file")"
    PROC_ENTRIES="${PROC_ENTRIES}  - ${rel_path}: ${trigger}
"
  done
done

if [ -n "$PROC_ENTRIES" ]; then
  echo "=== PROCEDURES (auto-injected) ==="
  echo "Available procedures (Read full file to load steps):"
  printf '%s' "$PROC_ENTRIES"
  echo ""
  echo "Self-patch rule: If a procedure fails or needs deviation, edit it immediately (increment version, update steps, add to Known Issues, write a lesson fact)."
  echo ""
fi
