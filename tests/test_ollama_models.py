"""The curated lightweight-model list, and talking to a running Ollama."""

from __future__ import annotations

import json

import pytest

from blackvoice import ollama_models as om


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #
def test_the_recommended_model_is_in_the_list() -> None:
    assert om.RECOMMENDED in om.LIGHTWEIGHT_MODELS


def test_every_entry_is_actually_small() -> None:
    """The whole point of the list: nothing on it should surprise anyone."""
    for name, model in om.LIGHTWEIGHT_MODELS.items():
        assert model.name == name
        assert 0 < model.download_gb <= 3
        assert 0 < model.ram_gb <= 6


def test_describe_a_known_model() -> None:
    line = om.describe("qwen2.5:1.5b")
    assert "1.5B" in line
    assert "GB download" in line
    assert "GB RAM" in line


def test_describe_an_unknown_model_says_so() -> None:
    assert "not in the curated list" in om.describe("llama3:70b")


# --------------------------------------------------------------------------- #
# reachability and listing
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload=None, status: int = 200):
        self._payload = payload or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_is_reachable_true_on_a_200(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.get", lambda url, timeout: _FakeResponse()
    )
    assert om.is_reachable("http://localhost:11434") is True


def test_is_reachable_false_when_nothing_answers(monkeypatch) -> None:
    def _raise(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr("requests.get", _raise)
    assert om.is_reachable("http://localhost:11434") is False


def test_pulled_models_parses_the_tags_response(monkeypatch) -> None:
    payload = {"models": [{"name": "qwen2.5:1.5b"}, {"name": "llama3.2:1b"}]}
    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse(payload))
    assert om.pulled_models("http://localhost:11434") == ["qwen2.5:1.5b", "llama3.2:1b"]


def test_pulled_models_is_none_when_unreachable(monkeypatch) -> None:
    def _raise(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr("requests.get", _raise)
    assert om.pulled_models("http://localhost:11434") is None


def test_not_running_message_distinguishes_missing_from_stopped(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: None)
    assert "not installed" in om.not_running_message()

    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/ollama")
    assert "not running" in om.not_running_message()


# --------------------------------------------------------------------------- #
# pulling
# --------------------------------------------------------------------------- #
class _FakeStreamingResponse:
    """Stands in for requests.post(..., stream=True)."""

    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _jsonl(*objs) -> list:
    return [json.dumps(o).encode("utf-8") for o in objs]


def test_pull_reports_progress_and_returns(monkeypatch) -> None:
    lines = _jsonl(
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 50, "total": 100},
        {"status": "downloading", "completed": 100, "total": 100},
        {"status": "success"},
    )
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamingResponse(lines)
    )

    seen = []
    om.pull("qwen2.5:1.5b", "http://localhost:11434", on_progress=lambda *a: seen.append(a))

    assert seen[-1] == ("success", 0, 0)
    assert ("downloading", 100, 100) in seen


def test_pull_raises_on_an_error_line(monkeypatch) -> None:
    lines = _jsonl({"status": "pulling manifest"}, {"error": "model not found"})
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamingResponse(lines)
    )

    with pytest.raises(om.OllamaError, match="model not found"):
        om.pull("nonsense:latest", "http://localhost:11434")


def test_pull_when_the_server_is_not_running(monkeypatch) -> None:
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("requests.post", _raise)
    monkeypatch.setattr(om.shutil, "which", lambda name: None)

    with pytest.raises(om.OllamaError, match="not installed"):
        om.pull("qwen2.5:1.5b", "http://localhost:11434")


def test_pull_rejects_an_empty_name() -> None:
    with pytest.raises(om.OllamaError, match="no model name"):
        om.pull("  ", "http://localhost:11434")


def test_pull_skips_blank_and_unparseable_lines(monkeypatch) -> None:
    lines = [b"", b"not json", *_jsonl({"status": "success"})]
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: _FakeStreamingResponse(lines)
    )
    # Must not raise despite the junk lines.
    om.pull("qwen2.5:1.5b", "http://localhost:11434")
