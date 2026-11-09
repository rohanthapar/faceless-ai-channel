"""
tests/test_config.py — settings validation tests.
"""
from __future__ import annotations

import os
import pytest

from app.config import Settings


def test_default_settings():
    s = Settings()
    assert s.video_width == 1080
    assert s.video_height == 1920
    assert s.video_fps == 30
    assert s.tts_primary_provider in ("edge", "piper", "none")


def test_pillar_percentages_sum_to_100():
    s = Settings()
    total = (
        s.pillar_psychology_pct
        + s.pillar_history_pct
        + s.pillar_technology_pct
        + s.pillar_storytelling_pct
        + s.pillar_science_pct
    )
    assert total == 100


def test_invalid_pillar_sum_raises(monkeypatch):
    monkeypatch.setenv("PILLAR_PSYCHOLOGY_PCT", "50")
    with pytest.raises(Exception):
        Settings()
