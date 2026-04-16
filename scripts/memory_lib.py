#!/usr/bin/env python3
"""
memory_lib.py — Core library for the MyProject persistent memory system.

Provides:
  - Fact data model (dataclass)
  - SQLite schema with FTS5 full-text search (Porter tokenizer)
  - Rule-based extraction from compaction markdown files
  - CRUD operations via MemoryDB class
  - JSONL backup sidecar for recovery and portability

Dependencies: Python 3.10+ stdlib only, plus vault_lib.parse_frontmatter
from the same directory.

Other scripts (memory-bootstrap.py, memory-query.py) import from this module.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from vault_lib import parse_frontmatter


# ---------------------------------------------------------------------------
# Content security scanner — runs on every add_fact() and importable standalone
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Result of scanning content for security threats."""
    safe: bool              # True if content passed all checks
    action: str             # 'allow', 'strip', 'block'
    threats: list           # List of matched threat descriptions
    cleaned_content: str    # Cleaned content (if stripped), else original


# --- Threat pattern constants ---

# Category 1: Prompt injection attempts
_INJECTION_PATTERNS = re.compile(
    r'(?:'
    # Instruction override attempts
    r'ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions'
    r'|you\s+are\s+now\s+a\b'
    r'|system\s+prompt\s+override'
    r'|forget\s+(?:all\s+)?(?:everything|your\s+instructions|your\s+rules)'
    r'|override\s+your\s+(?:safety|rules|instructions|guidelines)'
    r'|BEGIN\s+SYSTEM\s+PROMPT'
    r'|disregard\s+(?:all\s+)?(?:previous|prior|above)\b'
    r'|new\s+instructions?\s*:'
    # Fake system tags (case-sensitive check done separately for <system>)
    r'|\[SYSTEM\]'
    r'|\[INST\]'
    r'|\[/INST\]'
    r')',
    re.IGNORECASE,
)

# Separate pattern for XML-style system tags — must match actual tag syntax
# but NOT match markdown/lore references to "system" (e.g., "power system",
# "hook system", "memory system")
_INJECTION_XML_TAGS = re.compile(
    r'<\s*/?system\s*>',
    re.IGNORECASE,
)

# Category 2: Data exfiltration attempts
_EXFILTRATION_PATTERNS = re.compile(
    r'(?:'
    # Network exfil with sensitive data
    r'(?:curl|wget)\s+.*(?:secret|token|password|api.?key|credentials)'
    r'|(?:curl|wget)\s+.*\$(?:API|SECRET|TOKEN|PASSWORD|KEY)'
    # Reading sensitive files
    r'|(?:cat|less|more|head|tail)\s+.*(?:\.env\b|credentials|\.ssh/|id_rsa|id_ed25519)'
    # Code execution primitives
    r'|eval\s*\('
    r'|exec\s*\('
    r'|os\.system\s*\('
    r'|subprocess\.(?:call|run|Popen|check_output)\s*\('
    r'|__import__\s*\('
    # SSH key injection
    r'|>>\s*~?/?\.?ssh/authorized_keys'
    r'|echo\s+.*>>\s*.*authorized_keys'
    # Encoded payload execution
    r'|base64\s+(?:-d|--decode)\s*.*\|\s*(?:bash|sh|zsh|python)'
    r')',
    re.IGNORECASE,
)

# Category 3: Invisible unicode characters (zero-width, BOM, bidi, etc.)
# These can hide malicious content inside seemingly normal text.
_INVISIBLE_UNICODE = re.compile(
    r'['
    r'\u200b'   # Zero-width space
    r'\u200c'   # Zero-width non-joiner
    r'\u200d'   # Zero-width joiner
    r'\u2060'   # Word joiner
    r'\u2061'   # Function application (invisible math operator)
    r'\u2062'   # Invisible times
    r'\u2063'   # Invisible separator
    r'\u2064'   # Invisible plus
    r'\ufeff'   # BOM / zero-width no-break space
    r'\u00ad'   # Soft hyphen
    r'\u200e'   # Left-to-right mark
    r'\u200f'   # Right-to-left mark
    r'\u202a'   # Left-to-right embedding
    r'\u202b'   # Right-to-left embedding
    r'\u202c'   # Pop directional formatting
    r'\u202d'   # Left-to-right override
    r'\u202e'   # Right-to-left override
    r'\u2066'   # Left-to-right isolate
    r'\u2067'   # Right-to-left isolate
    r'\u2068'   # First strong isolate
    r'\u2069'   # Pop directional isolate
    r']'
)

# Category 4: Privilege escalation attempts
_ESCALATION_PATTERNS = re.compile(
    r'(?:'
    # World-writable chmod
    r'chmod\s+(?:777|666|o\+w)\b'
    # Targeting Claude config files
    r'|>\s*.*\.claude/settings\.json'
    r'|>\s*.*CLAUDE\.md'
    r'|echo\s+.*>\s*.*\.claude/'
    # sudo commands
    r'|sudo\s+(?:rm|chmod|chown|mv|cp|bash|sh|python|install|apt|yum|brew)\b'
    # Destructive root operations
    r'|rm\s+-rf\s+/'
    r'|rm\s+-rf\s+~/'
    r'|rm\s+-rf\s+\$HOME'
    r')',
    re.IGNORECASE,
)


def scan_content(content: str) -> ScanResult:
    """
    Scan content for security threats before it enters the memory index.

    Checks four threat categories in priority order:
      1. Prompt injection → BLOCK
      2. Privilege escalation → BLOCK
      3. Invisible unicode → STRIP (remove chars, return cleaned content)
      4. Data exfiltration → BLOCK

    Returns a ScanResult with the action taken and any threats found.
    Pure regex — no LLM calls, designed to be fast on every add_fact().
    """
    threats: list[str] = []

    # --- Check 1: Prompt injection ---
    if _INJECTION_PATTERNS.search(content):
        threats.append(f'prompt_injection: matched pattern in content')
        return ScanResult(safe=False, action='block', threats=threats, cleaned_content=content)

    if _INJECTION_XML_TAGS.search(content):
        threats.append(f'prompt_injection: fake system XML tag')
        return ScanResult(safe=False, action='block', threats=threats, cleaned_content=content)

    # --- Check 2: Privilege escalation ---
    if _ESCALATION_PATTERNS.search(content):
        threats.append(f'privilege_escalation: matched pattern in content')
        return ScanResult(safe=False, action='block', threats=threats, cleaned_content=content)

    # --- Check 3: Invisible unicode ---
    if _INVISIBLE_UNICODE.search(content):
        cleaned = _INVISIBLE_UNICODE.sub('', content)
        threats.append(f'invisible_unicode: stripped hidden characters')
        return ScanResult(safe=False, action='strip', threats=threats, cleaned_content=cleaned)

    # --- Check 4: Data exfiltration ---
    if _EXFILTRATION_PATTERNS.search(content):
        threats.append(f'exfiltration: matched pattern in content')
        return ScanResult(safe=False, action='block', threats=threats, cleaned_content=content)

    # --- All clear ---
    return ScanResult(safe=True, action='allow', threats=[], cleaned_content=content)


# ---------------------------------------------------------------------------
# Fact types and their base salience weights
# ---------------------------------------------------------------------------

FACT_TYPES = {
    'lesson': 0.9,
    'architecture': 0.9,
    'decision': 0.85,
    'preference': 0.85,
    'pattern': 0.8,
    'bug': 0.7,
    'workflow': 0.6,
    'fact': 0.5,
}


# ---------------------------------------------------------------------------
# Fact dataclass
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    """
    A single extracted fact from a compaction file or other source.

    The id is a SHA-256 hash of the content (first 12 hex chars), making
    identical content automatically deduplicate on insert.
    """
    id: str                          # SHA-256 hash of content (first 12 chars)
    content: str                     # The fact text
    fact_type: str                   # One of FACT_TYPES keys
    confidence: float = 0.5          # 0.0-1.0
    strength: float = 1.0            # Starts at 1.0, decays over time
    source_session_ids: list = field(default_factory=list)
    source_files: list = field(default_factory=list)
    source_section: str = ""         # e.g. "What Happened", "Conversations & Nuance"
    created_at: float = 0.0          # Unix timestamp
    last_accessed_at: float = 0.0    # Unix timestamp, updated on search access
    access_count: int = 0            # Incremented on search access
    concepts: list = field(default_factory=list)
    related_files: list = field(default_factory=list)
    importance: int = 3              # 1-5 scale

    def __post_init__(self):
        if not self.id:
            self.id = _content_id(self.content)
        if not self.created_at:
            self.created_at = time.time()
        if not self.last_accessed_at:
            self.last_accessed_at = self.created_at
        if self.fact_type not in FACT_TYPES:
            self.fact_type = 'fact'
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.strength = max(0.0, min(1.0, self.strength))
        self.importance = max(1, min(5, self.importance))


