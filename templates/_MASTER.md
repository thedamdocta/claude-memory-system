# Claude Memory Vault — Master Index

> This is the master registry for all projects. Claude reads this file to know
> which projects exist, where they live, and how to orient on session start.

---

## How This Works (Procedural Steps)

1. **Session starts** → MEMORY.md (auto-loaded from project directory) tells Claude to read this file
2. **Read working profile** → Load your working preferences and corrections log
3. **Identify the project** → Check working directory against the registry below
4. **If existing project** → Read that project's vault file, then read its `_SESSION_LOG.md`
5. **If new project** → Do ALL of the following:
   a. Create the working directory
   b. Create a vault file in the memory-vault using the template below
   c. Create a `_SESSION_LOG.md` in the working directory
   d. **Create a project-scoped MEMORY.md** (CRITICAL for memory persistence)
   e. Add to the registry in this file
6. **Session log prune check** → If >10 entries, archive older sessions (keep most recent 10)
7. **When done** → Update vault file, session log, and registry if status changed

---

## Project Registry

| Project | Vault File | Working Directory | Status | Summary |
|---------|-----------|-------------------|--------|---------|
| | | | | |

---

## Global Rules (Apply to ALL Projects)

- **ASK BEFORE WRITING** — Always get permission before committing file changes
- **Session logging** — Every project should have a `_SESSION_LOG.md`. Update after every session.
- **Single source of truth** — Don't duplicate content across files. Link to the canonical source.
- **Check before guessing** — If unsure about any detail, search project files before answering.
- **Save compaction summaries** — When a session starts with a compaction summary, immediately write it to `<project>/compactions/session-XX-compaction.md` BEFORE doing any other work.

---

## Session Log Pruning

When a project's `_SESSION_LOG.md` exceeds **10 session entries**, prune it:

1. Move all but the most recent 10 sessions to `_SESSION_ARCHIVE.md`
2. Add a note at the top of the archive: `> Older sessions moved here from _SESSION_LOG.md.`
3. If `_SESSION_ARCHIVE.md` already exists, append (don't overwrite)
4. Claude reads `_SESSION_LOG.md` at startup, **not** the archive

---

## New Project Setup — Templates

When a new project is identified, create ALL of the following:

### 1. Vault File

Create in `~/.claude/memory-vault/[project-name].md`:

```markdown
# [Project Name] — Claude Memory

## Project Overview
- **What:** [Brief description]
- **Vault/Working Directory:** [Path]
- **Tech Stack:** [If applicable]

## Session Start Protocol
1. Read this vault file
2. Read `_SESSION_LOG.md` in the working directory
3. Check for `.state` file — if exists, resume from checkpoint
4. Check Active Context below for current focus

## Project Rules
- [Project-specific rules and constraints]

## Key Files
| Purpose | File |
|---------|------|
| Session log | `_SESSION_LOG.md` |

## Active Context
- **Status:** [Current status]
- **Current Phase:** [What phase]
- **Next Step:** [What to do next]

## Working Notes
- [Lessons learned during this project]
```

### 2. Project-Scoped MEMORY.md

Create at `~/.claude/projects/-[path-with-dashes]/memory/MEMORY.md`:

The directory path uses the working directory with `/` replaced by `-` and prefixed with `-`. Example:
- Working dir `/Users/you/my-project/` → `-Users-you-my-project`

```markdown
# Claude Memory — Project Router

> **Memory lives in the vault.** This file is just the entry point.

## Session Start Protocol

1. **Read the master index:** `~/.claude/memory-vault/_MASTER.md`
2. **Identify the project** from the working directory
3. **If existing project** → Read vault file, then `_SESSION_LOG.md`
4. **If new project** → Create vault file using template in `_MASTER.md`, add to registry
5. **Follow the project's own session start protocol**

## This Project

**Working directory `[path]` = [Project Name] project.**
→ Read vault: `~/.claude/memory-vault/[project-name].md`
→ Read session log: `[path]/_SESSION_LOG.md`

## Global Rules (All Projects)

- **ASK BEFORE WRITING** — Discuss and confirm before committing file changes
- **Check before guessing** — Search files before answering uncertain questions
- **Session logging** — Update project session logs after every session
- **Single source of truth** — Link, don't duplicate
```

### 3. Session Log

Create at `[project-dir]/_SESSION_LOG.md`:

```markdown
---
title: "Session Log"
aliases: [session-log, sessions]
type: moc
scope: sessions
status: active
tags: [type/moc, status/active, ai/volatile]
updated: [today's date]
summary: >
  Session index. One-liner per session, detail lives in compaction files.
related:
  - "[[_INDEX]]"
---

# Session Log

| Session | Focus | Key Decisions | Detail |
|---------|-------|---------------|--------|
| | | | |
```

### 4. Registry Entry

Add the project to the Project Registry table in this file.
