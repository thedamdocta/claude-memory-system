---
title: "Search the Vault for Lore Answers"
type: procedure
status: active
version: 1
created: 2026-04-13
updated: 2026-04-13
project: my-project
trigger: >
  When answering a lore question, looking up character details, faction
  info, or any world-building fact from the MyProject vault.
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  Search memory index first, then Grep/Glob vault docs, then targeted
  Read -- never read whole files when frontmatter or search suffices.
---

# Search the Vault for Lore Answers

## When to Use
the user asks a lore question, or you need to reference character/faction/world details
to do creative work. Also when cross-referencing decisions across vault docs.

## Steps
1. Search memory index first: `memory-query.py --query "<terms>" --limit 5`
2. If memory has the answer with high confidence, use it directly
3. If memory is incomplete, use Grep to find which files mention the term
4. Use Glob to find candidate files by name pattern if Grep misses
5. Read only the specific files that matched -- use frontmatter-only reads
   (`offset=1 limit=15`) for status checks before committing to full reads
6. Synthesize across results. Cite the source file for each claim.

## Pitfalls
- NEVER read whole vault files as a first move. Search first, read targeted.
- NEVER guess at lore when the answer is in a file. Search before assuming.
- Use frontmatter-only reads for status questions (is this canon? what type is this doc?).
- Remember the _SEARCH_PLAYBOOK.md patterns: Glob for structure, Grep for content, Read for detail.

## Verification
- The answer cites specific vault files or memory facts.
- If multiple sources conflict, flag the conflict for the user rather than picking one silently.

## Known Issues
(None yet)
