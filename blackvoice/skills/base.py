"""Skill plumbing: the contract every skill implements."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from ..core.bus import EventBus
from ..nlu.intents import Intent

log = logging.getLogger(__name__)


#: The display/session vars a GUI app needs, that ``systemd-run`` does not
#: forward to the new scope on its own - confirmed live: even though
#: blackvoice.service's own process already has every one of these (it runs
#: under graphical-session.target), a scope started with bare ``--user
#: --scope`` gave the spawned app none of them, and Firefox exited with
#: "Error: no DISPLAY environment variable specified" before ever opening a
#: window. ``--setenv=NAME`` with no ``=value`` tells systemd-run to read the
#: value from its own environment - i.e. blackvoice's, since nothing here
#: overrides ``Popen``'s ``env`` - and hand that through instead.
_SCOPE_ENV_PASSTHROUGH = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
)


@functools.lru_cache(maxsize=1)
def _scope_wrapper() -> List[str]:
    """Prefix that gives a spawned GUI app its own transient scope unit.

    Empty wherever ``systemd-run`` is not there to give it - anywhere but a
    real systemd user session - so :meth:`Skill.spawn` falls back to exactly
    today's direct ``Popen`` in that case.
    """
    binary = shutil.which("systemd-run")
    if not binary:
        return []
    return (
        [binary, "--user", "--scope", "--quiet"]
        + [f"--setenv={name}" for name in _SCOPE_ENV_PASSTHROUGH]
        + ["--"]
    )


@dataclass
class Reply:
    """What a skill hands back to the engine."""

    #: spoken out loud; keep it short
    speech: str = ""
    #: longer text for the overlay; falls back to ``speech``
    display: str = ""
    ok: bool = True
    #: when set, the engine asks the user to confirm and re-invokes ``on_confirm``
    confirm: Optional[str] = None
    on_confirm: Optional[Callable[[], "Reply"]] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display:
            self.display = self.speech

    @classmethod
    def error(cls, message: str) -> "Reply":
        return cls(speech=message, ok=False)


@dataclass
class SkillContext:
    """Everything a skill is allowed to reach for."""

    config: Config
    bus: EventBus
    #: set by the engine; lets a skill speak mid-task
    say: Callable[[str], None] = lambda _text: None


class Skill(ABC):
    #: matches ``Intent.skill``
    name: str = ""

    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx
        self.config = ctx.config
        self.bus = ctx.bus

    @abstractmethod
    def handle(self, intent: Intent) -> Reply:
        """Execute ``intent`` and return what to tell the user."""

    # --------------------------------------------------------------- helpers
    @staticmethod
    def which(*candidates: str) -> Optional[str]:
        """First of ``candidates`` that exists on PATH."""
        for name in candidates:
            found = shutil.which(name)
            if found:
                return found
        return None

    @staticmethod
    def run(argv, timeout: float = 10.0, check: bool = False) -> subprocess.CompletedProcess:
        """Run a command without a shell and never raise on a non-zero exit."""
        log.debug("running %s", argv)
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=check,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(argv, 127, "", "command not found")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", "timed out")
        except subprocess.CalledProcessError as exc:
            return subprocess.CompletedProcess(argv, exc.returncode, exc.stdout or "", exc.stderr or "")

    @staticmethod
    def spawn(argv) -> bool:
        """Launch a GUI program and detach from it.

        Pinned to the user's home directory rather than inheriting whatever
        this engine process happens to be running from - a terminal opened
        by voice has no business starting in wherever blackvoice itself was
        launched from (its own data directory, if that is where a systemd
        unit or a manual `cd` left the working directory), and a user has no
        way to tell that apart from the assistant actually reporting a path.

        Run through ``systemd-run --user --scope`` when it is available,
        rather than as a direct child of this process. On a real desktop
        install blackvoice itself normally runs as a systemd user *service*
        (see packaging/blackvoice.service), and a plain ``Popen`` child
        inherits that service unit's own cgroup - which is not a session or
        scope. A snap-packaged app (Firefox, on Ubuntu, by default) checks
        its cgroup against snapd's confinement rules and refuses to run
        under one that is not: confirmed live, it exits immediately with
        "... is not a snap cgroup for tag snap.firefox.firefox", after
        Popen has already returned successfully - so nothing here saw a
        failure, and the assistant reported "Opening firefox" for an app
        that was never actually running. Handing it its own transient scope
        unit - the same shape of cgroup a normal login session's own
        session-N.scope already gives an app launched by hand - is what
        satisfies that check; confirmed by reproducing the exact failure
        from inside the service's cgroup and seeing it succeed once wrapped
        this way, in isolation from whatever else is on this machine.
        """
        log.debug("spawning %s", argv)
        wrapper = _scope_wrapper()
        try:
            subprocess.Popen(
                [*wrapper, *argv],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(Path.home()),
            )
            return True
        except (OSError, ValueError):
            log.debug("could not spawn %s", argv, exc_info=True)
            return False


class SkillRegistry:
    """Maps ``Intent.skill`` to a live skill instance."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError(f"{type(skill).__name__} has no name")
        self._skills[skill.name] = skill
        log.debug("registered skill %r", skill.name)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def dispatch(self, intent: Intent) -> Reply:
        skill = self.get(intent.skill)
        if skill is None:
            log.error("no skill registered for %r", intent.skill)
            return Reply.error("That feature is not available right now.")
        try:
            return skill.handle(intent)
        except Exception:
            log.exception("skill %r blew up on %s", intent.skill, intent.action)
            return Reply.error("Something went wrong while doing that.")

    def __iter__(self):
        return iter(self._skills.values())
