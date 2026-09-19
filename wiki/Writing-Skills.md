# Writing Skills

Adding a command takes three steps: write a skill, add its patterns, register it.

## 1. Write the skill

Create `blackvoice/skills/coffee.py`:

```python
"""A skill that keeps track of how much coffee you have had."""

from __future__ import annotations

from ..nlu.intents import Intent
from .base import Reply, Skill, SkillContext


class CoffeeSkill(Skill):
    name = "coffee"          # must match Intent.skill

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self._cups = 0

    def handle(self, intent: Intent) -> Reply:
        handler = getattr(self, f"_do_{intent.action}", None)
        if handler is None:
            return Reply.error("I do not know that coffee command.")
        return handler(intent)

    def _do_add(self, intent: Intent) -> Reply:
        self._cups += 1
        return Reply(
            f"That is cup number {self._cups}.",
            data={"cups": self._cups},
        )

    def _do_count(self, intent: Intent) -> Reply:
        if not self._cups:
            return Reply("No coffee yet today.")
        return Reply(f"You have had {self._cups} cups today.")
```

The `getattr(self, f"_do_{intent.action}")` dispatch is the convention every
built-in skill uses. It keeps `handle` short and makes each action easy to test.

## 2. Add the patterns

In `blackvoice/nlu/intents.py`, add to `RULES`:

```python
Rule("coffee_add", "coffee", "add", [
    r"\b(?:i\s+)?(?:had|drank)\s+(?:a\s+)?(?:cup\s+of\s+)?coffee\b",
]),
Rule("coffee_count", "coffee", "count", [
    r"\bhow\s+(?:much|many)\s+coffee\b",
]),
```

The three positional arguments are **rule name**, **skill name**, **action**. The
skill name must equal your `Skill.name`, and the action must match a `_do_*`
method.

### Where to put the rule

Order matters — rules are tried top to bottom and the first match wins. Put
specific rules **above** general ones. In particular, put yours above the generic
`open_app` / `close_app` rules at the very bottom of the list, or a phrase like
*“coffee open”* will be read as an application launch.

### Capturing values

Named groups become slots:

```python
Rule("coffee_add_n", "coffee", "add_n", [
    r"\bi\s+had\s+(?P<count>\d+)\s+coffees?\b",
]),
```

```python
def _do_add_n(self, intent: Intent) -> Reply:
    try:
        n = int(intent.slots.get("count", "1"))
    except ValueError:
        return Reply.error("I did not catch the number.")
    ...
```

Slots always arrive as **strings** — convert and validate them yourself. The
transcript has been lower-cased and stripped of punctuation before matching.

### Static slots

`defaults` merges fixed values in, which lets several phrasings share one action:

```python
Rule("media_next", "utils", "media", [r"\bnext\s+song\b"], defaults={"key": "next"}),
Rule("media_prev", "utils", "media", [r"\bprevious\s+song\b"], defaults={"key": "previous"}),
```

## 3. Register it

In `blackvoice/app.py`, inside `Engine.__init__`:

```python
from .skills.coffee import CoffeeSkill

for skill in (
    ControlSkill(ctx),
    SystemSkill(ctx),
    FilesSkill(ctx),
    TerminalSkill(ctx),
    CoffeeSkill(ctx),        # <- here
    self.ai_skill,
    self.utils_skill,
):
    self.skills.register(skill)
```

## 4. Try it

```bash
blackvoice text "i had a coffee"
blackvoice text "how much coffee"
```

Text mode uses the same router and skills as the voice path, so if it works here
it will work when spoken.

---

## The `Reply` object

```python
Reply(
    speech="Short — this gets read aloud",
    display="Longer text for the overlay; falls back to speech",
    ok=True,
    confirm=None,            # set to ask before acting
    on_confirm=None,         # callable returning another Reply
    data={},                 # structured result, useful in tests
)
```

`Reply.error("...")` is shorthand for `ok=False`.

Keep `speech` to a sentence. Put tables, paths and command output in `display` —
nobody wants twelve lines read to them.

