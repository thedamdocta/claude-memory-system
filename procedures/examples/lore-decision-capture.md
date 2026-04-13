---
title: "Capture a Confirmed Lore Decision"
type: procedure
status: active
version: 1
created: 2026-04-13
updated: 2026-04-13
project: my-project
trigger: >
  When the user confirms a lore decision, canon lock, retcon, or any
  creative ruling that should persist across sessions.
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  Write confirmed lore decisions to canonical vault docs immediately,
  never batching or deferring to session end.
---

# Capture a Confirmed Lore Decision

## When to Use
the user says something is "canon", "confirmed", "locked", "decided", or otherwise
ratifies a creative choice. Also when the user corrects existing lore (retcon).

## Steps
1. Identify the canonical vault doc where this decision belongs (character file, lore doc, faction doc, etc.)
2. Write the decision to that doc immediately -- do not wait
3. Add wikilinks to related entities ([[Character Name]], [[Faction]], etc.)
4. Update any cross-references in other docs that are affected
5. If it is a retcon, mark the old information clearly (strikethrough or "Previously: X, now: Y")
6. Write a fact to the memory index via the next bootstrap or inline note

## Pitfalls
- NEVER batch decisions to write later. Context can hit limits at any time. If you haven't written it, it is at risk.
- NEVER accumulate decisions in conversation and plan to write them all at session end.
- Ask the user which doc it belongs in if unclear -- do not guess and scatter lore across wrong files.
- Check frontmatter status before editing (some docs may be archived or deprecated).

## Verification
- The decision appears in the canonical vault doc with correct wikilinks.
- Grep for the key term confirms it is written and findable.
- Related docs are updated if the decision affects them.

## Known Issues
(None yet)
