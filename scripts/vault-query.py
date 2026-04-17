#!/usr/bin/env python3
"""
vault-query.py — CLI for querying the vault by frontmatter.

Query vault files by type, tag, status, related links, and update date.
Returns markdown table (default) or TSV/JSON.

Usage:
    vault-query.py --type moc
    vault-query.py --tag research --status active
    vault-query.py --related "ProjectRoot" --with-summary
    vault-query.py --query "WidgetName"                       # search title/aliases/name
    vault-query.py --content "project concept"                # search body text
    vault-query.py --content "api|auth|deploy"                # OR search (any term matches)
    vault-query.py --content "deploy" --path docs/            # search only docs directory
    vault-query.py --content "deploy" --type leaf             # body search narrowed by type
    vault-query.py --not-content "deprecated" --type leaf     # leaves WITHOUT "deprecated"
    vault-query.py --index                                    # build BM25 search index (run first)
    vault-query.py --search "project architecture"            # BM25 ranked search
    vault-query.py --search "hidden features" --search-limit 5
    vault-query.py --query "auth" --type leaf                 # combine with other filters
    vault-query.py --updated-since 2026-04-01 --format json
    vault-query.py --read-section "Plan.md" "Open Questions"
    vault-query.py --read-section "Design.md" "Overview" --with-summary
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
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
    skip_dirs = {".git", "node_modules", ".claude", "__pycache__", ".venv", "venv", "_workspace"}
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


_body_cache: dict[str, tuple] = {}

def _get_body(path: str) -> tuple:
    """Read file body (after frontmatter). Cached per path.
    Returns (body_text, frontmatter_line_count) or (None, 0) on error."""
    if path not in _body_cache:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            body = text
            fm_lines = 0
            if body.startswith('---\n'):
                end = body.find('\n---', 4)
                if end != -1:
                    fm_lines = body[:end + 4].count('\n')
                    body = body[end + 4:]
            _body_cache[path] = (body, fm_lines)
        except (OSError, UnicodeDecodeError):
            _body_cache[path] = (None, 0)
    return _body_cache[path]


# =========================================================================
# BM25 Ranked Search (FTS5)
# =========================================================================

def _db_path(vault_root: str, name: str) -> str:
    """Generate a per-vault DB path using a hash of the vault root."""
    root_hash = hashlib.md5(os.path.abspath(vault_root).encode()).hexdigest()[:12]
    return os.path.join(os.path.expanduser('~'), '.claude', f'{name}-{root_hash}.db')

_STOP_WORDS = frozenset(
    'a an and are as at be but by for from has have he her his how i if in is it its '
    'me my no not of on or our she so that the their them then there these they this to '
    'up us was we were what when where which who will with you your'.split()
)


def build_search_index(vault_root: str) -> int:
    """Build/rebuild the FTS5 search index from vault files."""
    conn = sqlite3.connect(_db_path(vault_root, 'vault-search'))
    conn.execute('DROP TABLE IF EXISTS vault_fts')
    conn.execute('''
        CREATE VIRTUAL TABLE vault_fts USING fts5(
            path, title, body,
            tokenize='porter unicode61'
        )
    ''')

    count = 0
    for filepath, fm, body_size in walk_vault(vault_root):
        body, _ = _get_body(filepath)
        if body is None or not body.strip():
            continue
        title = fm.get('title', os.path.basename(filepath))
        rel_path = os.path.relpath(filepath, vault_root)
        conn.execute(
            'INSERT INTO vault_fts(path, title, body) VALUES (?, ?, ?)',
            (rel_path, title, body)
        )
        count += 1

    conn.commit()
    conn.close()
    _body_cache.clear()
    return count


def _prepare_fts_query(raw_query: str) -> str:
    """Convert natural-language query to FTS5 OR query with stop-word removal."""
    words = re.findall(r'\w+', raw_query.lower())
    meaningful = [w for w in words if w not in _STOP_WORDS and len(w) > 1]
    if not meaningful:
        meaningful = words[:3]
    return ' OR '.join(f'"{w}"' for w in meaningful)


def _is_index_stale(vault_root: str, index_path: str) -> bool:
    """Check if any vault .md file is newer than the index."""
    if not os.path.exists(index_path):
        return True
    index_mtime = os.path.getmtime(index_path)
    skip_dirs = {'.git', 'node_modules', '.claude', '__pycache__', '.venv', 'venv', '.obsidian', '_workspace'}
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if fname.endswith('.md'):
                if os.path.getmtime(os.path.join(dirpath, fname)) > index_mtime:
                    return True
    return False


def _auto_reindex_bm25(vault_root: str) -> None:
    """Auto-rebuild BM25 index if stale. Fast (~2s)."""
    if _is_index_stale(vault_root, _db_path(vault_root, 'vault-search')):
        print("BM25 index stale — rebuilding...", file=sys.stderr)
        count = build_search_index(vault_root)
        print(f"BM25: re-indexed {count} files.", file=sys.stderr)


def _auto_reindex_vectors(vault_root: str) -> bool:
    """Auto-rebuild vector index if stale. Uses incremental mode — only re-embeds changed files."""
    try:
        from vault_embeddings import is_available, build_vector_index, get_vector_db_path
        if not is_available():
            return False
        if _is_index_stale(vault_root, get_vector_db_path(vault_root)):
            print("Vector index stale — incremental update...", file=sys.stderr)
            count = build_vector_index(vault_root, walk_fn=walk_vault, get_body_fn=_get_body, full_rebuild=False)
            _body_cache.clear()
            if count > 0:
                print(f"Vectors: re-embedded {count} sections from changed files.", file=sys.stderr)
            else:
                print("Vectors: no sections needed re-embedding.", file=sys.stderr)
            return True
    except ImportError:
        pass
    return False


def bm25_search(raw_query: str, limit: int = 10, vault_root: str = '') -> list:
    """Search the FTS5 index with BM25 ranking. Returns ranked results."""
    db_path = _db_path(vault_root, 'vault-search')
    if not os.path.exists(db_path):
        print("Search index not found. Run: vault-query.py --index", file=sys.stderr)
        sys.exit(1)

    fts_query = _prepare_fts_query(raw_query)
    if not fts_query:
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.execute('''
        SELECT path, title,
               snippet(vault_fts, 2, '>>>', '<<<', '...', 30) as snippet,
               bm25(vault_fts) as rank
        FROM vault_fts
        WHERE vault_fts MATCH ?
        ORDER BY bm25(vault_fts)
        LIMIT ?
    ''', (fts_query, limit))

    results = []
    for row in cursor:
        results.append({
            'rel_path': row[0],
            'title': row[1],
            'snippet': row[2].replace('>>>', '**').replace('<<<', '**'),
            'rank': round(row[3], 3)
        })

    conn.close()
    return results


def hybrid_search(raw_query: str, limit: int = 10, vault_root: str = '') -> list:
    """Combine BM25 + vector search using Reciprocal Rank Fusion (RRF).
    Documents found by both engines get boosted. Best of both worlds."""

    bm25_results = bm25_search(raw_query, limit=30, vault_root=vault_root)

    vec_results = []
    try:
        from vault_embeddings import is_available, vector_search, get_vector_db_path
        if is_available():
            vdb = get_vector_db_path(vault_root)
            if os.path.exists(vdb):
                vec_results = vector_search(raw_query, limit=30, vault_root=vault_root)
    except ImportError:
        pass

    if not vec_results:
        return bm25_results[:limit]
    if not bm25_results:
        return vec_results[:limit]

    # Reciprocal Rank Fusion (k=60, standard)
    k = 60
    merged: dict[str, dict] = {}

    # BM25 contributions (file-level)
    for rank, r in enumerate(bm25_results):
        path = r['rel_path']
        rrf = 1.0 / (k + rank + 1)
        if path not in merged:
            merged[path] = {'score': 0, 'result': dict(r), 'sources': set()}
        merged[path]['score'] += rrf
        merged[path]['sources'].add('BM25')

    # Vector contributions (section-level, deduplicate by file — keep best section)
    seen_paths = {}
    for rank, r in enumerate(vec_results):
        path = r['rel_path']
        rrf = 1.0 / (k + rank + 1)

        if path not in merged:
            merged[path] = {'score': 0, 'result': dict(r), 'sources': set()}

        merged[path]['score'] += rrf
        merged[path]['sources'].add('Vector')

        # Use vector's section detail (more precise than BM25's file-level)
        if path not in seen_paths:
            seen_paths[path] = True
            if r.get('section'):
                merged[path]['result']['section'] = r['section']
            if r.get('snippet'):
                merged[path]['result']['snippet'] = r['snippet']

    # Sort by combined RRF score (highest first)
    ranked = sorted(merged.values(), key=lambda x: x['score'], reverse=True)

    results = []
    for item in ranked[:limit]:
        r = item['result']
        r['score'] = round(item['score'], 4)
        r['sources'] = '+'.join(sorted(item['sources']))
        results.append(r)

    return results


def format_search_results(results: list) -> None:
    """Print ranked search results."""
    if not results:
        print("No matches found.")
        return

    title_w = max(len(r['title']) for r in results)
    title_w = min(title_w, 40)

    print(f"{'#':<3} {'Title':<{title_w}} | Score | Snippet | Path")
    print(f"{'---':<3} {'-' * title_w} | ----- | ------- | ----")

    for i, r in enumerate(results, 1):
        title = r['title']
        if len(title) > title_w:
            title = title[:title_w - 3] + '...'
        snippet = r.get('snippet', '')[:80].replace('\n', ' ')
        score = r.get('score', r.get('rank', 0))
        section = r.get('section', '')
        sources = r.get('sources', '')
        sec_str = f" §{section}" if section and section != title else ""
        src_str = f" [{sources}]" if sources else ""
        print(f"{i:<3} {title:<{title_w}} | {score:<6} | {snippet} | {r['rel_path']}{sec_str}{src_str}")


# =========================================================================
# Filter-based search (existing)
# =========================================================================

def matches_filters(fm: dict, path: str, layer: str, args) -> bool:
    """Check if frontmatter matches all filter criteria."""

    # Query filter — substring search against title, name, aliases, and codename
    # (case-insensitive). Primary "find the file" use case.
    if args.query:
        if not args.query.strip():
            return False  # empty query matches nothing
        query_lower = args.query.lower()
        title = str(fm.get('title', '')).lower()
        name = str(fm.get('name', '')).lower()
        codename = str(fm.get('codename', '')).lower()
        aliases = fm.get('aliases', [])
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases_str = ' '.join(str(a) for a in aliases).lower()

        if (query_lower not in title and
                query_lower not in name and
                query_lower not in codename and
                query_lower not in aliases_str):
            return False

    # Type filter — matches EITHER the classified layer (router/moc/leaf/meta)
    # OR the raw frontmatter type (e.g., character/episode/faction/lore/etc).
    # This way --type character works as expected even though classify_layer
    # returns "leaf" for character files.
    if args.type:
        raw_type = fm.get('type', '').lower()
        if layer != args.type.lower() and raw_type != args.type.lower():
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

    # Content filter — substring search in body text (everything after frontmatter).
    # Heavier than other filters (reads full file). Composable with all other filters
    # so you can narrow before searching: e.g., --type leaf --content "deploy steps"
    if args.content or args.not_content:
        # Empty content/not-content matches nothing
        if args.content and not args.content.strip():
            return False
        if args.not_content and not args.not_content.strip():
            return False

        body, _ = _get_body(path)
        if body is None:
            return False
        body_lower = body.lower()

        # Content filter — include only files containing term(s).
        # Supports OR syntax: "api|auth|deploy" matches if ANY term is found.
        # Single pass per file regardless of term count.
        if args.content:
            content_terms = [t.strip().lower() for t in args.content.split('|') if t.strip()]
            if not any(term in body_lower for term in content_terms):
                return False

        # Not-content filter — exclude files containing term(s).
        # Also supports OR: "foo|bar" excludes if ANY term is found.
        if args.not_content:
            not_terms = [t.strip().lower() for t in args.not_content.split('|') if t.strip()]
            if any(term in body_lower for term in not_terms):
                return False

    return True


def find_content_match(path: str, term_str: str, context: int = 0) -> tuple:
    """Find first line matching any term in file body. Supports OR via pipe delimiter.
    Uses cached body from _get_body() — zero additional file I/O.
    Returns (line_num, line_text, context_str) or (None, None, '')."""
    body, fm_lines = _get_body(path)
    if body is None:
        return (None, None, '')

    terms = [t.strip().lower() for t in term_str.split('|') if t.strip()]
    lines = body.split('\n')
    match_idx = None
    for i, line in enumerate(lines):
        if any(t in line.lower() for t in terms):
            match_idx = i
            break

    if match_idx is None:
        return (None, None, '')

    line_num = match_idx + 1 + fm_lines
    line_text = lines[match_idx].strip()[:120]

    if context > 0:
        start = max(0, match_idx - context)
        end = min(len(lines), match_idx + context + 1)
        context_lines = []
        for j in range(start, end):
            prefix = ">>>" if j == match_idx else "   "
            context_lines.append(f"{prefix} L{j + 1 + fm_lines}: {lines[j].rstrip()[:120]}")
        return (line_num, line_text, '\n'.join(context_lines))

    return (line_num, line_text, '')


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

        # Build match suffix when --content provides location
        match_str = ''
        if r.get('match_line'):
            match_str = f" | L{r['match_line']}: {r.get('match_text', '')[:80]}"

        if with_summary:
            summary = r.get('summary', '')
            if len(summary) > 50:
                summary = summary[:47] + '...'
            print(f"{title:<{title_w}} | {typ:<4} | {status:<6} | {updated:<7} | {summary:<50} | {path}{match_str}")
        else:
            print(f"{title:<{title_w}} | {typ:<4} | {status:<6} | {updated:<7} | {path}{match_str}")

        # Print context lines below matching row if available
        if r.get('match_context'):
            for ctx_line in r['match_context'].split('\n'):
                print(f"    {ctx_line}")


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
    ap.add_argument('--query', help='Search title/aliases/name for term (case-insensitive substring). Primary "find the file by name" use case.')
    ap.add_argument('--content', help='Search body text for term (case-insensitive substring). Composable with other filters to narrow scope first.')
    ap.add_argument('--not-content', help='Exclude files whose body text contains this term. Inverse of --content.')
    ap.add_argument('--type', help='Filter by frontmatter type or classified layer (character, episode, lore, leaf, moc, etc.)')
    ap.add_argument('--tag', help='Filter by tag (matches if tag is in tags list)')
    ap.add_argument('--status', help='Filter by status field')
    ap.add_argument('--related', help='Filter by related field containing this wikilink')
    ap.add_argument('--updated-since', help='Filter by updated >= date (ISO format)')
    ap.add_argument('--with-summary', action='store_true', help='Include summary field in output')
    ap.add_argument('--search', metavar='QUERY', help='Ranked search. Uses hybrid (BM25+vector) when both available, BM25 fallback otherwise. Run --index first.')
    ap.add_argument('--index', action='store_true', help='Full rebuild of search indexes (BM25 + vector). Auto-reindex uses incremental mode.')
    ap.add_argument('--search-limit', type=int, default=10, help='Max results for --search (default: 10)')
    ap.add_argument('--search-mode', choices=['auto', 'bm25', 'vector', 'hybrid'], default='auto',
                   help='Force search mode: auto (hybrid if both available, else BM25), bm25, vector, or hybrid.')
    ap.add_argument('--root', default=os.environ.get('CLAUDE_PROJECT_DIR', DEFAULT_VAULT_ROOT),
                   help='Vault root (default: $CLAUDE_PROJECT_DIR or configured vault path)')
    ap.add_argument('--context', '-C', type=int, default=0,
                   help='Show N lines of context around --content matches.')
    ap.add_argument('--format', choices=['markdown', 'tsv', 'json'], default='markdown',
                   help='Output format (default: markdown)')
    ap.add_argument('--path',
                   help='Restrict search to files under this subdirectory of the vault (e.g., docs/, notes/).')

    try:
        args = ap.parse_args()
    except SystemExit:
        # Check if user tried --content with a dash-prefixed value
        if any(a.startswith('--content') for a in sys.argv):
            print("Tip: Use --content='---' (equals syntax) for values starting with dashes.", file=sys.stderr)
        raise

    # No arguments — show help instead of dumping full vault
    has_action = any([args.read_section, args.write_section, args.index, args.search,
                      args.query, args.content, args.not_content, args.type,
                      args.tag, args.status, args.related, args.updated_since])
    if not has_action:
        ap.print_help()
        return

    # Validate root
    if not os.path.isdir(args.root):
        print(f"ERROR: Vault root does not exist: {args.root}", file=sys.stderr)
        sys.exit(1)

    # --index mode: build search indexes, early return
    if args.index:
        # Always build BM25 (standalone)
        print(f"Building BM25 index at {args.root}...", file=sys.stderr)
        bm25_count = build_search_index(args.root)
        print(f"BM25: indexed {bm25_count} files → {_db_path(args.root, 'vault-search')}", file=sys.stderr)

        # Build vector index if embeddings available
        try:
            from vault_embeddings import is_available, build_vector_index
            if is_available():
                print("Building vector embedding index...", file=sys.stderr)
                vec_count = build_vector_index(args.root, walk_fn=walk_vault, get_body_fn=_get_body)
                from vault_embeddings import get_vector_db_path
                print(f"Vectors: indexed {vec_count} sections → {get_vector_db_path(args.root)}", file=sys.stderr)
            else:
                print("Vector embeddings not available (install onnxruntime + tokenizers for semantic search).", file=sys.stderr)
        except ImportError:
            print("Vector embeddings module not found. BM25 only.", file=sys.stderr)

        print("OK")
        return

    # --search mode: ranked search with auto-reindex + auto-detection, early return
    if args.search:
        if not args.search.strip():
            print("No matches found.")
            return
        # Auto-reindex BM25 (fast, always)
        _auto_reindex_bm25(args.root)

        mode = args.search_mode

        # Check vector availability
        has_vectors = False
        try:
            from vault_embeddings import is_available, vector_search as vsearch, get_vector_db_path
            if is_available():
                has_vectors = os.path.exists(get_vector_db_path(args.root))
        except ImportError:
            pass

        # Auto-reindex vectors only if mode needs them
        if has_vectors and mode in ('auto', 'hybrid', 'vector'):
            _auto_reindex_vectors(args.root)

        # Resolve auto mode
        if mode == 'auto':
            mode = 'hybrid' if has_vectors else 'bm25'

        if mode == 'vector' and not has_vectors:
            print("Vector search not available. Install onnxruntime + tokenizers, then run --index.", file=sys.stderr)
            sys.exit(1)
        if mode == 'hybrid' and not has_vectors:
            mode = 'bm25'

        if mode == 'hybrid':
            results = hybrid_search(args.search, limit=args.search_limit, vault_root=args.root)
            print(f"(hybrid search — BM25+Vector — {len(results)} results)", file=sys.stderr)
        elif mode == 'vector':
            results = vsearch(args.search, limit=args.search_limit, vault_root=args.root)
            print(f"(vector search — {len(results)} results)", file=sys.stderr)
        else:
            results = bm25_search(args.search, limit=args.search_limit, vault_root=args.root)
            print(f"(BM25 search — {len(results)} results)", file=sys.stderr)

        # Post-filter search results by --type, --path, --status, --tag if specified
        if results and (args.type or args.path or args.status or args.tag):
            from vault_lib import classify_layer, parse_frontmatter
            filtered = []
            for r in results:
                rel_path = r.get('rel_path', '')
                abs_path = os.path.join(args.root, rel_path)

                # --path filter
                if args.path and not rel_path.startswith(args.path.rstrip('/')):
                    continue

                # --type and --status need frontmatter
                if args.type or args.status or args.tag:
                    if os.path.exists(abs_path):
                        fm = parse_frontmatter(abs_path)
                        layer = classify_layer(fm, abs_path)
                        raw_type = fm.get('type', '').lower()

                        if args.type and layer != args.type.lower() and raw_type != args.type.lower():
                            continue
                        if args.status and fm.get('status', '').lower() != args.status.lower():
                            continue
                        if args.tag:
                            tags = fm.get('tags', [])
                            if isinstance(tags, str):
                                tags = [tags]
                            if not any(args.tag.lower() in t.lower() for t in tags):
                                continue

                filtered.append(r)
            results = filtered

        if args.format == 'json':
            print(json.dumps(results, indent=2))
        else:
            format_search_results(results)
        return

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
        rel_path = os.path.relpath(filepath, args.root)
        if args.path:
            target = args.path.rstrip('/')
            if not rel_path.startswith(target):
                continue
        layer = classify_layer(fm, filepath)
        if matches_filters(fm, filepath, layer, args):
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
            if args.content:
                line_num, line_text, match_context = find_content_match(filepath, args.content, args.context)
                result['match_line'] = line_num
                result['match_text'] = line_text
                result['match_context'] = match_context
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
