#!/bin/bash
# Claude Memory System — Remove Project
# Cleanly tears down all artifacts created by add-project.sh.
#
# Usage:
#   ./remove-project.sh --name "My Project"
#   ./remove-project.sh --name "My Project" --path /path/to/project
#   ./remove-project.sh --name "My Project" --keep-files
#   ./remove-project.sh --name "My Project" --quiet
#
# What this removes:
#   1. Vault file:         ~/.claude/memory-vault/<project-name>.md
#   2. Session log:        <project-path>/_SESSION_LOG.md
#   3. Project MEMORY.md:  ~/.claude/projects/-<encoded-path>/memory/MEMORY.md
#   4. Registry entry:     line in ~/.claude/memory-vault/_MASTER.md
#   5. compactions/ dir:   <project-path>/compactions/

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Defaults ---
HOME_DIR="$HOME"
CLAUDE_DIR="$HOME_DIR/.claude"
VAULT_DIR="$CLAUDE_DIR/memory-vault"
MASTER_FILE="$VAULT_DIR/_MASTER.md"
PROJECT_NAME=""
PROJECT_PATH=""
KEEP_FILES=false
QUIET=false

# --- Usage ---
usage() {
  echo "Usage: remove-project.sh --name <project-name> [OPTIONS]"
  echo ""
  echo "Required:"
  echo "  --name NAME           Project name (used to find vault file and registry entry)"
  echo ""
  echo "Optional:"
  echo "  --path PATH           Project working directory (looked up from registry if omitted)"
  echo "  --keep-files          Remove registry + vault + MEMORY.md but leave session log and compactions"
  echo "  --quiet               Skip confirmation prompts (for agent use)"
  echo "  -h, --help            Show this help"
  exit 0
}

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --name)
      PROJECT_NAME="$2"
      shift 2
      ;;
    --path)
      PROJECT_PATH="$2"
      shift 2
      ;;
    --keep-files)
      KEEP_FILES=true
      shift
      ;;
    --quiet)
      QUIET=true
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      usage
      ;;
  esac
done

# --- Validate required args ---
if [ -z "$PROJECT_NAME" ]; then
  echo -e "${RED}Error: --name is required${NC}"
  echo ""
  usage
fi

# --- Look up project path from registry if not provided ---
if [ -z "$PROJECT_PATH" ]; then
  if [ -f "$MASTER_FILE" ]; then
    # Registry format: | Name | vault-file | `path/` | status | description |
    REGISTRY_LINE=$(grep "^| $PROJECT_NAME |" "$MASTER_FILE" 2>/dev/null || true)
    if [ -n "$REGISTRY_LINE" ]; then
      # Extract path: third column, strip backticks and trailing slash
      PROJECT_PATH=$(echo "$REGISTRY_LINE" | awk -F'|' '{print $4}' | sed 's/^[[:space:]]*`//;s/\/`[[:space:]]*$//;s/`[[:space:]]*$//')
      # Remove trailing slash if present
      PROJECT_PATH="${PROJECT_PATH%/}"
      echo -e "${BLUE}Resolved path from registry:${NC} $PROJECT_PATH"
    else
      echo -e "${RED}Error: Project '$PROJECT_NAME' not found in registry and no --path provided${NC}"
      exit 1
    fi
  else
    echo -e "${RED}Error: Registry file not found and no --path provided${NC}"
    exit 1
  fi
fi

# Expand ~ if present
PROJECT_PATH="${PROJECT_PATH/#\~/$HOME_DIR}"

