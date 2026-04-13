#!/usr/bin/env python3
"""
memory-bootstrap.py — One-time extraction pipeline for persistent memory.

Reads all compaction .md files from a project's compactions/ directory,
extracts structured facts using rule-based extraction from memory_lib,
and stores them in the project's SQLite memory database.

Idempotent: tracks processed files by mtime so re-runs skip unchanged files.
Use --force to reprocess everything.

Usage:
    memory-bootstrap.py --project my-project           # incremental
    memory-bootstrap.py --project my-project --force    # full reprocess
    memory-bootstrap.py --project my-project --dry-run  # preview only
    memory-bootstrap.py --project my-project --verbose  # show per-file details
"""

import argparse
import glob
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# Import memory_lib from the scripts directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.expanduser('~/.claude/scripts'))
from memory_lib import MemoryDB, extract_facts_from_file, extract_session_id, extract_facts_from_vault_doc, extract_facts_from_procedure


# ---------------------------------------------------------------------------
# Project root mapping
# ---------------------------------------------------------------------------
PROJECT_ROOTS = {
    'my-project': '__VAULT_PATH__',
    # More projects can be added here
}

# Paths
MEMORY_INDEX_DIR = os.path.expanduser('~/.claude/memory-index')
BOOTSTRAP_CACHE_DIR = os.path.join(MEMORY_INDEX_DIR, 'bootstrap-cache')


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


def get_vault_cache_path(project: str) -> str:
    """Return the path to the vault idempotency cache JSON for a project."""
    return os.path.join(BOOTSTRAP_CACHE_DIR, project, 'vault-processed-files.json')


def load_vault_cache(project: str) -> dict:
    """Load the vault idempotency cache. Returns empty dict if no cache exists."""
    path = get_vault_cache_path(project)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_vault_cache(project: str, cache: dict) -> None:
    """Write the vault idempotency cache to disk."""
    path = get_vault_cache_path(project)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def find_compaction_files(project_root: str) -> list[str]:
    """
    Find all compaction .md files in the project's compactions/ directory.

    Returns sorted list of absolute paths.
    """
    compactions_dir = os.path.join(project_root, 'compactions')
    if not os.path.isdir(compactions_dir):
        return []
    pattern = os.path.join(compactions_dir, '*compaction*.md')
    files = glob.glob(pattern)
    files.sort()
    return files


def relative_path(filepath: str, project_root: str) -> str:
    """Convert absolute path to project-relative path for cache keys."""
    return os.path.relpath(filepath, project_root)


def format_number(n: int) -> str:
    """Format an integer with comma separators."""
    return f'{n:,}'


# ---------------------------------------------------------------------------
# Vault doc directories and skip patterns
# ---------------------------------------------------------------------------

VAULT_SCAN_DIRS = [
    'characters',
    'Lore',
    'Factions',
    'Episodes',
    'Synthesis',
    'References',
    'Planning',
]

# Root-level file patterns to include (matched against project root, non-recursive)
VAULT_ROOT_PATTERNS = ['_*.md']

# Root-level files to skip even if they match VAULT_ROOT_PATTERNS
VAULT_ROOT_SKIP = {'_SESSION_ARCHIVE.md'}

# Directories to skip entirely (relative to project root)
VAULT_SKIP_DIRS = {
    '_workspace',
    'compactions',
    'References/Planning Conversations',
    'References/Notes App',
}

# Filename patterns to skip
_BACKUP_RE = re.compile(r'\.backup-.*\.md$')


