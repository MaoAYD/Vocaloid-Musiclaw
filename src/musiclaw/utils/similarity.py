from __future__ import annotations

from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised in lightweight environments
    fuzz = None

from musiclaw.utils.textnorm import normalize_text


def ratio(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    token_score = (fuzz.token_sort_ratio(left_norm, right_norm) / 100) if fuzz else 0.0
    seq_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    return round(max(token_score, seq_score), 4)


def any_ratio(needle: str | None, haystack: list[str]) -> float:
    return max((ratio(needle, candidate) for candidate in haystack), default=0.0)