# Make absolute
if [[ ! "$PROJECT_PATH" = /* ]]; then
  PROJECT_PATH="$(cd "$PROJECT_PATH" 2>/dev/null && pwd)" || {
    echo -e "${RED}Error: Path '$PROJECT_PATH' does not exist${NC}"
    exit 1
  }
fi

# --- Derived values ---
VAULT_FILE="$VAULT_DIR/$PROJECT_NAME.md"
SESSION_LOG="$PROJECT_PATH/_SESSION_LOG.md"
COMPACTIONS_DIR="$PROJECT_PATH/compactions"

# Encode path for Claude's project directory format:
# /Users/devon/my-project -> -Users-devon-my-project
ENCODED_PATH=$(echo "$PROJECT_PATH" | sed 's|/|-|g' | sed 's|^-||')
PROJECT_MEMORY_DIR="$CLAUDE_DIR/projects/-${ENCODED_PATH}/memory"
MEMORY_FILE="$PROJECT_MEMORY_DIR/MEMORY.md"

# --- Show plan ---
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Remove Project: $PROJECT_NAME${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}The following artifacts will be deleted:${NC}"
echo ""

ARTIFACT_COUNT=0

if [ -f "$VAULT_FILE" ]; then
  echo -e "  ${RED}[DELETE]${NC} Vault file:    $VAULT_FILE"
  ARTIFACT_COUNT=$((ARTIFACT_COUNT+1))
else
  echo -e "  ${YELLOW}[SKIP]${NC}   Vault file:    $VAULT_FILE (not found)"
fi

if [ "$KEEP_FILES" = false ]; then
  if [ -f "$SESSION_LOG" ]; then
    echo -e "  ${RED}[DELETE]${NC} Session log:   $SESSION_LOG"
    ARTIFACT_COUNT=$((ARTIFACT_COUNT+1))
  else
    echo -e "  ${YELLOW}[SKIP]${NC}   Session log:   $SESSION_LOG (not found)"
  fi
else
  echo -e "  ${GREEN}[KEEP]${NC}   Session log:   $SESSION_LOG (--keep-files)"
fi

if [ -f "$MEMORY_FILE" ]; then
  echo -e "  ${RED}[DELETE]${NC} MEMORY.md:     $MEMORY_FILE"
  ARTIFACT_COUNT=$((ARTIFACT_COUNT+1))
else
  echo -e "  ${YELLOW}[SKIP]${NC}   MEMORY.md:     $MEMORY_FILE (not found)"
fi

if grep -q "^| $PROJECT_NAME |" "$MASTER_FILE" 2>/dev/null; then
  echo -e "  ${RED}[DELETE]${NC} Registry entry in $MASTER_FILE"
  ARTIFACT_COUNT=$((ARTIFACT_COUNT+1))
else
  echo -e "  ${YELLOW}[SKIP]${NC}   Registry entry (not found in _MASTER.md)"
fi

if [ "$KEEP_FILES" = false ]; then
  if [ -d "$COMPACTIONS_DIR" ]; then
    COMPACTION_FILES=$(find "$COMPACTIONS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo -e "  ${RED}[DELETE]${NC} compactions/:  $COMPACTIONS_DIR ($COMPACTION_FILES files)"
    ARTIFACT_COUNT=$((ARTIFACT_COUNT+1))
  else
    echo -e "  ${YELLOW}[SKIP]${NC}   compactions/:  $COMPACTIONS_DIR (not found)"
  fi
else
  echo -e "  ${GREEN}[KEEP]${NC}   compactions/:  $COMPACTIONS_DIR (--keep-files)"
fi

echo ""

if [ "$ARTIFACT_COUNT" -eq 0 ]; then
  echo -e "${YELLOW}Nothing to remove — no artifacts found for '$PROJECT_NAME'.${NC}"
  exit 0
fi

# --- Confirmation ---
if [ "$QUIET" = false ]; then
  read -r -p "Proceed with removal? [y/N] " CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted. No changes made."
    exit 0
  fi
  echo ""
fi

# --- 1. Vault file ---
echo -e "${BLUE}--- 1/5: Vault file ---${NC}"
if [ -f "$VAULT_FILE" ]; then
  rm "$VAULT_FILE"
  echo -e "  ${GREEN}Removed:${NC} $VAULT_FILE"
else
  echo -e "  ${YELLOW}Skipped:${NC} vault file not found"
fi

# --- 2. Session log ---
echo -e "${BLUE}--- 2/5: Session log ---${NC}"
if [ "$KEEP_FILES" = false ]; then
  if [ -f "$SESSION_LOG" ]; then
    rm "$SESSION_LOG"
    echo -e "  ${GREEN}Removed:${NC} $SESSION_LOG"
  else
    echo -e "  ${YELLOW}Skipped:${NC} session log not found"
  fi
else
  echo -e "  ${GREEN}Kept:${NC} $SESSION_LOG (--keep-files)"
fi

# --- 3. Project MEMORY.md ---
echo -e "${BLUE}--- 3/5: Project MEMORY.md ---${NC}"
if [ -f "$MEMORY_FILE" ]; then
  rm "$MEMORY_FILE"
  echo -e "  ${GREEN}Removed:${NC} $MEMORY_FILE"
  # Clean up empty parent directories up to the projects/ level
  PROJECTS_DIR="$CLAUDE_DIR/projects"
  CURRENT_DIR="$PROJECT_MEMORY_DIR"
  while [ "$CURRENT_DIR" != "$PROJECTS_DIR" ] && [ "$CURRENT_DIR" != "/" ]; do
    if [ -d "$CURRENT_DIR" ] && [ -z "$(ls -A "$CURRENT_DIR" 2>/dev/null)" ]; then
      rmdir "$CURRENT_DIR"
      echo -e "  ${GREEN}Cleaned:${NC} removed empty directory $CURRENT_DIR"
    else
      break
    fi
    CURRENT_DIR=$(dirname "$CURRENT_DIR")
  done
else
  echo -e "  ${YELLOW}Skipped:${NC} MEMORY.md not found"
fi

# --- 4. Registry entry ---
echo -e "${BLUE}--- 4/5: Registry entry ---${NC}"
if grep -q "^| $PROJECT_NAME |" "$MASTER_FILE" 2>/dev/null; then
  # Delete the matching line (macOS sed, then fallback to GNU sed)
  sed -i '' "/^| $PROJECT_NAME |/d" "$MASTER_FILE" 2>/dev/null || \
  sed -i "/^| $PROJECT_NAME |/d" "$MASTER_FILE" 2>/dev/null || {
    echo -e "  ${RED}Failed to remove registry entry — edit $MASTER_FILE manually${NC}"
  }
  echo -e "  ${GREEN}Removed:${NC} registry entry from $MASTER_FILE"
else
  echo -e "  ${YELLOW}Skipped:${NC} no registry entry found"
fi

# --- 5. compactions/ directory ---
echo -e "${BLUE}--- 5/5: compactions/ directory ---${NC}"
if [ "$KEEP_FILES" = false ]; then
  if [ -d "$COMPACTIONS_DIR" ]; then
    rm -rf "$COMPACTIONS_DIR"
    echo -e "  ${GREEN}Removed:${NC} $COMPACTIONS_DIR"
  else
    echo -e "  ${YELLOW}Skipped:${NC} compactions/ not found"
  fi
else
  echo -e "  ${GREEN}Kept:${NC} $COMPACTIONS_DIR (--keep-files)"
fi

# --- Done ---
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Project '$PROJECT_NAME' removed.${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Note:${NC} The project directory itself was not deleted: $PROJECT_PATH"
