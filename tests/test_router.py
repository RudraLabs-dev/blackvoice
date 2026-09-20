"""Intent routing."""

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
    # A polite opener must not push the command to the AI skill instead of
    # running it - reported live: "can you open firefox" got only talk back
    # ("Sure, I can help you with that... Do you want me to do that?"),
    # because every rule here is written to match "open firefox", not a
    # sentence that happens to contain it.
    ("can you open firefox", "system", "open_app", {"target": "firefox"}),
    ("could you please open firefox", "system", "open_app", {"target": "firefox"}),
    ("please open firefox", "system", "open_app", {"target": "firefox"}),
    ("would you close chrome", "system", "close_app", {"target": "chrome"}),
    ("i want to open firefox", "system", "open_app", {"target": "firefox"}),
    ("i'd like to open firefox", "system", "open_app", {"target": "firefox"}),
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
    # No name given - previously matched nothing here at all (the pattern
    # above requires text after "file") and fell straight through to the AI
    # skill instead of ever reaching FilesSkill._do_find's own "what should
    # I look for?" - see test_find_with_no_name_asks_for_one below for the
    # query slot itself, which stays absent rather than empty-string.
    ("find a file", "files", "find", {}),
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

@pytest.mark.parametrize("text,skill,action,slots", ENGLISH)
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


@pytest.mark.parametrize(
    "text", ["find a file", "search for a file", "locate a document", "look for the folder"]
)
def test_find_with_no_name_still_reaches_the_files_skill(router: Router, text: str) -> None:
    """Every phrasing of "find/search for/locate a file" with no name at
    all must still reach files.find with an empty query, not fall through
    every rule to the AI skill the way it used to - that is what lets
    FilesSkill._do_find's own "what should I look for?" ever be reached.
    """
    intent = router.route(text)
    assert intent.skill == "files"
    assert intent.action == "find"
    assert not intent.slots.get("query")


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
