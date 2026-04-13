---
title: "Handle the Compaction Gate"
type: procedure
status: active
version: 1
created: 2026-04-13
updated: 2026-04-13
project: shared
trigger: >
  When the COMPACTION GATE ACTIVE warning appears at session start,
  indicating a previous session compacted and needs its summary saved.
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  Save a compaction continuation summary as ONE NEW small file before
  doing any other work. Never edit existing compaction files.
---

# Handle the Compaction Gate

## When to Use
The SessionStart hook injects a "COMPACTION GATE ACTIVE" warning. All Edit/Write/Bash/Task
tools are BLOCKED until the compaction summary is saved.

## Steps
1. Read the compaction summary from your conversation context (it is already there)
2. Determine the correct filename: `session-XX[-contN]-compaction.md`
   - Use the session ID from context
   - If a compaction file already exists for this session, use `-cont1`, `-cont2`, etc.
3. Write ONE NEW file to the project's `compactions/` directory
4. Include full vault-standard frontmatter (title, type, status, session, summary, etc.)
5. Keep it small -- only this compaction's continuation summary
6. The gate clears automatically after the Write succeeds

## Pitfalls
- NEVER Read-then-Write-amend an existing compaction file. Each compaction = one new file.
- NEVER Edit an existing compaction file. The gate structurally blocks Edit on compaction paths.
- NEVER merge multiple compactions into one file. Use -cont1, -cont2, -cont3 suffixes.
- Re-writing a 270-line compaction burns 20k+ tokens per round-trip. Small new files only.

## Verification
- The `.compaction-gate` marker file in the project root is automatically deleted.
- Subsequent tool calls (Edit, Write, Bash) are no longer blocked.
- `ls compactions/` shows the new file with correct naming.

## Known Issues
(None yet)
