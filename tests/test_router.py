"""Intent routing, in both languages."""

from __future__ import annotations

import pytest

from blackvoice.nlu.intents import normalise
from blackvoice.nlu.router import Router


@pytest.fixture(scope="module")
def router() -> Router:
    return Router()


ENGLISH = [
    ("open firefox", "system", "open_app", {"target": "firefox"}),
    ("close chrome", "system", "close_app", {"target": "chrome"}),
    ("volume up", "system", "volume_up", {}),
    ("set volume to 40", "system", "volume_set", {"value": "40"}),
    ("mute", "system", "volume_mute", {}),
    ("brightness down", "system", "brightness_down", {}),
    ("take a screenshot", "system", "screenshot", {}),
    ("lock the screen", "system", "lock", {}),
    ("shut down", "system", "shutdown", {}),
    ("turn off the wifi", "system", "wifi", {"state": "off"}),
    ("battery status", "system", "battery", {}),
    ("open downloads", "files", "open_folder", {"target": "downloads"}),
    ("find file report.pdf", "files", "find", {"query": "report.pdf"}),
    ("disk space", "files", "disk_space", {}),
    ("run command df -h", "terminal", "run", {"command": "df -h"}),
    ("what time is it", "utils", "time", {}),
    ("what is the date", "utils", "date", {}),
    ("set timer for 5 minutes", "utils", "timer", {"amount": "5", "unit": "minute"}),
    ("take a note buy milk", "utils", "note_add", {"text": "buy milk"}),
    ("read my notes", "utils", "note_read", {}),
    ("search for python decorators", "utils", "search", {"query": "python decorators"}),
    ("next song", "utils", "media", {"key": "next"}),
    ("calculate 12 * 8", "utils", "calculate", {"expression": "12 * 8"}),
    ("stop", "control", "cancel", {}),
    ("help", "control", "help", {}),
]

HINGLISH = [
    ("firefox kholo", "system", "open_app", {"target": "firefox"}),
    ("chrome band karo", "system", "close_app", {"target": "chrome"}),
    ("awaaz badhao", "system", "volume_up", {}),
    ("screenshot lo", "system", "screenshot", {}),
    ("computer band karo", "system", "shutdown", {}),
    ("battery kitni hai", "system", "battery", {}),
    ("downloads kholo", "files", "open_folder", {"target": "downloads"}),
    ("terminal me ls chalao", "terminal", "run", {"command": "ls"}),
    ("kitne baje hain", "utils", "time", {}),
    ("aaj ki date", "utils", "date", {}),
    ("10 minute ka timer", "utils", "timer", {"amount": "10", "unit": "minute"}),
    ("mausam", "utils", "weather", {}),
    ("haan", "control", "affirm", {}),
    ("nahi", "control", "deny", {}),
    ("so jao", "control", "sleep", {}),
]


@pytest.mark.parametrize("text,skill,action,slots", ENGLISH + HINGLISH)
def test_routes(router: Router, text: str, skill: str, action: str, slots: dict) -> None:
    intent = router.route(text)
    assert intent.skill == skill, f"{text!r} went to {intent.skill}.{intent.action}"
    assert intent.action == action
    for key, value in slots.items():
        assert intent.slots.get(key) == value


@pytest.mark.parametrize(
    "text",
    [
        "why is the sky blue",
        "who is the prime minister of india",
        "explain quantum tunnelling",
        "tell me a joke",
    ],
)
def test_questions_go_to_the_ai(router: Router, text: str) -> None:
    intent = router.route(text)
    assert intent.skill == "ai"
    assert intent.slots["question"] == text


def test_specific_rules_beat_the_generic_open_rule(router: Router) -> None:
    """'open downloads' is a folder, not an application called 'downloads'."""
    assert router.route("open downloads").skill == "files"
    assert router.route("run command ls").skill == "terminal"


def test_calculate_needs_an_operator(router: Router) -> None:
    """Without an operator it is a question, not arithmetic."""
    assert router.route("what is 42").skill == "ai"
    assert router.route("calculate 6 * 7").skill == "utils"


def test_empty_input(router: Router) -> None:
    assert router.route("").action == "noop"
    assert router.route("   ").action == "noop"


def test_normalise_strips_punctuation() -> None:
    assert normalise("Open  Firefox, please!") == "open firefox please"
    assert normalise("What's the time?") == "what's the time"


# --------------------------------------------------------------------------- #
# Devanagari
# --------------------------------------------------------------------------- #
# These were unreachable until normalise() stopped deleting combining marks:
# every Devanagari vowel sign is Unicode category Mn, which \w does not match,
# so "\u0916\u094b\u0932\u094b" arrived at the rules as two bare consonants and nothing matched.
# The whisper.cpp backend emits Devanagari for Hindi speech, so this path is
# now the normal one rather than a corner.
@pytest.mark.parametrize(
    "text, expected",
    [
        ("\u092b\u093e\u092f\u0930\u092b\u0949\u0915\u094d\u0938 \u0916\u094b\u0932\u094b", "open_app"),
        ("\u0938\u094d\u0915\u094d\u0930\u0940\u0928\u0936\u0949\u091f \u0932\u094b", "screenshot"),
        ("\u0935\u093e\u0908\u092b\u093e\u0908 \u092c\u0902\u0926 \u0915\u0930\u094b", "wifi_toggle"),
        ("\u0938\u094b \u091c\u093e\u0913", "sleep"),
    ],
)
def test_devanagari_commands_route(text: str, expected: str) -> None:
    assert Router().route(text).name == expected


def test_normalise_keeps_devanagari_intact() -> None:
    for word in ("\u0916\u094b\u0932\u094b", "\u0906\u0935\u093e\u091c\u093c", "\u0938\u094d\u0915\u094d\u0930\u0940\u0928\u0936\u0949\u091f", "\u092c\u0902\u0926"):
        assert normalise(word) == word
