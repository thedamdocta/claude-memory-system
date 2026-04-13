#!/usr/bin/env python3
"""
vault_lib.py — Shared library for MyProject vault three-layer architecture.

Provides frontmatter parsing, vault walking, layer classification, and file
measurement for the enforcement hooks and query tooling.
"""

import os
import re
from pathlib import Path
from typing import Iterator, Optional

# Layer caps and thresholds
ROUTER_CAP_BYTES = 15360  # 15 KB
MOC_ENTRY_CAP_CHARS = 200
LEAF_CAP_BYTES = 20480  # 20 KB
LEAF_WARN_THRESHOLD = 0.8  # 80%
DEFAULT_VAULT_ROOT = "__VAULT_PATH__"

# Directories to skip during vault walk
SKIP_DIRS = {".git", "node_modules", ".claude", "__pycache__", ".venv", "venv"}


def parse_frontmatter(path: str) -> dict:
    """
    Parse YAML frontmatter from a markdown file.

    Returns dict of frontmatter fields. Empty dict if no frontmatter.
    Frontmatter must be at the top of the file, delimited by '---'.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return {}

    # Check for frontmatter delimiters
    if not content.startswith('---\n'):
        return {}

    # Find closing delimiter
    end_idx = content.find('\n---\n', 4)
    if end_idx == -1:
        # Try alternate ending (---EOF)
        end_idx = content.find('\n---', 4)
        if end_idx == -1:
            return {}

    yaml_block = content[4:end_idx]

    # Simple YAML parser for common patterns
    fm = {}
    current_key = None
    current_list = []
    in_multiline = False
    multiline_key = None
    multiline_content = []

    for line in yaml_block.split('\n'):
        # Handle multiline values (key: >)
        if in_multiline:
            if line and not line[0].isspace() and ':' in line:
                # New key, end multiline
                fm[multiline_key] = ' '.join(multiline_content).strip()
                in_multiline = False
                multiline_content = []
            else:
                if line.strip():
                    multiline_content.append(line.strip())
                continue

        # Skip empty lines
        if not line.strip():
            continue

        # List item
        if line.strip().startswith('- '):
            if current_key:
                item = line.strip()[2:].strip('"').strip("'")
                current_list.append(item)
            continue

        # Key-value pair
        if ':' in line:
            # Save previous list if any
            if current_key and current_list:
                fm[current_key] = current_list
                current_list = []

            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()

            # Handle multiline indicator
            if value == '>':
                in_multiline = True
                multiline_key = key
                continue

            # Handle quoted strings
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            # Handle list start on same line
            if value.startswith('[') and value.endswith(']'):
                items = value[1:-1].split(',')
                fm[key] = [item.strip().strip('"').strip("'") for item in items if item.strip()]
            elif value:
                fm[key] = value
            else:
                current_key = key

    # Save final list or multiline
    if current_key and current_list:
        fm[current_key] = current_list
    if in_multiline and multiline_content:
        fm[multiline_key] = ' '.join(multiline_content).strip()

    return fm


def walk_vault(root: str) -> Iterator[tuple[str, dict, int]]:
    """
    Walk vault directory and yield (path, frontmatter_dict, body_size_bytes)
    for every .md file, skipping common ignore directories.
    """
    root_path = Path(root)
    if not root_path.exists():
        return

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith('.md'):
                continue

            filepath = os.path.join(dirpath, filename)
            fm = parse_frontmatter(filepath)

            # Measure body size (total - frontmatter)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                body_size = len(content.encode('utf-8'))
            except:
                body_size = 0

            yield (filepath, fm, body_size)


def classify_layer(frontmatter: dict, path: str) -> str:
    """
    Classify file as router, moc, leaf, meta, or unknown.

    Uses frontmatter 'type' field first, then filename patterns.
    """
    basename = os.path.basename(path)

    # Router patterns (OVERRIDE frontmatter - these are always routers)
    if basename in ('_INDEX.md', '_SESSION_LOG.md', 'MEMORY.md', '_AI_SESSION_GUIDE.md'):
        return 'router'
    # Forward-compatible: any _*_INDEX.md is a router (matches vault-router-cap.sh)
    if re.match(r'^_.*_INDEX\.md$', basename):
        return 'router'

    fm_type = frontmatter.get('type', '').lower()

    # Explicit type in frontmatter
    if fm_type == 'moc':
        return 'moc'
    if fm_type in ('meta', 'research', 'plan'):
        return 'meta'
    if fm_type in ('leaf', 'synthesis', 'character', 'episode', 'lore', 'faction'):
        return 'leaf'

    # MOC patterns
    if '_MOC.md' in basename or basename.startswith('_') and basename.endswith('_MOC.md'):
        return 'moc'

    # Meta patterns (compaction files, planning, research)
    if 'compactions/' in path or 'Planning/' in path:
        return 'meta'

    # Default to leaf for content files
    if not basename.startswith('_'):
        return 'leaf'

    return 'unknown'


def measure_file(path: str) -> dict:
    """
    Measure file and return size metrics.

    Returns dict with: size_bytes, lines, estimated_tokens
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        size_bytes = len(content.encode('utf-8'))
        lines = content.count('\n') + 1
        estimated_tokens = size_bytes // 4

        return {
            'size_bytes': size_bytes,
            'lines': lines,
            'estimated_tokens': estimated_tokens
        }
    except Exception as e:
        return {
            'size_bytes': 0,
            'lines': 0,
            'estimated_tokens': 0,
            'error': str(e)
        }


def measure_content(content: str) -> dict:
    """
    Measure in-memory content (for hooks checking proposed Write/Edit).

    Returns dict with: size_bytes, lines, estimated_tokens
    """
    size_bytes = len(content.encode('utf-8'))
    lines = content.count('\n') + 1
    estimated_tokens = size_bytes // 4

    return {
        'size_bytes': size_bytes,
        'lines': lines,
        'estimated_tokens': estimated_tokens
    }
