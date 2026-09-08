"""Token budget manager: relevance-sorted, drop-from-tail."""

from __future__ import annotations

import re
from dataclasses import dataclass


_WORD_RE = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ~1.3 tokens per whitespace-separated word."""
    if not text:
        return 0
    words = len(_WORD_RE.findall(text))
    return max(1, int(words * 1.3)) if words else 0


@dataclass
class BudgetedBlock:
    """One ranked text block (usually one hex render)."""

    text: str
    score: float  # higher = more relevant
    hex_id: str = ""
    ring: int = 0

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def trim_to_budget(blocks: list[BudgetedBlock], token_budget: int) -> tuple[str, list[BudgetedBlock]]:
    """Sort by score desc, keep blocks until budget exhausted.

    Returns (joined_text, kept_blocks). Truncation order is stable for equal scores
    by original list order (Python sort is stable).
    """
    if token_budget <= 0:
        return "", []

    # Stable sort: higher score first; preserve relative order for ties.
    ranked = sorted(enumerate(blocks), key=lambda iv: (-iv[1].score, iv[0]))
    kept: list[BudgetedBlock] = []
    used = 0
    for _, block in ranked:
        t = block.tokens
        if used + t > token_budget:
            continue
        kept.append(block)
        used += t

    # Restore ring/hex spatial order for readability (by ring, then hex_id).
    kept.sort(key=lambda b: (b.ring, b.hex_id))
    text = "\n".join(b.text for b in kept if b.text.strip())
    return text, kept
