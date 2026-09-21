"""Skill behaviour that can be exercised without a microphone or a desktop."""

from __future__ import annotations

import json
import time

import pytest

from blackvoice.audio.wake import strip_wake_word
from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.nlu.intents import Intent
from blackvoice.skills.base import Reply, SkillContext, SkillRegistry, Skill
from blackvoice.skills.control import ControlSkill
from blackvoice.skills.files import FilesSkill
from blackvoice.skills.system import SystemSkill
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


# ------------------------------------------------ timer persistence across restarts
#
# threading.Timer lives only as long as the process does - a restart (a
# crash, an upgrade, the lock/unlock cycle found to kill blackvoice.service
# on a real machine) silently dropped every pending timer and reminder with
# nothing left to show it had ever existed. These prove the fix from both
# ends: a scheduled timer actually lands on disk, and a fresh UtilsSkill
# picks a still-pending one back up rather than only starting empty.
def _timers_file(tmp_path, monkeypatch):
    path = tmp_path / "timers.json"
    monkeypatch.setattr("blackvoice.skills.utils.TIMERS_FILE", path)
    return path


def test_scheduling_a_timer_persists_it(ctx: SkillContext, tmp_path, monkeypatch) -> None:
    path = _timers_file(tmp_path, monkeypatch)
    skill = UtilsSkill(ctx)

    skill.handle(Intent("t", "utils", "timer", {"amount": "5", "unit": "minute"}))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 1
    (entry,) = saved.values()
    assert entry["message"] == "Your timer is done."
    assert entry["fire_at"] > time.time()
    skill.shutdown()


def test_shutdown_stops_the_live_timer_but_keeps_it_on_disk(
    ctx: SkillContext, tmp_path, monkeypatch
) -> None:
    path = _timers_file(tmp_path, monkeypatch)
    skill = UtilsSkill(ctx)
    skill.handle(Intent("t", "utils", "timer", {"amount": "5", "unit": "minute"}))

    skill.shutdown()

    assert not any(t.is_alive() for t in skill._timers)
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1, (
        "a timer that has not fired yet must survive a restart, not be "
        "deleted just because the process is shutting down"
    )


def test_a_still_pending_timer_is_rearmed_on_the_next_startup(
    ctx: SkillContext, tmp_path, monkeypatch
) -> None:
    path = _timers_file(tmp_path, monkeypatch)
    path.write_text(
        json.dumps({"abc": {"fire_at": time.time() + 300, "message": "Your timer is done."}}),
        encoding="utf-8",
    )

    skill = UtilsSkill(ctx)
    try:
        assert len(skill._timers) == 1
        assert skill._timers[0].is_alive()
        # Still pending, so restoring it must not have spoken it early.
        assert json.loads(path.read_text(encoding="utf-8"))
    finally:
        skill.shutdown()


def test_an_overdue_timer_fires_once_on_the_next_startup(
    ctx: SkillContext, tmp_path, monkeypatch
) -> None:
    path = _timers_file(tmp_path, monkeypatch)
    path.write_text(
        json.dumps({"abc": {"fire_at": time.time() - 30, "message": "Reminder: call mom"}}),
        encoding="utf-8",
    )
    spoken = []
    ctx = SkillContext(config=ctx.config, bus=ctx.bus, say=spoken.append)

    skill = UtilsSkill(ctx)
    try:
        assert spoken == ["Reminder: call mom"], (
            "a reminder that was due while nothing was running must be "
            "said once on restart, not silently dropped"
        )
        assert not skill._timers, "nothing left to schedule for one already fired"
        assert json.loads(path.read_text(encoding="utf-8")) == {}
    finally:
        skill.shutdown()


