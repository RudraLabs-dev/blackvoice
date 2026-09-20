"""Everyday helpers: clock, weather, timers, notes, search, media keys, maths."""

from __future__ import annotations

import ast
import json
import logging
import operator
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..config import NOTES_FILE, TIMERS_FILE
from ..nlu.intents import Intent
from .base import Reply, Skill, SkillContext

log = logging.getLogger(__name__)

_UNIT_SECONDS = {
    "sec": 1, "second": 1, "seconds": 1,
    "min": 60, "minute": 60, "minutes": 60,
    "hr": 3600, "hour": 3600, "hours": 3600,
}

#: playerctl / XF86 key name for each media action
_MEDIA_KEYS = {
    "playpause": ("play-pause", "XF86AudioPlay"),
    "next": ("next", "XF86AudioNext"),
    "previous": ("previous", "XF86AudioPrev"),
}


class UtilsSkill(Skill):
    name = "utils"

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self._timers: List[threading.Timer] = []
        self._timers_lock = threading.Lock()
        self._restore_timers()

    def handle(self, intent: Intent) -> Reply:
        handler = getattr(self, f"_do_{intent.action}", None)
        if handler is None:
            return Reply.error("I do not know that command.")
        return handler(intent)

    def shutdown(self) -> None:
        # Only the live threading.Timer objects are stopped here - the
        # persisted file is left untouched. Its fire times are still in the
        # future, and _restore_timers re-arms every one of them from there
        # the next time this skill starts, which is the whole point: a timer
        # set before the assistant restarts (a crash, an upgrade, a reboot)
        # must not simply vanish.
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()

    # ----------------------------------------------------------- clock
    def _do_time(self, intent: Intent) -> Reply:
        now = datetime.now()
        return Reply(
            f"It is {now.strftime('%I:%M %p').lstrip('0')}.",
            display=now.strftime("%H:%M:%S"),
        )

    def _do_date(self, intent: Intent) -> Reply:
        now = datetime.now()
        return Reply(
            f"Today is {now.strftime('%A, %d %B %Y')}.",
            display=now.strftime("%A, %d %B %Y"),
        )

    # --------------------------------------------------------- weather
    def _do_weather(self, intent: Intent) -> Reply:
        city = (intent.slots.get("city") or self.config.skills.weather_city or "").strip()

        try:
            import requests
        except ImportError:
            return Reply.error("The requests library is not installed.")

        # wttr.in needs no API key and geolocates by IP when the city is blank.
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "curl/8"})
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            log.debug("weather lookup failed", exc_info=True)
            if "timeout" in str(exc).lower():
                return Reply.error("The weather service did not respond in time.")
            return Reply.error("I could not fetch the weather right now.")

        try:
            current = data["current_condition"][0]
            area = data["nearest_area"][0]["areaName"][0]["value"]
            temp = current["temp_C"]
            feels = current["FeelsLikeC"]
            desc = current["weatherDesc"][0]["value"]
            humidity = current["humidity"]
        except (KeyError, IndexError, TypeError):
            return Reply.error("The weather service sent something I could not read.")

        speech = f"{area} is {temp} degrees, {desc.lower()}, feels like {feels}."
        display = (
            f"{area}\n"
            f"  {desc}\n"
            f"  Temperature  {temp} °C (feels {feels} °C)\n"
            f"  Humidity     {humidity} %"
        )
        return Reply(speech, display=display, data={"city": area, "temp_c": temp})

    # ---------------------------------------------------------- timers
    @staticmethod
    def _seconds(amount: str, unit: str) -> Optional[int]:
        try:
            value = int(amount)
        except (TypeError, ValueError):
            return None
        multiplier = _UNIT_SECONDS.get((unit or "").lower().rstrip("s"))
        if multiplier is None:
            multiplier = _UNIT_SECONDS.get((unit or "").lower())
        return value * multiplier if multiplier else None

    def _schedule(self, seconds: int, message: str) -> None:
        entry_id = uuid.uuid4().hex
        self._persist_upsert(entry_id, time.time() + seconds, message)
        self._arm(entry_id, seconds, message)

    def _arm(self, entry_id: str, seconds: float, message: str) -> None:
        """Start the in-memory countdown for an already-persisted entry."""

        def _fire() -> None:
            log.info("timer fired: %s", message)
            self.ctx.say(message)
            self._notify("Black Voice", message)
            self._persist_remove(entry_id)

        timer = threading.Timer(seconds, _fire)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        # Drop timers that have already run so the list does not grow forever.
        self._timers = [t for t in self._timers if t.is_alive()]

    # ------------------------------------------------- timer persistence
    #
    # A threading.Timer lives only as long as this process does - restarting
    # the service (a crash, an upgrade, the lock/unlock cycle that was found
    # to kill it) silently dropped every pending timer and reminder with no
    # trace either survived. Recorded here as {id: {fire_at, message}} under
    # TIMERS_FILE and replayed by _restore_timers on the next startup, the
    # same shape of fix as the notes file already gets from living in
    # DATA_DIR rather than memory.
    def _persist_upsert(self, entry_id: str, fire_at: float, message: str) -> None:
        with self._timers_lock:
            entries = self._read_persisted()
            entries[entry_id] = {"fire_at": fire_at, "message": message}
            self._write_persisted(entries)

    def _persist_remove(self, entry_id: str) -> None:
        with self._timers_lock:
            entries = self._read_persisted()
            if entries.pop(entry_id, None) is not None:
                self._write_persisted(entries)

    @staticmethod
    def _read_persisted() -> Dict[str, dict]:
        if not TIMERS_FILE.exists():
            return {}
        try:
            data = json.loads(TIMERS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("%s is unreadable; starting with no saved timers", TIMERS_FILE)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_persisted(entries: Dict[str, dict]) -> None:
        try:
            TIMERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TIMERS_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except OSError:
            log.debug("could not persist timers to %s", TIMERS_FILE, exc_info=True)

    def _restore_timers(self) -> None:
        entries = self._read_persisted()
        if not entries:
            return

        now = time.time()
        changed = False
        for entry_id, entry in list(entries.items()):
            fire_at = entry.get("fire_at") if isinstance(entry, dict) else None
            message = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(fire_at, (int, float)) or not isinstance(message, str) or not message:
                del entries[entry_id]
                changed = True
                continue

            delay = fire_at - now
            if delay <= 0:
                # It was due while nothing was running to say it. Firing it
                # once, late, on the next startup beats a reminder the user
                # is still waiting on just quietly never happening.
                log.info("firing overdue timer on restart: %s", message)
                self.ctx.say(message)
                self._notify("Black Voice", message)
                del entries[entry_id]
                changed = True
                continue

            self._arm(entry_id, delay, message)

        if changed:
            self._write_persisted(entries)

    def _notify(self, title: str, body: str) -> None:
        if not self.config.ui.show_notifications:
            return
        if self.which("notify-send"):
            self.run(["notify-send", "-a", "Black Voice", title, body], timeout=5)

    def _do_timer(self, intent: Intent) -> Reply:
        seconds = self._seconds(intent.slots.get("amount", ""), intent.slots.get("unit", ""))
        if not seconds:
            return Reply.error("How long should the timer run?")
        if seconds > 24 * 3600:
            return Reply.error("I can only set timers up to twenty four hours.")

        self._schedule(seconds, "Your timer is done.")
        pretty = self._pretty_duration(seconds)
        return Reply(f"Timer set for {pretty}.", data={"seconds": seconds})

    def _do_reminder(self, intent: Intent) -> Reply:
        seconds = self._seconds(intent.slots.get("amount", ""), intent.slots.get("unit", ""))
        what = (intent.slots.get("what") or "").strip()
        if not seconds:
            return Reply.error("When should I remind you?")
        if not what:
            return Reply.error("What should I remind you about?")
        if seconds > 24 * 3600:
            return Reply.error("I can only set reminders up to twenty four hours ahead.")

        self._schedule(seconds, f"Reminder: {what}")
        return Reply(
            f"I will remind you to {what} in {self._pretty_duration(seconds)}.",
            data={"seconds": seconds, "what": what},
        )

    @staticmethod
    def _pretty_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} seconds"
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        parts = []
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours > 1 else ""))
        if minutes:
            parts.append(f"{minutes} minute" + ("s" if minutes > 1 else ""))
        if secs and not hours:
            parts.append(f"{secs} seconds")
        return " ".join(parts) or f"{seconds} seconds"

    # ----------------------------------------------------------- notes
    def _do_note_add(self, intent: Intent) -> Reply:
        text = (intent.slots.get("text") or "").strip()
        if not text:
            return Reply.error("What should I write down?")

        try:
            NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with NOTES_FILE.open("a", encoding="utf-8") as handle:
                handle.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n")
        except OSError as exc:
            return Reply.error(f"I could not save the note: {exc.strerror}")

        return Reply("Noted.", display=f"Saved to {NOTES_FILE}:\n  {text}")

    def _do_note_read(self, intent: Intent) -> Reply:
        if not NOTES_FILE.exists():
            return Reply("You have no notes yet.")

        try:
            lines = [l for l in NOTES_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError as exc:
            return Reply.error(f"I could not read your notes: {exc.strerror}")

        if not lines:
            return Reply("You have no notes yet.")

        recent = lines[-5:]
        spoken = "; ".join(line.split("] ", 1)[-1].lstrip("- ") for line in recent)
        return Reply(
            f"Your last {len(recent)} notes: {spoken}",
            display="\n".join(recent),
            data={"count": len(lines)},
        )

    # ---------------------------------------------------------- search
    def _do_search(self, intent: Intent) -> Reply:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return Reply.error("What should I search for?")

        url = self.config.skills.search_url.format(query=urllib.parse.quote_plus(query))
        opener = self.which("xdg-open", "gio", "firefox", "chromium")
        if not opener:
            return Reply.error("I could not find a browser to open.")

        argv = [opener, "open", url] if opener.endswith("gio") else [opener, url]
        if self.spawn(argv):
            return Reply(f"Searching for {query}.", display=url, data={"url": url})
        return Reply.error("I could not open the browser.")

    # ----------------------------------------------------------- media
    def _do_media(self, intent: Intent) -> Reply:
        key = intent.slots.get("key", "playpause")
        playerctl_cmd, x_key = _MEDIA_KEYS.get(key, _MEDIA_KEYS["playpause"])

        if self.which("playerctl"):
            result = self.run(["playerctl", playerctl_cmd], timeout=5)
            if result.returncode == 0:
                return Reply(self._media_speech(key))
            # returncode != 0 usually means "no player is running"
            return Reply("No media player is running.", ok=False)

        if self.which("xdotool"):
            if self.run(["xdotool", "key", x_key], timeout=5).returncode == 0:
                return Reply(self._media_speech(key))

        return Reply.error("Install playerctl so I can control media playback.")

    @staticmethod
    def _media_speech(key: str) -> str:
        return {
            "playpause": "Toggled playback.",
            "next": "Next track.",
            "previous": "Previous track.",
        }.get(key, "Done.")

    # ------------------------------------------------------- arithmetic
    _OPS: Dict[type, Callable] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _do_calculate(self, intent: Intent) -> Reply:
        raw = (intent.slots.get("expression") or "").strip()
        expression = raw.replace("x", "*").replace("×", "*").replace("÷", "/")
        if not expression:
            return Reply.error("What should I calculate?")

        try:
            value = self._eval(ast.parse(expression, mode="eval").body)
        except ZeroDivisionError:
            return Reply.error("I cannot divide by zero.")
        except (SyntaxError, ValueError, TypeError, KeyError, OverflowError):
            return Reply.error("I could not work that out.")

        pretty = f"{value:g}" if isinstance(value, float) else str(value)
        return Reply(f"That is {pretty}.", display=f"{raw} = {pretty}", data={"result": value})

    def _eval(self, node: ast.AST):
        """Evaluate an arithmetic AST - no names, calls or attributes allowed."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("only numbers are allowed")
        if isinstance(node, ast.BinOp):
            op = self._OPS[type(node.op)]
            return op(self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return self._OPS[type(node.op)](self._eval(node.operand))
        raise ValueError(f"unsupported expression node {type(node).__name__}")
