# Claude Memory System — Setup Guide

## What Is This?

A persistent memory system for Claude Code that uses Obsidian as the knowledge backend.
Claude remembers across sessions, manages context compaction safely, and enforces vault
health automatically via hooks.

## Architecture

The system has three layers:

### Layer 1: Hooks (Shell Scripts)
Hooks fire automatically on Claude Code events. They enforce rules structurally —
Claude can't accidentally break them because they run before/after every tool call.

| Hook | Event | Purpose |
|------|-------|---------|
| `session-memory-inject.sh` | SessionStart | Injects working profile, vault file, session log, and latest compaction into Claude's context |
| `compaction-gate-set.sh` | PreCompact | Sets a gate marker that blocks all tools until compaction summary is saved |
| `compaction-gate-check.sh` | PreToolUse | Checks if gate is active; blocks Edit/Write/Bash/Task until compaction is saved |
| `compaction-gate-clear.sh` | PostToolUse:Write | Clears the gate after compaction file is written |
| `search-discipline-check.sh` | PreToolUse:Read | Nudges Claude to search (Glob/Grep) before reading whole files |
| `grep-growth-nudge.sh` | PreToolUse:Grep | Reminds Claude to use vault-query.py for metadata searches |
| `vault-router-cap.sh` | PreToolUse:Write/Edit | Blocks writes that would make router files (indexes) too large |
| `vault-moc-cap.sh` | PreToolUse:Write/Edit | Blocks writes with bloated MOC (Map of Content) entries |
| `vault-leaf-warn.sh` | PreToolUse:Write/Edit | Warns when content files approach size limits (20KB) |
| `resize-image.sh` | PreToolUse:Read | Resizes images before Claude reads them (saves tokens) |
| `memory-capture.sh` | PostToolUse:Write | Captures write events for memory tracking |
| `memory-nudge-counter.sh` | UserPromptSubmit | Tracks memory tool usage patterns |
| `session-log-rotate-hook.sh` | PostToolUse:Write | Triggers log rotation when session logs grow |
| `my-project-compass-reminder.sh` | UserPromptSubmit | **(Example)** Project-specific creative compass reminder |
| `my-project-precompact-steer.sh` | PreCompact | **(Example)** Project-specific compaction steering |

### Layer 2: Python Tools
CLI tools Claude calls for vault operations.

| Tool | Purpose |
|------|---------|
| `vault-query.py` | Search vault by frontmatter metadata (type, tag, status, related). Token-cheap — reads only YAML headers. |
| `vault_lib.py` | Shared library — frontmatter parsing, vault walking, layer classification, size measurement |
| `memory-query.py` | Fact index CRUD — add, query, update, delete facts with relevance scoring |
| `memory_lib.py` | Core memory library — indexing, search, management |
| `memory-bootstrap.py` | Initialize memory vault structure for new projects |
| `memory-consolidate.py` | Consolidate fragmented memory files |
| `vault-health-check.py` | Validate vault structure — checks for orphans, broken refs, size violations |
| `session-log-rotate.py` | Rotate and archive old session log entries |
| `count_tokens.py` | Estimate token count for text (requires tiktoken) |

### Layer 3: Vault Structure (Obsidian)
Your knowledge lives in `.md` files with YAML frontmatter and `[[wikilinks]]`.

**File types by role:**
- **Routers** (`_INDEX.md`, `_SESSION_LOG.md`, `MEMORY.md`) — Navigation files, kept small (<15KB)
- **MOCs** (`_CHARACTER_MOC.md`, `_LORE_MOC.md`) — Maps of Content, link collections
- **Leaves** (everything else) — Content files, capped at 20KB
- **Meta** (`compactions/`, `Planning/`) — Infrastructure files

**Every .md file gets YAML frontmatter:**
```yaml
---
title: "Document Title"
aliases: [short-name]
type: leaf          # leaf | moc | router | meta
status: active      # active | draft | locked | archived
tags: [type/leaf, topic/relevant]
updated: 2026-04-13
related:
  - "[[Related Doc 1]]"
  - "[[Related Doc 2]]"
summary: >
  One-sentence description for search and triage.
---
```

## Compaction Safety

When Claude's context window fills up, Claude Code "compacts" — summarizes the conversation
and continues. Without protection, this loses state. The compaction gate prevents that:

1. **PreCompact hook** sets a `.compaction-gate` marker file
2. **PreToolUse hook** blocks ALL tools (Edit, Write, Bash, Task) when gate is active
3. Claude MUST write a compaction summary to `compactions/session-XX-compaction.md`
4. **PostToolUse hook** clears the gate after the write
5. Normal operation resumes

This means Claude can never lose state on compaction — the summary is always saved first.

## Adding a New Project

Tell Claude: "Set up a new project called [name]"

Claude will automatically:
1. Create the working directory
2. Create a vault file in `~/.claude/memory-vault/`
3. Create `_SESSION_LOG.md` in the project directory
4. Create a project-scoped `MEMORY.md` in `~/.claude/projects/`
5. Add the project to the registry in `_MASTER.md`

The full template is in `~/.claude/memory-vault/_MASTER.md`.

## Customization

### Project-Specific Hooks
The `procedures/examples/` folder contains templates for project-specific hooks:
- **Creative compass** — Reminds Claude of your project's themes on every prompt
- **Compaction steering** — Focuses compaction summaries on what matters for your project

To create your own, copy the template and modify the project detection path and content.

### Working Profile
Edit `~/.claude/memory-vault/working-profile.md` to set:
- Your communication preferences
- Documentation standards
- Working rules
- Corrections log (Claude updates this when you correct it)

### Size Limits
Defaults in `vault_lib.py`:
- Router cap: 15KB
- Leaf cap: 20KB
- Leaf warning: 80% of cap
- MOC entry cap: 200 characters

Adjust these constants if your vault has different needs.

## Troubleshooting

**Hooks not firing:** Check `~/.claude/settings.json` — all hooks must be listed with correct paths.

**Compaction gate stuck:** Delete `.compaction-gate` in your project root. This can happen if a session crashes mid-compaction.

**Python script errors:** Most hooks degrade gracefully (warn but don't block). Check that `python3` is in your PATH and tiktoken is installed (`pip install tiktoken`).

**"vault-health-check.py not found":** The vault cap hooks (router, moc, leaf) call this script. Make sure it's at `~/.claude/scripts/vault-health-check.py` and is executable (`chmod +x`).
