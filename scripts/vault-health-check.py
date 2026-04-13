#!/usr/bin/env python3
"""
vault-health-check.py — Validator for MyProject three-layer architecture.

Validates files against layer rules. Used by PreToolUse hooks to block
writes that violate size caps or MOC entry length caps.

Exit codes:
  0 - passes layer rules (or shrinking allowed)
  1 - script error / bad args
  2 - file FAILS layer rules (stderr explains why)

Usage:
    vault-health-check.py --path /path/to/file.md
    vault-health-check.py --path /path/to/file.md --proposed-content-stdin

CRITICAL: Shrinking is always allowed. If proposed content size < current
size, exit 0 even if both exceed cap. Only block when GROWING past cap.
"""

import argparse
import os
import re
import sys

# Import from vault_lib in same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_lib import (
    parse_frontmatter, classify_layer, measure_file, measure_content,
    ROUTER_CAP_BYTES, MOC_ENTRY_CAP_CHARS, LEAF_CAP_BYTES, LEAF_WARN_THRESHOLD
)


def check_router_cap(path: str, current_size: int, proposed_size: int) -> tuple[int, str]:
    """Check router file against size cap."""
    if proposed_size <= ROUTER_CAP_BYTES:
        return (0, "")

    # Shrinking is always allowed
    if proposed_size < current_size:
        return (0, "")

    # Growing past cap
    msg = f"""FAIL: {path}
  Layer: router (cap: {ROUTER_CAP_BYTES} bytes)
  Current size: {current_size} bytes
  Proposed size: {proposed_size} bytes (GROWING)
  Reason: Router file would exceed 15 KB cap and is growing.
  Fix: Refactor narrative content to wikilinks per three-layer architecture."""

    return (2, msg)


def check_moc_entry_cap(path: str, proposed_content: str, max_entry_length: int) -> tuple[int, str]:
    """Check MOC entries against character cap."""
    # Parse table rows and bullet entries
    lines = proposed_content.split('\n')
    violations = []

    for i, line in enumerate(lines, 1):
        # Skip empty lines and headers
        if not line.strip() or line.startswith('---') or line.startswith('#'):
            continue

        # Table row
        if '|' in line:
            # Extract content cells (skip first/last if they're borders)
            cells = [c.strip() for c in line.split('|')]
            cells = [c for c in cells if c]  # Remove empty

            for cell in cells:
                if len(cell) > max_entry_length:
                    violations.append(f"  Line {i}: table cell exceeds {max_entry_length} chars ({len(cell)} chars)")

        # Bullet list item
        elif line.strip().startswith('- '):
            entry = line.strip()[2:]
            if len(entry) > max_entry_length:
                violations.append(f"  Line {i}: bullet entry exceeds {max_entry_length} chars ({len(entry)} chars)")

    if violations:
        msg = f"""FAIL: {path}
  Layer: moc (max entry length: {max_entry_length} chars)
  Violations:
""" + '\n'.join(violations) + """
  Reason: MOC entries exceed character cap.
  Fix: Move paragraph content to leaf files, replace with one-phrase notes."""
        return (2, msg)

    return (0, "")


def check_leaf_cap(path: str, current_size: int, proposed_size: int) -> tuple[int, str]:
    """Check leaf file against size cap."""
    warn_threshold = int(LEAF_CAP_BYTES * LEAF_WARN_THRESHOLD)

    # Pass if under cap
    if proposed_size <= LEAF_CAP_BYTES:
        return (0, "")

    # Shrinking is always allowed
    if current_size > 0 and proposed_size < current_size:
        return (0, "")

    # Growing past 100% cap - hard fail
    msg = f"""FAIL: {path}
  Layer: leaf (cap: {LEAF_CAP_BYTES} bytes, warn at {warn_threshold} bytes)
  Current size: {current_size} bytes
  Proposed size: {proposed_size} bytes (GROWING)
  Reason: Leaf file would exceed 20 KB cap and is growing.
  Fix: Split content into multiple leaf files per three-layer architecture."""

    return (2, msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--path', required=True, help='Path to file to validate')
    ap.add_argument('--proposed-content-stdin', action='store_true',
                   help='Read proposed content from stdin (for Write/Edit hooks)')

    args = ap.parse_args()

    # Validate path
    if not os.path.exists(args.path):
        # File doesn't exist yet - this is a new Write
        # Read proposed content from stdin
        if args.proposed_content_stdin:
            proposed_content = sys.stdin.read()
            proposed_metrics = measure_content(proposed_content)
            proposed_size = proposed_metrics['size_bytes']
            current_size = 0
        else:
            # New file, no proposed content - nothing to validate
            sys.exit(0)
    else:
        # File exists
        current_metrics = measure_file(args.path)
        current_size = current_metrics['size_bytes']

        if args.proposed_content_stdin:
            proposed_content = sys.stdin.read()
            proposed_metrics = measure_content(proposed_content)
            proposed_size = proposed_metrics['size_bytes']
        else:
            # No proposed content - validate current state
            proposed_content = open(args.path, 'r', encoding='utf-8').read()
            proposed_size = current_size

    # Parse frontmatter and classify layer
    if args.proposed_content_stdin:
        fm = parse_frontmatter_from_content(proposed_content)
    else:
        fm = parse_frontmatter(args.path)

    layer = classify_layer(fm, args.path)

    # Validate by layer
    if layer == 'router':
        exit_code, msg = check_router_cap(args.path, current_size, proposed_size)
    elif layer == 'moc':
        # Check entry cap
        max_entry = fm.get('max_entry_length', MOC_ENTRY_CAP_CHARS)
        if isinstance(max_entry, str):
            max_entry = int(max_entry)
        exit_code, msg = check_moc_entry_cap(args.path, proposed_content, max_entry)
    elif layer == 'leaf':
        exit_code, msg = check_leaf_cap(args.path, current_size, proposed_size)
    else:
        # meta, unknown, or other - no caps
        exit_code, msg = (0, "")

    if msg:
        print(msg, file=sys.stderr)

    sys.exit(exit_code)


def parse_frontmatter_from_content(content: str) -> dict:
    """Parse frontmatter from in-memory content."""
    if not content.startswith('---\n'):
        return {}

    end_idx = content.find('\n---\n', 4)
    if end_idx == -1:
        end_idx = content.find('\n---', 4)
        if end_idx == -1:
            return {}

    yaml_block = content[4:end_idx]

    # Reuse parsing logic from vault_lib
    from vault_lib import parse_frontmatter
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(content)
        temp_path = f.name

    try:
        fm = parse_frontmatter(temp_path)
    finally:
        os.unlink(temp_path)

    return fm


if __name__ == '__main__':
    main()
