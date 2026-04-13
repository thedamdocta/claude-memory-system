---
title: "Session Start Protocol"
type: procedure
status: active
version: 1
created: 2026-04-13
updated: 2026-04-13
project: shared
trigger: >
  When starting any new session or resuming after compaction.
  The SessionStart hook fires automatically.
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  Orient at session start using hook-injected context without
  re-reading files already in the system prompt.
---

# Session Start Protocol

## When to Use
Every session start. The SessionStart hook auto-injects working-profile.md, project vault file,
session log, and latest Conversations & Nuance section.

## Steps
1. If compaction gate is active: save compaction summary FIRST (see compaction-gate-protocol)
2. Read the injected context -- it is already in your system prompt. Do NOT re-read these files:
   - working-profile.md (working profile)
   - Project vault file (my-project.md, etc.)
   - Session log (_SESSION_LOG.md)
   - Latest compaction's C&N section
3. Identify the project from the working directory
4. Check if session log needs pruning (>10 active entries -> archive oldest)
5. Only NOW begin working on the user's request

## Pitfalls
- Do not call Read on files that are already injected. This wastes tokens and context.
- Do not skip the compaction save if the gate is active. Tools are blocked until you do it.
- Do not create session log entries proactively -- only update at session end.
- If the C&N section was skipped (bloated), only Read it if you actually need that context.

## Verification
- You can identify the current project and its state without any Read calls.
- The compaction gate (if active) has been cleared.
- Session log has 10 or fewer active entries.

## Known Issues
(None yet)
