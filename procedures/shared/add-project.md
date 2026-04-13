---
title: "Add Project Procedure"
type: procedure
version: 1
updated: 2026-04-13
summary: >
  Scaffold a new project with all required artifacts using add-project.sh.
  Use when session-memory-inject.sh can't find a vault file for the current working directory.
---

# Add Project

## When to Use
- SessionStart hook reports "no vault file found for this directory"
- User says "set up a new project" or "add this as a project"
- You detect an unregistered working directory in `_MASTER.md`

## Steps

1. **Gather info** — You need at minimum:
   - Project name (ask the user if unclear)
   - Working directory (default: current directory)
   - Optional: agent name, one-line description

2. **Run the script:**
   ```bash
   ~/.claude/scripts/add-project.sh \
     --name "Project Name" \
     --path /path/to/project \
     --description "One-line description" \
     --quiet
   ```
   Use `--quiet` to skip interactive prompts (you've already confirmed with the user).
   Use `--agent-name "Name"` if the user wants an agent identity for this project.

3. **Verify artifacts exist:**
   - `~/.claude/memory-vault/<project-name>.md` — vault file
   - `<project-path>/_SESSION_LOG.md` — session log
   - `~/.claude/projects/-<encoded-path>/memory/MEMORY.md` — project memory
   - Check `_MASTER.md` registry has new row

4. **Customize the vault file** — Edit `~/.claude/memory-vault/<project-name>.md`:
   - Fill in Project Overview with real details
   - Add project-specific rules
   - Add Key Files table entries
   - Update Active Context

5. **Optionally create project-specific hooks** — Copy from `~/.claude/procedures/examples/`:
   - `lore-decision-capture.md` → for creative/story projects
   - `vault-search-for-lore.md` → for vault-heavy projects
   - Rename with project prefix, update the project detection path inside

## Path Encoding Reference
Claude's project directory uses this encoding:
- `/Users/devon/my-project` → `-Users-devon-my-project`
- Rule: replace all `/` with `-`, prepend `-`

The script handles this automatically. You should never need to encode paths manually.

## Known Issues
- On Linux, `sed -i` behaves differently than macOS. The script tries both `sed -i ''` (macOS) and `sed -i` (Linux) with fallback.
- If `_MASTER.md` registry table has no empty rows AND no existing table rows, the entry appends to end of file. Manually reposition if needed.
