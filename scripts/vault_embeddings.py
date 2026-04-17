#!/usr/bin/env python3
"""
vault_embeddings.py — Vector embedding search for vault files.

Uses all-MiniLM-L6-v2 ONNX model for local, offline semantic search.
No external API, no Ollama required. Falls back gracefully if deps missing.

Usage (standalone test):
    python3 vault_embeddings.py embed "test sentence"
    python3 vault_embeddings.py index /path/to/vault
    python3 vault_embeddings.py search "grief and loss" --limit 5
"""

import hashlib
import json
import math
import os
import sqlite3
import struct
import sys

MODEL_DIR = os.path.join(os.path.expanduser('~'), '.claude', 'models', 'minilm-l6-v2')
MODEL_PATH = os.path.join(MODEL_DIR, 'onnx', 'model.onnx')
TOKENIZER_PATH = os.path.join(MODEL_DIR, 'tokenizer.json')
def get_vector_db_path(vault_root: str) -> str:
    """Generate a per-vault vector DB path using a hash of the vault root."""
    root_hash = hashlib.md5(os.path.abspath(vault_root).encode()).hexdigest()[:12]
    return os.path.join(os.path.expanduser('~'), '.claude', f'vault-vectors-{root_hash}.db')
VECTOR_DIM = 384

_ort_session = None
_tokenizer = None


def is_available() -> bool:
    """Check if embedding model + deps are available."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return os.path.exists(MODEL_PATH) and os.path.exists(TOKENIZER_PATH)
    except ImportError:
        return False


def _load_model():
    """Lazy-load ONNX model and tokenizer."""
    global _ort_session, _tokenizer
    if _ort_session is not None:
        return

    import onnxruntime as ort
    from tokenizers import Tokenizer

    _ort_session = ort.InferenceSession(MODEL_PATH, providers=['CPUExecutionProvider'])
    _tokenizer = Tokenizer.from_file(TOKENIZER_PATH)
    _tokenizer.enable_truncation(max_length=256)
    _tokenizer.enable_padding(length=256)


def embed_text(text: str) -> list:
    """Embed a single text string. Returns a list of floats (384-dim)."""
    _load_model()
    import numpy as np

    encoding = _tokenizer.encode(text)
    input_ids = [encoding.ids]
    attention_mask = [encoding.attention_mask]

    outputs = _ort_session.run(
        None,
        {
            'input_ids': np.array(input_ids, dtype=np.int64),
            'attention_mask': np.array(attention_mask, dtype=np.int64),
            'token_type_ids': np.zeros_like(input_ids, dtype=np.int64),
        }
    )

    # Mean pooling over token embeddings (masked)
    token_embeddings = outputs[0][0]  # (seq_len, 384)
    mask = np.array(attention_mask[0], dtype=np.float32)
    masked = token_embeddings * mask[:, None]
    summed = masked.sum(axis=0)
    count = mask.sum()
    vector = (summed / count).tolist()

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        vector = [x / norm for x in vector]

    return vector


def _serialize_vector(vec: list) -> bytes:
    """Pack float list to bytes for SQLite storage."""
    return struct.pack(f'{len(vec)}f', *vec)


def _deserialize_vector(data: bytes) -> list:
    """Unpack bytes to float list."""
    n = len(data) // 4
    return list(struct.unpack(f'{n}f', data))


def cosine_similarity(a: list, b: list) -> float:
    """Pure-Python cosine similarity. No numpy needed for search."""
    dot = sum(x * y for x, y in zip(a, b))
    # Vectors are already L2-normalized, so dot product = cosine similarity
    return dot


def _ensure_schema(conn):
    """Create or migrate the vault_vectors table to include mtime."""
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vault_vectors'")
    if not cursor.fetchone():
        conn.execute('''
            CREATE TABLE vault_vectors (
                path TEXT,
                title TEXT,
                section TEXT,
                body TEXT,
                vector BLOB,
                mtime REAL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_path ON vault_vectors(path)')
        return

    # Check if mtime column exists (migration from old schema)
    cursor = conn.execute('PRAGMA table_info(vault_vectors)')
    columns = {row[1] for row in cursor}
    if 'mtime' not in columns:
        conn.execute('ALTER TABLE vault_vectors ADD COLUMN mtime REAL DEFAULT 0')


def build_vector_index(vault_root: str, walk_fn=None, get_body_fn=None, full_rebuild: bool = True) -> int:
    """Build vector embeddings index from vault files.

    Args:
        vault_root: path to vault root
        walk_fn: callable that yields (filepath, frontmatter_dict, body_size)
        get_body_fn: callable(path) -> (body_text, fm_line_count)
        full_rebuild: if True, drop and recreate. If False, incremental.
    """
    if not is_available():
        print("Embedding model not available. Install: pip install onnxruntime tokenizers", file=sys.stderr)
        return 0

    conn = sqlite3.connect(get_vector_db_path(vault_root))

    if full_rebuild:
        conn.execute('DROP TABLE IF EXISTS vault_vectors')

    _ensure_schema(conn)

    count = 0
    for filepath, fm, body_size in walk_fn(vault_root):
        body, _ = get_body_fn(filepath)
        if body is None or not body.strip():
            continue

        title = fm.get('title', os.path.basename(filepath))
        rel_path = os.path.relpath(filepath, vault_root)
        file_mtime = os.path.getmtime(filepath)

        # Skip if file hasn't changed (incremental mode)
        if not full_rebuild:
            cursor = conn.execute(
                'SELECT mtime FROM vault_vectors WHERE path = ? LIMIT 1', (rel_path,)
            )
            row = cursor.fetchone()
            if row and row[0] >= file_mtime:
                continue

        # Remove old embeddings for this file (whether full or incremental)
        conn.execute('DELETE FROM vault_vectors WHERE path = ?', (rel_path,))

        # Split by ## headings for per-section indexing
        sections = _split_sections(body, title)

        for sec_title, sec_body in sections:
            if not sec_body.strip() or len(sec_body.strip()) < 20:
                continue
            embed_text_str = f"{title} — {sec_title}: {sec_body[:1000]}"
            try:
                vec = embed_text(embed_text_str)
                conn.execute(
                    'INSERT INTO vault_vectors(path, title, section, body, vector, mtime) VALUES (?, ?, ?, ?, ?, ?)',
                    (rel_path, title, sec_title, sec_body[:500], _serialize_vector(vec), file_mtime)
                )
                count += 1
            except Exception as e:
                print(f"Warning: failed to embed {rel_path}/{sec_title}: {e}", file=sys.stderr)
                continue

        if count % 50 == 0 and count > 0:
            print(f"  ...embedded {count} sections", file=sys.stderr)

    # Clean up files that no longer exist in the vault
    if not full_rebuild:
        all_vault_paths = set()
        for filepath, _, _ in walk_fn(vault_root):
            all_vault_paths.add(os.path.relpath(filepath, vault_root))
        cursor = conn.execute('SELECT DISTINCT path FROM vault_vectors')
        indexed_paths = {row[0] for row in cursor}
        removed = indexed_paths - all_vault_paths
        for old_path in removed:
            conn.execute('DELETE FROM vault_vectors WHERE path = ?', (old_path,))
        if removed:
            print(f"  Removed {len(removed)} deleted files from index.", file=sys.stderr)

    conn.commit()
    conn.close()
    return count


def _split_sections(body: str, default_title: str) -> list:
    """Split body text by ## headings into (heading, body) pairs."""
    lines = body.split('\n')
    sections = []
    current_heading = default_title
    current_body = []

    for line in lines:
        if line.startswith('## '):
            if current_body:
                sections.append((current_heading, '\n'.join(current_body)))
            current_heading = line.lstrip('#').strip()
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_heading, '\n'.join(current_body)))

    # If no sections found, return whole body as one section
    if not sections:
        sections = [(default_title, body)]

    return sections


