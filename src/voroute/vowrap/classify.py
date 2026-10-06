"""Map one transcript to CONFIRMED, DECLINED, or UNCLEAR. Never guess."""

import re
import unicodedata
from enum import Enum

MIN_CONFIDENCE = 0.70
SINGLE_WORD_CONFIDENCE = 0.55

_CONFIRMED = frozenset(
    {
        "haan",
        "han",
        "chahiye",
        "yes",
        "ok",
        "okay",
        "हाँ",
        "हां",
        "हांजी",
        "हाँजी",
        "चाहिए",
        "चाहिये",
        "येस",
        "ओके",
    }
)
_DECLINED = frozenset(
    {
        "na",
        "nahi",
        "nahin",
        "cancel",
        "no",
        "नहीं",
        "नही",
        "नहींजी",
        "नहीजी",
        "ना",
        "नो",
        "कैंसल",
        "कैंसिल",
    }
)
_FILLER = frozenset({"ji", "जी"})
_DROP = re.compile(r"[^a-z\u0900-\u097f\s]+|[\u0964-\u096f]")


class Verdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    UNCLEAR = "UNCLEAR"


def _content_tokens(transcript: str) -> list[str]:
    text = _DROP.sub("", unicodedata.normalize("NFC", transcript).lower())
    return [token for token in text.split() if token and token not in _FILLER]


def classify(transcript: str, confidence: float | None) -> Verdict:
    """Confirm or decline only when every token is in one family and confidence clears that length's gate."""

    tokens = _content_tokens(transcript)
    if not tokens:
        return Verdict.UNCLEAR
    confirmed = any(token in _CONFIRMED for token in tokens)
    declined = any(token in _DECLINED for token in tokens)
    outside = any(
        token not in _CONFIRMED and token not in _DECLINED for token in tokens
    )
    if outside or (confirmed and declined) or not (confirmed or declined):
        return Verdict.UNCLEAR
    threshold = SINGLE_WORD_CONFIDENCE if len(tokens) == 1 else MIN_CONFIDENCE
    if confidence is None or confidence < threshold:
        return Verdict.UNCLEAR
    if confirmed:
        return Verdict.CONFIRMED
    return Verdict.DECLINED
