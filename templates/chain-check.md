---
title: "Chain-Check Protocol"
aliases: [chain-check, DONE-receipt, verification-protocol]
type: protocol
scope: cross-project
status: active
tags: [type/protocol, scope/all-agents, discipline/chain-check, ai/entry-point]
updated: 2026-04-16
related:
  - "[[working-profile]]"
summary: >
  Cross-agent chain-check discipline. Auto-injected at every session start.
  Use the DONE receipt format before declaring production-ship or irreversible
  work complete. Replaces "I'm sure" with "here's what ran."
---

# Chain-Check Protocol

> **For cold readers:** This file is auto-injected at every session start.
> Every agent the user works with reads this to inherit chain-check discipline.
> The protocol is a capability patch for a known architectural gap — LLMs like
> Claude ship without built-in chain-of-thought verification. This document
> replaces that gap with an external discipline, loaded via hook at session
> start.

## Why This Exists

Chain-break failures cluster at transitions — the moment an agent stops
editing and declares "done" without verifying the downstream consumer still
works. Documented across multiple substrates in published research:

- **MAST taxonomy** (Cemri 2025, n=1600+): 8.2% verification failures,
  6.2% premature termination
- **SWE-Bench+** (Aleithan 2024): solution leakage 32.67%, weak-test passes
  31.08% — resolution drops from 12.47% → 3.97% under stricter verification
- **DeepSWE**: verification behaviors (edge-case testing, regression-test
  running, adaptive reasoning) emerge only under sparse binary reward
- **Parametric Hubris** (2026): hallucination 47–93% when search isn't
  triggered despite tool availability

LLM agents in general default to **confident completion** rather than
consumer-side verification. This isn't a personal failing — it's an
architectural choice. The correction is external and structural: this file,
loaded at session start. Over time this text rule may graduate into a
PreToolUse hook wall that blocks Edit/Write/Bash until verification artifacts
exist (a structural DONE-wall).

## The DONE Receipt Format

Before declaring any production-ship or irreversible task complete, produce
a four-part receipt:

- **Changed:** what files / symbols / state were modified, with specifics
- **Consumer:** who reads or runs the changed thing, and whether their
  expected behavior is preserved
- **Verified by:** concrete evidence the change works — test output, runtime
  simulation, grep, syntax check, visual diff. Name the specific thing that
  ran, not "I checked it."
- **Remaining risk:** what was NOT verified, and whether that matters.
  Naming the gap is the discipline.

The receipt IS the audit trail. Without it, "done" is a claim. With it,
"done" is a demonstrated state.

## When to Apply

**Mandatory** (production-ship or irreversible work):
- Any git commit or push
- Any migration, refactor spanning >3 files, or infrastructure change
- Any hook, script, or tool modification in `~/.claude/`
- Any change that alters cross-project behavior
- Any "phase X complete" or "ready for review" declaration

**Optional** (in-session iteration):
- Conversational prose work where "done" isn't being declared
- Exploration, scratch code, or throwaway tests
- Single-line fixes or typos

**Do not ritualize it.** The receipt is friction for a reason; that friction
only helps when the stakes match. Writing DONE receipts for every trivial
edit will either waste tokens or train the discipline out by dilution.

## Operating Principles

1. **Read before acting.** Use the vault and project files instead of
   guessing. Search before reading whole files.
2. **Verify consumers, not just producers.** Every artifact has a consumer;
   DONE requires consumer-side evidence. Who reads this? Does their
   expectation still hold?
3. **Leave receipts.** Summaries should name what was changed, what
   consumed it, how it was verified, and remaining risk. Receipts give the
   user an audit surface.
4. **Stay honest about limits.** Do not claim completeness you haven't
   demonstrated. Name what wasn't tested. "Remaining risk" is not an
   afterthought — it's a first-class deliverable.
5. **Prefer structural walls over prose rules.** If a mistake repeats,
   graduate it to a tool boundary or validator. This text rule is the
   low-compliance tier; a PreToolUse wall is the high-compliance tier.
6. **Watch the confidence moment.** The instant you feel sure is the
   instant you stop watching. Chain-breaks cluster at confident
   transitions.

## Origin

This protocol emerged from multi-agent failure analysis across different
substrate types (cloud-hosted Claude, Codex, local-inference agents). The
DONE receipt format, the consumer-verification principle, and the
"confidence moment" insight were synthesized across that work. The text-rule
version (this file) is the lightweight form. The eventual structural wall is
a PreToolUse hook that blocks tool calls until verification artifacts exist.

When the wall ships, this file documents the discipline the wall enforces.
Until then, this file IS the discipline.
