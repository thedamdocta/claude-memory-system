#!/bin/bash
# Claude Memory System — Installer
# Installs hooks, scripts, procedures, and templates for Claude Code + Obsidian integration.
#
# Usage: ./install.sh [--vault-path /path/to/obsidian/vault]
#
# What this does:
#   1. Creates ~/.claude/hooks/, ~/.claude/scripts/, ~/.claude/procedures/, ~/.claude/memory-vault/
#   2. Copies all hooks, scripts, and procedures
#   3. Replaces hardcoded paths with your system paths
#   4. Sets up settings.json with hook wiring
#   5. Creates template files for your first project
#   6. Installs Python dependencies (tiktoken)

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Claude Memory System — Installer${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# --- Detect home directory ---
HOME_DIR="$HOME"
CLAUDE_DIR="$HOME_DIR/.claude"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}Home directory:${NC} $HOME_DIR"
echo -e "${GREEN}Claude config:${NC} $CLAUDE_DIR"
echo -e "${GREEN}Package source:${NC} $SCRIPT_DIR"
echo ""

# --- Detect OS ---
OS="unknown"
case "$(uname -s)" in
  Darwin*) OS="mac" ;;
  Linux*)  OS="linux" ;;
  *)       OS="unknown" ;;
esac
echo -e "${GREEN}OS detected:${NC} $OS"
echo ""

# --- Check for Obsidian ---
OBSIDIAN_INSTALLED=false
if [ "$OS" = "mac" ]; then
  [ -d "/Applications/Obsidian.app" ] && OBSIDIAN_INSTALLED=true
elif [ "$OS" = "linux" ]; then
  command -v obsidian >/dev/null 2>&1 && OBSIDIAN_INSTALLED=true
  # Also check common install locations
  [ -f "/usr/bin/obsidian" ] || [ -f "$HOME_DIR/.local/bin/obsidian" ] && OBSIDIAN_INSTALLED=true
  # Check for flatpak/snap installs
  flatpak list 2>/dev/null | grep -qi obsidian && OBSIDIAN_INSTALLED=true
  snap list 2>/dev/null | grep -qi obsidian && OBSIDIAN_INSTALLED=true
fi

if [ "$OBSIDIAN_INSTALLED" = true ]; then
  echo -e "${GREEN}Obsidian:${NC} Found"
else
  echo -e "${YELLOW}Obsidian:${NC} Not found"
  echo ""
  echo "Obsidian is required for the vault to work as a knowledge base."
  echo "The memory system files will work without it, but you won't get"
  echo "graph view, backlinks, or search."
  echo ""
  read -r -p "Install Obsidian now? [Y/n] " INSTALL_OBS
  if [[ ! "$INSTALL_OBS" =~ ^[Nn]$ ]]; then
    if [ "$OS" = "mac" ]; then
      echo "Downloading Obsidian for macOS..."
      # Download latest universal DMG
      OBSIDIAN_DMG="/tmp/Obsidian.dmg"
      curl -L -o "$OBSIDIAN_DMG" "https://github.com/obsidianmd/obsidian-releases/releases/latest/download/Obsidian-universal.dmg" 2>&1
      echo "Mounting DMG..."
      hdiutil attach "$OBSIDIAN_DMG" -quiet
      MOUNT_POINT=$(hdiutil info | grep "Obsidian" | grep "/Volumes/" | awk '{print $NF}')
      if [ -z "$MOUNT_POINT" ]; then
        MOUNT_POINT="/Volumes/Obsidian"
      fi
      echo "Copying Obsidian to /Applications..."
      cp -R "$MOUNT_POINT/Obsidian.app" /Applications/ 2>/dev/null || {
        echo -e "${YELLOW}Need admin access to copy to /Applications${NC}"
        sudo cp -R "$MOUNT_POINT/Obsidian.app" /Applications/
      }
      hdiutil detach "$MOUNT_POINT" -quiet 2>/dev/null
      rm -f "$OBSIDIAN_DMG"
      echo -e "${GREEN}Obsidian installed to /Applications${NC}"
    elif [ "$OS" = "linux" ]; then
      echo "Select install method:"
      echo "  1) Flatpak (recommended)"
      echo "  2) Snap"
      echo "  3) AppImage (download to ~/Applications/)"
      echo "  4) Skip — I'll install it myself"
      read -r -p "Choice [1-4]: " LINUX_METHOD
      case "$LINUX_METHOD" in
        1)
          if command -v flatpak >/dev/null 2>&1; then
            flatpak install -y flathub md.obsidian.Obsidian
            echo -e "${GREEN}Obsidian installed via Flatpak${NC}"
          else
            echo -e "${RED}Flatpak not found. Install flatpak first, or choose another method.${NC}"
            exit 1
          fi
          ;;
        2)
          if command -v snap >/dev/null 2>&1; then
            sudo snap install obsidian --classic
            echo -e "${GREEN}Obsidian installed via Snap${NC}"
          else
            echo -e "${RED}Snap not found. Install snapd first, or choose another method.${NC}"
            exit 1
          fi
          ;;
        3)
          mkdir -p "$HOME_DIR/Applications"
          echo "Downloading Obsidian AppImage..."
          curl -L -o "$HOME_DIR/Applications/Obsidian.AppImage" "https://github.com/obsidianmd/obsidian-releases/releases/latest/download/Obsidian.AppImage" 2>&1
          chmod +x "$HOME_DIR/Applications/Obsidian.AppImage"
          echo -e "${GREEN}Obsidian AppImage saved to ~/Applications/Obsidian.AppImage${NC}"
          ;;
        4)
          echo "Skipping Obsidian install. You can install it later from https://obsidian.md"
          ;;
        *)
          echo "Skipping Obsidian install."
          ;;
      esac
    else
      echo -e "${YELLOW}Couldn't detect OS for auto-install. Download Obsidian from: https://obsidian.md${NC}"
    fi
  else
    echo "Skipping Obsidian install. You can get it later from https://obsidian.md"
  fi