def find_vault_files(project_root: str) -> list[str]:
    """
    Find all .md files in vault scan directories + root-level patterns,
    excluding backups and skip directories.

    Returns sorted list of absolute paths.
    """
    import fnmatch

    files = []

    # Scan subdirectories
    for scan_dir in VAULT_SCAN_DIRS:
        full_dir = os.path.join(project_root, scan_dir)
        if not os.path.isdir(full_dir):
            continue
        for root, dirs, filenames in os.walk(full_dir):
            # Check if this directory should be skipped
            rel_root = os.path.relpath(root, project_root)
            skip = False
            for skip_dir in VAULT_SKIP_DIRS:
                if rel_root == skip_dir or rel_root.startswith(skip_dir + os.sep):
                    skip = True
                    break
            if skip:
                continue

            for filename in filenames:
                if not filename.endswith('.md'):
                    continue
                # Skip backup files
                if _BACKUP_RE.search(filename):
                    continue
                files.append(os.path.join(root, filename))

    # Scan root-level files matching patterns
    for pattern in VAULT_ROOT_PATTERNS:
        for filepath in glob.glob(os.path.join(project_root, pattern)):
            basename = os.path.basename(filepath)
            if basename in VAULT_ROOT_SKIP:
                continue
            if _BACKUP_RE.search(basename):
                continue
            files.append(filepath)

    files.sort()
    return files


def run_vault_bootstrap(
    project: str,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """
    Vault doc bootstrap pipeline.

    1. Find all vault .md files (characters, lore, factions, episodes, synthesis)
    2. Check idempotency cache to skip already-processed files
    3. Extract facts from each new/modified file using vault extraction rules
    4. Store facts in MemoryDB (same DB as compaction facts)
    5. Update vault idempotency cache
    6. Print summary
    """
    if project not in PROJECT_ROOTS:
        print(f'Error: Unknown project "{project}"')
        print(f'Known projects: {", ".join(sorted(PROJECT_ROOTS.keys()))}')
        sys.exit(1)

    project_root = PROJECT_ROOTS[project]
    if not os.path.isdir(project_root):
        print(f'Error: Project root does not exist: {project_root}')
        sys.exit(1)

    print(f'Vault Bootstrap: {project}')
    print(f'  Scanning directories:')
    for d in VAULT_SCAN_DIRS:
        full_d = os.path.join(project_root, d)
        exists = os.path.isdir(full_d)
        marker = '  ' if exists else '  (not found)'
        print(f'    {d}/{marker}')

    # Step 1: Find all vault files
    all_files = find_vault_files(project_root)
    print(f'  Found: {len(all_files)} vault doc files')

    if not all_files:
        print('  No vault doc files found. Nothing to do.')
        return

    # Step 2: Check idempotency cache
    cache = {} if force else load_vault_cache(project)
    files_to_process = []
    skipped = 0

    for filepath in all_files:
        rel = relative_path(filepath, project_root)
        mtime = os.path.getmtime(filepath)

        if not force and rel in cache:
            cached_mtime = cache[rel].get('mtime', 0)
            if cached_mtime == mtime:
                skipped += 1
                continue

        files_to_process.append(filepath)

    print(f'  Skipping: {skipped} already processed')
    print(f'  Processing: {len(files_to_process)} files...')

    if not files_to_process:
        print('\n  Nothing to process. All files up to date.')
        return

    print()

    # Step 3 & 4: Extract and store facts
    db = None
    if not dry_run:
        db = MemoryDB(project)

    total_facts = 0
    new_facts = 0
    merged_facts = 0
    failures = []
    type_counts = {}
    file_results = {}

    for idx, filepath in enumerate(files_to_process, 1):
        rel = relative_path(filepath, project_root)
        basename = os.path.basename(filepath)

        try:
            facts = extract_facts_from_vault_doc(filepath)
        except Exception as e:
            failures.append((basename, str(e)))
            if verbose:
                print(f'  [{idx}/{len(files_to_process)}] {basename}: ERROR - {e}')
            continue

        fact_count = len(facts)
        total_facts += fact_count
        file_results[rel] = fact_count

        if verbose:
            print(f'  [{idx}/{len(files_to_process)}] {basename}: {fact_count} facts')

        if dry_run and verbose:
            for i, fact in enumerate(facts[:3]):
                truncated = fact.content[:100]
                if len(fact.content) > 100:
                    truncated += '...'
                print(f'      [{fact.fact_type}] {truncated}')
            if fact_count > 3:
                print(f'      ... and {fact_count - 3} more')
            if fact_count > 0:
                print()

        if not dry_run:
            for fact in facts:
                is_new = db.add_fact(fact)
                if is_new:
                    new_facts += 1
                else:
                    merged_facts += 1
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1
        else:
            new_facts += fact_count
            for fact in facts:
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1

        # Progress indicator for non-verbose mode
        if not verbose and idx % 10 == 0:
            print(f'  [{idx}/{len(files_to_process)}]...', end='\r')

    if not verbose and len(files_to_process) >= 10:
        print(f'  [{len(files_to_process)}/{len(files_to_process)}]...done')

    # Step 5: Update idempotency cache (skip if dry-run)
    if not dry_run:
        now = time.time()
        for filepath in files_to_process:
            rel = relative_path(filepath, project_root)
            basename = os.path.basename(filepath)
            if basename not in [f[0] for f in failures]:
                cache[rel] = {
                    'mtime': os.path.getmtime(filepath),
                    'facts_extracted': file_results.get(rel, 0),
                    'processed_at': now,
                }
        save_vault_cache(project, cache)

    # Step 6: Print summary
    mode_label = ' (DRY RUN)' if dry_run else ''
    print(f'\n  Vault bootstrap complete{mode_label}:')
    print(f'    Files processed: {format_number(len(files_to_process) - len(failures))}')
    print(f'    Total facts extracted: {format_number(total_facts)}')
    print(f'    New facts: {format_number(new_facts)}')
    print(f'    Merged (duplicate): {format_number(merged_facts)}')

    if type_counts:
        print(f'\n    Type distribution:')
        for fact_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f'      {fact_type}: {format_number(count)}')

    if not dry_run and db:
        stats = db.get_stats()
        print(f'\n    DB total facts (all sources): {format_number(stats["total_facts"])}')

    if failures:
        print(f'\n    Failures ({len(failures)}):')
        for filename, error in failures:
            print(f'      {filename}: {error}')

    if db:
        db.close()


