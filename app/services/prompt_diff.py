from __future__ import annotations
from difflib import unified_diff
from typing import List, Dict, Any
import re

RISK_PATTERNS = [
    (re.compile(r"\bVERY\b", re.I), "Over-emphatic instruction (VERY)."),
    (re.compile(r"\bMUST\b", re.I), "Hard MUST directive; may reduce creativity."),
    (re.compile(r"\bALWAYS\b", re.I), "ALWAYS directive; can cause rigidity."),
    (re.compile(r"\bNEVER\b", re.I), "Negative absolute (NEVER); may block valid output."),
]

SAFE_LENGTH_MAX = 8000  # char heuristic

def diff_prompts(old: str, new: str) -> Dict[str, Any]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = '\n'.join(unified_diff(old_lines, new_lines, lineterm=''))
    risks: List[str] = []
    if len(new) > SAFE_LENGTH_MAX:
        risks.append(f"Prompt length {len(new)} exceeds {SAFE_LENGTH_MAX} char heuristic.")
    for pattern, msg in RISK_PATTERNS:
        if pattern.search(new):
            risks.append(msg)
    return {
        'diff': diff[:20000],
        'new_length': len(new),
        'old_length': len(old),
        'risk_flags': risks,
    }
