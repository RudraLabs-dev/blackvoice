"""The accuracy harness: scoring, the corpus format, and recording."""

from __future__ import annotations

import json
import struct

import pytest

from blackvoice import evaluate
from blackvoice.audio.stt import Transcript
from blackvoice.config import Config


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pcm(amplitude: int, blocks: int = 1, block_size: int = 8000) -> bytes:
    """``blocks`` blocks of a square wave at ``amplitude``."""
    sample = struct.pack("<h", amplitude)
    return sample * block_size * blocks


def _corpus(tmp_path, entries) -> object:
    """Write a corpus of (filename, reference) pairs and return its directory."""
    root = tmp_path / "eval"
    root.mkdir()
    for name, reference in entries:
        evaluate.write_wav(root / name, _pcm(6000), 16000)
        evaluate.append_sample(root, evaluate.Sample(audio=name, reference=reference))
    return root


class _FakeMic:
    """Yields loud blocks, then silence for ever."""

    def __init__(self, loud_blocks: int, block_size: int = 8000) -> None:
        self.remaining = loud_blocks
        self.block_size = block_size
        self.seconds_per_block = block_size / 16000

    def read(self, timeout: float = 1.0) -> bytes:
        if self.remaining > 0:
            self.remaining -= 1
            return _pcm(6000, block_size=self.block_size)
        return _pcm(0, block_size=self.block_size)


# --------------------------------------------------------------------------- #
# edit distance
# --------------------------------------------------------------------------- #
def test_identical_text_has_no_edits() -> None:
    assert evaluate.word_edits("open firefox", "open firefox") == (0, 2)


def test_one_substitution() -> None:
    assert evaluate.word_edits("firefox kholo", "firefox hollow") == (1, 2)


def test_insertion_and_deletion() -> None:
    assert evaluate.word_edits("open firefox", "please open the firefox")[0] == 2
    assert evaluate.word_edits("open the firefox", "open firefox")[0] == 1


def test_nothing_heard_costs_every_word() -> None:
    assert evaluate.word_edits("volume forty percent", "") == (3, 3)


def test_scoring_ignores_the_casing_the_router_ignores() -> None:
    assert evaluate.word_edits("Open Firefox", "open firefox") == (0, 2)


def test_a_full_stop_does_count_as_an_error() -> None:
    """Deliberate, and worth pinning down.

    normalise() keeps the full stop because arithmetic needs "2.5", so it is a
    real difference by the time it reaches the scoring. The engine that writes
    prose is the one that has to drop it - see WhisperCppRecognizer._clean -
    rather than the scorer papering over it for every backend.
    """
    assert evaluate.word_edits("open firefox", "open firefox.")[0] == 1


def test_an_empty_reference_has_no_words() -> None:
    assert evaluate.word_edits("", "hello") == (1, 0)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _row(reference: str, hypothesis: str, edits: int, words: int, **kw):
    return evaluate.SampleResult(
        sample=evaluate.Sample(audio="x.wav", reference=reference),
        hypothesis=hypothesis,
        seconds=kw.get("seconds", 1.0),
        edits=edits,
        ref_words=words,
        expected_intent=kw.get("expected", "open_app"),
        got_intent=kw.get("got", "open_app"),
    )


def test_corpus_wer_pools_rather_than_averages() -> None:
    """A long sentence must weigh more than a short one."""
    result = evaluate.BackendResult("test")
    result.results = [
        _row("a b c d e f g h i j", "wrong " * 10, 10, 10),  # 100%
        _row("ek", "ek", 0, 1),                              # 0%
    ]
    # Pooled: 10 edits over 11 words. A per-sample mean would give 50%.
    assert result.wer == pytest.approx(10 / 11)


def test_intent_accuracy_only_counts_commands() -> None:
    result = evaluate.BackendResult("test")
    result.results = [
        _row("open firefox", "open firefox", 0, 2, expected="open_app", got="open_app"),
        _row("firefox kholo", "firefox hollow", 1, 2, expected="open_app", got="ask"),
        # A question: not a command, so it is outside the measurement.
        _row("who wrote it", "who wrote it", 0, 3, expected="ask", got="ask"),
    ]
    assert len(result.commands) == 2
    assert result.intent_accuracy == pytest.approx(0.5)