def run_bootstrap(
    project: str,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """
    Main bootstrap pipeline.

    1. Find all compaction files
    2. Check idempotency cache to skip already-processed files
    3. Extract facts from each new/modified file
    4. Store facts in MemoryDB
    5. Update idempotency cache
    6. Print summary
    """
    if project not in PROJECT_ROOTS:
        print(f'Error: Unknown project "{project}"')
        print(f'Known projects: {", ".join(sorted(PROJECT_ROOTS.keys()))}')
        sys.exit(1)

    project_root = PROJECT_ROOTS[project]
    if not os.path.isdir(project_root):
        print(f'Error: Project root does not exist: {project_root}')
        sys.exit(1)

    print(f'Bootstrap: {project}')
    print(f'  Scanning: {os.path.join(project_root, "compactions")}/')

    # Step 1: Find all compaction files
    all_files = find_compaction_files(project_root)
    print(f'  Found: {len(all_files)} compaction files')

    if not all_files:
        print('  No compaction files found. Nothing to do.')
        return

    # Step 2: Check idempotency cache
    cache = {} if force else load_cache(project)
    files_to_process = []
    skipped = 0

    for filepath in all_files:
        rel = relative_path(filepath, project_root)
        mtime = os.path.getmtime(filepath)

        if not force and rel in cache:
            cached_mtime = cache[rel].get('mtime', 0)
            if cached_mtime == mtime:
                skipped += 1
                continue

        files_to_process.append(filepath)

    print(f'  Skipping: {skipped} already processed')
    print(f'  Processing: {len(files_to_process)} files...')

    if not files_to_process:
        print('\n  Nothing to process. All files up to date.')
        return

    print()

    # Step 3 & 4: Extract and store facts
    db = None
    if not dry_run:
        db = MemoryDB(project)

    total_facts = 0
    new_facts = 0
    merged_facts = 0
    failures = []
    type_counts = {}
    file_results = {}  # rel_path -> fact_count (for cache update)

    for idx, filepath in enumerate(files_to_process, 1):
        rel = relative_path(filepath, project_root)
        basename = os.path.basename(filepath)

        try:
            facts = extract_facts_from_file(filepath)
        except Exception as e:
            failures.append((basename, str(e)))
            if verbose:
                print(f'  [{idx}/{len(files_to_process)}] {basename}: ERROR - {e}')
            continue

        fact_count = len(facts)
        total_facts += fact_count
        file_results[rel] = fact_count

        if verbose:
            print(f'  [{idx}/{len(files_to_process)}] {basename}: {fact_count} facts')

        if dry_run and verbose:
            # Show first 3 facts in dry-run+verbose mode
            for i, fact in enumerate(facts[:3]):
                truncated = fact.content[:100]
                if len(fact.content) > 100:
                    truncated += '...'
                print(f'      [{fact.fact_type}] {truncated}')
            if fact_count > 3:
                print(f'      ... and {fact_count - 3} more')
            if fact_count > 0:
                print()

        if not dry_run:
            for fact in facts:
                is_new = db.add_fact(fact)
                if is_new:
                    new_facts += 1
                else:
                    merged_facts += 1

                # Track type distribution
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1
        else:
            # In dry-run, all facts count as "new" for display purposes
            new_facts += fact_count
            for fact in facts:
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1

        # Progress indicator for non-verbose mode
        if not verbose and idx % 10 == 0:
            print(f'  [{idx}/{len(files_to_process)}]...', end='\r')

    if not verbose and len(files_to_process) >= 10:
        # Clear the progress line
        print(f'  [{len(files_to_process)}/{len(files_to_process)}]...done')

    # Step 5: Update idempotency cache (skip if dry-run)
    if not dry_run:
        now = time.time()
        for filepath in files_to_process:
            rel = relative_path(filepath, project_root)
            basename = os.path.basename(filepath)
            if basename not in [f[0] for f in failures]:
                cache[rel] = {
                    'mtime': os.path.getmtime(filepath),
                    'facts_extracted': file_results.get(rel, 0),
                    'processed_at': now,
                }
        save_cache(project, cache)

    # Step 6: Print summary
    mode_label = ' (DRY RUN)' if dry_run else ''
    print(f'\n  Bootstrap complete{mode_label}:')
    print(f'    Files processed: {format_number(len(files_to_process) - len(failures))}')
    print(f'    Total facts extracted: {format_number(total_facts)}')
    print(f'    New facts: {format_number(new_facts)}')
    print(f'    Merged (duplicate): {format_number(merged_facts)}')

    if type_counts:
        print(f'\n    Type distribution:')
        # Sort by count descending
        for fact_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f'      {fact_type}: {format_number(count)}')

    # Print DB stats if we actually wrote
    if not dry_run and db:
        stats = db.get_stats()
        print(f'\n    DB total facts: {format_number(stats["total_facts"])}')

    # Print failures
    if failures:
        print(f'\n    Failures ({len(failures)}):')
        for filename, error in failures:
            print(f'      {filename}: {error}')

    # Cleanup
    if db:
        db.close()


