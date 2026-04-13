#!/usr/bin/env python3
"""
count_tokens.py — Measure token usage of text files.

Uses tiktoken's cl100k_base encoding (GPT-4's tokenizer) as a proxy for
Claude's tokenizer. Community benchmarks put this within ~5-15% of Claude's
actual count for English text — much closer than a bytes/4 estimate.

For EXACT Claude token counts, set ANTHROPIC_API_KEY and pass --api to use
Anthropic's count_tokens endpoint (one network call per file).

Usage:
    count_tokens.py <file> [file2] [file3] ...
    count_tokens.py --api <file>              # exact Claude count via API
    count_tokens.py --ctx 200000 <file>       # custom context window size
    count_tokens.py --hook                    # measure MyProject hook-injected files

Output columns: bytes, chars, words, tokens, % of context window.
"""

import argparse
import os
import sys
from pathlib import Path

CTX_DEFAULT = 200_000  # Claude Opus/Sonnet context window

HOOK_FILES = [
    "__CLAUDE_DIR__/memory-vault/working-profile.md",
    "__CLAUDE_DIR__/memory-vault/my-project.md",
    "__VAULT_PATH__/_SESSION_LOG.md",
]

HOOK_EXTRACTS = [
    # (label, file_path, awk_section)  — for partial extractions the hook does
    ("66g C&N slice", "__VAULT_PATH__/compactions/session-66g-compaction.md",
     "Conversations & Nuance"),
]


def get_encoder():
    try:
        import tiktoken
    except ImportError:
        sys.exit("ERROR: tiktoken not installed. Run: pip3 install tiktoken")
    return tiktoken.get_encoding("cl100k_base")


def count_tiktoken(text: str, enc) -> int:
    return len(enc.encode(text))


def count_api(text: str, model: str = "claude-opus-4-5") -> int:
    """Exact Claude token count via Anthropic API. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic
    except ImportError:
        sys.exit("ERROR: anthropic SDK not installed. Run: pip3 install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set.")
    client = anthropic.Anthropic()
    resp = client.messages.count_tokens(
        model=model,
        messages=[{"role": "user", "content": text}],
    )
    return resp.input_tokens


def extract_section(text: str, heading: str) -> str:
    """Extract content from '## <heading>' until next '## ' or EOF."""
    lines = text.splitlines()
    out = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            if heading in line:
                in_section = True
                continue
        if in_section:
            out.append(line)
    return "\n".join(out)


def measure_file(path: str, enc, use_api: bool, ctx: int,
                 section: str = None, label: str = None) -> dict:
    p = Path(path)
    if not p.exists():
        return {"label": label or path, "error": "missing"}

    text = p.read_text(encoding="utf-8", errors="replace")
    if section:
        text = extract_section(text, section)

    tokens = count_api(text) if use_api else count_tiktoken(text, enc)
    return {
        "label": label or path,
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "words": len(text.split()),
        "tokens": tokens,
        "pct": 100.0 * tokens / ctx,
    }


def print_table(rows: list, ctx: int, method: str):
    label_w = max(len(r.get("label", "")) for r in rows)
    label_w = min(label_w, 55)

    hdr = f"{'file':<{label_w}}  {'bytes':>8} {'chars':>8} {'words':>7} {'tokens':>8} {'% ctx':>7}"
    sep = "-" * len(hdr)
    print(sep)
    print(hdr)
    print(sep)

    totals = {"bytes": 0, "chars": 0, "words": 0, "tokens": 0}
    for r in rows:
        label = r["label"]
        if len(label) > label_w:
            label = "..." + label[-(label_w - 3):]
        if r.get("error"):
            print(f"{label:<{label_w}}  <missing>")
            continue
        print(f"{label:<{label_w}}  {r['bytes']:>8} {r['chars']:>8} "
              f"{r['words']:>7} {r['tokens']:>8} {r['pct']:>6.1f}%")
        totals["bytes"] += r["bytes"]
        totals["chars"] += r["chars"]
        totals["words"] += r["words"]
        totals["tokens"] += r["tokens"]

    print(sep)
    print(f"{'TOTAL':<{label_w}}  {totals['bytes']:>8} {totals['chars']:>8} "
          f"{totals['words']:>7} {totals['tokens']:>8} "
          f"{100.0 * totals['tokens'] / ctx:>6.1f}%")
    print(sep)
    print(f"Method: {method}   Context window: {ctx:,} tokens")
    if method.startswith("tiktoken"):
        print("Note: tiktoken cl100k_base is a proxy for Claude's tokenizer")
        print("      (~5-15% off for English text). For exact counts use --api.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="Files to measure")
    ap.add_argument("--api", action="store_true",
                    help="Use Anthropic count_tokens API (exact, requires ANTHROPIC_API_KEY)")
    ap.add_argument("--ctx", type=int, default=CTX_DEFAULT,
                    help=f"Context window size (default: {CTX_DEFAULT})")
    ap.add_argument("--hook", action="store_true",
                    help="Measure the MyProject SessionStart hook-injected files")
    args = ap.parse_args()

    enc = None if args.api else get_encoder()
    method = "anthropic count_tokens API" if args.api else "tiktoken cl100k_base"

    rows = []

    if args.hook:
        for f in HOOK_FILES:
            rows.append(measure_file(f, enc, args.api, args.ctx,
                                     label=Path(f).name))
        for label, path, section in HOOK_EXTRACTS:
            rows.append(measure_file(path, enc, args.api, args.ctx,
                                     section=section, label=label))

    for f in args.files:
        rows.append(measure_file(f, enc, args.api, args.ctx, label=f))

    if not rows:
        ap.print_help()
        sys.exit(1)

    print_table(rows, args.ctx, method)


if __name__ == "__main__":
    main()
