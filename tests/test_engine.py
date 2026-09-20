"""Engine wiring: confirmations, event publishing, sleep."""

from __future__ import annotations

import pytest

from blackvoice.app import Engine, PendingConfirmation, State
from blackvoice.audio.stt import Transcript
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


# --------------------------------------------------------------------------- #
# PendingSlot: a skill needs one piece of free-text info, not a yes/no
# --------------------------------------------------------------------------- #
class Asking(Skill):
    """A skill that always asks for one piece of information before acting."""

    name = "demo2"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.received = None

    def handle(self, intent: Intent) -> Reply:
        def _answer(text: str) -> Reply:
            self.received = text
            return Reply(f"Got it: {text}.")

        return Reply("What should I use?", needs="What should I use?", on_answer=_answer)


def _arm_slot(engine: Engine) -> Asking:
    """Register the asking demo skill and send "do the other thing" to it.

    Same shape as _arm above: every other non-answer command routes to
    control.help, a real command that never arms a slot of its own, so a
    test can send an unrelated intervening command without it masking
    whether the *original* pending slot survived.
    """
    skill = Asking(SkillContext(config=engine.config, bus=engine.bus))
    engine.skills.register(skill)

    def _route(text: str) -> Intent:
        if text == "do the other thing":
            return Intent("demo2", "demo2", "do", text=text)
        return Intent("help", "control", "help", text=text)

    engine.router.route = _route
    return skill


def test_pending_slot_hands_the_next_utterance_to_on_answer(engine: Engine) -> None:
    skill = _arm_slot(engine)

    first = engine.process("do the other thing")
    assert first.needs == "What should I use?"
    assert skill.received is None

    second = engine.process("purple")
    assert skill.received == "purple"
    assert second.speech == "Got it: purple."


def test_pending_slot_answer_is_not_routed_through_the_nlu(engine: Engine) -> None:
    """Unlike a confirmation, anything said next is the answer - even text
    that would otherwise match a real command - since the question already
    established that whatever comes next is being asked for, not issued as
    a new instruction.
    """
    skill = _arm_slot(engine)
    engine.process("do the other thing")

    engine.process("do the other thing")  # would normally re-arm a fresh slot

    assert skill.received == "do the other thing"


def test_pending_slot_expires(engine: Engine, monkeypatch) -> None:
    from blackvoice.app import PendingSlot

    skill = _arm_slot(engine)
    engine.process("do the other thing")

    monkeypatch.setattr(PendingSlot, "expired", property(lambda self: True))

    engine.process("purple")
    assert skill.received is None, "an expired slot must not be answered"


def test_pending_slot_is_consumed_exactly_once(engine: Engine) -> None:
    """A slot cannot be answered twice: unlike a confirmation, there is no
    way to tell "that was not really an answer" from free text alone, so it
    is deliberately unconditional the *one* time it is live - and gone
    afterwards regardless of what came in, not lingering for a later "purple"
    to land on by surprise.
    """
    skill = _arm_slot(engine)
    engine.process("do the other thing")

    engine.process("purple")
    assert skill.received == "purple"

    skill.received = None
    engine.process("purple")  # nothing pending now - routes normally instead
    assert skill.received is None


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


# --------------------------------------------------------------------------- #
# conversation mode: a follow-up after a reply, without repeating the wake word
# --------------------------------------------------------------------------- #
class _FakeMic:
    """Just enough of Microphone for _converse: something to .drain()."""

    def drain(self) -> None:
        pass


def _scripted_listen(*texts):
    """A listen_once stand-in that returns each text once, in order, then
    "nothing heard" forever - the same shape a real follow-up window that
    eventually goes quiet produces. Every call's max_seconds is recorded so
    a test can check the wake-triggered listen and a follow-up listen were
    actually given different timeouts.
    """
    calls: list = []
    remaining = list(texts)

    def _listen_once(mic, on_partial=None, on_level=None, max_seconds=None):
        calls.append(max_seconds)
        text = remaining.pop(0) if remaining else ""
        return Transcript(text, 0.9) if text else Transcript("", 0.0)

    return _listen_once, calls


def test_converse_listens_for_one_followup_after_a_reply(engine: Engine, monkeypatch) -> None:
    # _running is normally set by Engine.start(); _converse is called
    # directly here without it, so it has to be set by hand - the same
    # "did shutdown happen mid-conversation" guard the follow-up loop checks
    # in real use, just simulating "yes, still running" rather than "no".
    engine._running.set()
    listen_once, calls = _scripted_listen("what time is it")
    monkeypatch.setattr(engine.stt, "listen_once", listen_once)

    engine._converse(_FakeMic())

    assert len(calls) == 2, "must listen once more for an optional follow-up"
    assert calls[0] is None, "the wake-triggered listen keeps the normal command timeout"
    assert calls[1] == engine.config.wake.followup_seconds, (
        "the follow-up listen must use the shorter follow-up window, not the "
        "full command timeout, on every single reply"
    )


def test_converse_keeps_going_through_several_followups(engine: Engine, monkeypatch) -> None:
    engine._running.set()
    listen_once, calls = _scripted_listen(
        "what time is it", "what is the date", "calculate 2 + 2"
    )
    monkeypatch.setattr(engine.stt, "listen_once", listen_once)

    engine._converse(_FakeMic())

    assert len(calls) == 4, "3 real follow-ups plus the one that finally hears nothing"


def test_converse_does_not_listen_for_a_followup_when_nothing_was_heard(
    engine: Engine, monkeypatch
) -> None:
    listen_once, calls = _scripted_listen()  # never says anything at all
    monkeypatch.setattr(engine.stt, "listen_once", listen_once)

    engine._converse(_FakeMic())

    assert len(calls) == 1


def test_converse_respects_followup_enabled_false(monkeypatch) -> None:
    eng = _engine_with(monkeypatch, **{"wake.followup_enabled": False})
    eng._running.set()
    try:
        listen_once, calls = _scripted_listen("what time is it")
        monkeypatch.setattr(eng.stt, "listen_once", listen_once)

        eng._converse(_FakeMic())

        assert len(calls) == 1, "must not listen for a follow-up when it is turned off"
    finally:
        eng.stop()


def test_converse_stops_the_followup_loop_on_go_to_sleep(engine: Engine, monkeypatch) -> None:
    # _running set so the earlier "still running?" check cannot be what
    # stops this - proving ASLEEP itself is what ends the loop.
    engine._running.set()
    # If the sleep state is not what ends this, the script would keep going
    # forever - proving the loop actually checked it, not just run out of
    # scripted text to say.
    listen_once, calls = _scripted_listen(
        "go to sleep", "what time is it", "what time is it", "what time is it"
    )
    monkeypatch.setattr(engine.stt, "listen_once", listen_once)

    engine._converse(_FakeMic())

    assert len(calls) == 1
    assert engine.state == State.ASLEEP
