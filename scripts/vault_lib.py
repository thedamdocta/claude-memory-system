#!/usr/bin/env python3
"""
vault_lib.py — Shared library for Misphitz vault three-layer architecture.

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
# Last-resort fallback only, and deliberately EMPTY by default.
#
# A hardcoded path here is what made every query silently answer from one project
# regardless of where it ran — and in a repo other people install, it would point at
# a stranger's machine. Set $DEFAULT_VAULT_ROOT if you want a personal fallback;
# otherwise resolution fails loudly, which is the correct outcome.
DEFAULT_VAULT_ROOT = os.environ.get("DEFAULT_VAULT_ROOT", "")

# Directories to skip during vault walk
SKIP_DIRS = {".git", "node_modules", ".claude", "__pycache__", ".venv", "venv", "_workspace"}

# An Obsidian vault is any directory containing this.
VAULT_MARKER = ".obsidian"


def find_vault_upward(start: Optional[str] = None) -> Optional[str]:
    """Walk up from `start` (default: cwd) looking for a directory that holds
    `.obsidian/`. That marker is what actually makes a folder a vault, so this
    works for any Obsidian project without configuration."""
    here = Path(start or os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / VAULT_MARKER).is_dir():
            return str(candidate)
    return None


# Vaults registered explicitly, one absolute path per line. The installer writes the
# vault it set up here. Without this, a vault outside $HOME — another volume, iCloud
# Drive, an external disk — is invisible to discovery, and the default "search
# everything" scope silently searches nothing.
VAULT_REGISTRY = os.path.expanduser("~/.claude/vaults.txt")


def registered_vaults() -> list:
    """Vault paths recorded in ~/.claude/vaults.txt, if any."""
    try:
        with open(VAULT_REGISTRY, "r", encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    except (OSError, UnicodeDecodeError):
        return []
    return [os.path.abspath(os.path.expanduser(p)) for p in lines
            if os.path.isdir(os.path.join(os.path.expanduser(p), VAULT_MARKER))]


def register_vault(path: str) -> bool:
    """Record a vault so discovery finds it regardless of where it lives.
    Returns True if it was newly added."""
    path = os.path.abspath(os.path.expanduser(path))
    existing = set(registered_vaults())
    if path in existing:
        return False
    os.makedirs(os.path.dirname(VAULT_REGISTRY), exist_ok=True)
    with open(VAULT_REGISTRY, "a", encoding="utf-8") as fh:
        fh.write(path + "\n")
    return True


def discover_vaults(search_root: Optional[str] = None, max_depth: int = 3) -> list:
    """Every Obsidian vault this machine knows about.

    Three sources, union'd and de-duplicated:
      1. ~/.claude/vaults.txt          (explicit — survives living outside $HOME)
      2. $VAULT_ROOT                   (env)
      3. a shallow scan under $HOME    (convenience)

    A $HOME-only scan was the original bug: an installer can place a vault on any
    volume, and discovery that only looks in one place reports "no vaults found"
    while the user is standing in one.
    """
    found = []
    if search_root is None:
        found.extend(registered_vaults())
        env_root = os.environ.get("VAULT_ROOT")
        if env_root:
            env_root = os.path.abspath(os.path.expanduser(env_root))
            if os.path.isdir(os.path.join(env_root, VAULT_MARKER)):
                found.append(env_root)
        here = find_vault_upward()
        if here:
            found.append(here)

    base = Path(search_root or os.path.expanduser("~")).resolve()
    for dirpath, dirnames, _ in os.walk(base):
        rel_depth = len(Path(dirpath).relative_to(base).parts)
        if rel_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") or d == VAULT_MARKER]
        if VAULT_MARKER in dirnames:
            found.append(dirpath)
            dirnames[:] = []          # don't descend into a vault looking for vaults

    seen, unique = set(), []
    for v in found:
        key = os.path.abspath(v)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return sorted(unique)


def resolve_vault_root(explicit: Optional[str] = None) -> tuple:
    """Work out which vault to query. Returns (path, how_we_decided).

    Precedence, most-specific first:
      1. an explicit --root
      2. $VAULT_ROOT
      3. $CLAUDE_PROJECT_DIR, if it exists on disk
      4. the nearest ancestor of cwd containing .obsidian/   ← the universal case
      5. DEFAULT_VAULT_ROOT, as a last resort

    Callers should PRINT the returned source. The original bug here was not that
    the default was wrong, but that it was invisible: with $CLAUDE_PROJECT_DIR
    unset, every query anywhere on the machine answered from one hardcoded project
    and said nothing about it. A wrong vault must be visible, not silent.
    """
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit)), "--root"

    env_vault = os.environ.get("VAULT_ROOT")
    if env_vault and os.path.isdir(os.path.expanduser(env_vault)):
        return os.path.abspath(os.path.expanduser(env_vault)), "$VAULT_ROOT"

    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj and os.path.isdir(proj):
        return os.path.abspath(proj), "$CLAUDE_PROJECT_DIR"

    upward = find_vault_upward()
    if upward:
        return upward, f"nearest {VAULT_MARKER}/ above cwd"

    if DEFAULT_VAULT_ROOT and os.path.isdir(DEFAULT_VAULT_ROOT):
        return DEFAULT_VAULT_ROOT, "$DEFAULT_VAULT_ROOT (fallback)"

    # Nothing resolved. Say so rather than guessing — a wrong vault that answers
    # confidently is worse than no answer.
    raise SystemExit(
        "vault-query: no Obsidian vault found.\n"
        "  Tried: --root, $VAULT_ROOT, $CLAUDE_PROJECT_DIR, and the nearest\n"
        "  .obsidian/ above the working directory.\n"
        "  Fix: run from inside a vault, pass --root PATH, or set $VAULT_ROOT.\n"
        "  See all vaults on this machine: vault-query.py --list-vaults")


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
    # NOTE: _SESSION_LOG.md removed from hardcode — it declares type: moc and uses
    # max_entry_length for validation. Hardcoding it router would block normal growth.
    if basename in ('_INDEX.md', 'MEMORY.md', '_AI_SESSION_GUIDE.md'):
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
    if fm_type in ('leaf', 'synthesis', 'character', 'episode', 'lore', 'faction', 'reference', 'guide', 'analysis'):
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
