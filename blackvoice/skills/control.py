"""Meta commands: cancel, sleep, help, and bare yes/no.

Yes and no are normally intercepted by the engine while a confirmation is
pending; the handlers here only run when nothing was waiting for an answer.
"""

from __future__ import annotations

from ..core.bus import Topic
from ..nlu.intents import Intent
from .base import Reply, Skill

HELP_TEXT = """Black Voice — what I understand

System
  open firefox                          launch an application
  close chrome                          quit an application
  volume up · volume 40 · mute          sound control
  brightness down · brightness 70       screen brightness
  screenshot · lock screen              screen actions
  wifi off · bluetooth on               radios
  battery · system info                 hardware status
  shut down · restart · log out         power (always asks first)

Files
  find file report.pdf                  search your home directory
  open downloads                        open a standard folder
  create folder demo                    new folder on the Desktop
  disk space                            free space on the root disk

Terminal
  run command df -h                     read-only commands run straight away
                                        anything else asks you first

Everyday
  what time is it · what is the date
  weather · weather in Jaipur
  set timer for 5 minutes
  remind me to call mom in 20 minutes
  take a note buy milk · read my notes
  search for python decorators
  play · next · previous
  calculate 12 * 8

Anything else becomes a question for the AI backend.
Say "stop" to cancel, "go to sleep" to stop listening."""


class ControlSkill(Skill):
    name = "control"

    def handle(self, intent: Intent) -> Reply:
        handler = getattr(self, f"_do_{intent.action}", None)
        if handler is None:
            return Reply(speech="", ok=False)
        return handler(intent)

    def _do_noop(self, intent: Intent) -> Reply:
        return Reply(speech="", display="")

    def _do_cancel(self, intent: Intent) -> Reply:
        return Reply("Okay.")

    def _do_sleep(self, intent: Intent) -> Reply:
        self.bus.publish(Topic.STATE, state="asleep")
        return Reply(
            "Going to sleep. Say Black to wake me.",
            data={"sleep": True},
        )

    def _do_help(self, intent: Intent) -> Reply:
        return Reply(
            speech="I can control apps, volume, files, the terminal and answer questions. "
                   "The full list is on screen.",
            display=HELP_TEXT,
        )

    def _do_affirm(self, intent: Intent) -> Reply:
        return Reply("There is nothing waiting for a yes.", ok=False)

    def _do_deny(self, intent: Intent) -> Reply:
        return Reply("Okay.")
