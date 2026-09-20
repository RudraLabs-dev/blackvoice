"""Engine wiring: confirmations, event publishing, sleep."""

from __future__ import annotations

import pytest

from blackvoice.app import Engine, PendingConfirmation, State
from blackvoice.config import Config
from blackvoice.core.bus import EventBus, Topic
from blackvoice.nlu.intents import Intent
from blackvoice.skills.base import Reply, Skill, SkillContext


class Confirming(Skill):
    """A skill that always asks before doing anything."""

    name = "demo"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.ran = False

    def handle(self, intent: Intent) -> Reply:
        def _commit() -> Reply:
            self.ran = True
            return Reply("Done it.")

        return Reply("Are you sure?", confirm="Do the thing?", on_confirm=_commit)


@pytest.fixture
def engine(monkeypatch) -> Engine:
    # Do not create directories in the developer's home while testing.
    monkeypatch.setattr("blackvoice.app.ensure_dirs", lambda: None)
    eng = Engine(Config())
    yield eng
    eng.stop()


_ANSWERS = {"yes", "yeah", "no", "stop"}


def _arm(engine: Engine) -> Confirming:
    """Register the demo skill and send "do the thing" to it.

    Every other non-answer command routes to control.help instead - a real,
    already-registered command that never asks for confirmation - so a test
    can send an unrelated intervening command without it re-arming a fresh
    confirmation of its own and masking whether the *original* one survived.
    """
    skill = Confirming(SkillContext(config=engine.config, bus=engine.bus))
    engine.skills.register(skill)

    def _route(text: str) -> Intent:
        if text.lower() in _ANSWERS:
            return _control_intent(text)
        if text == "do the thing":
            return Intent("demo", "demo", "do", text=text)
        return Intent("help", "control", "help", text=text)

    engine.router.route = _route
    return skill


def _control_intent(text: str) -> Intent:
    action = {
        "yes": "affirm", "yeah": "affirm",
        "no": "deny",
        "stop": "cancel",
    }[text.lower()]
    return Intent(action, "control", action, text=text)


def test_confirmation_runs_on_yes(engine: Engine) -> None:
    skill = _arm(engine)

    first = engine.process("do the thing")
    assert first.confirm
    assert not skill.ran

    second = engine.process("yes")
    assert skill.ran
    assert second.speech == "Done it."


def test_confirmation_is_dropped_on_no(engine: Engine) -> None:
    skill = _arm(engine)

    engine.process("do the thing")
    reply = engine.process("no")

    assert not skill.ran
    assert reply.speech == "Cancelled."


def test_any_word_the_router_calls_affirm_confirms(engine: Engine) -> None:
    """Engine only checks intent.action == "affirm" - it does not hardcode
    which word the router used to get there.
    """
    skill = _arm(engine)
    engine.process("do the thing")
    engine.process("yeah")
    assert skill.ran


def test_confirmation_expires(engine: Engine, monkeypatch) -> None:
    skill = _arm(engine)
    engine.process("do the thing")

    # Pretend the prompt has been sitting there for a minute.
    monkeypatch.setattr(PendingConfirmation, "expired", property(lambda self: True))

    engine.process("yes")
    assert not skill.ran, "an expired confirmation must not fire"


def test_a_second_command_cancels_the_pending_confirmation(engine: Engine) -> None:
    skill = _arm(engine)
    engine.process("do the thing")

    # Anything that is not yes/no is treated as a fresh command.
    engine.process("do something else")
    assert not skill.ran

    # And the question is gone for good, not just skipped once: a "yes"
    # said afterwards, for whatever unrelated reason, must not reach back
    # and run a request the user has moved on from. Before this was fixed,
    # a pending confirmation stayed live for PendingConfirmation.TTL
    # seconds regardless of what was said in between - a stray "yes" to a
    # colleague, on a call, or agreeing with something on screen would have
    # silently run it, with nothing at that moment to connect the two.
    engine.process("yes")
    assert not skill.ran, "a confirmation must not survive an intervening command"


def test_reply_is_published(engine: Engine) -> None:
    received = []
    engine.bus.subscribe(Topic.REPLY, lambda e: received.append(e.payload))

    engine._deliver(Reply("hello", display="HELLO"))

    assert received and received[0]["speech"] == "hello"
    assert received[0]["display"] == "HELLO"


def test_sleep_changes_state(engine: Engine) -> None:
    engine.process("go to sleep")
    assert engine.state == State.ASLEEP
    engine.wake_up()
    assert engine.state == State.IDLE


# ----------------------------------------------------------------- event bus
def test_bus_publish_and_unsubscribe() -> None:
    bus = EventBus()
    seen = []
    unsubscribe = bus.subscribe("topic", lambda e: seen.append(e.get("n")))

    bus.publish("topic", n=1)
    unsubscribe()
    bus.publish("topic", n=2)

    assert seen == [1]


def test_bus_survives_a_broken_subscriber() -> None:
    bus = EventBus()
    seen = []
    bus.subscribe("topic", lambda e: (_ for _ in ()).throw(RuntimeError("bad")))
    bus.subscribe("topic", lambda e: seen.append(e.get("n")))

    bus.publish("topic", n=7)

    assert seen == [7], "one bad subscriber must not stop the others"


# --------------------------------------------------------------------------- #
# wake-word model: always the English Vosk model
# --------------------------------------------------------------------------- #
def _engine_with(monkeypatch, **overrides) -> Engine:
    monkeypatch.setattr("blackvoice.app.ensure_dirs", lambda: None)
    config = Config()
    for key, value in overrides.items():
        section, name = key.split(".", 1)
        setattr(getattr(config, section), name, value)
    eng = Engine(config)
    return eng


def test_wake_word_uses_the_english_model(monkeypatch) -> None:
    eng = _engine_with(monkeypatch)
    try:
        assert eng.wake.model_path == eng.config.model_path()
    finally:
        eng.stop()
