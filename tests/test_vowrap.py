import pytest

from voroute.vowrap import MIN_CONFIDENCE, SINGLE_WORD_CONFIDENCE, Verdict, classify


def test_confidence_gates() -> None:
    assert MIN_CONFIDENCE == 0.70
    assert SINGLE_WORD_CONFIDENCE == 0.55


@pytest.mark.parametrize(
    "transcript",
    ["haan", "haan ji", "Haan Ji", "chahiye", "yes", "Yes!", "ok", "okay", "han", "ok ji"],
)
def test_haan_family_confirms(transcript: str) -> None:
    assert classify(transcript, 0.91) is Verdict.CONFIRMED


@pytest.mark.parametrize(
    "transcript",
    ["na", "nahi", "nahin", "cancel", "no", "nahi ji", "nahin ji"],
)
def test_na_family_declines(transcript: str) -> None:
    assert classify(transcript, 0.91) is Verdict.DECLINED


@pytest.mark.parametrize(
    "transcript",
    ["", "   ", "theek hai", "haan nahi", "haan bilkul", "bilkul", "yes no", "namaste"],
)
def test_empty_or_ambiguous_stays_unclear(transcript: str) -> None:
    assert classify(transcript, 0.91) is Verdict.UNCLEAR


def test_single_word_gate_is_lower_than_the_multi_word_gate() -> None:
    assert classify("haan", 0.69) is Verdict.CONFIRMED
    assert classify("haan", 0.55) is Verdict.CONFIRMED
    assert classify("haan", 0.54) is Verdict.UNCLEAR
    assert classify("haan", None) is Verdict.UNCLEAR
    assert classify("no", 0.50) is Verdict.UNCLEAR
    assert classify("haan", 0.70) is Verdict.CONFIRMED
    assert classify("chahiye haan", 0.65) is Verdict.UNCLEAR
    assert classify("chahiye haan", 0.70) is Verdict.CONFIRMED


@pytest.mark.parametrize(
    "transcript",
    ["हां", "हाँ", "हां जी", "हां।", "चाहिए", "हांजी"],
)
def test_devanagari_haan_at_the_single_word_gate(transcript: str) -> None:
    assert classify(transcript, 0.58) is Verdict.CONFIRMED


def test_devanagari_haan_below_the_single_word_gate_stays_unclear() -> None:
    assert classify("हां", 0.54) is Verdict.UNCLEAR


@pytest.mark.parametrize("transcript", ["नहीं", "नही", "ना", "नो"])
def test_devanagari_na_at_the_single_word_gate(transcript: str) -> None:
    assert classify(transcript, 0.58) is Verdict.DECLINED


@pytest.mark.parametrize(
    "transcript",
    ["आ", "न", "हां नहीं", "हां बिलकुल"],
)
def test_excluded_or_mixed_devanagari_stays_unclear(transcript: str) -> None:
    assert classify(transcript, 0.99) is Verdict.UNCLEAR
