@working-profile.md

## Memory System

This project uses the Claude Memory System — an Obsidian-based persistent memory
architecture with hooks for session management, compaction safety, and vault health.

Key tools:
- `~/.claude/scripts/vault-query.py` — Frontmatter-only vault search (token-cheap)
- `~/.claude/scripts/memory-query.py` — Fact index search
- `~/.claude/scripts/count_tokens.py` — Token estimation

## Rules

- **Search before Read** — Use Glob or Grep before reading whole files.
  For "find the file named X" questions, use `vault-query.py --query "X"` (searches title/aliases/name).
  For "which files touch X" questions, use `vault-query.py --related "X"` first.
- **Write decisions immediately** — Don't batch. Context can compact at any time.
- **Session logging** — Update `_SESSION_LOG.md` after every session.
- **Single source of truth** — Link, don't duplicate.
