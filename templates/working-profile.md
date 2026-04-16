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
| Using Grep for vault-wide metadata searches | Use `vault-query.py` first (`--query` for name/title/aliases, `--type`/`--tag`/`--status`/`--related` for metadata) — it reads only YAML frontmatter, costs almost zero tokens. Fall back to Grep only for content searches. |
| Using `browser-use` for web automation | Use `agent-browser` for ALL browser automation. Check the project's CLAUDE.md for the correct tool. |
| Guessing at file contents or lore details | ALWAYS search the vault first. Never fabricate or assume details. |

### Infrastructure
| Mistake | What Actually Works |
|---------|-------------------|
| Writing behavioral rules and hoping they stick | Build structural walls (hooks with `exit 2`), not text rules. If a rule is violated even once after being written down, escalate to structural enforcement. |
| Trusting text rules over structural enforcement | Walls, not rules. PreToolUse hooks that block bad actions are more reliable than instructions that ask nicely. |
| Not verifying after migrations or refactors | Always run a verification pass after any bulk operation. "Trust but verify" — the verification discipline catches gaps when they exist. |

## Corrections Log

These corrections were discovered across 66+ sessions of real production use. They're pre-loaded so you don't repeat them. Add your own as you work with the user.

| # | Category | What Went Wrong | What Actually Works |
|---|----------|----------------|---------------------|
| 1 | Onboarding | Forgot to create session log for a new project | Every project needs all artifacts automatically (vault file, MEMORY.md, session log, registry entry). Use `add-project.sh` — never scaffold by hand. |
| 2 | Onboarding | Didn't create project-scoped MEMORY.md | Without MEMORY.md, the project has no memory persistence. It's the most-missed artifact. `add-project.sh` handles this. |
| 3 | Onboarding | Duplicated active projects list in every MEMORY.md | Point to `_MASTER.md`, don't duplicate data that grows. Single source of truth. |
| 4 | References | Used vague "Section 3" references in analysis docs | Use specific `[[wikilinks]]` with section anchors, always. Never "the third file" or "Section 3". |
| 5 | Context | Launched 10 research agents; retrieving all results blew out the context window | Agents write results to MD files on disk, not back to context. Launch unlimited agents; read files one by one afterward. |
| 6 | Context | Applied "max 3 agents" rule when the real problem was results flooding context | The fix isn't limiting agent count — it's routing output to files. Unlimited parallelism, zero context blowout. |
| 7 | Context | Accumulated 36 screenshots in conversation context; API crashed (image size limit) | Save screenshots to disk only — don't view in context unless needed. One at a time max. Write findings incrementally. |
| 8 | Context | **REPEAT:** Accumulated screenshots in context during CSS work — hit 100% context and killed the session | This rule was ALREADY DOCUMENTED. Screenshots go to DISK ONLY. Never view in context unless absolutely necessary. Non-negotiable. |
| 9 | Context | Re-reading files that were already injected by hooks | The SessionStart hook already injected working profile, vault file, session log, and C&N. Don't waste tokens reading them again. |
| 10 | Context | Reading whole files to check status | Use frontmatter-only reads (`offset=1 limit=15`) for status checks. Use `vault-query.py` for metadata searches. |
| 11 | Context | Amending existing compaction files | NEVER. Each compaction event = one new small file. Use `-cont1`, `-cont2` suffixes. |
| 12 | Session | Skipped compaction save to start "real work" faster | The compaction gate blocks all tools until the save is done. The continuation summary IS a compaction. Save it FIRST. Always. |
| 13 | Session | **REPEAT:** Skipped compaction save on context continuation. Same mistake. | The continuation summary IS a compaction. Save it FIRST. Before any task. No exceptions. |
| 14 | Session | Launched work agents before completing Session Start Protocol | Complete ALL orientation steps AND save any compaction BEFORE any work. No exceptions. |
| 15 | Session | Batching session log updates to end of session | Context can compact at any time. Write to session log after every significant milestone. |
| 16 | Session | Didn't checkpoint live state during multi-step work; lost progress on compaction | Maintain `.state` file continuously during multi-step tasks — update after every step, not reactively. |
| 17 | Agents | Ran 3,498-contact API import as single sequential background task (~35 min blocking) | For large imports, split into parallel agent batches. Use `--start`/`--limit` parameters. Doesn't block conversation and is more resilient to failures. |
| 18 | Tools | Using Grep for vault-wide metadata searches | Use `vault-query.py` first (`--query` for name/title/aliases, `--type`/`--tag`/`--status`/`--related` for metadata) — reads only YAML frontmatter, costs almost zero tokens. Fall back to Grep only for content searches. |
| 19 | Tools | Guessing at file contents or lore details | ALWAYS search the vault first. Never fabricate or assume details. |
| 20 | Infra | Writing behavioral rules and hoping they stick | Build structural walls (hooks with `exit 2`), not text rules. If a rule is violated even once after being written down, escalate to structural enforcement. |
| 21 | Infra | Not verifying after migrations or refactors | Always run a verification pass after any bulk operation. "Trust but verify." |
| 22 | Design | Didn't measure merge targets before a migration — missed 3 oversize files | Drift audits need to measure BOTH drift sources AND merge targets. |
| 23 | Design | Proposed installing an external MCP tool when we could build the bridge ourselves | Builder over consumer — if the tool is simple enough to build in one session, build it. |

**Add your own corrections below as they happen. Include the date and source.**

| Date | What Went Wrong | What You Expect | Source |
|------|----------------|-----------------|--------|
| | | | |
