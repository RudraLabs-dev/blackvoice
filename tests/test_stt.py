"""The whisper.cpp backend and the tiering that chooses between engines."""

from __future__ import annotations

import struct
import subprocess
import wave

import pytest

from blackvoice.audio.stt import (
    HybridSTT,
    Transcript,
    WhisperCppRecognizer,
    _wav_bytes,
    find_whisper_binary,
)
from blackvoice.config import AudioConfig, SpeechConfig


def _pcm_block(amplitude: float, seconds: float, sample_rate: int = 16000) -> bytes:
    """A block of constant-amplitude int16 PCM, whose RMS equals ``amplitude``."""
    n = max(1, round(seconds * sample_rate))
    value = max(-32768, min(32767, int(round(amplitude * 32767))))
    return struct.pack(f"<{n}h", *([value] * n))


class _FakeMicrophone:
    """Hands out pre-recorded blocks, then silence, like a real Microphone."""

    def __init__(self, blocks, seconds_per_block: float = 0.5) -> None:
        self._blocks = list(blocks)
        self.seconds_per_block = seconds_per_block

    def read(self, timeout: float = 1.0):
        if self._blocks:
            return self._blocks.pop(0)
        return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _Completed:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def recognizer(tmp_path) -> WhisperCppRecognizer:
    model = tmp_path / "ggml-base-q5_1.bin"
    model.write_bytes(b"not a real model")
    return WhisperCppRecognizer(SpeechConfig(), model, 16000, binary="whisper-cli")


def _stub_run(monkeypatch, result=None, raises=None):
    def fake_run(argv, **kwargs):
        fake_run.argv = argv
        if raises is not None:
            raise raises
        return result

    fake_run.argv = None
    monkeypatch.setattr("blackvoice.audio.stt.subprocess.run", fake_run)
    return fake_run


