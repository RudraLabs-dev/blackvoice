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
    needs=None,              # set to ask for one missing piece of info
    on_answer=None,          # callable(str) -> Reply, given the answer as-is
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

## Asking for missing information

Return a `Reply` with `needs` and `on_answer` when a command is missing one
piece of free-text information you already know how to ask for yourself - a
file name, a duration, what to write down:

```python
def _do_find(self, intent: Intent) -> Reply:
    query = (intent.slots.get("query") or "").strip()
    if not query:
        return Reply(
            "What should I look for?",
            needs="What should I look for?",
            on_answer=self._find_with_query,
        )
    return self._find_with_query(query)

def _find_with_query(self, query: str) -> Reply:
    ...  # the real search, run either way
```

Unlike `confirm`, whatever is said next is handed to `on_answer` exactly as
heard - it is not routed through the NLU again, since the question already
established that it is the answer, not a new command to interpret. It is also
a one-shot: answered or not, it does not survive a second thing being said,
so nothing said later can land on a question the user has moved on from.

Reach for this instead of falling through to the AI skill and hoping a local
model asks the same question and then actually acts on the answer - confirmed
live that it does not reliably: a small model, asked to find a file and then
given a real name the next turn, acknowledged the name back in conversation
and never searched for anything. `needs`/`on_answer` is deterministic - no
model in the loop at all for that back-and-forth.

## Helpers on `Skill`

```python
self.which("wpctl", "pactl", "amixer")   # first one on PATH, or None
self.run(["df", "-h"], timeout=10)       # CompletedProcess, never raises
self.run_gui(["gnome-screenshot", "-f", path])  # like run, but for anything
                                                 # that talks to the display
self.spawn(["firefox"])                  # launch and detach, returns bool
self.suggest_install("code")             # install command, or None
self.config                              # the Config object
self.bus                                 # the event bus
self.ctx.say("...")                      # speak mid-task
```

`run` returns a `CompletedProcess` even when the binary is missing (exit 127) or
times out (exit 124), so you never need a try/except around it.

**Use `run_gui` instead of `run` for anything that needs the display or the
compositor** - a screenshot tool, a colour picker, anything short of a plain
CLI command. blackvoice itself normally runs as a systemd user service
(`packaging/blackvoice.service`), and a direct child of that service sits in
the wrong cgroup for some of these to work at all: confirmed live, a
screenshot tool found on PATH by `which` still silently failed run this way,
the identical shape of problem `spawn` already works around for launching an
app. `run_gui` wraps the same fix around a command you need to wait for and
check the result of, rather than only ever launch and leave running.

**Reach for `suggest_install` when the thing you needed is not on this
system at all**, rather than only ever saying "not found":

```python
def _do_open_app(self, intent: Intent) -> Reply:
    binary = self.which(candidate)
    if binary is None:
        install = self.suggest_install(pretty_name)
        if install:
            return Reply.error(f"{pretty_name} is not installed. To install it: {install}")
        return Reply.error(f"I could not find {pretty_name} on this system.")
    ...
```

It asks `AISkill.quick_answer` - whichever backend `ai.provider` is already
configured for - for the real install command, and returns `None` if no AI
backend is configured or it does not have a confident answer, so you always
still have a plain fallback message to fall back to. It never runs anything:
see [Security Model](Security-Model) for why that command is only ever
spoken, never executed on its own.

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