def find_procedure_files(project: str) -> list[str]:
    """
    Find all procedure .md files for a project.

    Scans ~/.claude/procedures/shared/ and ~/.claude/procedures/<project>/
    Returns sorted list of absolute paths.
    """
    procedures_base = os.path.expanduser('~/.claude/procedures')
    scan_dirs = ['shared', project]
    files = []

    for scope in scan_dirs:
        scope_dir = os.path.join(procedures_base, scope)
        if not os.path.isdir(scope_dir):
            continue
        for filename in os.listdir(scope_dir):
            if filename.endswith('.md'):
                files.append(os.path.join(scope_dir, filename))

    files.sort()
    return files


def run_procedure_bootstrap(
    project: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """
    Procedure bootstrap pipeline.

    Scans ~/.claude/procedures/shared/ and ~/.claude/procedures/<project>/,
    extracts facts from each active procedure file, and stores them in MemoryDB.

    Always reprocesses all procedure files (no idempotency cache -- procedure
    files are small and few, so full reprocess is cheap).
    """
    print(f'Procedure Bootstrap: {project}')

    all_files = find_procedure_files(project)
    print(f'  Found: {len(all_files)} procedure files')

    if not all_files:
        print('  No procedure files found. Nothing to do.')
        return

    db = None
    if not dry_run:
        db = MemoryDB(project)

    total_facts = 0
    new_facts = 0
    merged_facts = 0
    type_counts = {}
    failures = []

    for idx, filepath in enumerate(all_files, 1):
        basename = os.path.basename(filepath)
        # Determine scope from path
        parent = os.path.basename(os.path.dirname(filepath))
        rel_label = f'{parent}/{basename}'

        try:
            facts = extract_facts_from_procedure(filepath)
        except Exception as e:
            failures.append((rel_label, str(e)))
            if verbose:
                print(f'  [{idx}/{len(all_files)}] {rel_label}: ERROR - {e}')
            continue

        fact_count = len(facts)
        total_facts += fact_count

        if verbose:
            print(f'  [{idx}/{len(all_files)}] {rel_label}: {fact_count} facts')

        if dry_run and verbose:
            for fact in facts[:3]:
                truncated = fact.content[:100]
                if len(fact.content) > 100:
                    truncated += '...'
                print(f'      [{fact.fact_type}] {truncated}')
            if fact_count > 3:
                print(f'      ... and {fact_count - 3} more')
            if fact_count > 0:
                print()

        if not dry_run:
            for fact in facts:
                is_new = db.add_fact(fact)
                if is_new:
                    new_facts += 1
                else:
                    merged_facts += 1
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1
        else:
            new_facts += fact_count
            for fact in facts:
                type_counts[fact.fact_type] = type_counts.get(fact.fact_type, 0) + 1

    mode_label = ' (DRY RUN)' if dry_run else ''
    print(f'\n  Procedure bootstrap complete{mode_label}:')
    print(f'    Files processed: {format_number(len(all_files) - len(failures))}')
    print(f'    Total facts extracted: {format_number(total_facts)}')
    print(f'    New facts: {format_number(new_facts)}')
    print(f'    Merged (duplicate): {format_number(merged_facts)}')

    if type_counts:
        print(f'\n    Type distribution:')
        for fact_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f'      {fact_type}: {format_number(count)}')

    if not dry_run and db:
        stats = db.get_stats()
        print(f'\n    DB total facts (all sources): {format_number(stats["total_facts"])}')

    if failures:
        print(f'\n    Failures ({len(failures)}):')
        for label, error in failures:
            print(f'      {label}: {error}')

    if db:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description='Bootstrap persistent memory from compaction, vault, or procedure files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --project my-project                      # compaction bootstrap (incremental)
  %(prog)s --project my-project --vault              # vault doc bootstrap (incremental)
  %(prog)s --project my-project --procedures         # procedure bootstrap
  %(prog)s --project my-project --vault --force      # vault doc bootstrap (full reprocess)
  %(prog)s --project my-project --vault --dry-run    # vault preview only
  %(prog)s --project my-project --force              # compaction full reprocess
  %(prog)s --project my-project --verbose            # show each fact extracted""",
    )
    parser.add_argument(
        '--project', '-p',
        required=True,
        choices=sorted(PROJECT_ROOTS.keys()),
        help='Project to bootstrap (must be in PROJECT_ROOTS mapping)',
    )
    parser.add_argument(
        '--vault',
        action='store_true',
        help='Index vault docs (characters, lore, factions, episodes, synthesis) instead of compactions',
    )
    parser.add_argument(
        '--procedures',
        action='store_true',
        help='Index procedure files from ~/.claude/procedures/ (shared + project-specific)',
    )
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Ignore idempotency cache and reprocess all files',
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Show what would be extracted without writing to DB',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show per-file extraction details',
    )

    args = parser.parse_args()

    if args.procedures:
        run_procedure_bootstrap(
            project=args.project,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.vault:
        run_vault_bootstrap(
            project=args.project,
            force=args.force,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    else:
        run_bootstrap(
            project=args.project,
            force=args.force,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )


if __name__ == '__main__':
    main()
