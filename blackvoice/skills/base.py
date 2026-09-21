"""Skill plumbing: the contract every skill implements."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from ..config import Config
from ..core.bus import EventBus
from ..nlu.intents import Intent

if TYPE_CHECKING:
    # Only for the type hint below - importing AISkill for real would be
    # circular, since ai.py itself imports Reply/Skill/SkillContext from here.
    from .ai import AISkill

log = logging.getLogger(__name__)


#: The display/session vars a GUI app needs, that ``systemd-run`` does not
#: forward to the new unit on its own - confirmed live: even though
#: blackvoice.service's own process already has every one of these (it runs
#: under graphical-session.target), the spawned app got none of them, and
#: Firefox exited with "Error: no DISPLAY environment variable specified"
#: before ever opening a window. ``--setenv=NAME`` with no ``=value`` tells
#: systemd-run to read the value from its own environment - i.e.
#: blackvoice's, since nothing here overrides ``Popen``'s ``env`` - and hand
#: that through instead.
_UNIT_ENV_PASSTHROUGH = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
)


@functools.lru_cache(maxsize=1)
def _detached_launch_wrapper() -> List[str]:
    """Prefix that hands a spawned GUI app its own transient unit, forked by
    the systemd --user manager rather than by this process.

    Deliberately *not* ``--scope``: that mode has systemd-run exec the
    target in place of itself, so the new process is still a fork of
    blackvoice's own - and blackvoice.service runs with
    ``NoNewPrivileges=true`` (see packaging/blackvoice.service), a bit the
    kernel makes permanent across every future exec once set. Confirmed
    live: even inside a scope with a correct cgroup and a correct
    environment, Firefox's snap still would not start - "snap-confine is
    packaged without necessary permissions ... required permitted
    capability cap_dac_override not found" - because snap-confine itself
    needs to gain capabilities via a setuid/file-capability binary, which
    NoNewPrivileges blocks for the whole process tree, permanently, no
    matter how the process is later re-execed. Dropping ``--scope`` in
    favour of systemd-run's default (a transient *service*) routes the
    actual fork through the --user manager instead, a process that was
    never subject to blackvoice.service's own NoNewPrivileges - confirmed
    live to fix exactly this, with the identical launch otherwise unchanged.
    ``--collect`` clears the unit away once it exits either way, so a
    string of "open firefox" attempts does not leave failed units behind
    for `systemctl --user status` to report on forever.

    Empty wherever ``systemd-run`` is not there to give it - anywhere but a
    real systemd user session - so :meth:`Skill.spawn` falls back to exactly
    a plain ``Popen`` in that case.
    """
    binary = shutil.which("systemd-run")
    if not binary:
        return []
    return (
        [binary, "--user", "--collect", "--quiet"]
        + [f"--setenv={name}" for name in _UNIT_ENV_PASSTHROUGH]
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
    #: when set, the engine speaks this as a question and hands whatever the
    #: user says next - as raw text, not routed through the NLU again -
    #: straight to ``on_answer``. For a skill that is missing one piece of
    #: information it already knows how to ask for itself (a file name, a
    #: duration), rather than falling through to the AI skill and hoping a
    #: small local model reliably decides to call a tool with it - confirmed
    #: live that it does not: asked "find a file" then given a real file
    #: name in the next turn, qwen2.5:1.5b just acknowledged the name back
    #: in conversation and never actually searched for anything.
    needs: Optional[str] = None
    on_answer: Optional[Callable[[str], "Reply"]] = None
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
    #: set by the engine once AISkill exists, so any skill can ask it a
    #: single stateless question (AISkill.quick_answer) - a skill that just
    #: found its target is not installed, for one, asking how to install it
    #: rather than only ever saying "not found". None until the engine sets
    #: it, and still None in a test that builds a SkillContext by hand.
    ai: Optional["AISkill"] = None


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
    def run_gui(argv, timeout: float = 15.0) -> subprocess.CompletedProcess:
        """Like :meth:`run`, but through the same detached transient-unit
        wrapper :meth:`spawn` uses for launching an app - for a GUI-facing
        command (a screenshot tool, above all) whose exit code or resulting
        file this skill needs to wait for and check, unlike an app that is
        only ever launched and left running.

        A direct child of blackvoice.service is still a direct child of it
        regardless of whether anything here waits around for the result -
        the cgroup/session mismatch found breaking Firefox is not specific
        to a snap's own confinement check; anything that needs to talk to
        the display server or the compositor sits in the same position.
        """
        return Skill.run([*_detached_launch_wrapper(), *argv], timeout=timeout)

    @staticmethod
    def spawn(argv) -> bool:
        """Launch a GUI program and detach from it.

        Pinned to the user's home directory rather than inheriting whatever
        this engine process happens to be running from - a terminal opened
        by voice has no business starting in wherever blackvoice itself was
        launched from (its own data directory, if that is where a systemd
        unit or a manual `cd` left the working directory), and a user has no
        way to tell that apart from the assistant actually reporting a path.

        Run through ``systemd-run --user`` when it is available, rather than
        as a direct child of this process - see
        :func:`_detached_launch_wrapper` for why plainly wrapping it, or
        wrapping it in a scope, both still were not enough on a real
        machine, and why a transient *service* is. On a real desktop install
        blackvoice itself normally runs as a systemd user service (see
        packaging/blackvoice.service), and a launched app has no business
        being confined - or granted, or denied - by rules meant for the
        assistant's own process, only for its own.
        """
        log.debug("spawning %s", argv)
        wrapper = _detached_launch_wrapper()
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

    def suggest_install(self, name: str) -> Optional[str]:
        """Ask AISkill how ``name`` would actually get installed here, for a
        command whose target genuinely is not on this system - not a guess
        this project bakes in and lets go stale, and not an autonomous
        install either: this only ever returns a command for a human to
        decide whether to run, the same "tells you to run it yourself"
        stance the rest of this project already takes for anything needing
        root (see Security-Model). Returns ``None`` - never a made-up
        command - when no AI backend is configured, the call fails, or the
        model has nothing better than a guess.
        """
        if self.ctx.ai is None:
            return None

        manager = self.which("apt", "dnf", "pacman", "zypper", "snap", "flatpak")
        manager_name = Path(manager).name if manager else "an unknown"
        system = (
            "You help identify Linux install commands. Given the name of an "
            f"application or command, and that this system's package manager "
            f"is {manager_name}, reply with ONLY the exact shell command that "
            "installs it - nothing else, no explanation, no markdown, no "
            "backticks. If you are not confident of a real, correct command "
            "for that specific name, reply with exactly: UNKNOWN"
        )
        answer = self.ctx.ai.quick_answer(name, system)
        if not answer:
            return None
        if answer.strip().upper() == "UNKNOWN":
            return None
        return answer.strip().strip("`")


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
