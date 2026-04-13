#!/usr/bin/env python3
"""
vault-query.py — CLI for querying the MyProject vault by frontmatter.

Query vault files by type, tag, status, related links, and update date.
Returns markdown table (default) or TSV/JSON.

Usage:
    vault-query.py --type moc
    vault-query.py --tag character --status active
    vault-query.py --related "Yume" --with-summary
    vault-query.py --updated-since 2026-04-01 --format json
    vault-query.py --read-section "EP 01 - Be Careful Where You End Up.md" "Open Questions"
    vault-query.py --read-section "Laz - The Ender of Worlds.md" "Power" --with-summary
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Import from vault_lib in same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_lib import walk_vault, parse_frontmatter, DEFAULT_VAULT_ROOT


def resolve_file(filename: str, vault_root: str) -> str:
    """
    Resolve a file argument to an absolute path.

    Resolution order:
    1. Absolute path — use directly if it exists
    2. Relative path from vault root — check if it exists
    3. Filename only — search recursively under vault root

    Returns the resolved absolute path.
    Prints warnings to stderr for ambiguity, exits on failure.
    """
    # 1. Absolute path
    if os.path.isabs(filename):
        if os.path.isfile(filename):
            return filename
        print(f"ERROR: File not found: {filename}", file=sys.stderr)
        sys.exit(1)

    # 2. Relative path from vault root
    rel_candidate = os.path.join(vault_root, filename)
    if os.path.isfile(rel_candidate):
        return os.path.abspath(rel_candidate)

    # 3. Recursive filename search
    skip_dirs = {".git", "node_modules", ".claude", "__pycache__", ".venv", "venv"}
    basename = os.path.basename(filename)
    matches = []
    for dirpath, dirnames, filenames_list in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        if basename in filenames_list:
            matches.append(os.path.join(dirpath, basename))

    if not matches:
        print(f"ERROR: File not found in vault: {filename}", file=sys.stderr)
        sys.exit(1)

    if len(matches) > 1:
        print(f"WARNING: Multiple matches for '{filename}', using first:", file=sys.stderr)
        for m in matches:
            print(f"  {m}", file=sys.stderr)

    return matches[0]


def strip_markdown_formatting(text: str) -> str:
    """
    Strip markdown formatting from text for fuzzy heading comparison.

    Removes: bold (**), italic (*/_), wikilinks ([[...]]),
    inline code (`), links [text](url), HTML tags.
    """
    # Remove wikilinks: [[text]] -> text, [[target|display]] -> display
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
    # Remove markdown links: [text](url) -> text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Remove bold/italic markers
    text = re.sub(r'\*{1,3}', '', text)
    text = re.sub(r'_{1,3}', '', text)
    # Remove inline code
    text = re.sub(r'`', '', text)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_heading_line(line: str):
    """
    Parse a markdown heading line.

    Returns (level, raw_text, stripped_text) or None if not a heading.
    level: number of # characters (1-6)
    raw_text: the heading text as-is (without the # prefix)
    stripped_text: heading text with markdown formatting removed, lowercased
    """
    m = re.match(r'^(#{1,6})\s+(.*)', line)
    if not m:
        return None
    level = len(m.group(1))
    raw_text = m.group(2).strip()
    stripped_text = strip_markdown_formatting(raw_text).lower()
    return (level, raw_text, stripped_text)


def find_frontmatter_end(lines: list) -> int:
    """
    Find the line index where frontmatter ends.

    Returns the index of the first line AFTER the closing '---',
    or 0 if there's no frontmatter.
    """
    if not lines or lines[0].rstrip() != '---':
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip() == '---':
            return i + 1
    return 0


def extract_section(filepath: str, heading_query: str) -> tuple:
    """
    Extract a section from a markdown file by heading text.

    Args:
        filepath: absolute path to the markdown file
        heading_query: heading text to find (case-insensitive, fuzzy)

    Returns:
        (section_text, found) where found is True if the heading was matched.
        If not found, section_text contains a listing of available headings.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return (f"ERROR: Cannot read file: {e}", False)

    lines = content.split('\n')
    fm_end = find_frontmatter_end(lines)
    query_lower = heading_query.lower().strip()
    query_stripped = strip_markdown_formatting(query_lower)

    # Scan all headings
    headings = []  # (line_index, level, raw_text, stripped_text)
    for i in range(fm_end, len(lines)):
        parsed = parse_heading_line(lines[i])
        if parsed:
            level, raw_text, stripped_text = parsed
            headings.append((i, level, raw_text, stripped_text))

    # Find matching heading — try exact match first, then substring
    match_idx = None
    for idx, (line_i, level, raw_text, stripped_text) in enumerate(headings):
        if stripped_text == query_stripped:
            match_idx = idx
            break

    if match_idx is None:
        for idx, (line_i, level, raw_text, stripped_text) in enumerate(headings):
            if query_stripped in stripped_text:
                match_idx = idx
                break

    if match_idx is None:
        # Not found — list available headings
        if not headings:
            return ("No headings found in this file.", False)
        listing = "Section not found. Available headings:\n"
        for _, level, raw_text, _ in headings:
            indent = "  " * (level - 1)
            prefix = "#" * level
            listing += f"  {indent}{prefix} {raw_text}\n"
        return (listing, False)

    # Extract from matched heading to next heading of equal or higher level
    start_line, start_level = headings[match_idx][0], headings[match_idx][1]

    # Find end boundary
    end_line = len(lines)
    for subsequent_idx in range(match_idx + 1, len(headings)):
        next_line_i, next_level = headings[subsequent_idx][0], headings[subsequent_idx][1]
        if next_level <= start_level:
            end_line = next_line_i
            break

    # Extract and strip trailing blank lines
    section_lines = lines[start_line:end_line]
    while section_lines and not section_lines[-1].strip():
        section_lines.pop()

    return ('\n'.join(section_lines), True)


def find_section_boundaries(filepath: str, heading_query: str) -> tuple:
    """
    Find the line boundaries of a section in a markdown file.

    Args:
        filepath: absolute path to the markdown file
        heading_query: heading text to find (case-insensitive, fuzzy)

    Returns:
        (lines, heading_line_idx, end_line_idx, found, error_msg)

        lines: the full file as a list of lines (no trailing newlines)
        heading_line_idx: index of the heading line
        end_line_idx: index of the first line AFTER the section
                      (next same-or-higher heading, --- separator, or len(lines))
        found: True if heading was matched
        error_msg: if not found, a string listing available headings
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return ([], 0, 0, False, f"ERROR: Cannot read file: {e}")

    lines = content.split('\n')
    fm_end = find_frontmatter_end(lines)
    query_lower = heading_query.lower().strip()
    query_stripped = strip_markdown_formatting(query_lower)

    # Scan all headings
    headings = []  # (line_index, level, raw_text, stripped_text)
    for i in range(fm_end, len(lines)):
        parsed = parse_heading_line(lines[i])
        if parsed:
            level, raw_text, stripped_text = parsed
            headings.append((i, level, raw_text, stripped_text))

    # Find matching heading — exact match first, then substring
    match_idx = None
    for idx, (line_i, level, raw_text, stripped_text) in enumerate(headings):
        if stripped_text == query_stripped:
            match_idx = idx
            break

    if match_idx is None:
        for idx, (line_i, level, raw_text, stripped_text) in enumerate(headings):
            if query_stripped in stripped_text:
                match_idx = idx
                break

    if match_idx is None:
        if not headings:
            return (lines, 0, 0, False, "No headings found in this file.")
        listing = "Section not found. Available headings:\n"
        for _, level, raw_text, _ in headings:
            indent = "  " * (level - 1)
            prefix = "#" * level
            listing += f"  {indent}{prefix} {raw_text}\n"
        return (lines, 0, 0, False, listing)

    start_line = headings[match_idx][0]
    start_level = headings[match_idx][1]

    # Find end boundary: next heading of equal or higher level, or --- separator
    end_line = len(lines)
    for subsequent_idx in range(match_idx + 1, len(headings)):
        next_line_i, next_level = headings[subsequent_idx][0], headings[subsequent_idx][1]
        if next_level <= start_level:
            end_line = next_line_i
            break

    # Also check for --- separator between heading and end_line
    for i in range(start_line + 1, end_line):
        if lines[i].rstrip() == '---':
            end_line = i
            break

    return (lines, start_line, end_line, True, "")


def handle_write_section(args) -> None:
    """
    Handle the --write-section mode. Resolves the file, finds the section,
    and appends or replaces content. Early return from main().
    """
    file_arg, heading_arg = args.write_section

    # Validate: must have exactly one of --append or --replace
    if args.append and args.replace_text:
        print("ERROR: Cannot use both --append and --replace", file=sys.stderr)
        sys.exit(1)
    if not args.append and not args.replace_text:
        print("ERROR: --write-section requires either --append or --replace", file=sys.stderr)
        sys.exit(1)

    # Resolve file
    filepath = resolve_file(file_arg, args.root)
    print(f"file: {filepath}", file=sys.stderr)
    print(f"section: {heading_arg}", file=sys.stderr)

    # Find section boundaries
    lines, heading_idx, end_idx, found, error_msg = find_section_boundaries(filepath, heading_arg)

    if not found:
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    if args.append:
        # --- Append mode ---
        new_lines = args.append.split('\\n')

        # Determine insertion point: we want to insert before the end boundary,
        # but handle table rows and whitespace smartly.

        # Find the last non-blank line in the section body (between heading+1 and end_idx)
        last_content_idx = heading_idx  # fallback: right after heading
        for i in range(end_idx - 1, heading_idx, -1):
            if lines[i].strip():
                last_content_idx = i
                break

        # Table row detection: if last non-blank line is a table row and
        # new text starts with |, insert directly after the last table row
        last_content_line = lines[last_content_idx].strip() if last_content_idx > heading_idx else ""
        appending_table_row = new_lines[0].strip().startswith('|')
        existing_is_table_row = last_content_line.startswith('|')

        if appending_table_row and existing_is_table_row:
            # Insert right after the last table row
            insert_idx = last_content_idx + 1
            # Insert new lines without extra blank line
            for offset, nl in enumerate(new_lines):
                lines.insert(insert_idx + offset, nl)
            lines_added = len(new_lines)
        else:
            # Standard append: insert before the end boundary with a blank line separator
            insert_idx = end_idx

            # Build insertion block: blank line + new content
            insertion = []

            # Ensure one blank line before appended content
            # Check if the line before insert_idx is blank
            if insert_idx > 0 and lines[insert_idx - 1].strip():
                insertion.append('')

            insertion.extend(new_lines)

            # Ensure blank line after if we're not at EOF and next line isn't blank
            if insert_idx < len(lines) and lines[insert_idx].strip():
                insertion.append('')

            for offset, il in enumerate(insertion):
                lines.insert(insert_idx + offset, il)
            lines_added = len(new_lines)

        # Write file back
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        print(f"appended {lines_added} line(s)", file=sys.stderr)
        print("OK")

    elif args.replace_text:
        # --- Replace mode ---
        new_lines = args.replace_text.split('\\n')

        # Keep the heading line, replace everything between heading+1 and end_idx
        body_start = heading_idx + 1
        body_end = end_idx

        # Remove old body
        del lines[body_start:body_end]

        # Insert new body (with a blank line after heading if new content is non-empty)
        insertion = []
        if new_lines and new_lines[0].strip():
            insertion.append('')  # blank line after heading
        insertion.extend(new_lines)
        insertion.append('')  # trailing blank line

        for offset, il in enumerate(insertion):
            lines.insert(body_start + offset, il)

        lines_replaced = body_end - body_start
        lines_added = len(new_lines)

        # Write file back
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        print(f"replaced {lines_replaced} line(s) with {lines_added} line(s)", file=sys.stderr)
        print("OK")


def get_frontmatter_summary(filepath: str) -> str:
    """
    Extract the summary field from a file's frontmatter.

    Returns the summary string, or empty string if not present.
    """
    fm = parse_frontmatter(filepath)
    summary = fm.get('summary', '')
    title = fm.get('title', os.path.basename(filepath))
    if not summary:
        return ''
    return f"**{title}** — {summary}"


def handle_read_section(args) -> None:
    """
    Handle the --read-section mode. Resolves the file, extracts the section,
    and prints to stdout. Early return from main().
    """
    file_arg, heading_arg = args.read_section

    # Resolve file
    filepath = resolve_file(file_arg, args.root)
    print(f"source: {filepath}", file=sys.stderr)

    # Optionally prepend frontmatter summary
    if args.with_summary:
        summary = get_frontmatter_summary(filepath)
        if summary:
            print(summary)
            print()

    # Extract and print section
    section_text, found = extract_section(filepath, heading_arg)
    print(section_text)

    if not found:
        sys.exit(1)


def matches_filters(fm: dict, path: str, layer: str, args) -> bool:
    """Check if frontmatter matches all filter criteria."""

    # Type filter (matches CLASSIFIED layer, not raw frontmatter type)
    if args.type:
        if layer != args.type.lower():
            return False

    # Tag filter (matches if tag is in the file's tags list)
    if args.tag:
        tags = fm.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        tag_matches = any(args.tag.lower() in tag.lower() for tag in tags)
        if not tag_matches:
            return False

    # Status filter
    if args.status:
        fm_status = fm.get('status', '').lower()
        if fm_status != args.status.lower():
            return False

    # Related filter (wikilink contained in related field)
    if args.related:
        related = fm.get('related', [])
        if isinstance(related, str):
            related = [related]
        related_str = ' '.join(related).lower()
        if args.related.lower() not in related_str:
            return False

    # Updated-since filter
    if args.updated_since:
        fm_updated = fm.get('updated', '')
        if not fm_updated:
            return False
        try:
            updated_date = datetime.fromisoformat(str(fm_updated))
            filter_date = datetime.fromisoformat(args.updated_since)
            if updated_date < filter_date:
                return False
        except ValueError:
            return False

    return True


def format_markdown(results: list, with_summary: bool, vault_root: str):
    """Format results as markdown table."""
    if not results:
        print("No matches found.")
        return

    # Calculate column widths
    title_w = max(len(r.get('title', 'Untitled')) for r in results)
    title_w = min(title_w, 40)

    # Headers
    if with_summary:
        print(f"{'Title':<{title_w}} | Type | Status | Updated | Summary | Path")
        print(f"{'-' * title_w} | ---- | ------ | ------- | ------- | ----")
    else:
        print(f"{'Title':<{title_w}} | Type | Status | Updated | Path")
        print(f"{'-' * title_w} | ---- | ------ | ------- | ----")

    # Rows
    for r in results:
        title = r.get('title', 'Untitled')
        if len(title) > title_w:
            title = title[:title_w-3] + '...'

        typ = r.get('type', '')[:12]
        status = r.get('status', '')[:10]
        updated = r.get('updated', '')[:10]
        path = r['rel_path']

        if with_summary:
            summary = r.get('summary', '')
            if len(summary) > 50:
                summary = summary[:47] + '...'
            print(f"{title:<{title_w}} | {typ:<4} | {status:<6} | {updated:<7} | {summary:<50} | {path}")
        else:
            print(f"{title:<{title_w}} | {typ:<4} | {status:<6} | {updated:<7} | {path}")


def format_tsv(results: list, with_summary: bool, vault_root: str):
    """Format results as TSV."""
    if with_summary:
        print("Title\tType\tStatus\tUpdated\tSummary\tPath")
    else:
        print("Title\tType\tStatus\tUpdated\tPath")

    for r in results:
        title = r.get('title', 'Untitled')
        typ = r.get('type', '')
        status = r.get('status', '')
        updated = r.get('updated', '')
        path = r['rel_path']

        if with_summary:
            summary = r.get('summary', '').replace('\n', ' ')
            print(f"{title}\t{typ}\t{status}\t{updated}\t{summary}\t{path}")
        else:
            print(f"{title}\t{typ}\t{status}\t{updated}\t{path}")


def format_json_output(results: list, with_summary: bool, vault_root: str):
    """Format results as JSON."""
    print(json.dumps(results, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--read-section', nargs=2, metavar=('FILE', 'HEADING'),
                   help='Extract a section from a vault file by heading text')
    ap.add_argument('--write-section', nargs=2, metavar=('FILE', 'HEADING'),
                   help='Write to a section in a vault file (use with --append or --replace)')
    ap.add_argument('--append', metavar='TEXT',
                   help='Text to append at the end of the section (use with --write-section)')
    ap.add_argument('--replace', metavar='TEXT', dest='replace_text',
                   help='Text to replace the section body with (use with --write-section)')
    ap.add_argument('--type', help='Filter by frontmatter type (router|moc|leaf|meta)')
    ap.add_argument('--tag', help='Filter by tag (matches if tag is in tags list)')
    ap.add_argument('--status', help='Filter by status field')
    ap.add_argument('--related', help='Filter by related field containing this wikilink')
    ap.add_argument('--updated-since', help='Filter by updated >= date (ISO format)')
    ap.add_argument('--with-summary', action='store_true', help='Include summary field in output')
    ap.add_argument('--root', default=os.environ.get('CLAUDE_PROJECT_DIR', DEFAULT_VAULT_ROOT),
                   help='Vault root (default: $CLAUDE_PROJECT_DIR or __VAULT_PATH__)')
    ap.add_argument('--format', choices=['markdown', 'tsv', 'json'], default='markdown',
                   help='Output format (default: markdown)')

    args = ap.parse_args()

    # Validate root
    if not os.path.isdir(args.root):
        print(f"ERROR: Vault root does not exist: {args.root}", file=sys.stderr)
        sys.exit(1)

    # --read-section mode: early return, no filter logic needed
    if args.read_section:
        handle_read_section(args)
        return

    # --write-section mode: early return, no filter logic needed
    if args.write_section:
        handle_write_section(args)
        return

    # Walk vault and collect matches
    from vault_lib import classify_layer
    results = []
    for filepath, fm, body_size in walk_vault(args.root):
        layer = classify_layer(fm, filepath)
        if matches_filters(fm, filepath, layer, args):
            rel_path = os.path.relpath(filepath, args.root)
            result = {
                'title': fm.get('title', os.path.basename(filepath)),
                'type': fm.get('type', ''),
                'status': fm.get('status', ''),
                'updated': fm.get('updated', ''),
                'rel_path': rel_path,
                'abs_path': filepath
            }
            if args.with_summary:
                result['summary'] = fm.get('summary', '')
            results.append(result)

    # Sort by updated date (newest first), then by title
    results.sort(key=lambda x: (x.get('updated', ''), x['title']), reverse=True)

    # Format output
    if args.format == 'markdown':
        format_markdown(results, args.with_summary, args.root)
    elif args.format == 'tsv':
        format_tsv(results, args.with_summary, args.root)
    elif args.format == 'json':
        format_json_output(results, args.with_summary, args.root)


if __name__ == '__main__':
    main()
