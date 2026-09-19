"""AISkill's tool calling: a free-form question the router could not classify
can run a guarded shell command instead of only being talked about.

Every command still goes through the same ShellGuard TerminalSkill uses, so
these tests are mostly about proving that guard is never bypassed: a blocked
pattern must never reach subprocess, a command needing confirmation must wait
for it, and only an explicitly allowed command runs immediately.
"""

from __future__ import annotations

import json
import subprocess as subprocess_mod
from typing import List

import pytest

from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.nlu.intents import Intent
from blackvoice.skills.ai import AISkill, _TOOLS
from blackvoice.skills.base import SkillContext


# --------------------------------------------------------------------------- #
# helpers - the same shapes tests/test_ai_streaming.py uses, kept local so
# this file has no cross-module test dependency.
# --------------------------------------------------------------------------- #
def _text_lines(pieces: List[str]) -> List[bytes]:
    lines = [
        json.dumps({"message": {"content": piece}, "done": False}).encode()
        for piece in pieces
    ]
    lines.append(json.dumps({"done": True}).encode())
    return lines


def _tool_call_lines(name: str, arguments: dict) -> List[bytes]:
    """What Ollama sends when the model calls a tool instead of answering:
    one message carrying the call, arriving whole rather than incrementally.
    """
    return [
        json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
                },
                "done": True,
            }
        ).encode()
    ]


class _FakeStreamResponse:
    def __init__(self, lines: List[bytes]) -> None:
        self._lines = lines
        self.payload = None  # set by _FakeSession before returning this

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _ScriptedPost:
    """Stands in for requests.post: returns one scripted response per call,
    in order, and records the JSON payload each call was made with.
    """

    def __init__(self, *responses: List[bytes]) -> None:
        self._responses = [_FakeStreamResponse(lines) for lines in responses]
        self.calls: List[dict] = []

    def __call__(self, url, json=None, timeout=None, stream=None):
        response = self._responses[len(self.calls)]
        response.payload = json
        self.calls.append(json)
        return response