fi
echo ""

# --- Parse arguments ---
VAULT_PATH=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --vault-path)
      VAULT_PATH="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: ./install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --vault-path PATH    Path to your Obsidian vault (will be created if needed)"
      echo "  -h, --help           Show this help"
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      exit 1
      ;;
  esac
done

# --- Prompt for vault path if not provided ---
if [ -z "$VAULT_PATH" ]; then
  echo -e "${YELLOW}Where is your Obsidian vault? (This is the folder Obsidian opens as a vault)${NC}"
  echo -e "${YELLOW}If it doesn't exist yet, we'll create it.${NC}"
  read -r -p "Vault path: " VAULT_PATH
fi

# Expand ~ if present
VAULT_PATH="${VAULT_PATH/#\~/$HOME_DIR}"

# Make absolute
if [[ ! "$VAULT_PATH" = /* ]]; then
  VAULT_PATH="$HOME_DIR/$VAULT_PATH"
fi

echo ""
echo -e "${GREEN}Obsidian vault:${NC} $VAULT_PATH"
echo ""

# --- Confirmation ---
echo -e "${YELLOW}This will:${NC}"
echo "  1. Create directories under $CLAUDE_DIR/"
echo "  2. Copy hooks, scripts, procedures, and templates"
echo "  3. Update paths in all scripts for your system"
echo "  4. Create/update $CLAUDE_DIR/settings.json"
echo "  5. Set up your Obsidian vault with starter files"
echo "  6. Install Python dependency: tiktoken"
echo ""
read -r -p "Proceed? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo -e "${BLUE}--- Step 1: Create directories ---${NC}"

mkdir -p "$CLAUDE_DIR/hooks"
mkdir -p "$CLAUDE_DIR/scripts"
mkdir -p "$CLAUDE_DIR/procedures/shared"
mkdir -p "$CLAUDE_DIR/templates"
mkdir -p "$CLAUDE_DIR/memory-vault"
mkdir -p "$VAULT_PATH"
mkdir -p "$VAULT_PATH/compactions"
mkdir -p "$VAULT_PATH/Planning"
echo "  Created all directories."

echo ""
echo -e "${BLUE}--- Step 2: Copy hooks ---${NC}"

# Copy all hooks
for hook in "$SCRIPT_DIR/hooks/"*.sh; do
  BASENAME=$(basename "$hook")
  cp "$hook" "$CLAUDE_DIR/hooks/$BASENAME"
  chmod +x "$CLAUDE_DIR/hooks/$BASENAME"
  echo "  Installed: $BASENAME"
done

echo ""
echo -e "${BLUE}--- Step 3: Copy scripts ---${NC}"

for script in "$SCRIPT_DIR/scripts/"*.py; do
  BASENAME=$(basename "$script")
  cp "$script" "$CLAUDE_DIR/scripts/$BASENAME"
  chmod +x "$CLAUDE_DIR/scripts/$BASENAME"
  echo "  Installed: $BASENAME"
done

echo ""
echo -e "${BLUE}--- Step 4: Copy procedures ---${NC}"

# Shared procedures
for proc in "$SCRIPT_DIR/procedures/shared/"*.md; do
  BASENAME=$(basename "$proc")
  cp "$proc" "$CLAUDE_DIR/procedures/shared/$BASENAME"
  echo "  Installed: shared/$BASENAME"
done

# Example procedures (project-specific templates)
if [ -d "$SCRIPT_DIR/procedures/examples" ]; then
  mkdir -p "$CLAUDE_DIR/procedures/examples"
  for proc in "$SCRIPT_DIR/procedures/examples/"*.md; do
    BASENAME=$(basename "$proc")
    cp "$proc" "$CLAUDE_DIR/procedures/examples/$BASENAME"
    echo "  Installed: examples/$BASENAME (template)"
  done
fi

echo ""
echo -e "${BLUE}--- Step 5: Replace hardcoded paths ---${NC}"

# The original system was built for __VAULT_PATH__
# Replace all occurrences with the user's actual paths
OLD_HOME="__HOME_DIR__"
OLD_VAULT="__VAULT_PATH__"
OLD_CLAUDE="__CLAUDE_DIR__"

# Replace in all hooks
for file in "$CLAUDE_DIR/hooks/"*.sh; do
  sed -i '' "s|$OLD_VAULT|$VAULT_PATH|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_CLAUDE|$CLAUDE_DIR|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_HOME|$HOME_DIR|g" "$file" 2>/dev/null || true
done

# Replace in all scripts
for file in "$CLAUDE_DIR/scripts/"*.py; do
  sed -i '' "s|$OLD_VAULT|$VAULT_PATH|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_CLAUDE|$CLAUDE_DIR|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_HOME|$HOME_DIR|g" "$file" 2>/dev/null || true
done

# Replace in procedures
for file in "$CLAUDE_DIR/procedures/"*/*.md; do
  sed -i '' "s|$OLD_VAULT|$VAULT_PATH|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_CLAUDE|$CLAUDE_DIR|g" "$file" 2>/dev/null || true
  sed -i '' "s|$OLD_HOME|$HOME_DIR|g" "$file" 2>/dev/null || true
done

echo "  Replaced all placeholder paths with your system paths"

echo ""
echo -e "${BLUE}--- Step 6: Set up settings.json ---${NC}"

SETTINGS_FILE="$CLAUDE_DIR/settings.json"

# Back up existing settings if present
if [ -f "$SETTINGS_FILE" ]; then
  cp "$SETTINGS_FILE" "$SETTINGS_FILE.backup.$(date +%Y%m%d%H%M%S)"
  echo "  Backed up existing settings.json"
fi

cat > "$SETTINGS_FILE" << SETTINGS_EOF
{
  "permissions": {
    "allow": []
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/session-memory-inject.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/memory-nudge-counter.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/compaction-gate-set.sh"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/resize-image.sh"
          }
        ]
      },
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/search-discipline-check.sh"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/vault-router-cap.sh"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/vault-moc-cap.sh"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/vault-leaf-warn.sh"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/vault-schema-check.sh"
          }
        ]
      },
      {
        "matcher": "Edit|Write|Bash|Task",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/compaction-gate-check.sh"
          }
        ]
      },
      {
        "matcher": "Grep",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/grep-growth-nudge.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/compaction-gate-clear.sh"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/memory-capture.sh"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_DIR/hooks/session-log-rotate-hook.sh"
          }
        ]
      }
    ]
  }
}
SETTINGS_EOF

