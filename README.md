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
| `vault-schema-check.sh` | PreToolUse:Write | Validates frontmatter against `vault-schema.json` on every Write — blocks invalid files with specific error messages |
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
| `vault-query.py` | Swiss-army vault search: `--query` (name/codename lookup), `--content` (body text search with OR and match location), `--search` (ranked search: BM25 standalone, vector if available, hybrid default), `--index` (build/rebuild search indexes, auto-reindex when stale). Filter with `--type`, `--tag`, `--status`, `--related`, `--path` (subdirectory). `--context N` shows surrounding lines. Enforces schema via `vault-schema.json` when present. |
| `vault_lib.py` | Shared library — frontmatter parsing, vault walking, layer classification, size measurement |
| `memory-query.py` | Fact index CRUD — add, query, update, delete facts with relevance scoring |
| `memory_lib.py` | Core memory library — indexing, search, management |
| `memory-bootstrap.py` | Initialize memory vault structure for new projects |
| `memory-consolidate.py` | Consolidate fragmented memory files |
| `vault-health-check.py` | Validate vault structure — checks for orphans, broken refs, size violations |
| `session-log-rotate.py` | Rotate and archive old session log entries |
| `vault_embeddings.py` | Optional vector embedding search. Auto-detected by vault-query.py. Requires: onnxruntime, tokenizers. |
| `vault-schema-check.py` | Validates file frontmatter against `vault-schema.json`. Called by the schema enforcement hook. |
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

### Quick (one command):
```bash
~/.claude/scripts/add-project.sh --name "My Project" --path /path/to/project
```

Or from inside the project directory:
```bash
cd /path/to/project
~/.claude/scripts/add-project.sh --name "My Project"
```

### Options:
```
--name NAME           Project name (required)
--path PATH           Working directory (default: current directory)
--agent-name NAME     Agent identity name (optional)
--description DESC    One-line project description (optional)
--quiet               Skip interactive prompts (for agent/script use)
```

### What it creates:
1. **Vault file** — `~/.claude/memory-vault/<project-name>.md`
2. **Session log** — `<project-path>/_SESSION_LOG.md`
3. **Project MEMORY.md** — `~/.claude/projects/-<encoded-path>/memory/MEMORY.md`
4. **Registry entry** — row in `~/.claude/memory-vault/_MASTER.md`
5. **compactions/** — directory for compaction summaries

### Agent usage:
Claude agents can run this automatically during session start when they detect an
unregistered working directory. The procedure is at `~/.claude/procedures/shared/add-project.md`.

```bash
# Example: agent creates a project with --quiet to skip prompts
~/.claude/scripts/add-project.sh --name "Client App" --path /Users/me/client-app --quiet
```

The full template for manual setup is still available in `~/.claude/memory-vault/_MASTER.md`.

## Search Capabilities

### Keyword Search (standalone, zero dependencies)
```bash
vault-query.py --content "search term"                    # substring match
vault-query.py --content "term1|term2|term3"              # OR search
vault-query.py --content "term" --type episode --path Episodes/ --context 2
```

### Ranked Search (BM25, standalone)
```bash
vault-query.py --index                     # build search index (run once)
vault-query.py --search "grief and loss"   # ranked by relevance
```

### Semantic Search (optional, requires onnxruntime)
```bash
pip install onnxruntime tokenizers huggingface_hub   # one-time
vault-query.py --index                               # downloads model + indexes
vault-query.py --search "characters hiding their true nature"  # meaning-based
```
When both BM25 and vector indexes exist, `--search` automatically uses hybrid mode (combines both for best results).

### Schema Enforcement
Copy `vault-schema.json` to your vault root. Customize required fields per file type. The PreToolUse hook validates frontmatter on every Write — blocks invalid files with specific error messages. No schema file = no enforcement.

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

### Custom PreToolUse Hooks

You can add your own hooks that rewrite or intercept tool calls. A common pattern is a command proxy that transparently rewrites CLI commands before they execute.

Example: a PreToolUse hook on `Bash` that rewrites `git status` → `my-proxy git status`:

```bash
# ~/.claude/hooks/my-proxy-rewrite.sh
#!/bin/bash
INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$CMD" ] && exit 0

# Rewrite known commands through your proxy
case "$CMD" in
  git\ *|npm\ *|docker\ *)
    REWRITTEN=$(echo "$CMD" | sed "s|^|my-proxy |")
    echo "{\"decision\": \"allow\", \"tool_input\": {\"command\": \"$REWRITTEN\"}}"
    ;;
  *)
    exit 0
    ;;
esac
```

Wire it in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "~/.claude/hooks/my-proxy-rewrite.sh" }]
      }
    ]
  }
}
```

This pattern is useful for token-saving proxies, command audit logging, or security guards that block dangerous operations.

## Troubleshooting

**Hooks not firing:** Check `~/.claude/settings.json` — all hooks must be listed with correct paths.

**Compaction gate stuck:** Delete `.compaction-gate` in your project root. This can happen if a session crashes mid-compaction.

**Python script errors:** Most hooks degrade gracefully (warn but don't block). Check that `python3` is in your PATH and tiktoken is installed (`pip install tiktoken`).

**"vault-health-check.py not found":** The vault cap hooks (router, moc, leaf) call this script. Make sure it's at `~/.claude/scripts/vault-health-check.py` and is executable (`chmod +x`).
