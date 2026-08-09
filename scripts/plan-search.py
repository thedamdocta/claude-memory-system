#!/usr/bin/env python3
"""
plan-search.py — Multi-match context search for large vault files.

Fills the gap vault-query --content has with large unstructured files (planning
conversations, long reference docs): returns ALL matches with surrounding
context, not just the first. Use | for OR matching between terms.

Usage:
    plan-search.py "gate|solthralis"                     # search all planning convs
    plan-search.py "gate|solthralis" -C 10               # 10 lines context (default 8)
    plan-search.py "gate" --path "References/Planning Conversations/26 Branch · POWERS AND THEMES.md"
    plan-search.py "grey realm" --path "References/Planning Conversations/"
    plan-search.py "Hamick|gate|vibrat" --limit 5        # max 5 matches per file
    plan-search.py "solthralis" --files-only             # just list which files match
"""

import argparse
import os
import sys

DEFAULT_SEARCH_PATH = "References/Planning Conversations"
# Empty by default — a hardcoded personal path here would point at a stranger's
# machine once installed. resolve_vault_root() handles discovery.
DEFAULT_VAULT_ROOT = os.environ.get("DEFAULT_VAULT_ROOT", "")
try:
    from vault_lib import resolve_vault_root
except ImportError:                                    # stand-alone use
    def resolve_vault_root(explicit=None):
        return (explicit or DEFAULT_VAULT_ROOT), "fallback"


def search_file(filepath: str, terms: list, context: int = 8, limit: int = 0) -> list:
    """
    Find ALL lines matching any term in filepath, with surrounding context.
    Non-overlapping windows: if two matches are within the context window of
    each other, they are merged into one block rather than shown twice.

    Returns list of dicts: {line_num, line_text, context, filepath}
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.read().split('\n')
    except (OSError, UnicodeDecodeError):
        return []

    results = []
    last_match_end = -1

    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(t in line_lower for t in terms):
            # Skip if this match falls inside the previous context window (merge)
            if i <= last_match_end:
                continue

            start = max(0, i - context)
            end = min(len(lines), i + context + 1)
            last_match_end = end

            context_lines = []
            for j in range(start, end):
                prefix = ">>>" if j == i else "   "
                context_lines.append(f"{prefix} L{j+1}: {lines[j].rstrip()[:200]}")

            results.append({
                'line_num': i + 1,
                'line_text': line.strip()[:200],
                'context': '\n'.join(context_lines),
                'filepath': filepath,
            })

            if limit and len(results) >= limit:
                break

    return results


def collect_files(target: str) -> list:
    """Return sorted list of .md file paths under target (file or directory)."""
    if os.path.isfile(target):
        return [target]
    files = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for fname in sorted(filenames):
            if fname.endswith('.md'):
                files.append(os.path.join(dirpath, fname))
    return files


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument('query',
                    help='Search terms. Use | for OR: "gate|solthralis|vibrat"')
    ap.add_argument('-C', '--context', type=int, default=8,
                    help='Lines of context around each match (default: 8)')
    ap.add_argument('--path', default=DEFAULT_SEARCH_PATH,
                    help=f'File or directory to search, relative to vault root '
                         f'or absolute (default: {DEFAULT_SEARCH_PATH})')
    ap.add_argument('--root', default=None,
                    help='Vault root. Default: $VAULT_ROOT, else $CLAUDE_PROJECT_DIR, '
                         'else the nearest .obsidian/ above the working directory.')
    ap.add_argument('--limit', type=int, default=0,
                    help='Max matches per file, 0 = unlimited (default: 0)')
    ap.add_argument('--files-only', action='store_true',
                    help='Only print names of files that contain a match')

    args = ap.parse_args()
    args.root, _src = resolve_vault_root(args.root)

    terms = [t.strip().lower() for t in args.query.split('|') if t.strip()]
    if not terms:
        print("ERROR: no search terms provided.", file=sys.stderr)
        sys.exit(1)

    # Resolve target path
    if os.path.isabs(args.path):
        target = args.path
    else:
        target = os.path.join(args.root, args.path)

    if not os.path.exists(target):
        print(f"ERROR: path not found: {target}", file=sys.stderr)
        sys.exit(1)

    files = collect_files(target)
    if not files:
        print("No .md files found at target path.", file=sys.stderr)
        sys.exit(1)

    total_matches = 0
    files_with_matches = 0

    for filepath in files:
        rel_path = os.path.relpath(filepath, args.root)

        if args.files_only:
            try:
                body = open(filepath, encoding='utf-8').read().lower()
                if any(t in body for t in terms):
                    print(rel_path)
                    files_with_matches += 1
            except (OSError, UnicodeDecodeError):
                pass
            continue

        matches = search_file(filepath, terms, args.context, args.limit)
        if not matches:
            continue

        files_with_matches += 1
        total_matches += len(matches)

        print(f"\n{'=' * 60}")
        print(f"FILE: {rel_path}  ({len(matches)} match{'es' if len(matches) != 1 else ''})")
        print(f"{'=' * 60}")

        for m in matches:
            print(f"\n--- Match at L{m['line_num']} ---")
            print(m['context'])

    if total_matches == 0 and not args.files_only:
        print("No matches found.")
    elif args.files_only and files_with_matches == 0:
        print("No matching files.")
    elif not args.files_only:
        print(f"\n[{total_matches} total match{'es' if total_matches != 1 else ''} "
              f"in {files_with_matches} file{'s' if files_with_matches != 1 else ''}]")


if __name__ == '__main__':
    main()
