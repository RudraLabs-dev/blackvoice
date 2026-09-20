"""Skill behaviour that can be exercised without a microphone or a desktop."""

from __future__ import annotations

import pytest

from blackvoice.audio.wake import strip_wake_word
from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.nlu.intents import Intent
from blackvoice.skills.base import Reply, SkillContext, SkillRegistry, Skill
from blackvoice.skills.control import ControlSkill
from blackvoice.skills.utils import UtilsSkill


@pytest.fixture
def ctx() -> SkillContext:
    return SkillContext(config=Config(), bus=EventBus())


# ------------------------------------------------------------------ calculator
@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 2", "4"),
        ("12 * 8", "96"),
        ("100 / 4", "25"),
        ("2 ** 10", "1024"),
        ("7 % 3", "1"),
        ("10 x 3", "30"),      # the recogniser writes "x" for "times"
        ("-5 + 8", "3"),
    ],
)
def test_calculator(ctx: SkillContext, expression: str, expected: str) -> None:
    skill = UtilsSkill(ctx)
    reply = skill.handle(Intent("calculate", "utils", "calculate", {"expression": expression}))
    assert reply.ok
    assert expected in reply.speech


def test_calculator_rejects_code(ctx: SkillContext) -> None:
    """The evaluator must never execute names or calls."""
    skill = UtilsSkill(ctx)
    for hostile in ["__import__('os').system('ls')", "open('/etc/passwd')", "x + 1"]:
        reply = skill.handle(Intent("calculate", "utils", "calculate", {"expression": hostile}))
        assert not reply.ok


def test_calculator_divide_by_zero(ctx: SkillContext) -> None:
    skill = UtilsSkill(ctx)
    reply = skill.handle(Intent("calculate", "utils", "calculate", {"expression": "1 / 0"}))
    assert not reply.ok
    assert "zero" in reply.speech.lower()


# ----------------------------------------------------------------------- notes
def test_notes_round_trip(ctx: SkillContext, tmp_path, monkeypatch) -> None:
    notes = tmp_path / "notes.md"
    monkeypatch.setattr("blackvoice.skills.utils.NOTES_FILE", notes)
    skill = UtilsSkill(ctx)

    assert skill.handle(Intent("n", "utils", "note_read", {})).speech.startswith("You have no")

    skill.handle(Intent("n", "utils", "note_add", {"text": "buy milk"}))
    skill.handle(Intent("n", "utils", "note_add", {"text": "call mom"}))

    reply = skill.handle(Intent("n", "utils", "note_read", {}))
    assert "buy milk" in reply.display
    assert "call mom" in reply.display
    assert reply.data["count"] == 2


def test_empty_note_is_rejected(ctx: SkillContext) -> None:
    skill = UtilsSkill(ctx)
    assert not skill.handle(Intent("n", "utils", "note_add", {"text": "  "})).ok


# ---------------------------------------------------------------------- timers
def test_timer_parsing(ctx: SkillContext) -> None:
    skill = UtilsSkill(ctx)
    reply = skill.handle(Intent("t", "utils", "timer", {"amount": "5", "unit": "minute"}))
    assert reply.data["seconds"] == 300
    skill.shutdown()


def test_timer_rejects_absurd_durations(ctx: SkillContext) -> None:
    skill = UtilsSkill(ctx)
    reply = skill.handle(Intent("t", "utils", "timer", {"amount": "99", "unit": "hour"}))
    assert not reply.ok
    skill.shutdown()


def test_timer_without_a_duration(ctx: SkillContext) -> None:
    skill = UtilsSkill(ctx)
    assert not skill.handle(Intent("t", "utils", "timer", {})).ok
    skill.shutdown()


# ------------------------------------------------------------------ wake word
@pytest.mark.parametrize(
    "heard,expected",
    [
        ("black open firefox", "open firefox"),
        ("Black, open firefox", "open firefox"),
        ("black", ""),
        ("open firefox", "open firefox"),
        ("blackboard is here", "board is here"),  # known trade-off, see the docstring
    ],
)
def test_strip_wake_word(heard: str, expected: str) -> None:
    assert strip_wake_word(heard, ["black", "blek"]) == expected


# ------------------------------------------------------------------- registry
def test_registry_dispatch(ctx: SkillContext) -> None:
    registry = SkillRegistry()
    registry.register(ControlSkill(ctx))
    reply = registry.dispatch(Intent("help", "control", "help"))
    assert reply.ok
    assert "Black Voice" in reply.display


def test_registry_unknown_skill(ctx: SkillContext) -> None:
    registry = SkillRegistry()
    reply = registry.dispatch(Intent("x", "nope", "nope"))
    assert not reply.ok


def test_registry_survives_a_broken_skill(ctx: SkillContext) -> None:
    class Exploding(Skill):
        name = "boom"

        def handle(self, intent: Intent) -> Reply:
            raise RuntimeError("kaboom")

    registry = SkillRegistry()
    registry.register(Exploding(ctx))
    reply = registry.dispatch(Intent("x", "boom", "anything"))
    assert not reply.ok
    assert "went wrong" in reply.speech


def test_reply_display_defaults_to_speech() -> None:
    assert Reply(speech="hello").display == "hello"
    assert Reply(speech="hi", display="HI").display == "HI"


