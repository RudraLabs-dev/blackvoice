"""Endpointer: calibrated, hysteresis-based end-of-utterance detection."""

from __future__ import annotations

import struct
from typing import List, Optional

import pytest

from blackvoice.audio.mic import Endpointer
from blackvoice.config import AudioConfig


def _pcm_block(amplitude: float, seconds: float, sample_rate: int = 16000) -> bytes:
    """A block of constant-amplitude int16 PCM, whose RMS equals ``amplitude``."""
    n = max(1, round(seconds * sample_rate))
    value = max(-32768, min(32767, int(round(amplitude * 32767))))
    return struct.pack(f"<{n}h", *([value] * n))


def _cfg(**overrides) -> AudioConfig:
    return AudioConfig(**overrides)


# --------------------------------------------------------------------------- #
# the ordinary path
# --------------------------------------------------------------------------- #
def test_quiet_room_then_speech_then_silence_stops_promptly() -> None:
    cfg = _cfg(calibration_seconds=0.2, silence_timeout=0.3)
    ep = Endpointer(cfg)

    ep.feed(_pcm_block(0.001, 0.5))  # quiet room: calibrates, then stays silent
    assert not ep.heard_speech

    ep.feed(_pcm_block(0.5, 0.5))  # the actual utterance
    assert ep.heard_speech
    assert not ep.should_stop

    elapsed = 0.0
    for _ in range(10):
        ep.feed(_pcm_block(0.001, 0.1))
        elapsed += 0.1
        if ep.should_stop:
            break

    assert ep.should_stop
    assert elapsed <= 0.5, "should stop close to silence_timeout, nowhere near the 12s cap"


def test_pure_silence_never_reports_speech_or_a_stop() -> None:
    """Nothing was ever said - this must not hang, but it must not fire either."""
    cfg = _cfg(silence_timeout=0.3, calibration_seconds=0.3)
    ep = Endpointer(cfg)

    for _ in range(20):
        ep.feed(_pcm_block(0.0005, 0.1))

    assert not ep.heard_speech
    assert not ep.should_stop


def test_a_mid_sentence_pause_shorter_than_the_timeout_does_not_cut_off() -> None:
    cfg = _cfg(calibrate_noise=False, silence_threshold=0.02, silence_timeout=0.5)
    ep = Endpointer(cfg)

    ep.feed(_pcm_block(0.5, 0.3))  # "open the..."
    ep.feed(_pcm_block(0.001, 0.3))  # a breath - well under the 0.5s timeout
    assert not ep.should_stop

    ep.feed(_pcm_block(0.5, 0.3))  # "...terminal"
    assert ep.heard_speech
    assert not ep.should_stop

    ep.feed(_pcm_block(0.001, 0.5))  # only now has the speaker actually stopped
    assert ep.should_stop


# --------------------------------------------------------------------------- #
# the regression this class exists to fix
# --------------------------------------------------------------------------- #
def test_periodic_ambient_blips_do_not_prevent_stopping() -> None:
    """The reported bug: ambient noise occasionally crossing the threshold used
    to reset the silence clock to zero every time, so intermittent noise could
    keep the assistant "listening" for the full 12-second hard cap. A blip that
    barely crosses the threshold (but nowhere near confidently-still-talking
    loud) must now only pause the clock, not restart it.
    """
    cfg = _cfg(calibrate_noise=False, silence_threshold=0.02, silence_timeout=0.5)
    ep = Endpointer(cfg)

    ep.feed(_pcm_block(0.5, 0.3))
    assert ep.heard_speech

    blip = 0.021  # just crosses the threshold, as ambient noise does
    quiet = 0.001
    elapsed = 0.0
    for _ in range(60):
        ep.feed(_pcm_block(quiet, 0.1))
        elapsed += 0.1
        if ep.should_stop:
            break
        ep.feed(_pcm_block(blip, 0.1))
        elapsed += 0.1
        if ep.should_stop:
            break

    assert ep.should_stop
    assert elapsed < 2.0, "a hard reset on every blip would never have reached this"


