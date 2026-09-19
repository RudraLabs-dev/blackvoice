"""AISkill's streaming path: speak the first sentence immediately, keep
talking in the background instead of making the user wait for the whole
Ollama reply to finish generating.
"""

from __future__ import annotations

import json
from typing import List

import pytest

from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.nlu.intents import Intent
from blackvoice.skills.ai import AISkill
from blackvoice.skills.base import SkillContext


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ndjson_lines(pieces: List[str]) -> List[bytes]:
    """The NDJSON body a real /api/chat stream sends, one token-ish piece at
    a time, ending the way Ollama's own final line does.
    """
    lines = [
        json.dumps({"message": {"content": piece}, "done": False}).encode()
        for piece in pieces
    ]
    lines.append(json.dumps({"done": True}).encode())
    return lines


class _FakeStreamResponse:
    def __init__(self, lines: List[bytes]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _SyncThread:
    """Runs the target immediately on .start(), so a test does not have to
    poll or sleep to wait for the background streaming thread.
    """

    def __init__(self, target=None, args=(), name=None, daemon=None) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


@pytest.fixture
def sync_thread(monkeypatch) -> None:
    monkeypatch.setattr("blackvoice.skills.ai.threading.Thread", _SyncThread)


@pytest.fixture
def skill():
    said: List[str] = []
    config = Config()
    config.ai.provider = "ollama"
    ctx = SkillContext(config=config, bus=EventBus(), say=lambda t: said.append(t))
    ai_skill = AISkill(ctx)
    ai_skill.said = said  # type: ignore[attr-defined]
    return ai_skill


def _ask(skill, question: str = "where is the eiffel tower") -> "Reply":
    return skill.handle(Intent("ask", "ai", "ask", {"question": question}))


# --------------------------------------------------------------------------- #
# the punctuated, multi-sentence case - the main win
# --------------------------------------------------------------------------- #
def test_handle_returns_only_the_first_sentence(monkeypatch, skill, sync_thread) -> None:
    pieces = ["It", " is", " Paris.", " France", " is", " in", " Europe."]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines(pieces))
    )

    reply = _ask(skill)

    assert reply.speech == "It is Paris."
    assert reply.ok


def test_the_rest_is_spoken_in_the_background_in_order(monkeypatch, skill, sync_thread) -> None:
    pieces = ["It", " is", " Paris.", " France", " is", " in", " Europe.", " Anything else?"]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines(pieces))
    )

    _ask(skill)

    assert skill.said == ["France is in Europe.", "Anything else?"]


def test_the_full_reply_lands_in_history(monkeypatch, skill, sync_thread) -> None:
    pieces = ["It", " is", " Paris.", " France", " is", " in", " Europe."]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines(pieces))
    )

    _ask(skill, "where is the eiffel tower")

    assert list(skill._history) == [
        {"role": "user", "content": "where is the eiffel tower"},
        {"role": "assistant", "content": "It is Paris. France is in Europe."},
    ]


# --------------------------------------------------------------------------- #
# graceful degradation: no confirmed sentence boundary ever arrives
# --------------------------------------------------------------------------- #
def test_an_unpunctuated_reply_is_spoken_as_one_chunk(monkeypatch, skill, sync_thread) -> None:
    """A short reply with no sentence-final punctuation degrades to exactly
    today's non-streaming behaviour rather than never being spoken.
    """
    pieces = ["how", " are", " you"]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines(pieces))
    )

    reply = _ask(skill, "how are you")

    assert reply.speech == "how are you"
    assert skill.said == []
    assert list(skill._history)[-1] == {"role": "assistant", "content": "how are you"}


def test_a_short_single_sentence_reply_needs_no_background_thread(monkeypatch, skill, sync_thread) -> None:
    pieces = ["Yes."]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines(pieces))
    )

    reply = _ask(skill)

    assert reply.speech == "Yes."
    assert skill.said == []


def test_an_empty_stream_is_an_error(monkeypatch, skill, sync_thread) -> None:
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeStreamResponse(_ndjson_lines([])))

    reply = _ask(skill)

    assert not reply.ok


# --------------------------------------------------------------------------- #
# cancellation: a newer question must not be talked over by a stale stream
# --------------------------------------------------------------------------- #
def test_a_stale_generation_stops_saying_more_mid_stream(skill) -> None:
    skill._generation = 5

    def _stream():
        yield "second sentence just arrived. "
        skill._generation = 6  # a newer question started between these chunks
        yield "third sentence. "

    skill._speak_the_rest(
        stream=_stream(),
        buffer="",
        full_text="First sentence. ",
        rest=["First sentence."],
        question="q",
        generation=5,
    )

    assert skill.said == ["First sentence.", "second sentence just arrived."]
    assert list(skill._history) == []  # never remembered - the generation went stale


def test_a_generation_already_stale_before_starting_says_nothing(skill) -> None:
    skill._generation = 2

    skill._speak_the_rest(
        stream=iter([]),
        buffer="",
        full_text="First sentence.",
        rest=["First sentence.", "Second sentence."],
        question="q",
        generation=1,
    )

    assert skill.said == []


# --------------------------------------------------------------------------- #
# a non-ollama provider is unaffected - still a single blocking answer
# --------------------------------------------------------------------------- #
def test_anthropic_still_answers_synchronously(monkeypatch) -> None:
    config = Config()
    config.ai.provider = "anthropic"
    said: List[str] = []
    ctx = SkillContext(config=config, bus=EventBus(), say=lambda t: said.append(t))
    skill = AISkill(ctx)
    monkeypatch.setattr(skill, "_ask_anthropic", lambda question: "A single reply.")

    reply = _ask(skill)

    assert reply.speech == "A single reply."
    assert said == []