def vector_search(query: str, limit: int = 10, vault_root: str = '') -> list:
    """Search vault vectors by cosine similarity to query embedding."""
    db_path = get_vector_db_path(vault_root)
    if not os.path.exists(db_path):
        print("Vector index not found. Run: vault-query.py --index", file=sys.stderr)
        return []

    query_vec = embed_text(query)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute('SELECT path, title, section, body, vector FROM vault_vectors')

    scored = []
    for row in cursor:
        path, title, section, body, vec_bytes = row
        doc_vec = _deserialize_vector(vec_bytes)
        sim = cosine_similarity(query_vec, doc_vec)
        scored.append({
            'rel_path': path,
            'title': title,
            'section': section,
            'snippet': body[:120].replace('\n', ' '),
            'score': round(sim, 4)
        })

    conn.close()

    # Filter out low-confidence results (score floor)
    MIN_SIMILARITY = 0.25  # Threshold: results below this are noise
    scored = [r for r in scored if r['score'] >= MIN_SIMILARITY]

    # Sort by similarity (highest first)
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]


# Standalone test CLI
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == 'embed':
        text = ' '.join(sys.argv[2:])
        vec = embed_text(text)
        print(f"Vector ({len(vec)} dims): [{vec[0]:.4f}, {vec[1]:.4f}, ... {vec[-1]:.4f}]")
        print(f"Norm: {math.sqrt(sum(x*x for x in vec)):.4f}")

    elif cmd == 'test-similarity':
        if len(sys.argv) < 4:
            print("Usage: vault_embeddings.py test-similarity 'text1' 'text2'")
            sys.exit(1)
        v1 = embed_text(sys.argv[2])
        v2 = embed_text(sys.argv[3])
        sim = cosine_similarity(v1, v2)
        print(f"Similarity: {sim:.4f}")
        print(f"  '{sys.argv[2]}'")
        print(f"  '{sys.argv[3]}'")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
