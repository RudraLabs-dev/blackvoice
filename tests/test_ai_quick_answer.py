"""AISkill.quick_answer: a single stateless question another skill can ask,
for something like "how would I install this" - not part of the spoken
conversation, and never raising even when the backend fails.
"""

from __future__ import annotations

import pytest

from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.skills.ai import AISkill
from blackvoice.skills.base import SkillContext


def _skill(provider: str = "ollama") -> AISkill:
    config = Config()
    config.ai.provider = provider
    ctx = SkillContext(config=config, bus=EventBus())
    return AISkill(ctx)


class _FakeJSONResponse:
    def __init__(self, payload: dict, status_ok: bool = True) -> None:
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("bad status")

    def json(self) -> dict:
        return self._payload


# --------------------------------------------------------------------------- #
# no backend configured - nothing to ask
# --------------------------------------------------------------------------- #
def test_quick_answer_is_none_when_ai_is_switched_off() -> None:
    skill = _skill(provider="none")
    assert skill.quick_answer("code", "system prompt") is None


# --------------------------------------------------------------------------- #
# ollama
# --------------------------------------------------------------------------- #
def test_quick_answer_ollama_returns_the_reply_content(monkeypatch) -> None:
    skill = _skill(provider="ollama")
    calls = []

    def _post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _FakeJSONResponse({"message": {"content": "sudo apt install code"}})

    monkeypatch.setattr("requests.post", _post)

    answer = skill.quick_answer("visual studio code", "install-command system prompt")

    assert answer == "sudo apt install code"
    # Not streamed - a single request/response, unlike the conversational path.
    (url, payload, _timeout) = calls[0]
    assert url.endswith("/api/chat")
    assert payload["stream"] is False
    assert payload["messages"] == [
        {"role": "system", "content": "install-command system prompt"},
        {"role": "user", "content": "visual studio code"},
    ]


def test_quick_answer_ollama_empty_content_is_none(monkeypatch) -> None:
    skill = _skill(provider="ollama")
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeJSONResponse({"message": {"content": "  "}})
    )
    assert skill.quick_answer("thing", "sys") is None


def test_quick_answer_never_raises_when_the_backend_fails(monkeypatch) -> None:
    skill = _skill(provider="ollama")

    def _raise(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("requests.post", _raise)
    assert skill.quick_answer("thing", "sys") is None


def test_quick_answer_never_raises_on_a_bad_http_status(monkeypatch) -> None:
    skill = _skill(provider="ollama")
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: _FakeJSONResponse({}, status_ok=False),
    )
    assert skill.quick_answer("thing", "sys") is None


def test_quick_answer_does_not_touch_conversation_history(monkeypatch) -> None:
    skill = _skill(provider="ollama")
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: _FakeJSONResponse({"message": {"content": "an answer"}}),
    )
    skill._history.append({"role": "user", "content": "earlier question"})
    before = list(skill._history)

    skill.quick_answer("thing", "sys")

    assert list(skill._history) == before, "a quick_answer must not read or write history"


# --------------------------------------------------------------------------- #
# anthropic / openai - routing only, matching how test_ai_streaming.py already
# tests _ask_anthropic: monkeypatched directly rather than mocking the SDK.
# --------------------------------------------------------------------------- #
def test_quick_answer_routes_to_anthropic(monkeypatch) -> None:
    skill = _skill(provider="anthropic")
    monkeypatch.setattr(skill, "_quick_anthropic", lambda prompt, system: "an anthropic answer")
    assert skill.quick_answer("thing", "sys") == "an anthropic answer"


def test_quick_answer_routes_to_openai(monkeypatch) -> None:
    skill = _skill(provider="openai")
    monkeypatch.setattr(
        skill, "_quick_openai", lambda prompt, system, timeout: "an openai answer"
    )
    assert skill.quick_answer("thing", "sys") == "an openai answer"


def test_quick_answer_openai_without_a_key_is_none(monkeypatch) -> None:
    skill = _skill(provider="openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill.ai.api_key = ""
    assert skill.quick_answer("thing", "sys") is None


def test_quick_answer_anthropic_refusal_is_none() -> None:
    skill = _skill(provider="anthropic")

    class _FakeResponse:
        stop_reason = "refusal"
        content = []

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeClient:
        messages = _FakeMessages()

    skill._client = _FakeClient()

    assert skill.quick_answer("thing", "sys") is None