# ---------------------------------------------------------------------------
# SQLite schema — facts table + FTS5 virtual table + sync triggers
# ---------------------------------------------------------------------------

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    strength REAL DEFAULT 1.0,
    source_session_ids TEXT DEFAULT '[]',
    source_files TEXT DEFAULT '[]',
    source_section TEXT DEFAULT '',
    created_at REAL,
    last_accessed_at REAL,
    access_count INTEGER DEFAULT 0,
    concepts TEXT DEFAULT '[]',
    related_files TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 3
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content, concepts, fact_type,
    content='facts',
    content_rowid='rowid',
    tokenize='porter'
);

-- Triggers keep the FTS index in sync with the facts table.
-- The concepts column is stored as a JSON array in facts, so the trigger
-- flattens it to a space-separated string via replace() for FTS indexing.

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, concepts, fact_type)
    VALUES (
        new.rowid,
        new.content,
        REPLACE(REPLACE(REPLACE(REPLACE(new.concepts, '"', ''), '[', ''), ']', ''), ',', ' '),
        new.fact_type
    );
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, concepts, fact_type)
    VALUES (
        'delete',
        old.rowid,
        old.content,
        REPLACE(REPLACE(REPLACE(REPLACE(old.concepts, '"', ''), '[', ''), ']', ''), ',', ' '),
        old.fact_type
    );
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, concepts, fact_type)
    VALUES (
        'delete',
        old.rowid,
        old.content,
        REPLACE(REPLACE(REPLACE(REPLACE(old.concepts, '"', ''), '[', ''), ']', ''), ',', ' '),
        old.fact_type
    );
    INSERT INTO facts_fts(rowid, content, concepts, fact_type)
    VALUES (
        new.rowid,
        new.content,
        REPLACE(REPLACE(REPLACE(REPLACE(new.concepts, '"', ''), '[', ''), ']', ''), ',', ' '),
        new.fact_type
    );