def test_failures_lists_the_misrouted_commands() -> None:
    result = evaluate.BackendResult("test")
    result.results = [
        _row("open firefox", "open firefox", 0, 2),
        _row("firefox kholo", "nonsense", 2, 2, got="ask"),
    ]
    assert [r.sample.reference for r in result.failures] == ["firefox kholo"]


def test_exact_match_uses_the_same_normalisation() -> None:
    result = evaluate.BackendResult("test")
    result.results = [_row("Open Firefox", "open firefox", 0, 2)]
    assert result.exact_match == pytest.approx(1.0)


def test_median_of_an_even_number_of_times() -> None:
    result = evaluate.BackendResult("test")
    result.results = [
        _row("a", "a", 0, 1, seconds=1.0),
        _row("a", "a", 0, 1, seconds=3.0),
    ]
    assert result.median_seconds == pytest.approx(2.0)


def test_an_empty_result_does_not_divide_by_zero() -> None:
    empty = evaluate.BackendResult("test")
    assert empty.wer == 0.0
    assert empty.intent_accuracy == 0.0
    assert empty.exact_match == 0.0
    assert empty.median_seconds == 0.0


# --------------------------------------------------------------------------- #
# the corpus format
# --------------------------------------------------------------------------- #
def test_manifest_round_trip(tmp_path) -> None:
    root = tmp_path / "eval"
    evaluate.append_sample(root, evaluate.Sample("0001.wav", "firefox kholo"))
    evaluate.append_sample(
        root, evaluate.Sample("0002.wav", "volume 40", intent="volume_set", note="noisy")
    )

    samples = evaluate.load_corpus(root)
    assert [s.audio for s in samples] == ["0001.wav", "0002.wav"]
    assert samples[0].reference == "firefox kholo"
    assert samples[1].intent == "volume_set"
    assert samples[1].note == "noisy"


def test_devanagari_survives_the_manifest(tmp_path) -> None:
    root = tmp_path / "eval"
    evaluate.append_sample(root, evaluate.Sample("0001.wav", "आवाज़ बंद करो"))
    assert evaluate.load_corpus(root)[0].reference == "आवाज़ बंद करो"


def test_a_missing_manifest_is_an_empty_corpus(tmp_path) -> None:
    assert evaluate.load_corpus(tmp_path / "nothing") == []


def test_broken_lines_are_skipped_not_fatal(tmp_path) -> None:
    root = tmp_path / "eval"
    root.mkdir()
    (root / evaluate.MANIFEST_NAME).write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "{not json",
                json.dumps({"audio": "0001.wav"}),                 # no reference
                json.dumps({"reference": "no audio"}),              # no audio
                json.dumps({"audio": "0002.wav", "reference": "ok"}),
            ]
        ),
        encoding="utf-8",
    )
    samples = evaluate.load_corpus(root)
    assert [s.audio for s in samples] == ["0002.wav"]


def test_next_index_extends_an_existing_corpus(tmp_path) -> None:
    root = tmp_path / "eval"
    root.mkdir()
    assert evaluate.next_index(root) == 1
    for name in ("0001.wav", "0007.wav", "notanumber.wav"):
        (root / name).write_bytes(b"")
    assert evaluate.next_index(root) == 8


# --------------------------------------------------------------------------- #
# audio
# --------------------------------------------------------------------------- #
def test_wav_round_trip(tmp_path) -> None:
    pcm = _pcm(1234, blocks=1, block_size=100)
    path = tmp_path / "x.wav"
    evaluate.write_wav(path, pcm, 16000)
    assert evaluate.read_wav(path, 16000) == pcm


def test_a_wrong_sample_rate_is_refused_not_resampled(tmp_path) -> None:
    path = tmp_path / "x.wav"
    evaluate.write_wav(path, _pcm(1000, block_size=100), 8000)
    with pytest.raises(evaluate.WavMismatch):
        evaluate.read_wav(path, 16000)


