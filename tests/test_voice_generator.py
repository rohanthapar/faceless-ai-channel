"""Tests for TTS text normalisation."""
import pytest

from app.media.voice_generator import _clean_for_tts


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Even if the punch misses, nothing survives once...", "Even if the punch misses, nothing survives once."),
        ("Ends with comma,", "Ends with comma."),
        ("Wait… what — really?", "Wait, what, really?"),
        ("Normal sentence.", "Normal sentence."),
        ("No punctuation", "No punctuation."),
    ],
)
def test_clean_for_tts(raw, expected):
    assert _clean_for_tts(raw) == expected
