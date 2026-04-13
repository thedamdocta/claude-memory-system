#!/bin/bash
# Claude Memory System — Add Project
# Scaffolds all 4 required artifacts for a new project in one shot.
#
# Usage:
#   ./add-project.sh --name "My Project" --path /path/to/project
#   ./add-project.sh --name "My Project"                          # uses current directory
#   ./add-project.sh --name "My Project" --agent-name "Ori"       # optional agent name
#
# What this creates:
#   1. Vault file:         ~/.claude/memory-vault/<project-name>.md
#   2. Session log:        <project-path>/_SESSION_LOG.md
#   3. Project MEMORY.md:  ~/.claude/projects/-<encoded-path>/memory/MEMORY.md
#   4. Registry entry:     appended to ~/.claude/memory-vault/_MASTER.md
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
PROJECT_NAME=""
PROJECT_PATH=""
AGENT_NAME=""
DESCRIPTION=""
QUIET=false

# --- Usage ---
usage() {
  echo "Usage: add-project.sh --name <project-name> [OPTIONS]"
  echo ""
  echo "Required:"
  echo "  --name NAME           Project name (used for vault file and registry)"
  echo ""
  echo "Optional:"
  echo "  --path PATH           Project working directory (default: current directory)"
  echo "  --agent-name NAME     Agent identity name for this project"
  echo "  --description DESC    One-line project description"
  echo "  --quiet               Suppress interactive prompts (for agent use)"
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
    --agent-name)
      AGENT_NAME="$2"
      shift 2
      ;;
    --description)
      DESCRIPTION="$2"
      shift 2
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

# Default to current directory
if [ -z "$PROJECT_PATH" ]; then
  PROJECT_PATH="$(pwd)"
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
PROJECT_BASENAME=$(basename "$PROJECT_PATH")
VAULT_FILE="$VAULT_DIR/$PROJECT_NAME.md"
SESSION_LOG="$PROJECT_PATH/_SESSION_LOG.md"
COMPACTIONS_DIR="$PROJECT_PATH/compactions"
TODAY=$(date +%Y-%m-%d)

# Encode path for Claude's project directory format:
# /Users/devon/my-project → -Users-devon-my-project
ENCODED_PATH=$(echo "$PROJECT_PATH" | sed 's|/|-|g' | sed 's|^-||')
PROJECT_MEMORY_DIR="$CLAUDE_DIR/projects/-${ENCODED_PATH}/memory"
MEMORY_FILE="$PROJECT_MEMORY_DIR/MEMORY.md"
MASTER_FILE="$VAULT_DIR/_MASTER.md"