echo "  Created settings.json with all hooks wired."

echo ""
echo -e "${BLUE}--- Step 7: Set up memory vault ---${NC}"

# Copy _MASTER.md template
if [ ! -f "$CLAUDE_DIR/memory-vault/_MASTER.md" ]; then
  cp "$SCRIPT_DIR/templates/_MASTER.md" "$CLAUDE_DIR/memory-vault/_MASTER.md"
  # Replace placeholder paths
  sed -i '' "s|~/.claude|$CLAUDE_DIR|g" "$CLAUDE_DIR/memory-vault/_MASTER.md" 2>/dev/null || true
  echo "  Created _MASTER.md (project registry)"
else
  echo "  _MASTER.md already exists, skipping"
fi

# Copy working profile template
if [ ! -f "$CLAUDE_DIR/memory-vault/working-profile.md" ] && [ ! -f "$CLAUDE_DIR/memory-vault/working-profile.md" ]; then
  cp "$SCRIPT_DIR/templates/working-profile.md" "$CLAUDE_DIR/memory-vault/working-profile.md"
  echo "  Created working-profile.md (customize this with your preferences)"
else
  echo "  Working profile already exists, skipping"
fi

# Copy chain-check protocol (cross-agent DONE-receipt discipline)
if [ ! -f "$CLAUDE_DIR/memory-vault/chain-check.md" ]; then
  cp "$SCRIPT_DIR/templates/chain-check.md" "$CLAUDE_DIR/memory-vault/chain-check.md"
  echo "  Created chain-check.md (chain-check protocol, auto-injected every session)"
