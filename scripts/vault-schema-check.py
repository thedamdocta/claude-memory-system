#!/usr/bin/env python3
"""Validate vault file frontmatter against vault-schema.json.

Reads proposed file content from stdin, file path from argv[1].
Walks up directory tree looking for vault-schema.json.
No schema found = exit 0 (silent pass).
Validation pass = exit 0.
Validation fail = exit 2 + errors to stderr.
"""

import json
import os
import re
import sys




def find_schema(file_path: str) -> str | None:
    """Walk up from file_path looking for vault-schema.json."""
    directory = os.path.dirname(os.path.abspath(file_path))
    while True:
        candidate = os.path.join(directory, 'vault-schema.json')
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def load_schema(schema_path: str) -> dict | None:
    """Load and parse the schema file. Returns None on error."""
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: vault-schema.json malformed ({e}). Passing without validation.", file=sys.stderr)
        return None


def extract_frontmatter(content: str) -> dict | None:
    """Extract frontmatter dict from file content. Returns None if no frontmatter."""
    if not content.startswith('---\n') and not content.startswith('---\r\n'):
        return None
    end = content.find('\n---', 4)
    if end == -1:
        return None

    fm_text = content[4:end]
    fm = {}
    current_key = ''
    current_list = None

    for line in fm_text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # List item
        if stripped.startswith('- ') and current_key:
            val = stripped[2:].strip().strip('"\'')
            if current_list is not None:
                current_list.append(val)
            continue

        # Key: value
        if ':' in stripped:
            key, _, value = stripped.partition(':')
            key = key.strip()
            value = value.strip()
            current_key = key

            if value == '' or value in ('>', '|'):
                current_list = []
                fm[key] = current_list
                continue

            current_list = None

            # Inline list [a, b, c]
            if value.startswith('[') and value.endswith(']'):
                items = [i.strip().strip('"\'') for i in value[1:-1].split(',') if i.strip()]
                fm[key] = items
                continue

            fm[key] = value.strip('"\'')

    return fm


def validate(fm: dict, schema: dict) -> list[str]:
    """Validate frontmatter against schema. Returns list of error messages."""
    errors = []

    file_type = fm.get('type', '')
    if not file_type:
        errors.append("Missing required field: type")
        return errors

    # Get type-specific rules, or fall back to defaults
    type_rules = schema.get('types', {}).get(file_type.lower())
    if type_rules is None:
        # Unknown type — check only defaults
        type_rules = schema.get('defaults', {})

    # Required fields
    required = type_rules.get('required', schema.get('defaults', {}).get('required', []))
    for field in required:
        if field not in fm or fm[field] == '' or fm[field] == []:
            errors.append(f"Missing required field: '{field}' (type: {file_type})")

    # Allowed status values
    allowed_status = type_rules.get('allowed_status')
    if allowed_status and 'status' in fm:
        if fm['status'].lower() not in [s.lower() for s in allowed_status]:
            errors.append(f"Invalid status: '{fm['status']}' (allowed: {', '.join(allowed_status)})")

    # Updated date format (YYYY-MM-DD)
    updated = fm.get('updated', '')
    if updated and isinstance(updated, str):
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', updated):
            errors.append(f"Invalid date format for 'updated': '{updated}' (expected YYYY-MM-DD)")

    # Tags should be a list
    tags = fm.get('tags')
    if tags is not None and not isinstance(tags, list):
        errors.append(f"'tags' should be a list, got: {type(tags).__name__}")

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: vault-schema-check.py <file_path> < proposed_content", file=sys.stderr)
        sys.exit(0)

    file_path = sys.argv[1]
    content = sys.stdin.read()

    # Find schema
    schema_path = find_schema(file_path)
    if schema_path is None:
        sys.exit(0)  # No schema = no enforcement

    # Load schema
    schema = load_schema(schema_path)
    if schema is None:
        sys.exit(0)  # Malformed schema = pass with warning

    # Extract frontmatter
    fm = extract_frontmatter(content)
    if fm is None:
        sys.exit(0)  # No frontmatter = not a vault file

    # Validate
    errors = validate(fm, schema)
    if errors:
        print(f"Schema validation FAILED for {os.path.basename(file_path)}:", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        print(f"  Schema: {schema_path}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == '__main__':
    main()
