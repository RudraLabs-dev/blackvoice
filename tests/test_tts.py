"""Text-to-speech engine selection and the Piper subprocess wiring."""

from __future__ import annotations

import pytest

from blackvoice import piper_install
from blackvoice.audio import tts as tts_mod
from blackvoice.audio.tts import Speaker, detect_engine
from blackvoice.config import VoiceConfig


# --------------------------------------------------------------------------- #
# detect_engine: Piper detection goes through piper_install now, not a bare
# shutil.which("piper") - so a private, per-user install this project fetched
# itself is found too, not just one already on PATH.
# --------------------------------------------------------------------------- #
def test_detect_engine_prefers_piper_when_installed(monkeypatch) -> None:
    monkeypatch.setattr(piper_install, "find_binary", lambda: "/opt/piper/piper")
    assert detect_engine() == "piper"


def test_detect_engine_falls_back_when_piper_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(piper_install, "find_binary", lambda: None)
    monkeypatch.setattr("blackvoice.audio.tts._which", lambda name: "/usr/bin/espeak-ng" if "espeak" in name else None)
    assert detect_engine() == "espeak"


# --------------------------------------------------------------------------- #
# _speak_piper: the player must be handed the WAV stream as-is, not told to
# expect headerless raw PCM.
#
# `piper --output_file -` writes a complete WAV file - RIFF header and all -
# to stdout, confirmed by inspecting the actual bytes on a real machine. The
# previous code told aplay to expect raw S16_LE at a hardcoded 22050 Hz,
# which played the 44-byte header as if it were audio and would have played
# the wrong pitch and speed entirely for any Piper voice whose native rate is
# not 22050 Hz. Every player this project shells out to (aplay, paplay,
# pw-play) auto-detects a WAV stream from its own header, so the fix is to
# stop overriding that.
# --------------------------------------------------------------------------- #
class _FakeStream:
    def __init__(self) -> None:
        self.closed = False
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, argv, **kwargs) -> None:
        self.argv = argv
        self.stdin = _FakeStream()
        self.stdout = _FakeStream()
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        pass


@pytest.fixture
def piper_speaker(monkeypatch):
    monkeypatch.setattr(tts_mod, "_which", lambda name: "/usr/bin/aplay" if name == "aplay" else None)
    monkeypatch.setattr(piper_install, "find_binary", lambda: "/opt/piper/piper")
    monkeypatch.setattr(piper_install, "espeak_data_dir", lambda: None)

    speaker = Speaker(VoiceConfig(engine="piper"))
    monkeypatch.setattr(speaker, "_piper_voice_for", lambda: "/voices/en_US.onnx")

    calls = []
    monkeypatch.setattr(
        tts_mod.subprocess, "Popen",
        lambda argv, **k: calls.append(argv) or _FakeProc(argv, **k),
    )
    speaker._popen_calls = calls
    yield speaker
    speaker.shutdown()


def test_the_player_is_given_no_format_hints(piper_speaker) -> None:
    piper_speaker._speak_piper("hello")
    player_argv = piper_speaker._popen_calls[1]
    assert player_argv == ["/usr/bin/aplay", "-"]


def test_the_player_still_reads_from_pipers_stdout(piper_speaker) -> None:
    piper_speaker._speak_piper("hello")
    piper_call_kwargs_argv = piper_speaker._popen_calls[0]
    assert piper_call_kwargs_argv[0] == "/opt/piper/piper"


def test_espeak_data_is_passed_when_available(monkeypatch, piper_speaker) -> None:
    from pathlib import PurePosixPath

    data_dir = PurePosixPath("/opt/piper/espeak-ng-data")
    monkeypatch.setattr(piper_install, "espeak_data_dir", lambda: data_dir)
    piper_speaker._speak_piper("hello")
    piper_argv = piper_speaker._popen_calls[0]
    assert "--espeak_data" in piper_argv
    assert piper_argv[piper_argv.index("--espeak_data") + 1] == str(data_dir)


# --------------------------------------------------------------------------- #
# _piper_voice_for: always the configured English voice
# --------------------------------------------------------------------------- #
def test_piper_voice_for_uses_the_configured_english_voice(monkeypatch) -> None:
    speaker = Speaker(VoiceConfig(engine="none"))
    monkeypatch.setattr("blackvoice.voices.installed", lambda name: True)
    monkeypatch.setattr(
        "blackvoice.voices.voice_path", lambda name: __import__("pathlib").Path(f"/voices/{name}.onnx")
    )
    assert speaker._piper_voice_for() == f"/voices/{speaker.cfg.piper_voice_en}.onnx"
    speaker.shutdown()


def test_piper_voice_for_prefers_an_explicit_model_path() -> None:
    speaker = Speaker(VoiceConfig(engine="none", piper_model="/custom/voice.onnx"))
    assert speaker._piper_voice_for() == "/custom/voice.onnx"
    speaker.shutdown()


# --------------------------------------------------------------------------- #
# _speak_espeak: always the configured English voice id
# --------------------------------------------------------------------------- #
def test_speak_espeak_uses_the_english_voice(monkeypatch) -> None:
    speaker = Speaker(VoiceConfig(engine="none"))
    calls = []
    monkeypatch.setattr(speaker, "_run_proc", lambda argv, stdin_text=None: calls.append(argv))
    monkeypatch.setattr(tts_mod, "_which", lambda name: "/usr/bin/espeak-ng" if "espeak" in name else None)

    speaker._speak_espeak("hello")

    assert calls
    argv = calls[0]
    assert argv[argv.index("-v") + 1] == speaker.cfg.voice_en
    speaker.shutdown()
