#!/usr/bin/env python3
"""
memory-consolidate.py — Incremental fact extraction for the the agent Memory System.

Processes a single compaction file (or a batch of unprocessed files) and indexes
the extracted facts into the project's MemoryDB. Designed to be called by the
PostCompact hook (memory-capture.sh) after every context compaction.

Single-file mode (called by the hook):
    memory-consolidate.py --file /path/to/compaction.md --project my-project
    memory-consolidate.py --file /path/to/compaction.md --project my-project --dry-run

Batch mode (process all unprocessed compaction files):
    memory-consolidate.py --mode batch --project my-project
    memory-consolidate.py --mode batch --project my-project --dry-run

Idempotent: uses the same bootstrap cache as memory-bootstrap.py so running
on the same file twice merges rather than duplicates. The MemoryDB.add_fact()
method handles dedup by content hash — identical content returns False (merged),
new content returns True (inserted).
"""

import argparse
import glob
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# Import memory_lib from the scripts directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.expanduser('~/.claude/scripts'))
from memory_lib import MemoryDB, extract_facts_from_file, extract_session_id


# ---------------------------------------------------------------------------
# Project root mapping (must match memory-bootstrap.py)
# ---------------------------------------------------------------------------
PROJECT_ROOTS = {
    'my-project': '__VAULT_PATH__',
}

# Paths
MEMORY_INDEX_DIR = os.path.expanduser('~/.claude/memory-index')
BOOTSTRAP_CACHE_DIR = os.path.join(MEMORY_INDEX_DIR, 'bootstrap-cache')


# ---------------------------------------------------------------------------
# Idempotency cache (shared with memory-bootstrap.py)
# ---------------------------------------------------------------------------

def get_cache_path(project: str) -> str:
    """Return the path to the idempotency cache JSON for a project."""
    return os.path.join(BOOTSTRAP_CACHE_DIR, project, 'processed-files.json')


def load_cache(project: str) -> dict:
    """Load the idempotency cache. Returns empty dict if no cache exists."""
    path = get_cache_path(project)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(project: str, cache: dict) -> None:
    """Write the idempotency cache to disk."""
    path = get_cache_path(project)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def relative_path(filepath: str, project_root: str) -> str:
    """Convert absolute path to project-relative path for cache keys."""
    return os.path.relpath(filepath, project_root)


# ---------------------------------------------------------------------------
# Single-file consolidation
# ---------------------------------------------------------------------------

def consolidate_file(
    filepath: str,
    project: str,
    dry_run: bool = False,
) -> dict:
    """
    Extract facts from a single compaction file and index them.

    Returns a summary dict with keys:
        extracted: int — total facts extracted from the file
        new: int — facts newly inserted into the DB
        merged: int — facts that matched existing entries (deduplicated)
        session_id: str — the session ID parsed from the filename
        error: str or None — error message if extraction failed
    """
    result = {
        'extracted': 0,
        'new': 0,
        'merged': 0,
        'session_id': '',
        'error': None,
    }

    if not os.path.isfile(filepath):
        result['error'] = f'File not found: {filepath}'
        return result

    project_root = PROJECT_ROOTS.get(project)
    if not project_root:
        result['error'] = f'Unknown project: {project}'
        return result

    session_id = extract_session_id(filepath)
    result['session_id'] = session_id

    # Extract facts
    try:
        facts = extract_facts_from_file(filepath)
    except Exception as e:
        result['error'] = f'Extraction failed: {e}'
        return result

    result['extracted'] = len(facts)

    if dry_run:
        result['new'] = len(facts)
        return result

    # Index into MemoryDB
    db = MemoryDB(project)
    try:
        for fact in facts:
            is_new = db.add_fact(fact)
            if is_new:
                result['new'] += 1
            else:
                result['merged'] += 1
    finally:
        db.close()

    # Update the idempotency cache (shared with memory-bootstrap.py)
    cache = load_cache(project)
    rel = relative_path(filepath, project_root)
    cache[rel] = {
        'mtime': os.path.getmtime(filepath),
        'facts_extracted': len(facts),
        'processed_at': time.time(),
    }
    save_cache(project, cache)

    return result


# ---------------------------------------------------------------------------
# Batch mode — process all unprocessed compaction files
# ---------------------------------------------------------------------------