# ------------------------------------------------------------------ AI skill
def test_ollama_missing_and_ollama_stopped_read_differently(monkeypatch) -> None:
    """Nothing listening on the port means two very different things."""
    from blackvoice.skills import ai as ai_module

    monkeypatch.setattr(ai_module.shutil, "which", lambda _name: None)
    absent = ai_module.AISkill._friendly_error("ollama", Exception("connection refused"))
    assert "not installed" in absent
    assert "ollama.com" in absent
    # Reassure the user that the rest of the assistant is fine.
    assert "without it" in absent

    monkeypatch.setattr(ai_module.shutil, "which", lambda _name: "/usr/local/bin/ollama")
    stopped = ai_module.AISkill._friendly_error("ollama", Exception("connection refused"))
    assert "not running" in stopped
    assert "ollama serve" in stopped


# --------------------------------------------------------------------------- #
# Skill.spawn: a launched app must never inherit blackvoice's own working
# directory (its own data dir, if that is where a systemd unit or a manual
# `cd` left it) - a real machine reported "open terminal" opening a new
# terminal inside ~/.local/share/blackvoice/piper/, which reads exactly like
# the assistant reporting a path, when it is really just an inherited cwd.
# --------------------------------------------------------------------------- #
def test_spawn_pins_the_working_directory_to_home(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    calls = []
    monkeypatch.setattr(
        "blackvoice.skills.base.subprocess.Popen",
        lambda argv, **kwargs: calls.append(kwargs) or object(),
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    assert Skill.spawn(["gnome-terminal"]) is True
    assert calls[0]["cwd"] == str(tmp_path)


# --------------------------------------------------------------------------- #
# Skill.spawn: a launched app must land in its own transient unit, not as a
# direct child of blackvoice's own service unit - a real machine reported
# "open firefox" saying it opened while no firefox process ever appeared.
# Three things had to be true before it actually did, each found by
# reproducing the exact failure live rather than guessing:
#
# 1. snapd refuses to run Firefox's snap under blackvoice.service's own
#    cgroup ("... is not a snap cgroup for tag snap.firefox.firefox") -
#    fixed by giving it its own unit at all.
# 2. systemd-run does not forward the caller's environment to that unit, and
#    a real launch (unlike `--version`, which needs no display) then failed
#    a second way - "Error: no DISPLAY environment variable specified" -
#    even though blackvoice.service's own process already has DISPLAY,
#    WAYLAND_DISPLAY, XAUTHORITY and DBUS_SESSION_BUS_ADDRESS, being tied to
#    graphical-session.target. `--setenv=NAME` with no value pulls that name
#    from systemd-run's own environment instead.
# 3. Even with both of those fixed, wrapping in `--scope` specifically still
#    silently failed: `--scope` execs the target in place of systemd-run
#    itself, so the new process is still a fork of blackvoice's own, and
#    blackvoice.service runs with NoNewPrivileges=true - a bit the kernel
#    makes permanent across every future exec once set. Firefox's snap needs
#    to gain capabilities via a setuid/file-capability binary to start at
#    all, which that bit blocks: "snap-confine is packaged without necessary
#    permissions ... capability cap_dac_override not found". Dropping
#    `--scope` for systemd-run's default (a transient *service*) routes the
#    fork through the --user manager instead - a process that was never
#    subject to blackvoice.service's own NoNewPrivileges - and that is what
#    finally got Firefox's full process tree (parent, content processes,
#    the lot) actually running.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_launch_wrapper_cache():
    from blackvoice.skills.base import _detached_launch_wrapper

    _detached_launch_wrapper.cache_clear()
    yield
    _detached_launch_wrapper.cache_clear()


def test_spawn_wraps_the_launch_in_its_own_unit_when_systemd_run_exists(
    monkeypatch, tmp_path
) -> None:
    from pathlib import Path

    calls = []
    monkeypatch.setattr(
        "blackvoice.skills.base.shutil.which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )
    monkeypatch.setattr(
        "blackvoice.skills.base.subprocess.Popen",
        lambda argv, **kwargs: calls.append(argv) or object(),
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    assert Skill.spawn(["firefox"]) is True
    assert calls[0] == [
        "/usr/bin/systemd-run", "--user", "--collect", "--quiet",
        "--setenv=DISPLAY", "--setenv=WAYLAND_DISPLAY",
        "--setenv=XAUTHORITY", "--setenv=DBUS_SESSION_BUS_ADDRESS",
        "--", "firefox",
    ]
    # Not --scope: see the module comment above for why that specifically
    # still failed even with a correct cgroup and a correct environment.
    assert "--scope" not in calls[0]


def test_spawn_falls_back_to_a_bare_launch_without_systemd_run(
    monkeypatch, tmp_path
) -> None:
    from pathlib import Path

    calls = []
    monkeypatch.setattr("blackvoice.skills.base.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "blackvoice.skills.base.subprocess.Popen",
        lambda argv, **kwargs: calls.append(argv) or object(),
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    assert Skill.spawn(["firefox"]) is True
    assert calls[0] == ["firefox"]


def test_commands_work_without_any_ai_backend(ctx: SkillContext) -> None:
    """Ollama is optional: only free-form questions need a backend."""
    from blackvoice.app import Engine

    ctx.config.ai.provider = "none"
    engine = Engine(ctx.config)
    try:
        assert engine.process("what time is it").ok
        assert engine.process("calculate 6 * 7").ok
        # Only the open question is refused, and it says why.
        question = engine.process("why is the sky blue")
        assert not question.ok
        assert "switched off" in question.speech
    finally:
        engine.stop()