def test_a_confidently_loud_block_still_resets_the_clock() -> None:
    """The hysteresis band is not a blank cheque: something clearly loud after
    a pause is treated as resumed speech, exactly as it should be.
    """
    cfg = _cfg(calibrate_noise=False, silence_threshold=0.02, silence_timeout=0.5)
    ep = Endpointer(cfg)

    ep.feed(_pcm_block(0.5, 0.3))
    ep.feed(_pcm_block(0.001, 0.4))  # most of the way to the timeout
    assert not ep.should_stop

    ep.feed(_pcm_block(0.5, 0.1))  # unambiguously resumed speech
    assert not ep.should_stop

    ep.feed(_pcm_block(0.001, 0.4))
    assert not ep.should_stop  # the reset actually took effect
    ep.feed(_pcm_block(0.001, 0.2))
    assert ep.should_stop


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #
def test_calibration_raises_the_threshold_to_match_a_noisy_room() -> None:
    cfg = _cfg(calibrate_noise=True, calibration_seconds=0.4, calibration_margin=1.6,
               silence_threshold=0.012)
    ep = Endpointer(cfg)

    for _ in range(4):
        ep.feed(_pcm_block(0.02, 0.1))

    assert ep.calibrated
    assert ep.noise_floor == pytest.approx(0.02, rel=0.05)
    assert ep.threshold == pytest.approx(0.02 * 1.6, rel=0.05)


def test_calibration_percentile_survives_one_loud_outlier() -> None:
    """Someone who starts talking the instant the wake chime ends must not
    spoil the floor for the rest of the room - a low percentile sees past it.
    """
    cfg = _cfg(calibrate_noise=True, calibration_seconds=0.5, calibration_margin=1.6,
               silence_threshold=0.012)
    ep = Endpointer(cfg)

    for amplitude in (0.02, 0.02, 0.02, 0.02, 0.3):
        ep.feed(_pcm_block(amplitude, 0.1))

    assert ep.calibrated
    assert ep.noise_floor == pytest.approx(0.02, rel=0.05)


def test_calibration_threshold_is_capped_against_a_loud_room() -> None:
    cfg = _cfg(calibrate_noise=True, calibration_seconds=0.3, calibration_margin=1.6,
               silence_threshold=0.012)
    ep = Endpointer(cfg)

    for _ in range(3):
        ep.feed(_pcm_block(0.9, 0.1))  # a genuinely loud room throughout calibration

    assert ep.threshold == pytest.approx(0.012 * 5, rel=1e-6)


def test_calibration_never_lowers_the_threshold_below_the_configured_floor() -> None:
    cfg = _cfg(calibrate_noise=True, calibration_seconds=0.3, calibration_margin=1.6,
               silence_threshold=0.05)
    ep = Endpointer(cfg)

    for _ in range(3):
        ep.feed(_pcm_block(0.0001, 0.1))  # an extremely quiet room

    assert ep.threshold == pytest.approx(0.05)


def test_calibration_can_be_switched_off() -> None:
    cfg = _cfg(calibrate_noise=False, silence_threshold=0.02)
    ep = Endpointer(cfg)
    assert ep.calibrated

    ep.feed(_pcm_block(0.9, 1.0))
    assert ep.threshold == pytest.approx(0.02), "calibration is off; the floor must not move"


def test_should_stop_is_false_while_still_calibrating() -> None:
    cfg = _cfg(calibrate_noise=True, calibration_seconds=1.0, silence_timeout=0.1)
    ep = Endpointer(cfg)
    ep.feed(_pcm_block(0.9, 0.3))  # loud throughout, but calibration is not done yet
    assert not ep.should_stop


# --------------------------------------------------------------------------- #
# sub-block slicing
# --------------------------------------------------------------------------- #
def test_feed_returns_one_level_per_hundred_millisecond_sub_block() -> None:
    cfg = _cfg(sample_rate=16000)
    ep = Endpointer(cfg)
    levels = ep.feed(_pcm_block(0.5, 0.5))  # a real 0.5s mic block
    assert len(levels) == 5


def test_a_block_shorter_than_a_sub_block_is_handled_whole() -> None:
    cfg = _cfg(sample_rate=16000)
    ep = Endpointer(cfg)
    levels = ep.feed(_pcm_block(0.5, 0.03))
    assert len(levels) == 1
