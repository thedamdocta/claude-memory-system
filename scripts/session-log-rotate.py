#!/usr/bin/env python3
"""
session-log-rotate.py — Manages session log rotation for the Obsidian vault.

Parses the markdown table in _SESSION_LOG.md, inserts new session entries,
and rotates old entries to _SESSION_ARCHIVE.md when the table exceeds 10 rows.

Three modes:
  --add         Manual mode: supply session, focus, decisions, detail
  --auto        Auto mode: extract fields from a compaction file
  --rotate-only Just check count and rotate if needed
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

# Add scripts dir to path for vault_lib import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_lib import parse_frontmatter


# ---------------------------------------------------------------------------
# Lock helpers
# ---------------------------------------------------------------------------

LOCK_FILENAME = ".session-log-rotate.lock"


def acquire_lock(project_dir: str) -> str:
    """Create a lock file. Returns lock path. Exits on conflict."""
    lock_path = os.path.join(project_dir, LOCK_FILENAME)
    if os.path.exists(lock_path):
        # Check staleness (> 60 seconds = stale)
        try:
            age = time.time() - os.path.getmtime(lock_path)
            if age > 60:
                os.remove(lock_path)
            else:
                print(f"Error: lock file exists at {lock_path} (age {age:.0f}s). "
                      "Another rotation may be running.", file=sys.stderr)
                sys.exit(1)
        except OSError:
            pass
    try:
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        print(f"Error: cannot create lock file: {e}", file=sys.stderr)
        sys.exit(1)
    return lock_path


def release_lock(lock_path: str) -> None:
    """Remove the lock file."""
    try:
        os.remove(lock_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Session log parsing
# ---------------------------------------------------------------------------

TABLE_HEADER = "| Session | Focus | Key Lore Decisions | Detail |"
TABLE_SEP_PATTERN = re.compile(r"^\|[-\s|]+\|$")


def parse_table_row(line: str) -> list[str]:
    """
    Parse a markdown table row into cell values.
    Splits on ' | ' (space-pipe-space) to handle pipes inside cell content.
    Leading and trailing pipes are stripped first.
    """
    stripped = line.strip()
    # Remove leading pipe
    if stripped.startswith("|"):
        stripped = stripped[1:]
    # Remove trailing pipe
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    # Split on ' | ' — the column delimiter with spaces
    cells = stripped.split(" | ")
    # Trim whitespace from each cell
    return [c.strip() for c in cells]


def format_table_row(cells: list[str]) -> str:
    """Format cells back into a markdown table row."""
    return "| " + " | ".join(cells) + " |"


def parse_session_log(content: str) -> dict:
    """
    Parse _SESSION_LOG.md into structured components.

    Returns dict with:
        before_table: str  — everything before the table header line
        header_line: str   — the header line
        sep_line: str      — the separator line
        rows: list[list[str]]  — parsed data rows (each row = list of 4 cells)
        raw_rows: list[str]    — original raw lines for each data row
        after_table: str   — everything after the table (--- separator + Current State)
    """
    lines = content.split("\n")
    result = {
        "before_table": "",
        "header_line": "",
        "sep_line": "",
        "rows": [],
        "raw_rows": [],
        "after_table": "",
    }

    # Find the header line
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == TABLE_HEADER:
            header_idx = i
            break

    if header_idx is None:
        return None

    # Everything before the header
    result["before_table"] = "\n".join(lines[:header_idx])
    result["header_line"] = lines[header_idx]

    # Next line should be separator
    sep_idx = header_idx + 1
    if sep_idx < len(lines) and TABLE_SEP_PATTERN.match(lines[sep_idx]):
        result["sep_line"] = lines[sep_idx]
    else:
        return None

    # Parse data rows until we hit a non-table line (e.g., '---' separator or empty)
    row_start = sep_idx + 1
    i = row_start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Table rows start with |
        if stripped.startswith("|") and not TABLE_SEP_PATTERN.match(stripped):
            cells = parse_table_row(line)
            result["rows"].append(cells)
            result["raw_rows"].append(line)
            i += 1
        else:
            break

    # Everything from here on is after_table
    result["after_table"] = "\n".join(lines[i:])

    return result


def reassemble_session_log(parsed: dict) -> str:
    """Reassemble the session log from parsed components."""
    parts = [
        parsed["before_table"],
        parsed["header_line"],
        parsed["sep_line"],
    ]
    for row in parsed["rows"]:
        parts.append(format_table_row(row))
    parts.append(parsed["after_table"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Auto mode helpers
# ---------------------------------------------------------------------------

def extract_session_id_from_filename(filepath: str) -> tuple[str, bool]:
    """
    Extract session ID from compaction filename.

    Pattern: session-{ID}-compaction.md or session-{ID}-cont{N}-compaction.md
    Returns (base_session_id, is_continuation)

    Examples:
        session-66m-compaction.md          -> ("66m", False)
        session-66m-cont29-compaction.md   -> ("66m", True)
        session-66l-cont6-compaction.md    -> ("66l", True)
    """
    basename = os.path.basename(filepath)
    # Try continuation pattern first
    m = re.match(r"^session-(.+?)-cont\d+-compaction\.md$", basename)
    if m:
        return (m.group(1), True)
    # Try base pattern
    m = re.match(r"^session-(.+?)-compaction\.md$", basename)
    if m:
        return (m.group(1), False)
    return (None, False)


def extract_bold_texts(text: str) -> list[str]:
    """Extract all **bold** text fragments from a string."""
    return re.findall(r"\*\*([^*]+)\*\*", text)


def extract_auto_fields(compaction_path: str) -> dict:
    """
    Extract session entry fields from a compaction file.

    Returns dict with: session_id, focus, decisions, detail, is_continuation
    """
    session_id, is_cont = extract_session_id_from_filename(compaction_path)
    if session_id is None:
        print(f"Error: cannot parse session ID from filename: {compaction_path}",
              file=sys.stderr)
        sys.exit(1)

    fm = parse_frontmatter(compaction_path)

    # Focus: from frontmatter 'summary' field
    focus = fm.get("summary", "")
    if isinstance(focus, list):
        focus = " ".join(focus)

    # Detail: from frontmatter 'related' entries that look like [[...]]
    related = fm.get("related", [])
    if isinstance(related, str):
        related = [related]
    wikilinks = []
    for item in related:
        # Extract wikilinks from the item
        links = re.findall(r"\[\[[^\]]+\]\]", item)
        wikilinks.extend(links)
    detail = " ".join(wikilinks)

    # Decisions: bold text from ## Conversations & Nuance section
    decisions = ""
    try:
        with open(compaction_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Find the Conversations & Nuance section
        cn_match = re.search(
            r"## Conversations & Nuance\s*\n(.*?)(?=\n## |\Z)",
            content,
            re.DOTALL,
        )
        if cn_match:
            cn_text = cn_match.group(1)
            bolds = extract_bold_texts(cn_text)
            if bolds:
                decisions = " ".join(f"**{b}**" for b in bolds)
    except (OSError, UnicodeDecodeError):
        pass

    return {
        "session_id": session_id,
        "focus": focus,
        "decisions": decisions,
        "detail": detail,
        "is_continuation": is_cont,
    }


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def find_row_by_session(rows: list[list[str]], session_id: str) -> int:
    """Find the index of a row with the exact session ID. Returns -1 if not found."""
    for i, row in enumerate(rows):
        if row and row[0].strip() == session_id:
            return i
    return -1


def append_to_archive(project_dir: str, row: list[str]) -> None:
    """Blind-append a table row to _SESSION_ARCHIVE.md."""
    archive_path = os.path.join(project_dir, "_SESSION_ARCHIVE.md")
    line = format_table_row(row) + "\n"
    try:
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        print(f"Warning: failed to append to archive: {e}", file=sys.stderr)


def rotate_oldest(parsed: dict, project_dir: str) -> str:
    """
    If table has > 10 rows, rotate the oldest (last) row to the archive.
    Table is newest-first: newest at index 0, oldest at end.
    Returns the session ID of the rotated row, or None.
    """
    if len(parsed["rows"]) <= 10:
        return None

    oldest = parsed["rows"].pop(-1)
    if parsed["raw_rows"]:
        parsed["raw_rows"].pop(-1)

    append_to_archive(project_dir, oldest)
    return oldest[0].strip() if oldest else None


def do_add(parsed: dict, session_id: str, focus: str, decisions: str,
           detail: str) -> str:
    """Insert a new row at the top (newest-first ordering). Returns status message."""
    new_row = [session_id, focus, decisions, detail]
    parsed["rows"].insert(0, new_row)
    return f"Added session {session_id}"


def do_auto(parsed: dict, fields: dict) -> str:
    """
    Auto-insert or update a row based on compaction file fields.
    Returns status message.
    """
    session_id = fields["session_id"]
    existing_idx = find_row_by_session(parsed["rows"], session_id)

    if existing_idx >= 0:
        # Dedup: only append new compaction links to Detail column
        existing_row = parsed["rows"][existing_idx]
        existing_detail = existing_row[3] if len(existing_row) > 3 else ""
        new_links = fields["detail"]
        if new_links:
            # Extract individual wikilinks from both existing and new
            existing_links = set(re.findall(r"\[\[[^\]]+\]\]", existing_detail))
            new_link_list = re.findall(r"\[\[[^\]]+\]\]", new_links)
            added = []
            for link in new_link_list:
                if link not in existing_links:
                    added.append(link)
            if added:
                if existing_detail:
                    existing_row[3] = existing_detail + " " + " ".join(added)
                else:
                    existing_row[3] = " ".join(added)
                # Also update raw_rows to match
                if existing_idx < len(parsed["raw_rows"]):
                    parsed["raw_rows"][existing_idx] = format_table_row(existing_row)
                return f"Updated detail for session {session_id}"
            else:
                return f"Updated detail for session {session_id} (no new links)"
        return f"Updated detail for session {session_id} (no new links)"
    else:
        # New row — insert at top (newest-first ordering)
        new_row = [
            session_id,
            fields["focus"],
            fields["decisions"],
            fields["detail"],
        ]
        parsed["rows"].insert(0, new_row)
        return f"Added session {session_id}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Manage session log rotation for Obsidian vault."
    )
    parser.add_argument(
        "--project", required=True,
        help="Path to the project vault directory containing _SESSION_LOG.md"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--add", action="store_true", help="Manual add mode")
    mode.add_argument("--auto", action="store_true", help="Auto mode from compaction file")
    mode.add_argument("--rotate-only", action="store_true", help="Only rotate if needed")

    # Manual mode args
    parser.add_argument("--session", help="Session ID (manual mode)")
    parser.add_argument("--focus", help="Focus text (manual mode)")
    parser.add_argument("--decisions", default="", help="Key lore decisions (manual mode)")
    parser.add_argument("--detail", default="", help="Detail / links (manual mode)")

    # Auto mode args
    parser.add_argument("--compaction-file", help="Path to compaction file (auto mode)")

    args = parser.parse_args()

    project_dir = os.path.abspath(args.project)
    log_path = os.path.join(project_dir, "_SESSION_LOG.md")

    # Validate inputs
    if args.add:
        if not args.session or not args.focus:
            print("Error: --add requires --session and --focus", file=sys.stderr)
            sys.exit(1)
    if args.auto:
        if not args.compaction_file:
            print("Error: --auto requires --compaction-file", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(args.compaction_file):
            print(f"Error: compaction file not found: {args.compaction_file}",
                  file=sys.stderr)
            sys.exit(1)

    # Check session log exists
    if not os.path.exists(log_path):
        print(f"Error: _SESSION_LOG.md not found at {log_path}", file=sys.stderr)
        sys.exit(1)

    # Acquire lock
    lock_path = acquire_lock(project_dir)

    try:
        # Read and parse session log
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()

        parsed = parse_session_log(content)
        if parsed is None:
            print("Error: cannot parse table in _SESSION_LOG.md", file=sys.stderr)
            sys.exit(1)

        status_msg = ""

        # Execute mode
        if args.add:
            status_msg = do_add(
                parsed, args.session, args.focus,
                args.decisions, args.detail
            )
        elif args.auto:
            fields = extract_auto_fields(os.path.abspath(args.compaction_file))
            status_msg = do_auto(parsed, fields)
        elif args.rotate_only:
            status_msg = "Rotate check"

        # Rotate if needed
        rotated_id = rotate_oldest(parsed, project_dir)
        row_count = len(parsed["rows"])

        if rotated_id:
            if status_msg:
                status_msg += f", rotated session {rotated_id} to archive"
            else:
                status_msg = f"Rotated session {rotated_id} to archive"

        # Write updated session log
        new_content = reassemble_session_log(parsed)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Final status
        print(f"{status_msg}, {row_count} rows in table")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        release_lock(lock_path)
        sys.exit(1)

    release_lock(lock_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