# --------------------------------------------------------------------------- #
# recording
# --------------------------------------------------------------------------- #
def test_capture_stops_after_the_speaker_does() -> None:
    cfg = Config().audio
    mic = _FakeMic(loud_blocks=2, block_size=cfg.block_size)
    pcm = evaluate.capture_utterance(mic, cfg)

    # Two loud blocks, then enough silence to end the utterance.
    blocks = len(pcm) / 2 / cfg.block_size
    assert blocks >= 2
    assert blocks * cfg.block_size / cfg.sample_rate < cfg.max_command_seconds


def test_capture_returns_nothing_when_no_one_spoke() -> None:
    cfg = Config().audio
    mic = _FakeMic(loud_blocks=0, block_size=cfg.block_size)
    assert evaluate.capture_utterance(mic, cfg) == b""


def test_capture_respects_the_hard_cap() -> None:
    cfg = Config().audio
    # Someone who never stops talking must still be cut off.
    mic = _FakeMic(loud_blocks=10**6, block_size=cfg.block_size)
    pcm = evaluate.capture_utterance(mic, cfg)
    seconds = len(pcm) / 2 / cfg.sample_rate
    assert seconds <= cfg.max_command_seconds + cfg.block_size / cfg.sample_rate


class _BlipMic:
    """One loud block, then ambient blips that cross the raw threshold
    interleaved with real silence - the shape of the reported "keeps
    listening too long" bug. A corpus recorded through this harness has to
    stop the same way the live assistant now does, which is the point of
    both sharing :class:`~blackvoice.audio.mic.Endpointer` rather than each
    keeping their own copy of the endpointing decision.
    """

    def __init__(self, block_size: int) -> None:
        self.block_size = block_size
        self.seconds_per_block = block_size / 16000
        self._queue = [_pcm(6000, block_size=block_size)]
        for _ in range(20):
            # 500/32767 ~= 0.0153: crosses the default 0.012 threshold, as
            # ambient noise does, but stays well under the 1.5x confirm line.
            self._queue.append(_pcm(500, block_size=block_size))   # a blip
            self._queue.append(_pcm(0, block_size=block_size))     # real silence

    def read(self, timeout: float = 1.0):
        if self._queue:
            return self._queue.pop(0)
        return _pcm(0, block_size=self.block_size)


def test_capture_survives_periodic_ambient_blips_like_the_live_assistant_does() -> None:
    cfg = Config().audio
    cfg.calibrate_noise = False
    cfg.silence_timeout = 0.8
    mic = _BlipMic(block_size=cfg.block_size)

    pcm = evaluate.capture_utterance(mic, cfg)
    seconds = len(pcm) / 2 / cfg.sample_rate
    assert seconds < cfg.max_command_seconds, (
        "a hard reset on every blip would have run this all the way to the cap"
    )


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def test_the_builtin_prompts_cover_all_three_registers() -> None:
    prompts = evaluate.load_prompts(None)
    assert len(prompts) > 30
    assert any("kholo" in p for p in prompts)              # Hinglish
    assert any("खोलो" in p for p in prompts)               # Devanagari
    assert any("open firefox" in p for p in prompts)       # English


def test_prompts_from_a_file_skip_comments(tmp_path) -> None:
    path = tmp_path / "prompts.txt"
    path.write_text(
        "# heading\n\nblack open firefox\n  black volume 40  \n", encoding="utf-8"
    )
    assert evaluate.load_prompts(path) == ["black open firefox", "black volume 40"]