## Asking before you act

Return a `Reply` with `confirm` and `on_confirm`:

```python
def _do_delete_everything(self, intent: Intent) -> Reply:
    def _commit() -> Reply:
        # only runs after the user says yes
        return Reply("Done.")

    return Reply(
        speech="Are you sure? Say yes to confirm.",
        confirm="Delete everything?",
        on_confirm=_commit,
    )
```

The engine holds `on_confirm` for 30 seconds. Yes runs it; no, stop, a different
command, or the timeout all discard it. You do not have to handle any of that.

## Helpers on `Skill`

```python
self.which("wpctl", "pactl", "amixer")   # first one on PATH, or None
self.run(["df", "-h"], timeout=10)       # CompletedProcess, never raises
self.spawn(["firefox"])                  # launch and detach, returns bool
self.config                              # the Config object
self.bus                                 # the event bus
self.ctx.say("...")                      # speak mid-task
```

`run` returns a `CompletedProcess` even when the binary is missing (exit 127) or
times out (exit 124), so you never need a try/except around it.

**Always probe with `which` before assuming a tool exists.** That is what makes
Black Voice work across desktops:

```python
def _do_something(self, intent: Intent) -> Reply:
    tool = self.which("gnome-screenshot", "spectacle", "grim", "scrot")
    if tool is None:
        return Reply.error("No screenshot tool found. Install grim or scrot.")
    ...
```

## Errors

Return `Reply.error(...)` with something the user can act on:

```python
return Reply.error("Install playerctl so I can control media playback.")
```

Not this:

```python
return Reply.error("Error 3")
```

You do not need to catch everything — `SkillRegistry.dispatch` wraps every call,
logs the traceback and returns a generic error reply. A crashing skill cannot
take down the audio loop. But a specific message is always better than the
generic one.

## Long-running work

`handle` blocks the engine thread. For anything slow, speak first and finish in
the background:

```python
def _do_slow_thing(self, intent: Intent) -> Reply:
    def _work() -> None:
        result = expensive()
        self.ctx.say(f"Finished: {result}")

    threading.Thread(target=_work, daemon=True).start()
    return Reply("Working on it.")
```

Look at how `UtilsSkill` handles timers — it schedules a `threading.Timer` and
returns immediately.

## Testing

Skills are plain objects; no microphone required.

```python
import pytest

from blackvoice.config import Config
from blackvoice.core.bus import EventBus
from blackvoice.nlu.intents import Intent
from blackvoice.skills.base import SkillContext
from blackvoice.skills.coffee import CoffeeSkill


@pytest.fixture
def ctx() -> SkillContext:
    return SkillContext(config=Config(), bus=EventBus())


def test_counting(ctx: SkillContext) -> None:
    skill = CoffeeSkill(ctx)
    skill.handle(Intent("c", "coffee", "add"))
    skill.handle(Intent("c", "coffee", "add"))
    reply = skill.handle(Intent("c", "coffee", "count"))
    assert reply.ok
    assert "2 cups" in reply.speech
```

Add routing cases to `tests/test_router.py` too, covering the phrasings your
patterns are meant to catch:

```python
("i had a coffee", "coffee", "add", {}),
("how many coffee", "coffee", "count", {}),
```

```bash
pytest -q
```

## Reusable pattern fragments

Common verb forms live at the top of `intents.py` — `_OPEN`, `_CLOSE`,
`_INCREASE`, `_DECREASE`, `_WHAT`. Use them rather than re-spelling every verb
form:

```python
rf"\b(?:coffee)\s*{_INCREASE}\b"
```

## Checklist

- [ ] Skill class with a unique `name`
- [ ] A `_do_<action>` method per action
- [ ] Rules added, in the right place in the list
- [ ] Registered in `Engine.__init__`
- [ ] `which()` probes for every external tool
- [ ] Useful error messages
- [ ] Tests for the skill and the routing
- [ ] `blackvoice text "..."` does the right thing
