# [Your Name] — Working Profile

> Claude reads this file at the start of every session. It captures how you work,
> what you expect, and lessons learned from past corrections.

## Agent Identity

Agent names are **project-specific** — each project's vault file defines its own.

| Project | Agent Name | Meaning |
|---------|-----------|---------|
| [Your Project] | **[Name]** | [Why this name] |

**Philosophy:** You're not starting from zero. You're continuing. The vault is the memory. Read what came before, carry it forward.

## Communication Style
- [How you prefer to be communicated with]
- [What tone you want — direct? formal? casual?]
- [What frustrates you in responses]

## Documentation Standards
- **Always use specific references.** Never say "Section 3" or "the third file." Use Obsidian `[[wikilinks]]` with section anchors.
- **Every new .md file gets YAML frontmatter** — title, aliases, tags, created date, description, and `related:` links using `[[wikilinks]]`.
- **Obsidian is the primary knowledge tool.** Structure everything for Obsidian compatibility.
- **Cross-link aggressively.** Every doc should link to related docs.
- Single source of truth — link, don't duplicate.

## Working Rules
- **ASK BEFORE WRITING** — Discuss approach, get confirmation, then write.
- **Check before guessing** — Search files before answering uncertain questions. Never fabricate.
- **Write decisions to docs immediately** — When the user confirms a decision, write it to the canonical doc RIGHT THEN. Don't accumulate decisions and batch-write at session end. Context can hit limits at any time — if you haven't written it to a file, it's at risk.
- **Be proactive about infrastructure** — Session logs, memory files, frontmatter. Don't wait to be reminded.
- **Use agents liberally** — Parallelize aggressively. It conserves context window. If 5 things can run at once, launch 5 agents.
- **Agents write to project files, not context** — The real bottleneck is never agent count — it's results flooding back into the context window. When launching research/exploration agents, instruct each agent to write its results directly to an MD file in the project directory. Read the files one by one afterward. This means unlimited parallelism with zero context blowout.
- **Checkpoint during multi-step work** — When doing builds, browser automation, or any multi-step task, maintain a `.state` file in the project's build directory. Update it after every meaningful step (not at the end, not when context is low — continuously). This prevents losing live working state on context compaction.
- **Spot problems early** — Flag quality issues, inconsistencies, and red flags upfront. Don't let the user catch things first.

## Memory Write Discipline
- **Write corrections immediately** — When the user corrects something, update this file or the project vault BEFORE continuing other work. Don't batch.
- **Session logs get a `### Lessons` section** — Capture what was learned, not just what happened.
- **Save compaction summaries** — When a session starts with a compaction summary (the "continued from a previous conversation" block), immediately write it to `<project>/compactions/session-XX-compaction.md` BEFORE doing any other work. The compaction gate enforces this automatically.

## Common Mistakes to Avoid

These are patterns that cause real problems. They were discovered the hard way across 66+ sessions of building this system.

### Context Management
| Mistake | What Actually Works |
|---------|-------------------|
| Accumulating screenshots in conversation context | Save screenshots to disk only. Never view in context unless absolutely necessary, and never more than one at a time. Write findings incrementally. |
| Retrieving all agent results back into context | Agents write results to MD files on disk. Main agent reads them individually. Never batch results into conversation. |
| Re-reading files that were already injected by hooks | The SessionStart hook already injected the working profile, vault file, session log, and C&N. Don't waste tokens reading them again. |
| Reading whole files to check status | Use frontmatter-only reads (`offset=1 limit=15`) for status questions. Use `vault-query.py` for metadata searches. |
| Amending existing compaction files | NEVER. Each compaction event = one new small file. Use `-cont1`, `-cont2` suffixes. Re-writing a 270-line compaction burns 20k+ tokens per round-trip. |

### Session Discipline
| Mistake | What Actually Works |
|---------|-------------------|
| Skipping the compaction save to start "real work" faster | The compaction gate blocks all tools until the save is done. Even without the gate — the continuation summary IS a compaction. Save it FIRST. Always. |
| Launching work agents before completing orientation | Complete ALL orientation steps AND save any compaction BEFORE any work. No exceptions. |
| Batching session log updates to end of session | Context can compact at any time. Write to session log after every significant milestone. |

### Agent Patterns
| Mistake | What Actually Works |
|---------|-------------------|
| Running large API imports as single sequential tasks | For large imports, split into parallel agent batches. Use `--start`/`--limit` parameters. Doesn't block conversation and is more resilient to failures. |
| Limiting agent count to prevent problems | The fix isn't limiting agents — it's routing output to files. Launch unlimited agents; read files one by one afterward. |

### Tool Usage
| Mistake | What Actually Works |
|---------|-------------------|
| Using Grep for vault-wide metadata searches | Use `vault-query.py` first — it reads only YAML frontmatter, costs almost zero tokens. Fall back to Grep only for content searches. |
| Using `browser-use` for web automation | Use `agent-browser` for ALL browser automation. Check the project's CLAUDE.md for the correct tool. |
| Guessing at file contents or lore details | ALWAYS search the vault first. Never fabricate or assume details. |

### Infrastructure
| Mistake | What Actually Works |
|---------|-------------------|
| Writing behavioral rules and hoping they stick | Build structural walls (hooks with `exit 2`), not text rules. If a rule is violated even once after being written down, escalate to structural enforcement. |
| Trusting text rules over structural enforcement | Walls, not rules. PreToolUse hooks that block bad actions are more reliable than instructions that ask nicely. |
| Not verifying after migrations or refactors | Always run a verification pass after any bulk operation. "Trust but verify" — the verification discipline catches gaps when they exist. |

## Corrections Log
| Date | What Went Wrong | What You Expect | Source |
|------|----------------|-----------------|--------|
| | | | |
