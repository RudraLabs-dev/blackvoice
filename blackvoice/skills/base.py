"""Skill plumbing: the contract every skill implements."""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..config import Config
from ..core.bus import EventBus
from ..nlu.intents import Intent

log = logging.getLogger(__name__)


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
        """
        log.debug("spawning %s", argv)
        try:
            subprocess.Popen(
                argv,
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