else
  echo "  Chain-check protocol already exists, skipping"
fi

# Copy schema template (not activated by default — user copies to vault root when ready)
if [ -f "$SCRIPT_DIR/templates/vault-schema.json" ]; then
  cp "$SCRIPT_DIR/templates/vault-schema.json" "$CLAUDE_DIR/templates/vault-schema.json"
  echo "  Installed: vault-schema.json (template — copy to vault root to activate)"
fi

echo ""
echo -e "${BLUE}--- Step 8: Set up CLAUDE.md ---${NC}"

if [ ! -f "$CLAUDE_DIR/CLAUDE.md" ]; then
  cp "$SCRIPT_DIR/templates/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"
  # Fix the reference to working profile
  sed -i '' "s|@working-profile.md|@$CLAUDE_DIR/memory-vault/working-profile.md|g" "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null || true
  echo "  Created CLAUDE.md (global instructions)"
else
  echo "  CLAUDE.md already exists, skipping"
fi

echo ""
echo -e "${BLUE}--- Step 9: Set up first project ---${NC}"

# Create the project-scoped MEMORY.md for the vault
# Convert vault path to Claude's project directory format
PROJECT_DIR_NAME=$(echo "$VAULT_PATH" | sed 's|/|-|g' | sed 's|^-||')
PROJECT_MEMORY_DIR="$CLAUDE_DIR/projects/-${PROJECT_DIR_NAME}/memory"
mkdir -p "$PROJECT_MEMORY_DIR"

if [ ! -f "$PROJECT_MEMORY_DIR/MEMORY.md" ]; then
  VAULT_BASENAME=$(basename "$VAULT_PATH")
  cat > "$PROJECT_MEMORY_DIR/MEMORY.md" << MEMORY_EOF
# Claude Memory — Project Router

> **Memory lives in the vault.** This file is just the entry point.

## Session Start Protocol