class _FakeWhisper:
    """A whisper recogniser that returns whatever it was given."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.ready = True

    def transcribe(self, pcm: bytes) -> Transcript:
        self.calls += 1
        return Transcript(self.text, 0.9, "en", source="whisper")


class _FakeVosk:
    def __init__(self, text: str, confidence: float = 0.7) -> None:
        self.result = Transcript(text, confidence, "en", source="vosk")
        self.ready = True
        self.calls = 0

    def reset(self) -> None:
        pass

    def accept(self, block: bytes) -> bool:
        self.calls += 1
        return True

    def final(self) -> Transcript:
        return self.result


def _hybrid(whisper=None, recognizers=(), mode: str = "offline") -> HybridSTT:
    """A HybridSTT with the loading skipped and the engines injected."""
    speech = SpeechConfig()
    speech.mode = mode
    stt = HybridSTT(speech, AudioConfig())
    stt._loaded = True
    stt.whisper = whisper
    stt.recognizers = list(recognizers)
    return stt


# --------------------------------------------------------------------------- #
# output parsing
# --------------------------------------------------------------------------- #
def test_clean_strips_timestamps() -> None:
    stdout = "[00:00:00.000 --> 00:00:02.000]   open firefox\n"
    assert WhisperCppRecognizer._clean(stdout) == "open firefox"


def test_clean_drops_non_speech_annotations() -> None:
    assert WhisperCppRecognizer._clean("[BLANK_AUDIO]") == ""
    assert WhisperCppRecognizer._clean("(music playing)") == ""
    assert WhisperCppRecognizer._clean("volume 40 (buzzing)") == "volume 40"


def test_clean_joins_segments_and_collapses_space() -> None:
    stdout = " open  firefox \n\n and close the terminal \n"
    assert WhisperCppRecognizer._clean(stdout) == "open firefox and close the terminal"


def test_clean_of_nothing_is_empty() -> None:
    assert WhisperCppRecognizer._clean("") == ""
    assert WhisperCppRecognizer._clean("   \n  \n") == ""


def test_sentence_punctuation_is_dropped() -> None:
    """Whisper writes prose; a trailing stop defeats every $-anchored rule."""
    assert WhisperCppRecognizer._clean("Open Firefox.") == "Open Firefox"
    assert WhisperCppRecognizer._clean("volume 40.") == "volume 40"
    assert WhisperCppRecognizer._clean("what time is it?") == "what time is it"
    assert WhisperCppRecognizer._clean("stop!") == "stop"


def test_a_decimal_point_survives() -> None:
    """Arithmetic is a real command here, so "2.5" must not lose its point."""
    assert WhisperCppRecognizer._clean("what is 2.5 plus 3?") == "what is 2.5 plus 3"


# --------------------------------------------------------------------------- #
# locating the binary
# --------------------------------------------------------------------------- #
def test_configured_absolute_path_is_used(tmp_path) -> None:
    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    assert find_whisper_binary(str(binary)) == str(binary)


def test_configured_path_that_does_not_exist_is_none(tmp_path) -> None:
    assert find_whisper_binary(str(tmp_path / "nope")) is None


def test_path_is_searched_before_the_bundle(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("blackvoice.audio.stt.BUNDLED_BIN_DIR", tmp_path)
    (tmp_path / "whisper-cli").write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(
        "blackvoice.audio.stt.shutil.which",
        lambda name: "/usr/bin/whisper-cli" if name == "whisper-cli" else None,
    )
    assert find_whisper_binary() == "/usr/bin/whisper-cli"


def test_the_bundle_is_the_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("blackvoice.audio.stt.BUNDLED_BIN_DIR", tmp_path)
    (tmp_path / "whisper-cli").write_text("bundled", encoding="utf-8")
    monkeypatch.setattr("blackvoice.audio.stt.shutil.which", lambda name: None)
    assert find_whisper_binary() == str(tmp_path / "whisper-cli")


def test_bare_main_is_never_taken_from_path(monkeypatch, tmp_path) -> None:
    """"main" is only trusted inside our own directory, never off PATH."""
    monkeypatch.setattr("blackvoice.audio.stt.BUNDLED_BIN_DIR", tmp_path / "empty")
    monkeypatch.setattr(
        "blackvoice.audio.stt.shutil.which",
        lambda name: "/usr/local/bin/main" if name == "main" else None,
    )
    assert find_whisper_binary() is None


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #
def test_argv_carries_the_essentials(recognizer) -> None:
    argv = recognizer._argv("/tmp/x.wav")
    assert argv[0] == "whisper-cli"
    assert "--no-timestamps" in argv
    assert argv[argv.index("--file") + 1] == "/tmp/x.wav"
    assert argv[argv.index("--language") + 1] == "en"
    assert argv[argv.index("--model") + 1] == str(recognizer.model_path)


def test_the_command_vocabulary_is_passed_as_a_prompt(recognizer) -> None:
    argv = recognizer._argv("/tmp/x.wav")
    assert "open firefox" in argv[argv.index("--prompt") + 1]


def test_an_empty_prompt_is_omitted(recognizer) -> None:
    recognizer.speech.whisper_prompt = "   "
    assert "--prompt" not in recognizer._argv("/tmp/x.wav")


def test_threads_only_when_asked_for(recognizer) -> None:
    assert "--threads" not in recognizer._argv("/tmp/x.wav")
    recognizer.speech.whisper_threads = 4
    argv = recognizer._argv("/tmp/x.wav")
    assert argv[argv.index("--threads") + 1] == "4"


def test_a_forced_language_is_honoured(recognizer) -> None:
    recognizer.speech.whisper_language = "fr"
    argv = recognizer._argv("/tmp/x.wav")
    assert argv[argv.index("--language") + 1] == "fr"


# --------------------------------------------------------------------------- #
# transcribing
# --------------------------------------------------------------------------- #
def test_transcribe_returns_the_text(monkeypatch, recognizer) -> None:
    _stub_run(monkeypatch, _Completed(stdout=b" open firefox \n"))
    result = recognizer.transcribe(b"\x01\x02" * 800)
    assert result.text == "open firefox"
    assert result.source == "whisper"
    assert result.confidence > 0
    assert result.language == "en"


def test_a_nonzero_exit_yields_nothing(monkeypatch, recognizer) -> None:
    _stub_run(monkeypatch, _Completed(stderr=b"bad model", returncode=1))
    assert not recognizer.transcribe(b"\x01\x02" * 800)


def test_a_timeout_yields_nothing(monkeypatch, recognizer) -> None:
    _stub_run(monkeypatch, raises=subprocess.TimeoutExpired("whisper-cli", 30))
    assert not recognizer.transcribe(b"\x01\x02" * 800)


def test_an_unrunnable_binary_is_not_retried(monkeypatch, recognizer) -> None:
    """A binary that cannot be executed is dropped rather than paid for twice."""
    _stub_run(monkeypatch, raises=OSError("Exec format error"))
    assert not recognizer.transcribe(b"\x01\x02" * 800)
    assert recognizer.binary is None
    assert recognizer.ready is False


def test_silence_yields_nothing(monkeypatch, recognizer) -> None:
    _stub_run(monkeypatch, _Completed(stdout=b"[BLANK_AUDIO]\n"))
    assert not recognizer.transcribe(b"\x00\x00" * 800)


def test_empty_audio_does_not_spawn_anything(monkeypatch, recognizer) -> None:
    run = _stub_run(monkeypatch, _Completed(stdout=b"something"))
    assert not recognizer.transcribe(b"")
    assert run.argv is None


def test_a_missing_model_is_not_ready(tmp_path) -> None:
    rec = WhisperCppRecognizer(
        SpeechConfig(), tmp_path / "absent.bin", 16000, binary="whisper-cli"
    )
    assert rec.ready is False
    assert rec.load() is False


def test_the_scratch_wav_is_removed(monkeypatch, recognizer, tmp_path) -> None:
    monkeypatch.setattr("blackvoice.audio.stt._runtime_dir", lambda: str(tmp_path))
    _stub_run(monkeypatch, _Completed(stdout=b"open firefox"))
    recognizer.transcribe(b"\x01\x02" * 800)
    assert list(tmp_path.glob("*.wav")) == []


# --------------------------------------------------------------------------- #
# the WAV wrapper
# --------------------------------------------------------------------------- #
def test_wav_bytes_round_trip(tmp_path) -> None:
    pcm = bytes(range(256)) * 4
    path = tmp_path / "x.wav"
    path.write_bytes(_wav_bytes(pcm, 16000))

    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.readframes(handle.getnframes()) == pcm


# --------------------------------------------------------------------------- #
# tiering
# --------------------------------------------------------------------------- #
def test_whisper_wins_when_it_has_an_answer() -> None:
    whisper = _FakeWhisper("open firefox")
    vosk = _FakeVosk("open fire fox", confidence=0.99)
    stt = _hybrid(whisper, [vosk])

    result = stt.transcribe_pcm(b"\x01\x02" * 800)
    assert result.text == "open firefox"
    assert result.source == "whisper"
    # A high Vosk confidence must not outrank it: the scores share no scale.
    assert vosk.calls == 0


def test_an_empty_whisper_result_falls_through_to_vosk() -> None:
    whisper = _FakeWhisper("")
    vosk = _FakeVosk("open firefox")
    stt = _hybrid(whisper, [vosk])

    result = stt.transcribe_pcm(b"\x01\x02" * 800)
    assert result.text == "open firefox"
    assert result.source == "vosk"


def test_vosk_alone_still_races_on_confidence() -> None:
    quiet = _FakeVosk("open fire fox", confidence=0.3)
    loud = _FakeVosk("open firefox", confidence=0.8)
    stt = _hybrid(None, [quiet, loud])
    assert stt.transcribe_pcm(b"\x01\x02" * 800).text == "open firefox"


def test_online_mode_skips_whisper() -> None:
    whisper = _FakeWhisper("open firefox")
    stt = _hybrid(whisper, [], mode="online")
    stt.online = type("_None", (), {"transcribe": staticmethod(lambda pcm: None)})()

    stt.transcribe_pcm(b"\x01\x02" * 800)
    assert whisper.calls == 0


def test_offline_engine_names_what_would_run() -> None:
    assert _hybrid(_FakeWhisper("x"), [_FakeVosk("y")]).offline_engine == "whisper"
    assert _hybrid(None, [_FakeVosk("y")]).offline_engine == "vosk"
    assert _hybrid(None, []).offline_engine == "none"


def test_has_offline_counts_whisper() -> None:
    assert _hybrid(_FakeWhisper("x"), []).has_offline is True
    assert _hybrid(None, []).has_offline is False


# --------------------------------------------------------------------------- #
# listen_once: the full capture loop, with the calibrated Endpointer wired in
# --------------------------------------------------------------------------- #
def test_listen_once_stops_on_trailing_silence_and_transcribes() -> None:
    stt = _hybrid(_FakeWhisper("open firefox"), [], mode="offline")
    stt.audio.calibrate_noise = False
    stt.audio.silence_threshold = 0.02
    stt.audio.silence_timeout = 0.3

    blocks = [_pcm_block(0.5, 0.5)] + [_pcm_block(0.001, 0.5)] * 3
    mic = _FakeMicrophone(blocks)

    result = stt.listen_once(mic)
    assert result.text == "open firefox"


def test_listen_once_reports_nothing_when_only_silence_was_heard() -> None:
    stt = _hybrid(_FakeWhisper("should never be called"), [], mode="offline")
    stt.audio.calibrate_noise = False
    stt.audio.silence_threshold = 0.02
    stt.audio.max_command_seconds = 1.0

    mic = _FakeMicrophone([_pcm_block(0.001, 0.5)] * 3)

    assert not stt.listen_once(mic)


def test_listen_once_survives_periodic_ambient_blips() -> None:
    """The same regression :mod:`tests.test_mic` covers directly, exercised
    through the real capture loop end to end.
    """
    stt = _hybrid(_FakeWhisper("open firefox"), [], mode="offline")
    stt.audio.calibrate_noise = False
    stt.audio.silence_threshold = 0.02
    stt.audio.silence_timeout = 0.4
    stt.audio.max_command_seconds = 3.0

    blocks = (
        [_pcm_block(0.5, 0.5)]
        + [_pcm_block(0.021, 0.1), _pcm_block(0.001, 0.4)] * 4
    )
    mic = _FakeMicrophone(blocks)

    result = stt.listen_once(mic)
    assert result.text == "open firefox"
