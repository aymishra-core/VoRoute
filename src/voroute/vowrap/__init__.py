"""Call-result capture: confirmed, declined, or unclear."""

from voroute.vowrap.classify import (
    MIN_CONFIDENCE,
    SINGLE_WORD_CONFIDENCE,
    Verdict,
    classify,
)

__all__ = [
    "MIN_CONFIDENCE",
    "SINGLE_WORD_CONFIDENCE",
    "Verdict",
    "classify",
]