def consolidate_batch(
    project: str,
    dry_run: bool = False,
) -> None:
    """
    Find and process all unprocessed compaction files for a project.

    Uses the idempotency cache to skip files that have already been processed
    at their current mtime. Prints a summary when done.
    """
    project_root = PROJECT_ROOTS.get(project)
    if not project_root:
        print(f'Error: Unknown project "{project}"')
        print(f'Known projects: {", ".join(sorted(PROJECT_ROOTS.keys()))}')
        sys.exit(1)

    compactions_dir = os.path.join(project_root, 'compactions')
    if not os.path.isdir(compactions_dir):
        print(f'No compactions directory found at: {compactions_dir}')
        return

    # Find all compaction files
    pattern = os.path.join(compactions_dir, '*compaction*.md')
    all_files = sorted(glob.glob(pattern))

    if not all_files:
        print('No compaction files found.')
        return

    # Check cache to find unprocessed files
    cache = load_cache(project)
    files_to_process = []

    for filepath in all_files:
        rel = relative_path(filepath, project_root)
        mtime = os.path.getmtime(filepath)

        if rel in cache:
            cached_mtime = cache[rel].get('mtime', 0)
            if cached_mtime == mtime:
                continue  # Already processed at this mtime

        files_to_process.append(filepath)

    mode_label = ' (DRY RUN)' if dry_run else ''
    print(f'Batch consolidation{mode_label}: {project}')
    print(f'  Total compaction files: {len(all_files)}')
    print(f'  Already processed: {len(all_files) - len(files_to_process)}')
    print(f'  To process: {len(files_to_process)}')

    if not files_to_process:
        print('\n  All files up to date. Nothing to process.')
        return

    total_extracted = 0
    total_new = 0
    total_merged = 0
    failures = []

    for idx, filepath in enumerate(files_to_process, 1):
        basename = os.path.basename(filepath)
        result = consolidate_file(filepath, project, dry_run=dry_run)

        if result['error']:
            failures.append((basename, result['error']))
            print(f'  [{idx}/{len(files_to_process)}] {basename}: ERROR - {result["error"]}')
            continue

        total_extracted += result['extracted']
        total_new += result['new']
        total_merged += result['merged']
        print(f'  [{idx}/{len(files_to_process)}] {basename}: '
              f'{result["extracted"]} facts ({result["new"]} new, {result["merged"]} merged)')

    print(f'\n  Batch complete{mode_label}:')
    print(f'    Files processed: {len(files_to_process) - len(failures)}')
    print(f'    Total facts extracted: {total_extracted}')
    print(f'    New facts: {total_new}')
    print(f'    Merged: {total_merged}')

    if failures:
        print(f'\n    Failures ({len(failures)}):')
        for filename, error in failures:
            print(f'      {filename}: {error}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Incremental fact extraction from compaction files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --file /path/to/compaction.md --project my-project
  %(prog)s --file /path/to/compaction.md --project my-project --dry-run
  %(prog)s --mode batch --project my-project
  %(prog)s --mode batch --project my-project --dry-run""",
    )
    parser.add_argument(
        '--file', '-f',
        help='Path to a single compaction .md file to process',
    )
    parser.add_argument(
        '--project', '-p',
        required=True,
        choices=sorted(PROJECT_ROOTS.keys()),
        help='Project name (must be in PROJECT_ROOTS mapping)',
    )
    parser.add_argument(
        '--mode', '-m',
        choices=['single', 'batch'],
        default='single',
        help='Processing mode: "single" (default, requires --file) or "batch" (all unprocessed)',
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Show what would be extracted without writing to DB',
    )

    args = parser.parse_args()

    if args.mode == 'batch':
        consolidate_batch(project=args.project, dry_run=args.dry_run)
    else:
        if not args.file:
            parser.error('--file is required in single mode')

        filepath = os.path.abspath(args.file)
        result = consolidate_file(filepath, args.project, dry_run=args.dry_run)

        mode_label = ' (DRY RUN)' if args.dry_run else ''
        if result['error']:
            print(f'Error: {result["error"]}')
            sys.exit(1)

        print(f'Consolidate{mode_label}: {os.path.basename(filepath)}')
        print(f'  Session: {result["session_id"]}')
        print(f'  Facts extracted: {result["extracted"]}')
        print(f'  New: {result["new"]}')
        print(f'  Merged: {result["merged"]}')


if __name__ == '__main__':
    main()