# --------------------------------------------------------------------------- #
# scoring a whole corpus
# --------------------------------------------------------------------------- #
def test_score_derives_the_expected_intent_from_the_reference(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(
        tmp_path,
        [("0001.wav", "black open firefox"), ("0002.wav", "black screenshot lo")],
    )
    # A backend that hears perfectly.
    heard = {"0001.wav": "black open firefox", "0002.wav": "black screenshot lo"}
    order = iter(["0001.wav", "0002.wav"])
    monkeypatch.setattr(
        evaluate,
        "build_backend",
        lambda name, config: (
            lambda pcm: Transcript(heard[next(order)], 0.9, "en", source="test"),
            "",
        ),
    )

    result = evaluate.score(
        evaluate.load_corpus(root), root, "test", Config()
    )
    assert result.wer == 0.0
    assert result.intent_accuracy == pytest.approx(1.0)
    assert [r.expected_intent for r in result.results] == ["open_app", "screenshot"]


def test_score_notices_when_a_mishearing_changes_the_routing(
    monkeypatch, tmp_path
) -> None:
    root = _corpus(tmp_path, [("0001.wav", "black firefox kholo")])
    monkeypatch.setattr(
        evaluate,
        "build_backend",
        # What a monolingual English model does to a code-switched command.
        lambda name, config: (
            lambda pcm: Transcript("black firefox hollow oh", 0.9, "en"),
            "",
        ),
    )

    result = evaluate.score(evaluate.load_corpus(root), root, "test", Config())
    assert result.intent_accuracy == 0.0
    assert result.failures[0].expected_intent == "open_app"


def test_an_explicit_intent_overrides_the_derived_one(monkeypatch, tmp_path) -> None:
    root = tmp_path / "eval"
    root.mkdir()
    evaluate.write_wav(root / "0001.wav", _pcm(6000), 16000)
    evaluate.append_sample(
        root,
        evaluate.Sample("0001.wav", "black volume chalis karo", intent="volume_set"),
    )
    monkeypatch.setattr(
        evaluate,
        "build_backend",
        lambda name, config: (lambda pcm: Transcript("black volume 40", 0.9, "en"), ""),
    )

    result = evaluate.score(evaluate.load_corpus(root), root, "test", Config())
    row = result.results[0]
    assert row.expected_intent == "volume_set"
    assert row.got_intent == "volume_set"
    assert row.intent_ok


def test_an_unreadable_recording_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    root = tmp_path / "eval"
    root.mkdir()
    evaluate.append_sample(root, evaluate.Sample("missing.wav", "black open firefox"))
    monkeypatch.setattr(
        evaluate,
        "build_backend",
        lambda name, config: (lambda pcm: Transcript("x", 0.9, "en"), ""),
    )

    result = evaluate.score(evaluate.load_corpus(root), root, "test", Config())
    assert result.results[0].error
    assert result.results[0].hypothesis == ""


def test_an_unavailable_backend_says_why(monkeypatch, tmp_path) -> None:
    root = _corpus(tmp_path, [("0001.wav", "black open firefox")])
    monkeypatch.setattr(
        evaluate, "build_backend", lambda name, config: (None, "not installed")
    )
    result = evaluate.score(evaluate.load_corpus(root), root, "whisper", Config())
    assert result.unavailable == "not installed"
    assert result.results == []


def test_unknown_backend_is_unavailable() -> None:
    runner, reason = evaluate.build_backend("nonsense", Config())
    assert runner is None
    assert "nonsense" in reason


def test_a_devanagari_command_is_scored_as_a_command(monkeypatch, tmp_path) -> None:
    """The Hindi path, which is what the whisper backend actually emits."""
    root = tmp_path / "eval"
    root.mkdir()
    evaluate.write_wav(root / "0001.wav", _pcm(6000), 16000)
    evaluate.append_sample(
        root, evaluate.Sample("0001.wav", "black \u0938\u094d\u0915\u094d\u0930\u0940\u0928\u0936\u0949\u091f \u0932\u094b")
    )
    monkeypatch.setattr(
        evaluate,
        "build_backend",
        lambda name, config: (
            lambda pcm: Transcript(
                "black \u0938\u094d\u0915\u094d\u0930\u0940\u0928\u0936\u0949\u091f \u0932\u094b.", 0.9, "hi"
            ),
            "",
        ),
    )

    result = evaluate.score(evaluate.load_corpus(root), root, "test", Config())
    row = result.results[0]
    assert row.expected_intent == "screenshot"
    assert row.got_intent == "screenshot"