1. **Read the master index:** \`$CLAUDE_DIR/memory-vault/_MASTER.md\`
2. **Identify the project** from the working directory
3. **If existing project** -> Read vault file, then \`_SESSION_LOG.md\`
4. **If new project** -> Create vault file using template in \`_MASTER.md\`
5. **Follow the project's own session start protocol**

## This Project

**Working directory \`$VAULT_PATH/\` = $VAULT_BASENAME project.**
-> Read vault: \`$CLAUDE_DIR/memory-vault/$VAULT_BASENAME.md\`
-> Read session log: \`$VAULT_PATH/_SESSION_LOG.md\`
-> Read working profile: \`$CLAUDE_DIR/memory-vault/working-profile.md\`

## Global Rules (All Projects)

- **ASK BEFORE WRITING** — Discuss and confirm before committing file changes
- **Check before guessing** — Search files before answering uncertain questions
- **Session logging** — Update project session logs after every session
- **Single source of truth** — Link, don't duplicate

## Memory Vault Location
\`$CLAUDE_DIR/memory-vault/\`
MEMORY_EOF
  echo "  Created project MEMORY.md at $PROJECT_MEMORY_DIR/"
else
  echo "  Project MEMORY.md already exists, skipping"
fi

# Create _SESSION_LOG.md in the vault
if [ ! -f "$VAULT_PATH/_SESSION_LOG.md" ]; then
  TODAY=$(date +%Y-%m-%d)
  VAULT_BASENAME=$(basename "$VAULT_PATH")
  cat > "$VAULT_PATH/_SESSION_LOG.md" << LOG_EOF
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
| 1 | Initial setup — Claude Memory System installed | System configured | |
LOG_EOF
  echo "  Created _SESSION_LOG.md in vault"
else
  echo "  _SESSION_LOG.md already exists, skipping"
fi

# Create vault file in memory-vault
VAULT_BASENAME=$(basename "$VAULT_PATH")
VAULT_FILE="$CLAUDE_DIR/memory-vault/$VAULT_BASENAME.md"
if [ ! -f "$VAULT_FILE" ]; then
  cat > "$VAULT_FILE" << VAULT_EOF
# $VAULT_BASENAME — Claude Memory

## Project Overview
- **What:** [Describe your project]
- **Vault Directory:** \`$VAULT_PATH/\`
- **Format:** Obsidian vault — .md files with YAML frontmatter, \`[[wiki-links]]\` for cross-references

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
  echo "  Created vault file: $VAULT_FILE"
else
  echo "  Vault file already exists, skipping"
fi

echo ""
echo -e "${BLUE}--- Step 10: Install Python dependencies ---${NC}"

if command -v pip3 &>/dev/null; then
  pip3 install tiktoken --quiet 2>/dev/null && echo "  Installed tiktoken" || echo "  tiktoken install failed (optional — count_tokens.py won't work without it)"
elif command -v pip &>/dev/null; then
  pip install tiktoken --quiet 2>/dev/null && echo "  Installed tiktoken" || echo "  tiktoken install failed (optional)"
else
  echo -e "  ${YELLOW}pip not found — install tiktoken manually: pip install tiktoken${NC}"
fi

echo ""
echo -e "${BLUE}--- Step 11: Install semantic search (embedding model) ---${NC}"

SEMANTIC_OK=false
if command -v pip3 &>/dev/null; then
  pip3 install --break-system-packages --quiet onnxruntime tokenizers huggingface_hub 2>/dev/null && {
    echo "  Installed: onnxruntime, tokenizers, huggingface_hub"
    SEMANTIC_OK=true
  } || {
    pip3 install --user --quiet onnxruntime tokenizers huggingface_hub 2>/dev/null && {
      echo "  Installed (user): onnxruntime, tokenizers, huggingface_hub"
      SEMANTIC_OK=true
    } || echo -e "  ${YELLOW}Semantic search deps failed to install. BM25 keyword search still works.${NC}"
  }
elif command -v pip &>/dev/null; then
  pip install --break-system-packages --quiet onnxruntime tokenizers huggingface_hub 2>/dev/null && {
    echo "  Installed: onnxruntime, tokenizers, huggingface_hub"
    SEMANTIC_OK=true
  } || echo -e "  ${YELLOW}Semantic search deps failed to install. BM25 keyword search still works.${NC}"
else
  echo -e "  ${YELLOW}pip not found — semantic search requires: pip install onnxruntime tokenizers huggingface_hub${NC}"
fi

if [ "$SEMANTIC_OK" = true ]; then
  echo "  Downloading embedding model (~90MB, one-time)..."
  python3 -c "
import sys, os
sys.path.insert(0, '$CLAUDE_DIR/scripts')
from vault_embeddings import _ensure_model
_ensure_model()
" 2>&1 | sed 's/^/  /'
fi

echo ""
echo -e "${BLUE}--- Step 12: Build search index ---${NC}"
echo "  Building search index for your vault..."
python3 "$CLAUDE_DIR/scripts/vault-query.py" --index --root "$VAULT_PATH" 2>&1 | sed 's/^/  /'

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "What was installed:"
echo "  Hooks:      $(ls "$CLAUDE_DIR/hooks/"*.sh 2>/dev/null | wc -l | tr -d ' ') shell scripts in $CLAUDE_DIR/hooks/"
echo "  Scripts:    $(ls "$CLAUDE_DIR/scripts/"*.py 2>/dev/null | wc -l | tr -d ' ') Python tools in $CLAUDE_DIR/scripts/"
echo "  Procedures: $(find "$CLAUDE_DIR/procedures" -name '*.md' 2>/dev/null | wc -l | tr -d ' ') workflow docs in $CLAUDE_DIR/procedures/"
echo "  Vault:      $VAULT_PATH/"
echo "  Config:     $SETTINGS_FILE"
echo ""
echo "Next steps:"
echo "  1. Open Obsidian and add $VAULT_PATH as a vault"
echo "  2. Edit $CLAUDE_DIR/memory-vault/working-profile.md with your preferences"
echo "  3. Start Claude Code in your vault directory: cd $VAULT_PATH && claude"
echo "  4. Claude will auto-orient using the hooks and memory system"
echo ""
echo "To add a new project later:"
echo "  $CLAUDE_DIR/scripts/add-project.sh --name \"Project Name\" --path /path/to/project"
echo "  Or just: cd /your/project && $CLAUDE_DIR/scripts/add-project.sh --name \"Project Name\""
echo "  Run with --help for all options (--agent-name, --description, --quiet)."
echo ""
echo "  Claude agents can also run this automatically — see the add-project procedure."
echo ""
echo -e "${YELLOW}Note: The project-specific hooks (project-compass-reminder, project-precompact-steer)${NC}"
echo -e "${YELLOW}are example templates. Customize them for your project or ask Claude to${NC}"
echo -e "${YELLOW}create new ones tailored to your needs.${NC}"
