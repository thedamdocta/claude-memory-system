---
title: "Session Log — Write Protocol"
type: procedure
status: active
version: 1
created: 2026-05-08
updated: 2026-05-08
project: shared
trigger: >
  When writing a compaction file, ending a session, or needing to add
  a session entry to _SESSION_LOG.md. Also when session log feels out
  of date or a session had significant decisions worth capturing.
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  Protocol for writing session log entries correctly and consistently.
  Covers manual --add mode (preferred), auto mode, rotation behavior,
  and when to update Current State. Universal across all projects.
---

# Session Log — Write Protocol

## When to Use
- After writing a compaction file
- After any session with significant decisions worth capturing
- When the "Current State" section is out of date
- When context monitor warns session is running long

---

## Preferred Method — Manual Add Mode

```bash
python3 /Users/devon/.claude/scripts/session-log-rotate.py \
  --project <PROJECT_ROOT> \
  --add \
  --session "XX" \
  --focus "One sentence describing the main work of the session." \
  --decisions "**Decision 1** — why it matters. **Decision 2** — why it matters." \
  --detail "[[Relevant File]] [[session-XX-compaction]]"
```

Replace `<PROJECT_ROOT>` with the project directory path (e.g. `/Users/devon/misphitz`).

### Good Focus text:
- One sentence, plain English, covers the main arc
- Not a bullet list

### Good Decisions text:
- Bold the decision: `**Decision text**`
- Follow with ` — short explanation`
- 3–6 decisions max — only what should survive as reminders

---

## Auto Mode (requires formatted compaction file)

```bash
python3 /Users/devon/.claude/scripts/session-log-rotate.py \
  --project <PROJECT_ROOT> \
  --auto \
  --compaction-file <PATH_TO_COMPACTION>
```

Auto mode extracts from the compaction file's frontmatter (`summary`, `related`) and `## Conversations & Nuance` section. If those are sparse, use `--add` mode instead.

---

## Rotation Behavior

- Table holds 10 rows, **newest at the TOP**
- New entries insert at position 0
- When table exceeds 10 rows, the **oldest entry (bottom)** rotates to `_SESSION_ARCHIVE.md`
- Archive is append-only

Verify order after running:
```bash
grep '^| [0-9]' _SESSION_LOG.md | awk -F'|' '{print $2}'
```
Newest session should appear first.

---

## Updating Current State

The `## Current State` section is NOT auto-updated. Update manually when:
- Episode/feature status changes
- Active work focus shifts
- Major decisions complete

Read `_SESSION_LOG.md`, find `## Current State`, edit in place.

---

## Pitfalls
- Sparse compaction files produce empty auto entries — use `--add` instead
- Never amend existing compaction files — one new file per compaction event
- Check ordering after running — newest must be at top