class _SyncThread:
    """Runs the target immediately on .start() - no polling for the
    background streaming thread to finish.
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


def _ask(skill, question: str = "how much disk space is free") -> "Reply":
    return skill.handle(Intent("ask", "ai", "ask", {"question": question}))


class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# --------------------------------------------------------------------------- #
# the tool is offered only when configured to be, only to ollama
# --------------------------------------------------------------------------- #
def test_the_tool_is_offered_when_enabled(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(_text_lines(["Sure."]))
    monkeypatch.setattr("requests.post", post)

    _ask(skill)

    assert post.calls[0]["tools"] == _TOOLS


def test_the_tool_is_withheld_when_disabled(monkeypatch, skill, sync_thread) -> None:
    skill.ai.tools_enabled = False
    post = _ScriptedPost(_text_lines(["Sure."]))
    monkeypatch.setattr("requests.post", post)

    _ask(skill)

    assert "tools" not in post.calls[0]


# --------------------------------------------------------------------------- #
# an allowed (read-only) command runs immediately and is narrated
# --------------------------------------------------------------------------- #
def test_an_allowed_command_runs_and_is_narrated(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(
        _tool_call_lines("run_command", {"command": "df -h"}),
        _text_lines(["You have", " forty gigabytes free."]),
    )
    monkeypatch.setattr("requests.post", post)

    ran = {}

    def _fake_run(argv, **kwargs):
        ran["argv"] = argv
        return _FakeCompleted(stdout="Filesystem  Size  Used Avail\n/dev/sda1  100G  60G  40G\n")

    monkeypatch.setattr("blackvoice.skills.ai.subprocess.run", _fake_run)

    reply = _ask(skill, "how much disk space is free")

    assert ran["argv"] == ["df", "-h"]
    assert reply.ok
    assert reply.speech == "You have forty gigabytes free."
    # The command's raw output never reached the user directly - only the
    # model's narration of it, fed back as a second request.
    assert len(post.calls) == 2
    followup = post.calls[1]["messages"]
    assert any("df -h" in m.get("content", "") for m in followup)
    assert "tools" not in post.calls[1]


def test_the_raw_command_is_never_spoken_verbatim(monkeypatch, skill, sync_thread) -> None:
    """The narration prompt explicitly tells the model not to read the
    command or its output back - this checks the instruction is actually
    sent, not just hoped for.
    """
    post = _ScriptedPost(
        _tool_call_lines("run_command", {"command": "df -h"}),
        _text_lines(["Plenty of room."]),
    )
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: _FakeCompleted(stdout="60% used"),
    )

    _ask(skill)

    narration_request = post.calls[1]["messages"][-1]["content"]
    assert "do not show me the raw command" in narration_request.lower()


# --------------------------------------------------------------------------- #
# a command that could change the system pauses for a spoken "yes" - exactly
# TerminalSkill's own confirm flow, reused rather than duplicated
# --------------------------------------------------------------------------- #
def test_a_command_needing_confirmation_does_not_run_yet(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(_tool_call_lines("run_command", {"command": "touch newfile.txt"}))
    monkeypatch.setattr("requests.post", post)

    called = []
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: called.append(argv) or _FakeCompleted(),
    )

    reply = _ask(skill, "make a new file called newfile.txt")

    assert called == [], "must not execute before the user confirms"
    assert reply.confirm == "Run: touch newfile.txt"
    assert reply.on_confirm is not None
    assert len(post.calls) == 1, "no narration call before the command has even run"


def test_confirming_then_runs_the_command_and_narrates_it(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(
        _tool_call_lines("run_command", {"command": "touch newfile.txt"}),
        _text_lines(["Done, I made the file."]),
    )
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: _FakeCompleted(returncode=0),
    )

    reply = _ask(skill, "make a new file called newfile.txt")
    confirmed = reply.on_confirm()

    assert confirmed.ok
    assert confirmed.speech == "Done, I made the file."


# --------------------------------------------------------------------------- #
# a blocked pattern is refused outright and never reaches subprocess
# --------------------------------------------------------------------------- #
def test_a_blocked_command_is_refused_and_never_executed(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(_tool_call_lines("run_command", {"command": "rm -rf /"}))
    monkeypatch.setattr("requests.post", post)

    called = []
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: called.append(argv) or _FakeCompleted(),
    )

    reply = _ask(skill, "delete everything")

    assert not reply.ok
    assert called == []
    assert len(post.calls) == 1, "a refusal never triggers a narration round-trip"


def test_sudo_is_refused_like_every_other_entry_point(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(
        _tool_call_lines("run_command", {"command": "sudo apt install htop"})
    )
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: pytest.fail("sudo must never execute"),
    )

    reply = _ask(skill, "install htop")

    assert not reply.ok
    assert "root" in reply.speech.lower()


# --------------------------------------------------------------------------- #
# malformed or unknown tool calls fail closed
# --------------------------------------------------------------------------- #
def test_an_unknown_tool_name_is_rejected(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(_tool_call_lines("delete_everything", {}))
    monkeypatch.setattr("requests.post", post)

    reply = _ask(skill)

    assert not reply.ok


def test_a_call_with_no_command_argument_is_rejected(monkeypatch, skill, sync_thread) -> None:
    post = _ScriptedPost(_tool_call_lines("run_command", {}))
    monkeypatch.setattr("requests.post", post)

    reply = _ask(skill)

    assert not reply.ok


# --------------------------------------------------------------------------- #
# no tool-call loops: the narration turn is never offered tools again
# --------------------------------------------------------------------------- #
def test_a_second_tool_call_in_the_narration_turn_is_not_acted_on(
    monkeypatch, skill, sync_thread
) -> None:
    post = _ScriptedPost(
        _tool_call_lines("run_command", {"command": "df -h"}),
        _tool_call_lines("run_command", {"command": "rm -rf /"}),
    )
    monkeypatch.setattr("requests.post", post)

    executed = []
    monkeypatch.setattr(
        "blackvoice.skills.ai.subprocess.run",
        lambda argv, **k: executed.append(argv) or _FakeCompleted(stdout="ok"),
    )

    reply = _ask(skill)

    # The first command (df -h) ran once; the model's second attempt at a
    # tool call, mid-narration, is treated as inert (no text) rather than
    # ever being executed.
    assert executed == [["df", "-h"]]
    assert reply.ok
    assert reply.speech == "Done. Exit code 0."
