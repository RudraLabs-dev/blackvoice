"""_looks_hindi: which voice speaks a reply.

Devanagari is unambiguous. Romanised Hindi is not - "hai", "kya" and "kar"
are also just English-looking tokens with nothing in the script to tell them
apart - and AISkill's own system prompt asks it to answer "in the same
Hinglish mixture when that is how the question came," so a real reply very
often carries no Devanagari at all. Missing that sent every such reply to the
English voice, which guesses English pronunciations for Hindi words it was
never trained on - reported from a real machine as the TTS output sounding
"robotic" despite Piper - a natural-sounding neural voice - being the engine
actually speaking it.
"""

from __future__ import annotations

import pytest

from blackvoice import piper_install
from blackvoice.audio import tts as tts_mod
from blackvoice.audio.tts import Speaker, _looks_hindi, detect_engine
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

    def wait(self, timeout=None) -> int:
        return 0


@pytest.fixture
def piper_speaker(monkeypatch):
    monkeypatch.setattr(tts_mod, "_which", lambda name: "/usr/bin/aplay" if name == "aplay" else None)
    monkeypatch.setattr(piper_install, "find_binary", lambda: "/opt/piper/piper")
    monkeypatch.setattr(piper_install, "espeak_data_dir", lambda: None)

    speaker = Speaker(VoiceConfig(engine="piper"))
    monkeypatch.setattr(speaker, "_piper_voice_for", lambda hindi: "/voices/en_US.onnx")

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
# Devanagari - the unambiguous case, unaffected by any of this
# --------------------------------------------------------------------------- #
def test_devanagari_is_always_hindi() -> None:
    assert _looks_hindi("आवाज़ अच्छी नहीं है") is True
    assert _looks_hindi("खोलो") is True


def test_empty_text_is_not_hindi() -> None:
    assert _looks_hindi("") is False


# --------------------------------------------------------------------------- #
# romanised Hindi / Hinglish - the case this exists to catch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "yeh ek test hai natural voice ka",
        "aap kaise hain",
        "main theek hoon",
        "volume 40 kar do",
        "band karo",
        "haan bilkul",
        "nahi, aisa nahi hai",
        "aapka din kaisa raha",
        "firefox kholo",
        "kya haal hai",
        "wifi band karo",
        "screenshot le lo",
    ],
)
def test_romanised_hindi_is_recognised(text: str) -> None:
    assert _looks_hindi(text) is True


# --------------------------------------------------------------------------- #
# ordinary English - real reply strings actually spoken by this codebase,
# pulled from the skills modules, so this is not a hypothetical battery
# --------------------------------------------------------------------------- #
_REAL_ENGLISH_REPLIES = [
    "I am not able to answer that one.",
    "I can control apps, volume, files, the terminal and answer questions.",
    "I can only set reminders up to twenty four hours ahead.",
    "I cannot divide by zero.",
    "I could not change the Bluetooth state.",
    "I could not change the Wi-Fi state. Is NetworkManager installed?",
    "I could not fetch the weather right now.",
    "I could not find a browser to open.",
    "I could not find a file manager to open that with.",
    "I could not find a screen locker.",
    "I could not open the browser.",
    "I could not parse that command safely.",
    "I could not reach the AI backend.",
    "I could not work that out.",
    "I did not catch the brightness level.",
    "I did not catch the question.",
    "I did not understand that, and the AI backend is switched off.",
    "I do not know how to do that yet.",
    "I do not know that command.",
    "Install playerctl so I can control media playback.",
    "Locking the screen.",
    "No media player is running.",
    "No screenshot tool found. Install gnome-screenshot, grim or scrot.",
    "No volume control tool found.",
    "Power actions always ask first - a misheard word here is expensive.",
    "Screenshot saved to your Pictures folder.",
    "Something went wrong while doing that.",
    "That command failed.",
    "That feature is not available right now.",
    "That name has no characters I can use for a folder.",
    "The AI backend is rate limiting me. Try again shortly.",
    "The AI backend returned an empty answer.",
    "The AI backend took too long to answer.",
    "The full list is on screen.",
    "The requests library is not installed.",
    "The weather service did not respond in time.",
    "The weather service sent something I could not read.",
    "There is nothing waiting for a yes.",
    "This machine has no battery - it looks like a desktop.",
    "Toggled playback.",
    "Going to sleep. Say Black to wake me.",
    "Volume set to 40 percent.",
    "Opening firefox.",
    "What is the weather like today?",
    "Set a timer for five minutes.",
]


@pytest.mark.parametrize("text", _REAL_ENGLISH_REPLIES)
def test_real_english_replies_are_not_flagged_as_hindi(text: str) -> None:
    assert _looks_hindi(text) is False


# --------------------------------------------------------------------------- #
# the specific collision that caught the fix in review: "the" is a real
# romanised Hindi word (the plural past-tense "the", as in "ve gaye the") and
# also the single most common word in English - keeping it in the list broke
# ordinary English on contact.
# --------------------------------------------------------------------------- #
def test_the_is_not_treated_as_a_hindi_word() -> None:
    assert _looks_hindi("he said the cat sat on the mat") is False


def test_main_alone_does_not_force_the_hindi_voice_in_an_english_sentence() -> None:
    """"main" is genuinely Hindi for "I" - and also an ordinary English word.
    Short commands lean Hindi on a single hit, so this only holds for a
    sentence long enough to dilute one incidental match.
    """
    assert _looks_hindi("I could not open the main window right now") is False


def test_a_single_common_hindi_word_is_enough_for_a_short_command() -> None:
    """The flip side of the rule above: a short phrase does not get the
    luxury of dilution, so one clearly-Hindi word must be enough on its own.
    """
    assert _looks_hindi("theek hai") is True