# ------------------------------------------------------------------ wake word
@pytest.mark.parametrize(
    "heard,expected",
    [
        ("black open firefox", "open firefox"),
        ("Black, open firefox", "open firefox"),
        ("black", ""),
        ("open firefox", "open firefox"),
        # Word-boundary matched, not a bare prefix: "blackboard" no longer
        # loses its first syllable to a false match on "black".
        ("blackboard is here", "blackboard is here"),
        # A repeated attempt, captured as one utterance because the second
        # "Black" came before the endpointer's silence timeout - keep only
        # what was said after the last one, not the abandoned first try.
        ("open firefox black open firefox", "open firefox"),
        (
            "set volume to 40 black set a timer black set a timer black set a timer",
            "set a timer",
        ),
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


# --------------------------------------------------------------------------- #
# find with no name: asks deterministically, does not hand it to the AI skill
#
# Confirmed live that hoping a local model would ask for a name and then
# actually search does not pay off: qwen2.5:1.5b, given "find a file" and
# then a real file name the very next turn, just acknowledged the name back
# in conversation and never called a tool. Reply.needs/on_answer routes the
# next thing said straight back to FilesSkill._find_with_query instead,
# deterministically - no model in the loop for this at all.
# --------------------------------------------------------------------------- #
def test_find_with_no_name_asks_for_one_deterministically(ctx: SkillContext) -> None:
    skill = FilesSkill(ctx)
    reply = skill.handle(Intent("find_file", "files", "find", {}))

    assert reply.ok, "a clarifying question is not a failure"
    assert reply.needs == "What should I look for?"
    assert callable(reply.on_answer)


def test_answering_the_asked_name_actually_searches(ctx: SkillContext, monkeypatch) -> None:
    from pathlib import Path

    skill = FilesSkill(ctx)
    expected = Path("/home/x") / "report.pdf"
    monkeypatch.setattr(skill, "_search", lambda query: [Path("/home/x") / query])

    reply = skill.handle(Intent("find_file", "files", "find", {}))
    found = reply.on_answer("report.pdf")

    assert found.ok
    assert "report.pdf" in found.speech
    assert found.data["matches"] == [str(expected)]


def test_find_with_a_name_already_given_skips_the_question(
    ctx: SkillContext, monkeypatch
) -> None:
    from pathlib import Path

    skill = FilesSkill(ctx)
    monkeypatch.setattr(skill, "_search", lambda query: [Path(f"/home/x/{query}")])

    reply = skill.handle(Intent("find_file", "files", "find", {"query": "report.pdf"}))

    assert reply.needs is None
    assert "report.pdf" in reply.speech


def test_the_full_conversation_actually_finds_the_file(ctx: SkillContext, monkeypatch) -> None:
    """The same round trip end to end, through Engine.process - what a real
    "find a file" ... "report.pdf" exchange looks like from the outside.
    """
    from pathlib import Path

    from blackvoice.app import Engine

    ctx.config.ai.provider = "none"
    engine = Engine(ctx.config)
    files_skill = engine.skills.get("files")
    monkeypatch.setattr(files_skill, "_search", lambda query: [Path(f"/home/x/{query}")])
    try:
        first = engine.process("find a file")
        assert first.speech == "What should I look for?"

        second = engine.process("report.pdf")
        assert second.ok
        assert "report.pdf" in second.speech
    finally:
        engine.stop()


# --------------------------------------------------------------------------- #
# suggest_install: a missing app or tool gets a real install command, not
# just "not found" - via AISkill.quick_answer, never made up here, and never
# run automatically. See wiki/Security-Model: this project never escalates
# privileges on its own, so the command is only ever told to the user, the
# same stance as everything else here that needs root.
# --------------------------------------------------------------------------- #
class _FakeAI:
    """Stands in for ctx.ai (a real AISkill) in tests that only care about
    the prompt Skill.suggest_install builds and what it does with the reply.
    """

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def quick_answer(self, prompt, system, timeout=12.0):
        self.calls.append((prompt, system))
        return self.answer


def test_suggest_install_is_none_without_an_ai_backend(ctx: SkillContext) -> None:
    ctx.ai = None
    skill = SystemSkill(ctx)
    assert skill.suggest_install("code") is None


def test_suggest_install_asks_and_strips_backticks(ctx: SkillContext) -> None:
    fake_ai = _FakeAI("`sudo snap install code --classic`")
    ctx.ai = fake_ai
    skill = SystemSkill(ctx)

    result = skill.suggest_install("code")

    assert result == "sudo snap install code --classic"
    prompt, system = fake_ai.calls[0]
    assert prompt == "code"
    assert "install" in system.lower()


def test_suggest_install_filters_out_unknown(ctx: SkillContext) -> None:
    ctx.ai = _FakeAI("UNKNOWN")
    skill = SystemSkill(ctx)
    assert skill.suggest_install("some obscure thing") is None


def test_suggest_install_is_none_when_quick_answer_gives_nothing(ctx: SkillContext) -> None:
    ctx.ai = _FakeAI(None)
    skill = SystemSkill(ctx)
    assert skill.suggest_install("code") is None


def test_open_app_not_found_offers_the_install_command(ctx: SkillContext, monkeypatch) -> None:
    ctx.ai = _FakeAI("sudo snap install code --classic")
    skill = SystemSkill(ctx)
    # _resolve_app calls shutil.which directly, not Skill.which - nothing on
    # PATH either way.
    monkeypatch.setattr("blackvoice.skills.system.shutil.which", lambda *a: None)

    reply = skill.handle(Intent("open_app", "system", "open_app", {"target": "code"}))

    assert not reply.ok
    assert "not installed" in reply.speech
    assert "sudo snap install code --classic" in reply.speech


def test_open_app_not_found_without_ai_uses_the_plain_message(ctx: SkillContext) -> None:
    ctx.ai = None
    skill = SystemSkill(ctx)

    reply = skill.handle(
        Intent("open_app", "system", "open_app", {"target": "definitely-not-a-real-app"})
    )

    assert not reply.ok
    assert "I could not find" in reply.speech


def test_run_gui_wraps_the_launch_wrapper(monkeypatch) -> None:
    """The same wrapper spawn() uses - see
    test_spawn_wraps_the_launch_in_its_own_unit_when_systemd_run_exists for
    why: a direct child of blackvoice.service could not talk to a snap's
    confinement, and the same cgroup mismatch applies to anything else that
    needs the display or the compositor, not only to launching an app - a
    screenshot tool above all, confirmed live to fail silently exactly the
    same way without this.
    """
    import subprocess as subprocess_module

    monkeypatch.setattr(
        "blackvoice.skills.base.shutil.which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess_module.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("blackvoice.skills.base.subprocess.run", _run)

    result = Skill.run_gui(["gnome-screenshot", "-f", "/tmp/x.png"])

    assert result.returncode == 0
    assert calls[0] == [
        "/usr/bin/systemd-run", "--user", "--collect", "--quiet",
        "--setenv=DISPLAY", "--setenv=WAYLAND_DISPLAY",
        "--setenv=XAUTHORITY", "--setenv=DBUS_SESSION_BUS_ADDRESS",
        "--", "gnome-screenshot", "-f", "/tmp/x.png",
    ]


def test_run_gui_falls_back_to_a_bare_run_without_systemd_run(monkeypatch) -> None:
    import subprocess as subprocess_module

    monkeypatch.setattr("blackvoice.skills.base.shutil.which", lambda name: None)
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess_module.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("blackvoice.skills.base.subprocess.run", _run)

    Skill.run_gui(["gnome-screenshot", "-f", "/tmp/x.png"])

    assert calls[0] == ["gnome-screenshot", "-f", "/tmp/x.png"]
