#!/usr/bin/env python3
"""
memory-query.py — CLI search tool for the persistent memory index.

Uses SQLite FTS5 BM25 for ranked text search with strength/decay scoring.
Called by the agent during sessions to find relevant facts.

Usage:
    memory-query.py --query "authentication" --limit 10
    memory-query.py --query "refactor" --type decision
    memory-query.py --session 12
    memory-query.py --stats
    memory-query.py --decay [--dry-run]
    memory-query.py --query "deploy" --format json
    memory-query.py --add "fact content here" --type lesson --importance 5 --session-id 12
    memory-query.py --get <FACT_ID>
    memory-query.py --get <FACT_ID> --format json
    memory-query.py --update <FACT_ID> --importance 5
    memory-query.py --update <FACT_ID> --type decision --confidence 0.95
    memory-query.py --update <FACT_ID> --concepts "hooks,memory,architecture"
    memory-query.py --update <FACT_ID> --session-id 67a
    memory-query.py --delete <FACT_ID>
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

# Import from sibling module
sys.path.insert(0, os.path.expanduser('~/.claude/scripts'))
from memory_lib import MemoryDB, Fact, FACT_TYPES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_DIR = os.path.expanduser('~/.claude/memory-index')
DEFAULT_PROJECT = 'my-project'
DEFAULT_LIMIT = 10

# Strength tier thresholds (must match MemoryDB.get_stats / apply_decay)
TIER_HOT = 0.8
TIER_WARM = 0.5
TIER_COLD = 0.2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def db_path_for_project(project: str) -> str:
    return os.path.join(INDEX_DIR, f'{project}.db')


def fmt_ts(ts: float) -> str:
    """Format a unix timestamp as YYYY-MM-DD HH:MM."""
    if not ts:
        return 'never'
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def pct(count: int, total: int) -> str:
    """Format a percentage string like '22.6%'."""
    if total == 0:
        return '0.0%'
    return f'{count / total * 100:.1f}%'


def truncate(text: str, length: int = 80) -> str:
    """Truncate text to length, appending '...' if cut."""
    text = text.replace('\n', ' ').strip()
    if len(text) <= length:
        return text
    return text[:length - 3] + '...'


def sessions_str(sessions: list) -> str:
    """Join session IDs with ' | '."""
    if not sessions:
        return '-'
    return ' | '.join(str(s) for s in sessions)


def files_short(files: list) -> str:
    """Show just the filenames, not full paths."""
    if not files:
        return '-'
    return ', '.join(os.path.basename(str(f)) for f in files)


def tier_label(strength: float) -> str:
    """Return the human-readable tier label for a strength value."""
    if strength >= TIER_HOT:
        return 'Hot'
    if strength >= TIER_WARM:
        return 'Warm'
    if strength >= TIER_COLD:
        return 'Cold'
    return 'Floor'


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_search_markdown(results: list, query: str) -> str:
    """Render search results in human-readable markdown.
    results: list of (Fact, score) tuples from MemoryDB.search()"""
    n = len(results)
    lines = [f'## Search: "{query}" ({n} result{"s" if n != 1 else ""})', '']

    for i, (fact, score) in enumerate(results, 1):
        lines.append(f'### {i}. [{fact.fact_type}] (score: {score:.3f}, strength: {fact.strength:.2f})')
        lines.append(fact.content)
        lines.append('')
        lines.append(f'  Sessions: {sessions_str(fact.source_session_ids)} | Files: {files_short(fact.source_files)}')
        if fact.concepts:
            lines.append(f'  Concepts: {", ".join(str(c) for c in fact.concepts)}')
        lines.append(f'  Accessed: {fact.access_count} time{"s" if fact.access_count != 1 else ""} | Last: {fmt_ts(fact.last_accessed_at)}')
        lines.append('')
        lines.append('---')
        lines.append('')

    return '\n'.join(lines)


def format_search_json(results: list) -> str:
    """Render search results as a JSON array.
    results: list of (Fact, score) tuples from MemoryDB.search()"""
    out = []
    for fact, score in results:
        d = {
            'id': fact.id, 'content': fact.content, 'fact_type': fact.fact_type,
            'confidence': fact.confidence, 'strength': fact.strength,
            'source_session_ids': fact.source_session_ids,
            'source_files': fact.source_files, 'source_section': fact.source_section,
            'concepts': fact.concepts, 'related_files': fact.related_files,
            'importance': fact.importance, 'access_count': fact.access_count,
            'created_at': fact.created_at, 'last_accessed_at': fact.last_accessed_at,
            'score': score,
        }
        out.append(d)
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def format_search_compact(results: list) -> str:
    """Render one line per result.
    results: list of (Fact, score) tuples from MemoryDB.search()"""
    lines = []
    for fact, score in results:
        content = truncate(fact.content, 80)
        sessions = sessions_str(fact.source_session_ids)
        lines.append(f'[{fact.fact_type}|{score:.2f}] {content} ({sessions}, str:{fact.strength:.2f})')
    return '\n'.join(lines)


def format_session(facts: list, session_id: str) -> str:
    """Render facts for a session lookup.
    facts: list of Fact objects from MemoryDB.get_by_session()"""
    n = len(facts)
    lines = [f'## Session: {session_id} ({n} fact{"s" if n != 1 else ""})', '']
    for i, f in enumerate(facts, 1):
        content = truncate(f.content, 120)
        lines.append(f'{i}. [{f.fact_type}] {content}')
    return '\n'.join(lines)


def format_stats(stats: dict, project: str) -> str:
    """Render database statistics."""
    total = stats.get('total_facts', 0)
    types = stats.get('type_distribution', {})
    tiers = stats.get('strength_distribution', {})
    newest = stats.get('newest_fact')

    lines = [
        f'Memory Index: {project}',
        f'  Database: ~/.claude/memory-index/{project}.db',
        f'  Total facts: {total:,}',
        '',
    ]

    # Type distribution sorted by count descending
    if types:
        lines.append('  Type distribution:')
        for ftype, count in sorted(types.items(), key=lambda x: -x[1]):
            lines.append(f'    {ftype + ":":16s} {count:>5,} ({pct(count, total)})')
        lines.append('')

    # Strength tiers
    lines.append('  Strength tiers:')
    hot = tiers.get('hot', 0)
    warm = tiers.get('warm', 0)
    cold = tiers.get('cold', 0)
    floor = tiers.get('floor', 0)
    lines.append(f'    {"Hot  (>=0.7):":<20s} {hot:>5,} ({pct(hot, total)})')
    lines.append(f'    {"Warm (0.4-0.7):":<20s} {warm:>5,} ({pct(warm, total)})')
    lines.append(f'    {"Cold (0.15-0.4):":<20s} {cold:>5,} ({pct(cold, total)})')
    lines.append(f'    {"Floor (<0.15):":<20s} {floor:>5,} ({pct(floor, total)})')
    lines.append('')

    # Session count: count distinct session IDs across all facts
    # (Not available from get_stats; would need a separate query, so skip for now)

    if newest:
        lines.append(f'  Last updated: {fmt_ts(newest)}')

    return '\n'.join(lines)


def format_fact_detail_markdown(fact) -> str:
    """Render a single fact with all fields in human-readable markdown."""
    lines = [
        f'## Fact: {fact.id}',
        '',
        f'**Type:** {fact.fact_type}',
        f'**Importance:** {fact.importance}/5',
        f'**Confidence:** {fact.confidence:.2f}',
        f'**Strength:** {fact.strength:.2f} ({tier_label(fact.strength)})',
        '',
        '### Content',
        fact.content,
        '',
        '### Metadata',
        f'- **Sessions:** {sessions_str(fact.source_session_ids)}',
        f'- **Source files:** {files_short(fact.source_files)}',
        f'- **Source section:** {fact.source_section or "-"}',
        f'- **Concepts:** {", ".join(str(c) for c in fact.concepts) if fact.concepts else "-"}',
        f'- **Related files:** {", ".join(str(f) for f in fact.related_files) if fact.related_files else "-"}',
        '',
        '### Access',
        f'- **Created:** {fmt_ts(fact.created_at)}',
        f'- **Last accessed:** {fmt_ts(fact.last_accessed_at)}',
        f'- **Access count:** {fact.access_count}',
    ]
    return '\n'.join(lines)


def format_fact_detail_json(fact) -> str:
    """Render a single fact as JSON with all fields."""
    d = {
        'id': fact.id, 'content': fact.content, 'fact_type': fact.fact_type,
        'confidence': fact.confidence, 'strength': fact.strength,
        'importance': fact.importance,
        'source_session_ids': fact.source_session_ids,
        'source_files': fact.source_files, 'source_section': fact.source_section,
        'concepts': fact.concepts, 'related_files': fact.related_files,
        'created_at': fact.created_at, 'last_accessed_at': fact.last_accessed_at,
        'access_count': fact.access_count,
    }
    return json.dumps(d, indent=2, ensure_ascii=False, default=str)


def format_fact_detail_compact(fact) -> str:
    """Render a single fact in compact one-line format."""
    content = truncate(fact.content, 100)
    sessions = sessions_str(fact.source_session_ids)
    return f'[{fact.id}] [{fact.fact_type}|imp:{fact.importance}|str:{fact.strength:.2f}] {content} ({sessions})'


def format_decay(result: dict, total_checked: int, dry_run: bool) -> str:
    """Render decay application results."""
    prefix = 'DRY RUN: ' if dry_run else ''
    decayed = result.get('decayed_count', 0)

    lines = [
        f'{prefix}Applying decay to index...',
        f'  Facts checked: {total_checked:,}',
        f'  Facts decayed: {decayed:,}',
    ]

    # Compute average strength change -- not directly available from API,
    # so we report what we can.
    if decayed > 0:
        lines.append('')
        lines.append('  Strength tiers (post-decay):')
        lines.append(f'    Hot:   {result.get("hot", 0):,}')
        lines.append(f'    Warm:  {result.get("warm", 0):,}')
        lines.append(f'    Cold:  {result.get("cold", 0):,}')
        lines.append(f'    Floor: {result.get("floor", 0):,}')
    elif not dry_run:
        lines.append('  No facts needed decay.')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Dry-run decay — computes what would change without writing
# ---------------------------------------------------------------------------

def compute_decay_dry_run(db: MemoryDB) -> dict:
    """
    Simulate decay without persisting.  Returns the same shape as
    MemoryDB.apply_decay() plus 'total_checked'.
    """
    now = time.time()
    cursor = db.conn.execute(
        "SELECT id, strength, last_accessed_at FROM facts"
    )
    rows = cursor.fetchall()
    total_checked = len(rows)
    decayed_count = 0

    # Simulate new strengths to compute tier counts
    new_strengths = []
    for row in rows:
        days_since = (now - row['last_accessed_at']) / 86400.0
        s = row['strength']
        if days_since > 30:
            decay_periods = math.floor(days_since / 30)
            new_s = max(0.1, s * (0.9 ** decay_periods))
            if new_s != s:
                decayed_count += 1
                s = new_s
        new_strengths.append(s)

    hot = sum(1 for s in new_strengths if s >= 0.7)
    warm = sum(1 for s in new_strengths if 0.4 <= s < 0.7)
    cold = sum(1 for s in new_strengths if 0.15 <= s < 0.4)
    floor = sum(1 for s in new_strengths if s < 0.15)

    return {
        'hot': hot,
        'warm': warm,
        'cold': cold,
        'floor': floor,
        'decayed_count': decayed_count,
        'total_checked': total_checked,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Search the persistent memory index.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
examples:
  %(prog)s --query "authentication" --limit 10
  %(prog)s --query "refactor" --type decision
  %(prog)s --query "hook infrastructure" --type architecture --limit 5
  %(prog)s --session 12
  %(prog)s --stats
  %(prog)s --decay
  %(prog)s --decay --dry-run
  %(prog)s --query "deploy" --format json
  %(prog)s --query "deploy" --format compact
  %(prog)s --get abc123def456
  %(prog)s --get abc123def456 --format json
  %(prog)s --update abc123def456 --importance 5
  %(prog)s --update abc123def456 --type decision --confidence 0.95
  %(prog)s --update abc123def456 --concepts "hooks,memory,architecture"
  %(prog)s --update abc123def456 --session-id 67a
  %(prog)s --delete abc123def456
''',
    )

    # Modes (mutually exclusive at the top level, enforced in main)
    p.add_argument('--query', '-q', type=str, default=None,
                   help='Text search query')
    p.add_argument('--session', '-s', type=str, default=None,
                   help='Lookup all facts from a session ID (e.g., 66m, 66m-cont15)')
    p.add_argument('--stats', action='store_true',
                   help='Show database statistics')
    p.add_argument('--decay', action='store_true',
                   help='Apply time decay to all fact strengths')
    p.add_argument('--add', type=str, default=None, metavar='CONTENT',
                   help='Add a new fact. Content text (required). Use --type, --importance, --session-id, --concepts, --source-files to set metadata.')
    p.add_argument('--get', type=str, default=None, metavar='FACT_ID',
                   help='Show full detail for a fact by its 12-char hex ID.')
    p.add_argument('--update', type=str, default=None, metavar='FACT_ID',
                   help='Update a fact\'s metadata. Combine with --type, --importance, --confidence, --concepts, --session-id, --source-files.')
    p.add_argument('--delete', type=str, default=None, metavar='FACT_ID',
                   help='Delete a fact by its 12-char hex ID.')

    # Filters / options
    p.add_argument('--type', '-t', type=str, default=None, dest='fact_type',
                   help=f'Filter by fact type: {", ".join(sorted(FACT_TYPES))}')
    p.add_argument('--limit', '-l', type=int, default=DEFAULT_LIMIT,
                   help=f'Max results to return (default: {DEFAULT_LIMIT})')
    p.add_argument('--min-strength', type=float, default=0.0,
                   help='Minimum strength filter (default: 0.0)')
    p.add_argument('--project', '-p', type=str, default=DEFAULT_PROJECT,
                   help=f'Project name (default: {DEFAULT_PROJECT})')
    p.add_argument('--format', '-f', type=str, default='markdown',
                   choices=['markdown', 'json', 'compact'],
                   dest='output_format',
                   help='Output format (default: markdown)')
    p.add_argument('--dry-run', action='store_true',
                   help='With --decay: show what would change without applying')

    # --add / --update metadata options
    p.add_argument('--importance', type=int, default=3, choices=range(1, 6),
                   help='Importance 1-5 (default: 3). Used with --add or --update.')
    p.add_argument('--confidence', type=float, default=0.9,
                   help='Confidence 0.0-1.0 (default: 0.9). Used with --add or --update.')
    p.add_argument('--session-id', type=str, default=None,
                   help='Source session ID (e.g., 66m-cont31). Used with --add or --update (adds to existing).')
    p.add_argument('--concepts', type=str, default=None,
                   help='Comma-separated concept tags. Used with --add or --update.')
    p.add_argument('--source-files', type=str, default=None,
                   help='Comma-separated source file paths. Used with --add or --update.')

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    # --- Validate mode: at least one mode must be chosen ---
    modes = sum([
        args.query is not None,
        args.session is not None,
        args.stats,
        args.decay,
        args.add is not None,
        args.get is not None,
        args.update is not None,
        args.delete is not None,
    ])
    if modes == 0:
        parser.print_help()
        sys.exit(1)
    if modes > 1:
        print('Error: specify only one of --query, --session, --stats, --decay, --add, --get, --update, --delete',
              file=sys.stderr)
        sys.exit(1)

    # --- Validate fact_type filter ---
    if args.fact_type and args.fact_type not in FACT_TYPES:
        print(f'Error: unknown fact type "{args.fact_type}"', file=sys.stderr)
        print(f'Valid types: {", ".join(sorted(FACT_TYPES))}', file=sys.stderr)
        sys.exit(1)

    # --- Check database exists ---
    path = db_path_for_project(args.project)
    if not os.path.exists(path):
        print(f'Error: database not found at {path}', file=sys.stderr)
        print(f'Run memory-bootstrap.py first to create the index for "{args.project}".',
              file=sys.stderr)
        sys.exit(1)

    # --- Open database ---
    db = MemoryDB(args.project)

    try:
        # ===== SEARCH =====
        if args.query is not None:
            results = db.search(
                query=args.query,
                limit=args.limit,
                fact_type=args.fact_type,
                min_strength=args.min_strength,
            )
            if not results:
                print(f'No results found for "{args.query}".')
                print('Try a broader query or remove type/strength filters.')
                sys.exit(0)

            if args.output_format == 'json':
                print(format_search_json(results))
            elif args.output_format == 'compact':
                print(format_search_compact(results))
            else:
                print(format_search_markdown(results, args.query))

        # ===== SESSION LOOKUP =====
        elif args.session is not None:
            facts = db.get_by_session(args.session)
            if not facts:
                print(f'No facts found for session "{args.session}".')
                sys.exit(0)
            print(format_session(facts, args.session))

        # ===== STATS =====
        elif args.stats:
            stats = db.get_stats()
            print(format_stats(stats, args.project))

        # ===== DECAY =====
        elif args.decay:
            if args.dry_run:
                result = compute_decay_dry_run(db)
                total_checked = result.pop('total_checked')
                print(format_decay(result, total_checked, dry_run=True))
            else:
                # Get stats before and after decay for reporting
                stats_before = db.get_stats()
                total = stats_before.get('total_facts', 0)
                db.apply_decay()
                stats_after = db.get_stats()
                tiers = stats_after.get('strength_distribution', {})
                result = {
                    'hot': tiers.get('hot', 0),
                    'warm': tiers.get('warm', 0),
                    'cold': tiers.get('cold', 0),
                    'floor': tiers.get('floor', 0),
                    'decayed_count': total,  # approximate
                }
                print(format_decay(result, total, dry_run=False))

        # ===== ADD FACT =====
        elif args.add is not None:
            ft = args.fact_type or 'fact'
            if ft not in FACT_TYPES:
                print(f'Error: unknown fact type "{ft}"', file=sys.stderr)
                print(f'Valid types: {", ".join(sorted(FACT_TYPES))}', file=sys.stderr)
                sys.exit(1)

            sessions = [args.session_id] if args.session_id else []
            concepts = [c.strip() for c in args.concepts.split(',')] if args.concepts else []
            source_files = [f.strip() for f in args.source_files.split(',')] if args.source_files else []

            fact = Fact(
                id='',
                content=args.add,
                fact_type=ft,
                confidence=args.confidence,
                strength=args.confidence,
                source_session_ids=sessions,
                source_files=source_files,
                source_section='manual',
                concepts=concepts,
                related_files=[],
                importance=args.importance,
            )
            ok = db.add_fact(fact)
            if ok:
                print(f'Added [{ft}] (importance={args.importance}): {truncate(args.add, 100)}')
            else:
                print(f'Duplicate or failed — fact already exists with same content hash.')

        # ===== GET FACT BY ID =====
        elif args.get is not None:
            fact = db.get_by_id(args.get)
            if fact is None:
                print(f'Error: fact not found with id "{args.get}"', file=sys.stderr)
                sys.exit(1)

            if args.output_format == 'json':
                print(format_fact_detail_json(fact))
            elif args.output_format == 'compact':
                print(format_fact_detail_compact(fact))
            else:
                print(format_fact_detail_markdown(fact))

        # ===== UPDATE FACT =====
        elif args.update is not None:
            # Build kwargs from the metadata flags
            kwargs = {}
            if args.fact_type is not None:
                kwargs['fact_type'] = args.fact_type
            # Only pass importance/confidence if explicitly set by the user
            # (check if they differ from defaults — argparse doesn't track "was set")
            # We use a sentinel approach: re-parse to detect explicit flags
            if '--importance' in sys.argv:
                kwargs['importance'] = args.importance
            if '--confidence' in sys.argv:
                kwargs['confidence'] = args.confidence
            if args.concepts is not None:
                kwargs['concepts'] = [c.strip() for c in args.concepts.split(',')]
            if args.source_files is not None:
                kwargs['source_files'] = [f.strip() for f in args.source_files.split(',')]

            # --session-id ADDS to existing source_session_ids (not replaces)
            if args.session_id is not None:
                existing_fact = db.get_by_id(args.update)
                if existing_fact is None:
                    print(f'Error: fact not found with id "{args.update}"', file=sys.stderr)
                    sys.exit(1)
                merged = list(dict.fromkeys(
                    existing_fact.source_session_ids + [args.session_id]
                ))
                kwargs['source_session_ids'] = merged

            if not kwargs:
                print('Error: --update requires at least one field flag (--type, --importance, --confidence, --concepts, --session-id, --source-files)',
                      file=sys.stderr)
                sys.exit(1)

            ok = db.update_fact(args.update, **kwargs)
            if ok:
                changed = ', '.join(kwargs.keys())
                print(f'Updated fact {args.update}: {changed}')
            else:
                print(f'Error: fact not found with id "{args.update}"', file=sys.stderr)
                sys.exit(1)

        # ===== DELETE FACT =====
        elif args.delete is not None:
            ok = db.delete_fact(args.delete)
            if ok:
                print(f'Deleted fact {args.delete}')
            else:
                print(f'Error: fact not found with id "{args.delete}"', file=sys.stderr)
                sys.exit(1)

    finally:
        db.close()


if __name__ == '__main__':
    main()