# --- Pre-flight checks ---
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Add Project: $PROJECT_NAME${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check prerequisites
if [ ! -d "$CLAUDE_DIR" ]; then
  echo -e "${RED}Error: $CLAUDE_DIR not found. Run the installer first.${NC}"
  exit 1
fi

if [ ! -d "$VAULT_DIR" ]; then
  echo -e "${RED}Error: $VAULT_DIR not found. Run the installer first.${NC}"
  exit 1
fi

if [ ! -f "$MASTER_FILE" ]; then
  echo -e "${RED}Error: $MASTER_FILE not found. Run the installer first.${NC}"
  exit 1
fi

# Check for existing artifacts
EXISTING=0
[ -f "$VAULT_FILE" ] && { echo -e "${YELLOW}  Warning: Vault file already exists: $VAULT_FILE${NC}"; EXISTING=$((EXISTING+1)); }
[ -f "$SESSION_LOG" ] && { echo -e "${YELLOW}  Warning: Session log already exists: $SESSION_LOG${NC}"; EXISTING=$((EXISTING+1)); }
[ -f "$MEMORY_FILE" ] && { echo -e "${YELLOW}  Warning: MEMORY.md already exists: $MEMORY_FILE${NC}"; EXISTING=$((EXISTING+1)); }

if [ "$EXISTING" -gt 0 ] && [ "$QUIET" = false ]; then
  echo ""
  read -r -p "Some artifacts already exist. Overwrite? [y/N] " CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted. Existing files left untouched."
    exit 0
  fi
fi

# Show plan
echo ""
echo -e "${GREEN}Project name:${NC}    $PROJECT_NAME"
echo -e "${GREEN}Working dir:${NC}     $PROJECT_PATH"
echo -e "${GREEN}Vault file:${NC}      $VAULT_FILE"
echo -e "${GREEN}Session log:${NC}     $SESSION_LOG"
echo -e "${GREEN}MEMORY.md:${NC}       $MEMORY_FILE"
[ -n "$AGENT_NAME" ] && echo -e "${GREEN}Agent name:${NC}      $AGENT_NAME"
[ -n "$DESCRIPTION" ] && echo -e "${GREEN}Description:${NC}     $DESCRIPTION"
echo ""

if [ "$QUIET" = false ]; then
  read -r -p "Create project? [Y/n] " CONFIRM
  if [[ "$CONFIRM" =~ ^[Nn]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# --- Create directories ---
echo ""
echo -e "${BLUE}--- Creating directories ---${NC}"
mkdir -p "$PROJECT_PATH"
mkdir -p "$COMPACTIONS_DIR"
mkdir -p "$PROJECT_MEMORY_DIR"
echo "  Created project dir, compactions/, and memory dir"

# --- 1. Vault file ---
echo ""
echo -e "${BLUE}--- 1/4: Vault file ---${NC}"

AGENT_SECTION=""
if [ -n "$AGENT_NAME" ]; then
  AGENT_SECTION="- **Agent Name:** $AGENT_NAME"
fi

DESC_LINE="${DESCRIPTION:-[Describe your project]}"

cat > "$VAULT_FILE" << VAULT_EOF
# $PROJECT_NAME — Claude Memory

## Project Overview
- **What:** $DESC_LINE
- **Vault Directory:** \`$PROJECT_PATH/\`
- **Format:** Obsidian vault — .md files with YAML frontmatter, \`[[wiki-links]]\` for cross-references
$AGENT_SECTION

## Session Start Protocol
1. Read this vault file
2. Read \`_SESSION_LOG.md\` in the working directory
3. Check for \`.state\` file — if exists, resume from checkpoint
4. Check Active Context below for current focus

## Project Rules
- [Add project-specific rules here]

## Key Files
| Purpose | File |
|---------|------|
| Session log | \`_SESSION_LOG.md\` |

## Active Context
- **Status:** Just started
- **Current Phase:** Setup
- **Next Step:** Begin working on the project
VAULT_EOF
echo -e "  ${GREEN}Created:${NC} $VAULT_FILE"

# --- 2. Session log ---
echo ""
echo -e "${BLUE}--- 2/4: Session log ---${NC}"

cat > "$SESSION_LOG" << LOG_EOF
---
title: "Session Log"
aliases: [session-log, sessions]
type: moc
scope: sessions
status: active
tags: [type/moc, status/active, ai/volatile]
updated: $TODAY
summary: >
  Session index. One-liner per session, detail lives in compaction files.
related:
  - "[[_INDEX]]"
---

# Session Log

| Session | Focus | Key Decisions | Detail |
|---------|-------|---------------|--------|
| 1 | Project setup — Claude Memory System configured | System initialized | |
LOG_EOF
echo -e "  ${GREEN}Created:${NC} $SESSION_LOG"

# --- 3. Project-scoped MEMORY.md ---
echo ""
echo -e "${BLUE}--- 3/4: Project MEMORY.md ---${NC}"

cat > "$MEMORY_FILE" << MEMORY_EOF
# Claude Memory — Project Router

> **Memory lives in the vault.** This file is just the entry point.

## Session Start Protocol

1. **Read the master index:** \`$VAULT_DIR/_MASTER.md\`
2. **Identify the project** from the working directory
3. **If existing project** -> Read vault file, then \`_SESSION_LOG.md\`
4. **If new project** -> Create vault file using template in \`_MASTER.md\`
5. **Follow the project's own session start protocol**

## This Project

**Working directory \`$PROJECT_PATH/\` = $PROJECT_NAME project.**
-> Read vault: \`$VAULT_FILE\`
-> Read session log: \`$SESSION_LOG\`
-> Read working profile: \`$VAULT_DIR/working-profile.md\`

## Global Rules (All Projects)

- **ASK BEFORE WRITING** — Discuss and confirm before committing file changes
- **Check before guessing** — Search files before answering uncertain questions
- **Session logging** — Update project session logs after every session
- **Single source of truth** — Link, don't duplicate

## Memory Vault Location
\`$VAULT_DIR/\`
MEMORY_EOF
echo -e "  ${GREEN}Created:${NC} $MEMORY_FILE"

# --- 4. Registry entry ---
echo ""
echo -e "${BLUE}--- 4/4: Registry entry ---${NC}"

# Check if project already exists in registry
if grep -q "| $PROJECT_NAME |" "$MASTER_FILE" 2>/dev/null; then
  echo -e "  ${YELLOW}Project already in registry, skipping${NC}"
else
  # Find the empty row in the registry table and replace it,
  # or append after the last table row
  REGISTRY_LINE="| $PROJECT_NAME | $PROJECT_NAME.md | \`$PROJECT_PATH/\` | active | $DESC_LINE |"

  # Check if there's an empty row to replace
  if grep -q "^| | | | | |$" "$MASTER_FILE"; then
    # Replace first empty row
    sed -i '' "0,/^| | | | | |$/s||$REGISTRY_LINE|" "$MASTER_FILE" 2>/dev/null || \
    sed -i "0,/^| | | | | |$/s||$REGISTRY_LINE|" "$MASTER_FILE" 2>/dev/null || {
      # Fallback: append before the --- after the table
      echo "$REGISTRY_LINE" >> "$MASTER_FILE"
    }
    echo -e "  ${GREEN}Added to registry (replaced empty row)${NC}"
  else
    # Find the last line of the registry table and append after it
    # The registry table is between "## Project Registry" and the next "---"
    # Append the new row after the last | line in the table
    LAST_TABLE_LINE=$(grep -n "^|" "$MASTER_FILE" | head -20 | tail -1 | cut -d: -f1)
    if [ -n "$LAST_TABLE_LINE" ]; then
      sed -i '' "${LAST_TABLE_LINE}a\\
$REGISTRY_LINE" "$MASTER_FILE" 2>/dev/null || \
      sed -i "${LAST_TABLE_LINE}a\\
$REGISTRY_LINE" "$MASTER_FILE" 2>/dev/null || {
        echo "$REGISTRY_LINE" >> "$MASTER_FILE"
      }
      echo -e "  ${GREEN}Appended to registry table${NC}"
    else
      echo "$REGISTRY_LINE" >> "$MASTER_FILE"
      echo -e "  ${GREEN}Appended to registry (fallback)${NC}"
    fi
  fi
fi

# --- 5. Procedures directory ---
echo ""
echo -e "${BLUE}--- 5/5: Procedures directory ---${NC}"

PROCEDURES_BASE="$HOME/.claude/procedures"
PROJECT_PROC_DIR="$PROCEDURES_BASE/$PROJECT_NAME"

if [ -d "$PROJECT_PROC_DIR" ]; then
  echo -e "  ${YELLOW}Procedures directory already exists, skipping${NC}"
else
  mkdir -p "$PROJECT_PROC_DIR"
  echo -e "  ${GREEN}Created:${NC} $PROJECT_PROC_DIR/"

  # Copy example procedures if they exist, renaming to project scope
  EXAMPLES_DIR="$PROCEDURES_BASE/examples"
  if [ -d "$EXAMPLES_DIR" ]; then
    COPIED=0
    for example in "$EXAMPLES_DIR"/*.md; do
      [ ! -f "$example" ] && continue
      EXAMPLE_BASENAME=$(basename "$example")
      cp "$example" "$PROJECT_PROC_DIR/$EXAMPLE_BASENAME"
      echo -e "  ${GREEN}Copied example:${NC} $EXAMPLE_BASENAME"
      COPIED=$((COPIED+1))
    done
    if [ "$COPIED" -gt 0 ]; then
      echo -e "  ${YELLOW}Note:${NC} Example procedures copied. Edit them to fit your project."
    fi
  else
    echo -e "  ${YELLOW}No example procedures found at $EXAMPLES_DIR/${NC}"
  fi
fi

# --- Done ---
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Project '$PROJECT_NAME' created!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Created:"
echo "  1. Vault file:     $VAULT_FILE"
echo "  2. Session log:    $SESSION_LOG"
echo "  3. MEMORY.md:      $MEMORY_FILE"
echo "  4. Registry:       Updated in $MASTER_FILE"
echo "  5. compactions/:   $COMPACTIONS_DIR/"
echo "  6. Procedures:     $PROJECT_PROC_DIR/"
echo ""
echo "Next steps:"
echo "  1. Edit $VAULT_FILE to add project-specific rules"
echo "  2. Edit procedures in $PROJECT_PROC_DIR/ to fit your project"
echo "  3. Start Claude Code: cd $PROJECT_PATH && claude"
echo "  4. Claude will auto-orient using the memory system"
echo ""
echo "To remove this project later:"
echo "  ~/.claude/scripts/remove-project.sh --name \"$PROJECT_NAME\""
