"""Token counting utilities with graceful fallbacks.

Primary strategy:
1. Use tiktoken (cl100k_base) as an approximate tokenizer for Gemini style models.
2. Fallback to simple heuristic (len(text)//4) if tiktoken unavailable or errors.

Public functions:
    estimate_tokens(text: str, model: str | None = None) -> int
    estimate_batch(texts: list[str], model: str | None = None) -> int
"""
from __future__ import annotations

from typing import List, Optional

try:  # optional dependency
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore

DEFAULT_ENCODING = "cl100k_base"  # widely used; decent proxy

def _fallback_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_tokens(text: str, model: Optional[str] = None) -> int:
    if not text:
        return 0
    if tiktoken is None:
        return _fallback_count(text)
    enc_name = DEFAULT_ENCODING
    # Map some known model prefixes if desired (extendable)
    if model:
        low = model.lower()
        if "gpt-4" in low or "gpt-3.5" in low or "o1" in low:
            enc_name = "cl100k_base"
        elif "gemini" in low:
            enc_name = DEFAULT_ENCODING
    try:
        enc = tiktoken.get_encoding(enc_name)
        return len(enc.encode(text))
    except Exception:  # pragma: no cover
        return _fallback_count(text)


def estimate_batch(texts: List[str], model: Optional[str] = None) -> int:
    total = 0
    for t in texts:
        total += estimate_tokens(t, model=model)
    return total

__all__ = ["estimate_tokens", "estimate_batch"]
