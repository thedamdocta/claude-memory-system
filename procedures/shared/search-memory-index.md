---
title: "Search the Memory Index Effectively"
type: procedure
status: active
version: 1
created: 2026-04-13
updated: 2026-04-13
project: shared
trigger: >
  When searching the memory index for answers to factual questions,
  lore lookups, or recalling past decisions/discussions. Also when
  about to say "I don't remember," "the compaction took that," or
  "I don't have that context."
tags: [type/procedure]
success_count: 0
failure_count: 0
last_used: null
summary: >
  How to search the memory index effectively. MANDATORY before claiming
  lost context — search first, claim ignorance only after finding nothing.
---

# Search the Memory Index Effectively

## When to Use
When answering a question that may have been discussed, decided, or recorded in
a previous session. When Devon asks "do you remember" or references past work.
**Critically: before saying "I don't remember," "the compaction took that," or
"I don't have context" — search first. Claiming ignorance before searching is the failure mode this procedure exists to prevent.**

## Steps
1. Start with simple keywords from the question (2-3 core terms)
2. Run `memory-query.py --query "<terms>" --limit 5 --format compact`
3. If zero results, try synonyms or alternate spellings (codenames, lore aliases)
4. If still zero, try OR fallback: split terms and search each individually
5. If memory misses entirely, fall back to Grep/Glob on vault docs
6. Synthesize across multiple results -- don't just return the first hit
7. Commit to an answer when you have enough data. Do not hedge.

## Pitfalls
- Do not hedge when you have sufficient data from the index. If 3+ results agree, commit to the answer.
- Do not re-search for things already answered in the current conversation.
- Do not read whole vault files when a targeted search would suffice.
- Remember that synonym expansion is automatic for codenames (Gunraven/Tori, etc.) but not for general terms.

## Verification
- The answer should cite which facts or sessions it came from.
- If confidence is low (few results, low scores), say so explicitly rather than guessing.

## Known Issues
(None yet)