END;
"""


# ---------------------------------------------------------------------------
# Utility / extraction helpers
# ---------------------------------------------------------------------------

def _content_id(text: str) -> str:
    """Generate a fact ID: SHA-256 of the content, first 12 hex chars."""
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:12]


def extract_session_id(filepath: str) -> str:
    """
    Extract session ID from a compaction filename.

    Examples:
        'session-66m-cont15-compaction.md' -> '66m-cont15'
        'session-66m-compaction.md'        -> '66m'
        '/full/path/session-66m-cont3-compaction.md' -> '66m-cont3'
    """
    basename = os.path.basename(filepath)
    match = re.match(r'session-(.+?)-compaction\.md$', basename)
    if match:
        return match.group(1)
    # Fallback: try without the -compaction suffix
    match = re.match(r'session-(.+?)\.md$', basename)
    if match:
        return match.group(1)
    return basename


def extract_wikilinks(text: str) -> list[str]:
    """Extract [[wikilink]] targets from text. Returns list of inner text."""
    return re.findall(r'\[\[([^\]]+)\]\]', text)


def classify_fact_type(content: str, section: str) -> str:
    """
    Classify a fact's type based on its content and the section it came from.

    Uses a scoring system: each type accumulates a score from keyword matches
    and section-based boosts. The type with the highest score wins, subject to
    a priority tiebreaker. Falls back to 'fact' if no type scores above 0.

    Priority order (highest first, used as tiebreaker):
      1. lesson
      2. decision
      3. preference
      4. architecture
      5. pattern
      6. bug
      7. workflow
      8. fact (default fallback)
    """
    lowered = content.lower()
    section_lower = section.lower()

    # --- Score accumulators for each type ---
    scores: dict[str, float] = {
        'lesson': 0.0,
        'decision': 0.0,
        'preference': 0.0,
        'architecture': 0.0,
        'pattern': 0.0,
        'bug': 0.0,
        'workflow': 0.0,
    }

    # Priority tiebreaker (lower = higher priority)
    _PRIORITY = {
        'lesson': 0,
        'decision': 1,
        'preference': 2,
        'architecture': 3,
        'pattern': 4,
        'bug': 5,
        'workflow': 6,
    }

    # === LESSON (corrections, growth patterns, self-awareness) ===
    if re.search(
        r'(?:'
        # Correction patterns
        r'\blearned\b|\blesson\b|\bcorrection\b|\bmistake\b'
        r'|\bshould\s+have\b|\bshouldn\'?t\s+have\b'
        r'|\buser\s+corrected\b|\buser\s+caught\b'
        r'|\brepeat\s+offense\b|\bsame\s+mistake\b'
        r'|\bdon\'?t\s+do\s+this\b|\bnever\s+do\s+this\b'
        r'|\bthe\s+fix\s+is\b|\bthe\s+right\s+approach\b'
        # Growth patterns
        r'|\binternalize\b|\bremember\s+to\b|\bnext\s+time\b|\bgoing\s+forward\b'
        r'|\bthe\s+real\s+issue\s+was\b|\broot\s+cause\b|\bwhat\s+i\s+missed\b'
        r'|\bpattern\s+to\s+break\b|\bpattern\s+to\s+keep\b'
        # Self-awareness patterns
        r'|\bi\s+was\s+wrong\b|\bi\s+hedged\b|\bi\s+should\s+have\s+committed\b'
        r'|\bi\s+need\s+to\b|\bnote\s+to\s+self\b|\bfor\s+future\s+sessions\b'
        r')',
        lowered,
    ):
        scores['lesson'] += 3.0

    # === DECISION (lore decisions, canon locks, story choices) ===

    # Strong decision signals (phrases that almost always mean a decision)
    if re.search(
        r'(?:'
        r'\buser\s+confirmed\b|\buser\s+said\b'
        r'|\bis\s+canon\b|\bnot\s+canon\b|\bcanon\s+(?:locked|confirmed|closed|rewrite)\b'
        r'|\bthe\s+rule\s+is\b|\bthe\s+answer\s+is\b|\bfinal\s+answer\b'
        r'|\bchanged\s+from\b|\bno\s+longer\b'
        r'|\bruling\b|\bapproved\b|\boverride\b'
        r'|\breordered\b|\brenamed\b|\breplaced\b|\brevised\b'
        r')',
        lowered,
    ):
        scores['decision'] += 3.0

    # "locked" as a decision marker — guard against narrative "locked up/in/away"
    if re.search(r'\blocked\b', lowered):
        if re.search(r'\bLOCKED\b', content):
            # All-caps LOCKED is almost always a decision status marker
            scores['decision'] += 3.0
        elif not re.search(r'\blocked\s+(?:up|in|away|down|out|inside|behind|into)\b', lowered):
            # "locked" not followed by spatial prepositions = likely a decision
            scores['decision'] += 1.5

    # Medium decision signals (individual keywords — no "locked", handled above)
    _DECISION_KEYWORDS = frozenset({
        'confirmed', 'decided', 'canon', 'retcon',
        'resolved', 'closed', 'finalized', 'ratified',
    })
    for kw in _DECISION_KEYWORDS:
        if re.search(r'\b' + kw + r'\b', lowered):
            scores['decision'] += 1.5

    # Section-based boost: facts from decision-related sections
    if re.search(r'(?:decision|lore\s+decision|key\s+decision)', section_lower):
        scores['decision'] += 2.5

    # Bold markers in "What Happened" with decision keywords
    if section_lower == 'what happened' and '**' in content:
        if any(kw in lowered for kw in ('locked', 'canon', 'decided', 'confirmed', 'resolved', 'closed')):
            scores['decision'] += 1.5

    # === ARCHITECTURE (system design, infrastructure, hooks, scripts) ===

    # Check for architecture keywords using set membership for speed
    _ARCHITECTURE_KEYWORDS = frozenset({
        'hook', 'hooks', 'schema', 'pipeline',
        'database', 'sqlite', 'fts5', 'bootstrap',
        'infrastructure', 'three-layer', 'two-layer',
        'jsonl', 'sidecar', 'trigger', 'tokenizer', 'bm25',
    })
    for kw in _ARCHITECTURE_KEYWORDS:
        if kw in lowered:
            scores['architecture'] += 1.5

    # Stronger signals: multi-word architecture phrases
    if re.search(
        r'(?:'
        r'\bmemory\s+system\b|\bcompaction\s+gate\b|\bsession\s+start\b'
        r'|\bthree[\s-]layer\b|\bfts5?\b|\bbm25\b|\bsqlite\b'
        r'|\bvault[\s-]health\b|\bvault[\s-]rename\b|\bauto[\s-]prune\b'
        r'|\bhook\s+system\b|\bbootstrap\b|\bschema\b'
        r')',
        lowered,
    ):
        scores['architecture'] += 2.0

    # File path patterns — strong architecture signal
    if re.search(r'(?:/Users/|~/\.claude/|\.py\b|\.sh\b|\.db\b|\.jsonl\b)', content):
        scores['architecture'] += 2.0

    # "script" and "vault" and "layer" need context guards — these words
    # appear in narrative too ("script format", "vault" as story location).
    # Only count them with technical co-occurring terms.
    _TECH_COOCCUR = frozenset({
        'python', 'bash', 'code', 'function', 'module', 'import', 'file',
        'path', 'command', 'terminal', 'output', 'config', 'json', 'yaml',
        'test', 'debug', 'log', 'deploy', 'build', 'compile', 'memory',
        'index', 'query', 'parse', 'system', 'hook', 'database', 'sqlite',
        'script', 'bootstrap', 'schema', 'pipeline', 'error',
    })
    has_tech_context = any(tc in lowered for tc in _TECH_COOCCUR)

    if has_tech_context:
        for kw in ('script', 'vault', 'layer'):
            if kw in lowered:
                scores['architecture'] += 1.5

    # Section-based: facts from architecture/build/infrastructure sections
    if re.search(r'(?:architect|infrastruc|build|system|technical)', section_lower):
        scores['architecture'] += 1.5

    # === PREFERENCE (the user's working style, expectations, rules) ===

    # Strong preference signals: the user + directive verb (unambiguous)
    if re.search(
        r'(?:'
        r'\buser\s+prefers\b|\buser\s+expects\b|\buser\s+wants\b'
        r'|\buser\s+likes\b|\buser\s+hates\b|\buser\s+dislikes\b'
        r'|\buser\'?s\s+(?:preference|style|approach|philosophy|rule|expectation)\b'
        r'|\bask\s+before\b|\bcheck\s+before\b|\bsearch\s+before\b'
        r'|\bwrite\s+immediately\b|\bsingle\s+source\s+of\s+truth\b'
        r')',
        lowered,
    ):
        scores['preference'] += 3.0

    # Medium preference signals: directive "always/never + verb" — could be
    # preference or pattern, so lower weight to allow pattern to win when
    # pattern keywords are also present (e.g., "carry forward: always check...")
    if re.search(
        r'(?:'
        r'\balways\s+(?:do|use|check|ask|save|write|read|search|verify)\b'
        r'|\bnever\s+(?:do|use|skip|ignore|assume|guess|commit|push|amend)\b'
        r')',
        lowered,
    ):
        scores['preference'] += 1.5

    # Medium preference signals: the user's name + opinion/directive language
    if 'user' in lowered:
        if re.search(
            r'(?:'
            r'\buser\b.*\b(?:prefers|expects|wants|likes|hates|insists|requires)\b'
            r'|\buser\b.*\b(?:style|tone|approach|method|philosophy)\b'
            r'|\buser\'?s\s+(?:creative|writing|working|editorial)\b'
            r')',
            lowered,
        ):
            scores['preference'] += 2.0

    # Section-based: C&N paragraphs about the user with directive language
    if section_lower == 'conversations & nuance' and 'user' in lowered:
        if re.search(r'(?:prefers|expects|wants|approach|style|philosophy|discipline|rule)', lowered):
            scores['preference'] += 1.5

    # Note: bare "always"/"never" without the user context do NOT boost
    # preference. They are usually lore/character statements. The medium
    # regex above catches the relevant "always + directive verb" cases.

    # === PATTERN (recurring workflows, repeated observations) ===

    if re.search(
        r'(?:'
        r'\bpattern\b|\brepeats\b|\brecurring\b|\broutine\b'
        r'|\bconvention\b|\bbest\s+practice\b|\brule\s+of\s+thumb\b'
        r'|\bthe\s+same\s+mistake\b|\bevery\s+time\b|\bevery\s+session\b'
        r'|\bconsistent(?:ly)?\b|\bstandard\s+(?:practice|approach|workflow)\b'
        r'|\bthe\s+discipline\b|\bthe\s+habit\b|\bthe\s+convention\b'
        r'|\bcarry\s+forward\b|\bstanding\s+rule\b'
        r')',
        lowered,
    ):
        scores['pattern'] += 2.0

    # Section-based: "State Carried Forward" often contains patterns
    if re.search(r'(?:state\s+carried|carried\s+forward|standing|recurring)', section_lower):
        scores['pattern'] += 1.5

    # === BUG (system/code bugs, NOT narrative "broke") ===

    # Strong bug signals: Python exception types are unambiguous
    if re.search(
        r'(?:'
        r'\btypeerror\b|\battributeerror\b|\bsyntaxerror\b|\bkeyerror\b'
        r'|\bimporterror\b|\bvalueerror\b|\bindexerror\b|\bnameerror\b'
        r'|\bruntime\s*error\b|\bstack\s+trace\b|\btraceback\b'
        r'|\bsegfault\b'
        r'|\bregression\b|\bbroken\s+(?:build|test|pipe|hook|script)\b'
        r'|\bfix\s+needed\b|\bneeds?\s+fix(?:ing)?\b|\bhotfix\b'
        r')',
        lowered,
    ):
        scores['bug'] += 6.0

    # Medium-strong bug: "crash" in tech context (not narrative car crash)
    if re.search(r'\bcrash(?:ed|es|ing)?\b', lowered) and has_tech_context:
        scores['bug'] += 3.0

    # Medium bug signals: require technical context to avoid matching
    # narrative uses of "broke" (e.g., "he broke out of the room")
    if has_tech_context:
        if re.search(r'\b(?:bug|error|broke|broken|wrong|failed|failing|issue|problem)\b', lowered):
            scores['bug'] += 2.0
        if re.search(r'\b(?:fix|fixed|fixing|patch|patched)\b', lowered):
            scores['bug'] += 1.0

    # === WORKFLOW (process sequences, how-to patterns) ===

    if re.search(
        r'(?:'
        r'\bworkflow\b|\bprocedure\b|\bsequence\b'
        r'|\bprotocol\b'
        r'|\bstep\s+\d+\b|\bstep\s+one\b|\bstep\s+two\b|\bstep\s+three\b'
        r'|\bfirst\s+then\b|\brun\s+this\b'
        r')',
        lowered,
    ):
        scores['workflow'] += 1.5

    # Guard: "step" alone often appears in narrative; "process" can be
    # narrative too. Only count bare "step"/"process" with tech context.
    if re.search(r'\b(?:step|process)\b', lowered):
        if has_tech_context or re.search(r'(?:procedure|protocol|workflow|process)', section_lower):
            scores['workflow'] += 1.0

    # Section-based: procedural/protocol sections
    if re.search(r'(?:procedure|protocol|workflow|how[\s-]to|pending\s+work|work\s+completed)', section_lower):
        scores['workflow'] += 1.0

    # === FRONTMATTER special handling ===
    # Frontmatter summaries can be anything — only strong content signals
    # should override 'fact'. Require score >= 3.0 to override.
    if section == 'frontmatter':
        best_type = max(scores, key=lambda t: (scores[t], -_PRIORITY[t]))
        if scores[best_type] >= 3.0:
            return best_type
        return 'fact'

    # === Pick the winner ===
    # Find type with highest score; on tie, use priority order
    best_type = max(scores, key=lambda t: (scores[t], -_PRIORITY[t]))
    if scores[best_type] > 0:
        return best_type

    return 'fact'


# Contradiction / retcon markers that adjust confidence and importance
_CONTRADICTION_RE = re.compile(
    r'\b(?:retcon|changed\s+from|previously|used\s+to\s+be|no\s+longer)\b',
    re.IGNORECASE,
)


def _make_fact(
    content: str,
    confidence: float,
    importance: int,
    source_session_ids: list[str],
    source_files: list[str],
    source_section: str,
    created_at: float,
) -> Optional['Fact']:
    """
    Build a Fact with automatic classification, concept extraction,
    wikilink extraction, and contradiction adjustment.

    Returns None if content is empty after stripping.
    """
    content = content.strip()
    if not content:
        return None

    # Contradiction handling
    if _CONTRADICTION_RE.search(content):
        confidence = max(0.0, confidence - 0.1)
        importance = min(5, importance + 1)

    wikilinks = extract_wikilinks(content)
    # Concepts = wikilinks + any additional key terms (wikilinks are the
    # primary concept signal from compaction files)
    concepts = list(wikilinks)

    fact_type = classify_fact_type(content, source_section)

    return Fact(
        id=_content_id(content),
        content=content,
        fact_type=fact_type,
        confidence=confidence,
        strength=1.0,
        source_session_ids=list(source_session_ids),
        source_files=list(source_files),
        source_section=source_section,
        created_at=created_at,
        last_accessed_at=created_at,
        access_count=0,
        concepts=concepts,
        related_files=wikilinks,
        importance=importance,
    )


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def _parse_sections(text: str) -> dict[str, str]:
    """
    Split markdown body (after frontmatter) into sections by ## headers.

    Returns dict mapping section name -> section body text.
    The key "" (empty string) holds any content before the first ## header.
    """
    sections: dict[str, str] = {}
    current_name = ""
    current_lines: list[str] = []

    for line in text.split('\n'):
        if line.startswith('## '):
            sections[current_name] = '\n'.join(current_lines)
            current_name = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    sections[current_name] = '\n'.join(current_lines)
    return sections


# ---------------------------------------------------------------------------
# Extraction: compaction markdown -> Fact objects
# ---------------------------------------------------------------------------

def extract_facts_from_file(filepath: str) -> list[Fact]:
    """
    Parse a compaction .md file and extract facts using rule-based extraction.

    Steps:
      1. Parse frontmatter via vault_lib.parse_frontmatter()
      2. Extract session ID from filename
      3. Read file content and split into sections by ## headings
      4. Apply extraction rules per section (see below)

    Extraction rules:
      a) Frontmatter 'summary' field -> 1 fact (confidence=0.9, importance=4)
      b) Bold text **...** in any section -> 1 fact each (confidence=0.95, importance=5)
         - The ** markers are stripped from the content
      c) Bullet points (lines starting with '- ') under '## What Happened'
         -> 1 fact each (confidence=0.75, importance=3)
      d) Bold headings in '## Conversations & Nuance' (lines starting with **...**)
         -> 1 fact each (confidence=0.85, importance=4)
         - Includes the paragraph text after the bold heading
      e) Standalone paragraphs in '## Conversations & Nuance'
         (non-bullet, non-heading lines with 20+ chars)
         -> 1 fact each (confidence=0.7, importance=3)

    Contradiction handling:
      If content contains retcon/changed from/previously/used to be/no longer:
        confidence -= 0.1, importance += 1 (capped at 5)

    Returns list of Fact objects, deduplicated by id.
    """
    # Parse frontmatter
    fm = parse_frontmatter(filepath)

    # Read full file content
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            full_content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return []

    session_id = extract_session_id(filepath)
    source_session_ids = [session_id]
    source_files = [filepath]

    # Use file mtime as created_at timestamp
    try:
        created_at = os.path.getmtime(filepath)
    except OSError:
        created_at = time.time()

    # Accumulate facts in a dict keyed by id for dedup
    facts_by_id: dict[str, Fact] = {}

    def _add(fact: Optional[Fact]):
        if fact and fact.id not in facts_by_id:
            facts_by_id[fact.id] = fact

    # --- Rule (a): Frontmatter summary ---
    summary = fm.get('summary', '')
    if summary:
        _add(_make_fact(
            content=summary,
            confidence=0.9,
            importance=4,
            source_session_ids=source_session_ids,
            source_files=source_files,
            source_section='frontmatter',
            created_at=created_at,
        ))

    # Strip frontmatter to get body
    body = full_content
    if full_content.startswith('---\n'):
        end_idx = full_content.find('\n---\n', 4)
        if end_idx != -1:
            body = full_content[end_idx + 5:]
        else:
            end_idx = full_content.find('\n---', 4)
            if end_idx != -1:
                body = full_content[end_idx + 4:]

    sections = _parse_sections(body)

    # --- Rule (b): Bold text **...** in any section ---
    # Extract from all sections. Strip the ** markers.
    for section_name, section_body in sections.items():
        if section_name == '':
            continue
        bold_matches = re.findall(r'\*\*(.+?)\*\*', section_body)
        for bold_text in bold_matches:
            bold_text = bold_text.strip()
            if bold_text:
                _add(_make_fact(
                    content=bold_text,
                    confidence=0.95,
                    importance=5,
                    source_session_ids=source_session_ids,
                    source_files=source_files,
                    source_section=section_name,
                    created_at=created_at,
                ))

    # --- Rule (c): Bullet points under "What Happened" ---
    what_happened = sections.get('What Happened', '')
    if what_happened:
        for line in what_happened.split('\n'):
            stripped = line.strip()
            if stripped.startswith('- '):
                bullet_text = stripped[2:].strip()
                if bullet_text:
                    _add(_make_fact(
                        content=bullet_text,
                        confidence=0.75,
                        importance=3,
                        source_session_ids=source_session_ids,
                        source_files=source_files,
                        source_section='What Happened',
                        created_at=created_at,
                    ))

    # --- Rules (d) and (e): Conversations & Nuance ---
    conv_nuance = sections.get('Conversations & Nuance', '')
    if conv_nuance:
        # Split into paragraphs (blocks separated by one or more blank lines)
        paragraphs = re.split(r'\n\s*\n', conv_nuance)
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Rule (d): Bold heading at start of paragraph
            if para.startswith('**'):
                # Include the full paragraph (bold heading + following text)
                cn_fact = _make_fact(
                    content=para,
                    confidence=0.85,
                    importance=4,
                    source_session_ids=source_session_ids,
                    source_files=source_files,
                    source_section='Conversations & Nuance',
                    created_at=created_at,
                )
                # Override to lesson type if the bold heading contains
                # lesson/correction keywords (these are growth patterns)
                if cn_fact is not None:
                    heading_match = re.match(r'\*\*(.+?)\*\*', para)
                    if heading_match:
                        heading_lower = heading_match.group(1).lower()
                        if re.search(
                            r'(?:user\s+caught|repeat\s+offense|learned'
                            r'|lesson|correction|mistake|wrong|should\s+have'
                            r'|never\s+do|don\'?t\s+do|note\s+to\s+self'
                            r'|going\s+forward|next\s+time|root\s+cause'
                            r'|the\s+fix|the\s+real\s+issue|internalize)',
                            heading_lower,
                        ):
                            cn_fact.fact_type = 'lesson'
                _add(cn_fact)
            else:
                # Rule (e): Standalone paragraph, must be 20+ chars
                if len(para) >= 20:
                    _add(_make_fact(
                        content=para,
                        confidence=0.7,
                        importance=3,
                        source_session_ids=source_session_ids,
                        source_files=source_files,
                        source_section='Conversations & Nuance',
                        created_at=created_at,
                    ))

    return list(facts_by_id.values())


# ---------------------------------------------------------------------------
# Extraction: vault markdown docs -> Fact objects
# ---------------------------------------------------------------------------

# Section headings to skip during vault doc extraction (purely structural)
_VAULT_SKIP_HEADINGS = {
    'related', 'references', 'see also', 'how to use', 'source',
}

# Map frontmatter type -> fact_type for vault doc summaries
_VAULT_TYPE_MAP = {
    'character': 'fact',
    'lore': 'architecture',
    'faction': 'fact',
    'episode': 'fact',
    'synthesis': 'architecture',
    'worldbuilding': 'architecture',
    'power-system': 'architecture',
    'technology': 'architecture',
    'history': 'fact',
    'cosmology': 'architecture',
    'politics': 'fact',
    'location': 'fact',
    'group': 'fact',
    'nation': 'fact',
}


def extract_facts_from_vault_doc(filepath: str) -> list['Fact']:
    """
    Parse a vault .md file (character, lore, faction, episode, synthesis)
    and extract facts using rule-based extraction tailored to vault doc
    structure.

    Unlike compaction files, vault docs are canonical reference documents
    curated by the author. They use different extraction rules:

      1. Frontmatter 'summary' field -> 1 fact (confidence=0.95, importance=5)
      2. Section headings + first paragraph -> 1 fact per section
         (confidence=0.85, importance=4). Skips structural headings.
      3. Bold text **...** with surrounding sentence -> 1 fact each
         (confidence=0.9, importance=4)
      4. Bullet list items (20+ chars) -> 1 fact each
         (confidence=0.8, importance=3). Capped at 30 per file.
      5. Table rows -> 1 fact per data row (confidence=0.85, importance=3).
         Capped at 20 rows per table.

    All facts use session_id "vault" to distinguish them from compaction
    facts.

    Returns list of Fact objects, deduplicated by id.
    """
    # Parse frontmatter
    fm = parse_frontmatter(filepath)

    # Read full file content
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            full_content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return []

    source_session_ids = ['vault']
    source_files = [filepath]
    doc_type = fm.get('type', 'fact')

    # Use file mtime as created_at timestamp
    try:
        created_at = os.path.getmtime(filepath)
    except OSError:
        created_at = time.time()

    # Accumulate facts in a dict keyed by id for dedup
    facts_by_id: dict[str, Fact] = {}

    def _add(fact: Optional['Fact']):
        if fact and fact.id not in facts_by_id:
            facts_by_id[fact.id] = fact

    # --- Rule 1: Frontmatter summary ---
    summary = fm.get('summary', '')
    if summary:
        summary_fact_type = _VAULT_TYPE_MAP.get(doc_type, 'fact')
        _add(_make_fact(
            content=summary,
            confidence=0.95,
            importance=5,
            source_session_ids=source_session_ids,
            source_files=source_files,
            source_section='frontmatter',
            created_at=created_at,
        ))
        # Override the auto-classified type with the vault-specific type
        if summary in [f.content for f in facts_by_id.values()]:
            fact_id = _content_id(summary)
            if fact_id in facts_by_id:
                facts_by_id[fact_id].fact_type = summary_fact_type

    # Strip frontmatter to get body
    body = full_content
    if full_content.startswith('---\n'):
        end_idx = full_content.find('\n---\n', 4)
        if end_idx != -1:
            body = full_content[end_idx + 5:]
        else:
            end_idx = full_content.find('\n---', 4)
            if end_idx != -1:
                body = full_content[end_idx + 4:]

    # Parse into sections (reuse existing _parse_sections)
    sections = _parse_sections(body)

    # Track bullet count across whole file (cap at 30)
    total_bullets = 0
    BULLET_CAP = 30

    for section_name, section_body in sections.items():
        # Skip empty pre-header content and structural headings
        if section_name == '':
            continue
        if section_name.lower().strip() in _VAULT_SKIP_HEADINGS:
            continue

        # --- Rule 2: Section heading + first paragraph ---
        # Get first 1-3 sentences, up to 300 chars
        lines = section_body.strip().split('\n')
        # Collect first non-empty paragraph lines (stop at blank line)
        first_para_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if first_para_lines:
                    break  # end of first paragraph
                continue  # skip leading blank lines
            # Skip lines that are sub-headings, bullets, or table rows
            if stripped.startswith('#') or stripped.startswith('- ') or stripped.startswith('|'):
                if not first_para_lines:
                    continue  # skip, keep looking for paragraph text
                break
            first_para_lines.append(stripped)

        if first_para_lines:
            first_para = ' '.join(first_para_lines)
            if len(first_para) > 300:
                # Truncate at sentence boundary near 300 chars
                cut = first_para[:300].rfind('.')
                if cut > 100:
                    first_para = first_para[:cut + 1]
                else:
                    first_para = first_para[:300] + '...'
            heading_fact_content = f"{section_name}: {first_para}"
            _add(_make_fact(
                content=heading_fact_content,
                confidence=0.85,
                importance=4,
                source_session_ids=source_session_ids,
                source_files=source_files,
                source_section=section_name,
                created_at=created_at,
            ))

        # --- Rule 3: Bold text with surrounding sentence ---
        # Find bold phrases and extract with context
        for m in re.finditer(r'([^.\n]*\*\*(.+?)\*\*[^.\n]*)', section_body):
            full_match = m.group(1).strip()
            bold_text = m.group(2).strip()
            if bold_text and len(bold_text) >= 3:
                _add(_make_fact(
                    content=full_match,
                    confidence=0.9,
                    importance=4,
                    source_session_ids=source_session_ids,
                    source_files=source_files,
                    source_section=section_name,
                    created_at=created_at,
                ))

        # --- Rule 4: Bullet list items (20+ chars, cap at 30 total) ---
        for line in lines:
            if total_bullets >= BULLET_CAP:
                break
            stripped = line.strip()
            if stripped.startswith('- '):
                bullet_text = stripped[2:].strip()
                if len(bullet_text) >= 20:
                    _add(_make_fact(
                        content=bullet_text,
                        confidence=0.8,
                        importance=3,
                        source_session_ids=source_session_ids,
                        source_files=source_files,
                        source_section=section_name,
                        created_at=created_at,
                    ))
                    total_bullets += 1

        # --- Rule 5: Table rows (cap at 20 per table) ---
        table_rows_found = 0
        TABLE_ROW_CAP = 20
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith('|'):
                continue
            # Skip separator rows (|---|---|---|)
            if re.match(r'^\|[\s\-:]+\|', stripped):
                continue
            # Skip header rows — the first | row before a separator
            # We detect this by checking if the next non-empty line is a separator
            # Instead, just parse all non-separator | rows
            cells = [c.strip() for c in stripped.split('|') if c.strip()]
            if not cells:
                continue
            # Skip if it looks like a header (often bold or all-caps)
            # Heuristic: if all cells are short single words, likely a header
            row_text = ' | '.join(cells)
            if len(row_text) < 10:
                continue
            if table_rows_found >= TABLE_ROW_CAP:
                break
            _add(_make_fact(
                content=row_text,
                confidence=0.85,
                importance=3,
                source_session_ids=source_session_ids,
                source_files=source_files,
                source_section=section_name,
                created_at=created_at,
            ))
            table_rows_found += 1

    return list(facts_by_id.values())


# ---------------------------------------------------------------------------
# Extraction: procedure markdown -> Fact objects
# ---------------------------------------------------------------------------

def extract_facts_from_procedure(filepath: str) -> list['Fact']:
    """
    Parse a procedure .md file and extract facts for the memory index.

    Procedure files have YAML frontmatter with a summary, plus markdown
    sections: Steps, Pitfalls, Known Issues. Extraction rules:

      1. Frontmatter 'summary' -> 1 fact (confidence=0.95, importance=5, type=workflow)
      2. Each numbered step under '## Steps' -> 1 fact each
         (confidence=0.9, importance=4, type=workflow)
      3. Each bullet under '## Pitfalls' -> 1 fact each
         (confidence=0.9, importance=4, type=lesson)
      4. Each bullet under '## Known Issues' (non-empty) -> 1 fact each
         (confidence=0.85, importance=4, type=lesson)

    All facts use session_id "procedure" and the procedure filepath as source.
    Returns list of Fact objects, deduplicated by id.
    """
    fm = parse_frontmatter(filepath)

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            full_content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return []

    # Only extract from active procedures
    if fm.get('status', '') != 'active':
        return []

    source_session_ids = ['procedure']
    source_files = [filepath]
    title = fm.get('title', os.path.basename(filepath))

    try:
        created_at = os.path.getmtime(filepath)
    except OSError:
        created_at = time.time()

    facts_by_id: dict[str, Fact] = {}

    def _add(fact: Optional['Fact']):
        if fact and fact.id not in facts_by_id:
            facts_by_id[fact.id] = fact

    # --- Rule 1: Frontmatter summary ---
    summary = fm.get('summary', '')
    if summary:
        content_text = f"Procedure: {title} -- {summary}"
        fact = Fact(
            id=_content_id(content_text),
            content=content_text,
            fact_type='workflow',
            confidence=0.95,
            strength=1.0,
            source_session_ids=list(source_session_ids),
            source_files=list(source_files),
            source_section='frontmatter',
            created_at=created_at,
            last_accessed_at=created_at,
            access_count=0,
            concepts=[title],
            related_files=[],
            importance=5,
        )
        _add(fact)

    # Strip frontmatter to get body
    body = full_content
    if full_content.startswith('---\n'):
        end_idx = full_content.find('\n---\n', 4)
        if end_idx != -1:
            body = full_content[end_idx + 5:]
        else:
            end_idx = full_content.find('\n---', 4)
            if end_idx != -1:
                body = full_content[end_idx + 4:]

    sections = _parse_sections(body)

    # --- Rule 2: Steps ---
    steps_body = sections.get('Steps', '')
    if steps_body:
        for line in steps_body.split('\n'):
            stripped = line.strip()
            m = re.match(r'^\d+\.\s+(.+)$', stripped)
            if m:
                step_text = m.group(1).strip()
                if step_text:
                    content_text = f"[{title}] Step: {step_text}"
                    fact = Fact(
                        id=_content_id(content_text),
                        content=content_text,
                        fact_type='workflow',
                        confidence=0.9,
                        strength=1.0,
                        source_session_ids=list(source_session_ids),
                        source_files=list(source_files),
                        source_section='Steps',
                        created_at=created_at,
                        last_accessed_at=created_at,
                        access_count=0,
                        concepts=[title],
                        related_files=[],
                        importance=4,
                    )
                    _add(fact)

    # --- Rule 3: Pitfalls ---
    pitfalls_body = sections.get('Pitfalls', '')
    if pitfalls_body:
        for line in pitfalls_body.split('\n'):
            stripped = line.strip()
            if stripped.startswith('- '):
                pitfall_text = stripped[2:].strip()
                if pitfall_text:
                    content_text = f"[{title}] Pitfall: {pitfall_text}"
                    fact = Fact(
                        id=_content_id(content_text),
                        content=content_text,
                        fact_type='lesson',
                        confidence=0.9,
                        strength=1.0,
                        source_session_ids=list(source_session_ids),
                        source_files=list(source_files),
                        source_section='Pitfalls',
                        created_at=created_at,
                        last_accessed_at=created_at,
                        access_count=0,
                        concepts=[title],
                        related_files=[],
                        importance=4,
                    )
                    _add(fact)

    # --- Rule 4: Known Issues ---
    known_issues_body = sections.get('Known Issues', '')
    if known_issues_body:
        for line in known_issues_body.split('\n'):
            stripped = line.strip()
            if stripped.startswith('- '):
                issue_text = stripped[2:].strip()
                if issue_text:
                    content_text = f"[{title}] Known issue: {issue_text}"
                    fact = Fact(
                        id=_content_id(content_text),
                        content=content_text,
                        fact_type='lesson',
                        confidence=0.85,
                        strength=1.0,
                        source_session_ids=list(source_session_ids),
                        source_files=list(source_files),
                        source_section='Known Issues',
                        created_at=created_at,
                        last_accessed_at=created_at,
                        access_count=0,
                        concepts=[title],
                        related_files=[],
                        importance=4,
                    )
                    _add(fact)

    return list(facts_by_id.values())


# ---------------------------------------------------------------------------
# FTS5 query preprocessing — stop word stripping + OR fallback support
# ---------------------------------------------------------------------------

# Common words that poison FTS5 implicit-AND queries.
# Organized by category for readability; flattened to a set for O(1) lookup.
_STOP_WORDS: set[str] = {
    # Question words
    'who', 'what', 'when', 'where', 'why', 'how', 'which',
    'does', 'did', 'do', 'is', 'are', 'was', 'were',
    'will', 'would', 'could', 'should', 'can',
    # Articles / prepositions
    'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for',
    'with', 'from', 'by', 'about', 'between', 'into',
    'through', 'during', 'before', 'after',
    # Connectors / pronouns
    'and', 'or', 'but', 'not', 'if', 'then',
    'that', 'this', 'these', 'those', 'it', 'its',
}

# FTS5 operators that should be left alone when the user explicitly uses them.
_FTS5_OPERATORS = {'AND', 'OR', 'NOT', 'NEAR'}


# ---------------------------------------------------------------------------
# Alias / synonym map — bidirectional expansion for codenames, names, lore
# ---------------------------------------------------------------------------

# Each entry is a frozenset of terms that should be treated as equivalent.
# When any member appears in a query, the query is expanded to OR all members.
# Case-insensitive matching; original case preserved in output.
_SYNONYM_GROUPS: list[frozenset[str]] = [
    # Populate with project-specific synonym groups.
    # Example patterns:
    #   frozenset({'CodeName', 'RealName'}),                # alias <-> name
    #   frozenset({'Term', 'TermAlt', 'TermVariant'}),      # spelling variants
    #   frozenset({'FullName', 'Abbreviation'}),            # acronym <-> full
    # When any member appears in a query, the query auto-expands to OR all members.
    # Add entries that match your project's terminology.
]

# Build a lookup: lowercase term -> frozenset of all synonyms (original case).
_SYNONYM_LOOKUP: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _term in _group:
        _SYNONYM_LOOKUP[_term.lower()] = _group


def expand_synonyms(query: str) -> str:
    """
    Expand synonyms in a query string for FTS5 matching.

    For each token (or multi-word phrase from the synonym map) found in the
    query, replaces it with an OR group: term -> (term1 OR term2 OR ...).

    Rules:
      - Quoted phrases are NOT expanded (preserve exact-match intent).
      - Matching is case-insensitive; original case from the synonym map is
        used in the expansion output.
      - If a term is not in the synonym map, it passes through unchanged.
      - Multi-word synonyms (e.g. "Motherland of Sol") are matched before
        single-word tokens to avoid partial replacement.

    Args:
        query: The preprocessed query string (stop words already stripped).

    Returns:
        Query string with synonym expansions in FTS5 OR syntax.
    """
    if not query.strip():
        return query

    def _fts5_safe(term: str) -> str:
        """Quote a synonym term if it contains chars that would break FTS5 syntax."""
        if ' ' in term or "'" in term or '-' in term:
            return f'"{term}"'
        return term

    # Step 1: Extract and protect quoted phrases — they must not be expanded.
    quoted_phrases: list[str] = []

    def _protect_quoted(m: re.Match) -> str:
        placeholder = f'\x00QUOTED{len(quoted_phrases)}\x00'
        quoted_phrases.append(m.group(0))
        return placeholder

    working = re.sub(r'"[^"]*"', _protect_quoted, query)

    # Step 2: Try multi-word synonyms first (longest match).
    # Replace matched phrases with placeholders to prevent re-expansion
    # of individual words inside the OR group during step 3.
    expanded_groups: list[str] = []
    multi_word_terms = sorted(
        [t for t in _SYNONYM_LOOKUP if ' ' in t],
        key=len,
        reverse=True,
    )
    for term_lower in multi_word_terms:
        pattern = re.compile(re.escape(term_lower), re.IGNORECASE)
        match = pattern.search(working)
        if match:
            group = _SYNONYM_LOOKUP[term_lower]
            parts = [_fts5_safe(syn) for syn in sorted(group)]
            expansion = '(' + ' OR '.join(parts) + ')'
            placeholder = f'\x00GROUP{len(expanded_groups)}\x00'
            expanded_groups.append(expansion)
            working = pattern.sub(placeholder, working)

    # Step 3: Expand single-word tokens.
    tokens = working.split()
    expanded_tokens: list[str] = []
    for token in tokens:
        # Skip placeholders (quoted phrases and multi-word groups)
        if token.startswith('\x00'):
            expanded_tokens.append(token)
            continue

        # Strip trailing punctuation for lookup (FTS5 tokenizer ignores it,
        # but it prevents matching "TermName?" against "termname").
        stripped = token.rstrip('?!.,;:')
        stripped_lower = stripped.lower()
        if stripped_lower in _SYNONYM_LOOKUP:
            group = _SYNONYM_LOOKUP[stripped_lower]
            if len(group) > 1:
                parts = [_fts5_safe(syn) for syn in sorted(group)]
                expansion = '(' + ' OR '.join(parts) + ')'
                expanded_tokens.append(expansion)
            else:
                expanded_tokens.append(token)
        else:
            expanded_tokens.append(token)

    result = ' '.join(expanded_tokens)

    # Step 4: Restore placeholders — multi-word groups first, then quoted phrases.
    for i, group_str in enumerate(expanded_groups):
        result = result.replace(f'\x00GROUP{i}\x00', group_str)
    for i, phrase in enumerate(quoted_phrases):
        result = result.replace(f'\x00QUOTED{i}\x00', phrase)

    return result


def preprocess_query(raw_query: str) -> str:
    """
    Clean a natural-language query for FTS5 matching.

    Steps:
      1. If the query contains explicit FTS5 operators (AND, OR, NOT, NEAR)
         in uppercase, return it unchanged — the user knows what they're doing.
      2. Extract and preserve any "quoted phrases" as-is.
      3. Expand synonyms BEFORE stop word stripping so multi-word synonyms
         that contain stop words (e.g. "Motherland of Sol") can still match.
      4. Strip stop words from remaining (non-expanded) tokens. Tokens inside
         synonym OR-groups are left alone.
      5. Reassemble: quoted phrases + expanded/surviving tokens.
      6. If nothing survives, fall back to the original query.

    Returns:
        A cleaned query string suitable for FTS5 MATCH.
    """
    query = raw_query.strip()
    if not query:
        return query

    # Step 1: Detect explicit FTS5 operators — pass through unchanged.
    tokens_upper = query.split()
    if any(tok in _FTS5_OPERATORS for tok in tokens_upper):
        return query

    # Step 2: Extract quoted phrases before any further processing.
    quoted_phrases: list[str] = []
    def _save_phrase(m: re.Match) -> str:
        quoted_phrases.append(m.group(0))  # Keep the quotes intact for FTS5
        return ''  # Remove from the remainder so we don't process words inside
    remainder = re.sub(r'"[^"]*"', _save_phrase, query)

    # Step 3: Expand synonyms BEFORE stop word stripping.
    # This ensures multi-word synonyms containing stop words can match
    # (e.g. "Motherland of Sol" contains stop word "of").
    remainder = expand_synonyms(remainder)

    # Step 4: Strip stop words from tokens that are NOT inside OR-groups.
    # OR-groups look like "(term1 OR term2 OR ...)" — preserve them intact.
    # Strategy: split on OR-group boundaries, strip stop words only from
    # non-group segments.
    or_group_re = re.compile(r'\([^)]*\bOR\b[^)]*\)')
    segments: list[str] = []
    last_end = 0
    for m in or_group_re.finditer(remainder):
        # Process the text before this OR-group: strip stop words
        before = remainder[last_end:m.start()]
        before_tokens = before.split()
        before_cleaned = [t for t in before_tokens
                         if t.rstrip('?!.,;:') and t.lower().rstrip('?!.,;:') not in _STOP_WORDS]
        if before_cleaned:
            segments.append(' '.join(before_cleaned))
        # Keep the OR-group as-is
        segments.append(m.group(0))
        last_end = m.end()
    # Process any trailing text after the last OR-group
    after = remainder[last_end:]
    after_tokens = after.split()
    after_cleaned = [t for t in after_tokens
                     if t.rstrip('?!.,;:') and t.lower().rstrip('?!.,;:') not in _STOP_WORDS]
    if after_cleaned:
        segments.append(' '.join(after_cleaned))

    # Step 5: Reassemble with quoted phrases first.
    parts = quoted_phrases + segments
    cleaned = ' '.join(parts).strip()

    # Step 6: If stripping removed everything, fall back to the original query.
    if not cleaned:
        return query

    return cleaned


# ---------------------------------------------------------------------------
# MemoryDB — SQLite + FTS5 persistent memory store
# ---------------------------------------------------------------------------

class MemoryDB:
    """
    Persistent memory database backed by SQLite with FTS5 full-text search.

    Each project gets its own database file under base_dir, plus a companion
    JSONL backup file for recovery and portability.

    Usage:
        db = MemoryDB('my-project')
        db.add_fact(some_fact)
        results = db.search('hook architecture')
        db.close()
    """

    def __init__(self, project: str, base_dir: str = '~/.claude/memory-index'):
        """
        Open or create the SQLite DB for the given project.

        Args:
            project:  Project name, used as the DB filename stem.
            base_dir: Parent directory for all memory index files.
                      Defaults to ~/.claude/memory-index.
        """
        self.project = project
        self.base_dir = os.path.expanduser(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

        self.db_path = os.path.join(self.base_dir, f'{project}.db')
        self.jsonl_path = os.path.join(self.base_dir, f'{project}.jsonl')

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.init_db()

    def init_db(self):
        """Create tables, FTS virtual table, and sync triggers.
        Safe to call multiple times (all statements use IF NOT EXISTS)."""
        self.conn.executescript(SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def fact_to_dict(self, fact: Fact) -> dict:
        """Convert a Fact dataclass to a JSON-serializable dict."""
        return {
            'id': fact.id,
            'content': fact.content,
            'fact_type': fact.fact_type,
            'confidence': fact.confidence,
            'strength': fact.strength,
            'source_session_ids': fact.source_session_ids,
            'source_files': fact.source_files,
            'source_section': fact.source_section,
            'created_at': fact.created_at,
            'last_accessed_at': fact.last_accessed_at,
            'access_count': fact.access_count,
            'concepts': fact.concepts,
            'related_files': fact.related_files,
            'importance': fact.importance,
        }

    def dict_to_fact(self, d: dict) -> Fact:
        """Convert a dict back to a Fact dataclass."""
        return Fact(
            id=d.get('id', ''),
            content=d.get('content', ''),
            fact_type=d.get('fact_type', 'fact'),
            confidence=d.get('confidence', 0.5),
            strength=d.get('strength', 1.0),
            source_session_ids=d.get('source_session_ids', []),
            source_files=d.get('source_files', []),
            source_section=d.get('source_section', ''),
            created_at=d.get('created_at', 0.0),
            last_accessed_at=d.get('last_accessed_at', 0.0),
            access_count=d.get('access_count', 0),
            concepts=d.get('concepts', []),
            related_files=d.get('related_files', []),
            importance=d.get('importance', 3),
        )

    def _fact_to_row(self, fact: Fact) -> dict:
        """Convert a Fact to a dict for SQLite insertion (JSON-encode lists)."""
        return {
            'id': fact.id,
            'content': fact.content,
            'fact_type': fact.fact_type,
            'confidence': fact.confidence,
            'strength': fact.strength,
            'source_session_ids': json.dumps(fact.source_session_ids),
            'source_files': json.dumps(fact.source_files),
            'source_section': fact.source_section,
            'created_at': fact.created_at,
            'last_accessed_at': fact.last_accessed_at,
            'access_count': fact.access_count,
            'concepts': json.dumps(fact.concepts),
            'related_files': json.dumps(fact.related_files),
            'importance': fact.importance,
        }

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        """Convert a sqlite3.Row to a Fact, deserializing JSON list fields."""
        d = dict(row)
        for key in ('source_session_ids', 'source_files', 'concepts', 'related_files'):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        return self.dict_to_fact(d)

    def _append_jsonl(self, fact: Fact):
        """Append one fact as a JSON line to the JSONL backup file."""
        record = self.fact_to_dict(fact)
        try:
            with open(self.jsonl_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            pass  # Non-fatal; SQLite is the primary store.

    # ------------------------------------------------------------------
    # Security logging
    # ------------------------------------------------------------------

    def _log_security(self, action: str, content_preview: str, threats: list):
        """
        Append a security event to ~/.claude/memory-index/logs/security.log.

        Non-fatal: if the log directory or file can't be written, silently skip.
        """
        log_dir = os.path.join(self.base_dir, 'logs')
        log_path = os.path.join(log_dir, 'security.log')
        try:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            threat_str = '; '.join(threats) if threats else 'none'
            # Sanitize content preview — collapse newlines for single-line log
            preview = content_preview.replace('\n', ' ').replace('\r', '')
            entry = f"[{timestamp}] {action} | threats={threat_str} | content={preview}\n"
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(entry)
        except OSError:
            pass  # Non-fatal; security logging must not break add_fact()

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def add_fact(self, fact: Fact) -> bool:
        """
        Insert a new fact or merge it with an existing one if the id matches.

        Merge behavior (when id already exists):
          - Union source_session_ids and source_files from both records
          - Update fact_type to the incoming value (allows reclassification)
          - Increment access_count
          - Keep the higher confidence and importance values
          - Keep the original created_at but update last_accessed_at

        Also appends the fact to the JSONL backup file.

        Returns:
            True if the fact was newly inserted.
            False if an existing fact was merged/updated (or blocked by security scan).
        """
        # --- Security scan gate ---
        scan = scan_content(fact.content)
        if scan.action == 'block':
            self._log_security('BLOCKED', fact.content[:200], scan.threats)
            return False
        if scan.action == 'strip':
            fact = Fact(**{**asdict(fact), 'content': scan.cleaned_content})
            fact.id = _content_id(fact.content)  # Rehash after cleaning
            self._log_security('STRIPPED', fact.content[:200], scan.threats)

        row = self._fact_to_row(fact)

        # Check for existing fact with same id
        existing = self.conn.execute(
            "SELECT * FROM facts WHERE id = ?", (fact.id,)
        ).fetchone()

        if existing is None:
            # New fact — insert
            self.conn.execute(
                """INSERT INTO facts
                   (id, content, fact_type, confidence, strength,
                    source_session_ids, source_files, source_section,
                    created_at, last_accessed_at, access_count,
                    concepts, related_files, importance)
                   VALUES
                   (:id, :content, :fact_type, :confidence, :strength,
                    :source_session_ids, :source_files, :source_section,
                    :created_at, :last_accessed_at, :access_count,
                    :concepts, :related_files, :importance)""",
                row,
            )
            self.conn.commit()
            self._append_jsonl(fact)
            return True

        # Existing fact — merge
        old = self._row_to_fact(existing)

        # Union session IDs and source files
        merged_sessions = list(dict.fromkeys(
            old.source_session_ids + fact.source_session_ids
        ))
        merged_files = list(dict.fromkeys(
            old.source_files + fact.source_files
        ))

        self.conn.execute(
            """UPDATE facts SET
                source_session_ids = ?,
                source_files = ?,
                fact_type = ?,
                access_count = access_count + 1,
                last_accessed_at = ?,
                confidence = MAX(confidence, ?),
                importance = MAX(importance, ?)
               WHERE id = ?""",
            (
                json.dumps(merged_sessions),
                json.dumps(merged_files),
                fact.fact_type,
                time.time(),
                fact.confidence,
                fact.importance,
                fact.id,
            ),
        )
        self.conn.commit()
        self._append_jsonl(fact)
        return False

    def _execute_fts_query(
        self,
        fts_query: str,
        fact_type: Optional[str],
        limit: int,
        min_strength: float,
    ) -> list[tuple[Fact, float]]:
        """
        Execute a single FTS5 MATCH query and return scored results.

        This is the inner engine used by search(). It does NOT update
        access metadata — the caller (search) handles that.

        Returns:
            List of (Fact, score) tuples, ordered by score descending.
            Returns [] on FTS syntax errors or empty results.
        """
        where_parts: list[str] = []
        params: list = []

        if fact_type:
            where_parts.append("f.fact_type = ?")
            params.append(fact_type)
        if min_strength > 0:
            where_parts.append("f.strength >= ?")
            params.append(min_strength)

        where_clause = ""
        if where_parts:
            where_clause = "AND " + " AND ".join(where_parts)

        sql = f"""
            SELECT f.*,
                   abs(bm25(facts_fts)) AS raw_bm25,
                   abs(bm25(facts_fts)) * f.strength * (f.importance / 5.0) AS rank_score
            FROM facts_fts fts
            JOIN facts f ON f.rowid = fts.rowid
            WHERE facts_fts MATCH ?
            {where_clause}
            ORDER BY rank_score DESC
            LIMIT ?
        """
        params_full = [fts_query] + params + [limit]

        try:
            cursor = self.conn.execute(sql, params_full)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return []

        results: list[tuple[Fact, float]] = []
        for row in rows:
            d = dict(row)
            score = d.pop('rank_score', 0.0)
            d.pop('raw_bm25', None)
            fact = self._row_to_fact(row)
            results.append((fact, score))

        return results

    def search(
        self,
        query: str,
        fact_type: Optional[str] = None,
        limit: int = 10,
        min_strength: float = 0.0,
    ) -> list[tuple[Fact, float]]:
        """
        BM25-ranked full-text search via FTS5 with query preprocessing.

        Query preprocessing:
          1. Strip stop words (question words, articles, connectors) that
             poison FTS5 implicit-AND matching.
          2. Preserve quoted phrases and explicit FTS5 operators (AND/OR/NOT/NEAR).
          3. If the preprocessed AND query returns 0 results and there are 2+
             content terms, retry with OR between terms as a fallback.

        Ranking formula:
            score = abs(bm25(facts_fts)) * strength * (importance / 5.0)

        Side effect: updates last_accessed_at and increments access_count
        for every returned fact.

        Args:
            query:        Search query string (natural language or FTS5 syntax).
            fact_type:    Optional filter — only return this fact_type.
            limit:        Maximum number of results (default 10).
            min_strength: Minimum strength threshold (default 0.0).

        Returns:
            List of (Fact, score) tuples, ordered by score descending.
        """
        # --- Preprocess the query: strip stop words, preserve phrases ---
        cleaned = preprocess_query(query)

        # --- First attempt: AND (FTS5 default implicit AND) ---
        results = self._execute_fts_query(cleaned, fact_type, limit, min_strength)

        # --- OR fallback: if AND returned nothing and there are 2+ terms ---
        if not results:
            # Tokenize the cleaned query to see if OR fallback makes sense.
            # Must preserve parenthesized OR groups and quoted phrases as units.
            # Step 1: extract parenthesized groups like (A OR B)
            paren_groups = re.findall(r'\([^)]+\)', cleaned)
            remaining = re.sub(r'\([^)]+\)', '', cleaned)
            # Step 2: extract quoted phrases
            quoted = re.findall(r'"[^"]*"', remaining)
            remaining = re.sub(r'"[^"]*"', '', remaining)
            # Step 3: get bare tokens, filtering out empty/whitespace
            bare = [t for t in remaining.split() if t.strip()]
            all_terms = paren_groups + quoted + bare

            if len(all_terms) >= 2:
                or_query = ' OR '.join(all_terms)
                results = self._execute_fts_query(or_query, fact_type, limit, min_strength)

        # --- Persist access metadata updates ---
        ids_to_touch = [fact.id for fact, _ in results]
        if ids_to_touch:
            now = time.time()
            placeholders = ','.join('?' for _ in ids_to_touch)
            self.conn.execute(
                f"""UPDATE facts
                    SET access_count = access_count + 1,
                        last_accessed_at = ?
                    WHERE id IN ({placeholders})""",
                [now] + ids_to_touch,
            )
            self.conn.commit()

        return results

    def get_by_session(self, session_id: str) -> list[Fact]:
        """
        Find all facts where session_id appears in source_session_ids.

        Uses a JSON LIKE pattern against the serialized array.
        """
        pattern = f'%"{session_id}"%'
        cursor = self.conn.execute(
            "SELECT * FROM facts WHERE source_session_ids LIKE ? ORDER BY created_at",
            (pattern,),
        )
        return [self._row_to_fact(row) for row in cursor.fetchall()]

    def get_by_id(self, fact_id: str) -> Optional[Fact]:
        """
        Look up a single fact by its hash ID.

        Args:
            fact_id: The 12-char hex hash ID of the fact.

        Returns:
            A Fact object if found, None otherwise.
        """
        row = self.conn.execute(
            "SELECT * FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_fact(row)

    def update_fact(self, fact_id: str, **kwargs) -> bool:
        """
        Update specified fields on an existing fact.

        Allowed fields:
            content, fact_type, confidence, strength, importance,
            concepts, source_session_ids, source_files, source_section

        If `content` is updated, the FTS index is automatically kept in sync
        by the facts_au AFTER UPDATE trigger.

        List fields (concepts, source_session_ids, source_files) are
        JSON-serialized before storage.

        Args:
            fact_id: The 12-char hex hash ID of the fact to update.
            **kwargs: Field name/value pairs to update.

        Returns:
            True if the fact was found and updated, False if not found.
        """
        allowed = {
            'content', 'fact_type', 'confidence', 'strength', 'importance',
            'concepts', 'source_session_ids', 'source_files', 'source_section',
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        # Check the fact exists
        existing = self.conn.execute(
            "SELECT id FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if existing is None:
            return False

        # JSON-encode list fields
        json_fields = {'concepts', 'source_session_ids', 'source_files'}
        for key in json_fields:
            if key in updates and isinstance(updates[key], list):
                updates[key] = json.dumps(updates[key])

        # Build SET clause
        set_parts = [f"{col} = ?" for col in updates]
        values = list(updates.values()) + [fact_id]

        self.conn.execute(
            f"UPDATE facts SET {', '.join(set_parts)} WHERE id = ?",
            values,
        )
        self.conn.commit()
        return True

    def delete_fact(self, fact_id: str) -> bool:
        """
        Delete a fact by its hash ID from both facts and facts_fts tables.

        The FTS cleanup is handled automatically by the facts_ad AFTER DELETE
        trigger defined in SCHEMA_SQL.

        Args:
            fact_id: The 12-char hex hash ID of the fact to delete.

        Returns:
            True if the fact was found and deleted, False if not found.
        """
        # Check the fact exists first
        existing = self.conn.execute(
            "SELECT id FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if existing is None:
            return False

        self.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        return True

    def get_stats(self) -> dict:
        """
        Return summary statistics about the memory database.

        Returns dict with keys:
          - total_facts: int
          - type_distribution: dict[str, int]
          - strength_distribution: dict with keys hot/warm/cold/floor
            Hot: >= 0.7, Warm: 0.4 to <0.7, Cold: 0.15 to <0.4, Floor: < 0.15
        """
        stats: dict = {}

        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM facts").fetchone()
        stats['total_facts'] = row['cnt'] if row else 0

        if stats['total_facts'] == 0:
            stats['type_distribution'] = {}
            stats['strength_distribution'] = {
                'hot': 0, 'warm': 0, 'cold': 0, 'floor': 0,
            }
            return stats

        # Type distribution
        cursor = self.conn.execute(
            "SELECT fact_type, COUNT(*) AS cnt FROM facts GROUP BY fact_type"
        )
        stats['type_distribution'] = {
            row['fact_type']: row['cnt'] for row in cursor
        }

        # Strength distribution (hot >= 0.7, warm 0.4-0.7, cold 0.15-0.4, floor < 0.15)
        cursor = self.conn.execute("""
            SELECT
                SUM(CASE WHEN strength >= 0.7 THEN 1 ELSE 0 END) AS hot,
                SUM(CASE WHEN strength >= 0.4 AND strength < 0.7 THEN 1 ELSE 0 END) AS warm,
                SUM(CASE WHEN strength >= 0.15 AND strength < 0.4 THEN 1 ELSE 0 END) AS cold,
                SUM(CASE WHEN strength < 0.15 THEN 1 ELSE 0 END) AS floor
            FROM facts
        """)
        row = cursor.fetchone()
        stats['strength_distribution'] = {
            'hot': row['hot'] or 0,
            'warm': row['warm'] or 0,
            'cold': row['cold'] or 0,
            'floor': row['floor'] or 0,
        }

        return stats

    def apply_decay(self):
        """
        Apply time-based strength decay to all facts.

        For each fact where days since last access exceeds 30:
            decay_periods = floor(days_since_access / 30)
            new_strength = max(0.1, strength * 0.9 ^ decay_periods)

        Updates are applied in-place in the database.
        """
        now = time.time()
        cursor = self.conn.execute(
            "SELECT id, strength, last_accessed_at FROM facts"
        )
        updates: list[tuple[float, str]] = []

        for row in cursor.fetchall():
            days_since = (now - row['last_accessed_at']) / 86400.0
            if days_since > 30:
                decay_periods = math.floor(days_since / 30)
                new_strength = max(0.1, row['strength'] * (0.9 ** decay_periods))
                if abs(new_strength - row['strength']) > 1e-9:
                    updates.append((new_strength, row['id']))

        if updates:
            self.conn.executemany(
                "UPDATE facts SET strength = ? WHERE id = ?",
                updates,
            )
            self.conn.commit()

    def close(self):
        """Close the SQLite connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
