"""Intent patterns for English commands.

Every rule is a regular expression with optional named groups; a named group
becomes a slot passed to the skill. Rules are matched in the order they appear
in :data:`RULES`, so put the specific ones before the general ones.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern


@dataclass
class Intent:
    """A matched command, ready to be dispatched to a skill."""

    name: str
    skill: str
    action: str
    slots: Dict[str, str] = field(default_factory=dict)
    text: str = ""
    score: float = 1.0


@dataclass
class Rule:
    name: str
    skill: str
    action: str
    patterns: List[str]
    #: static slot values merged into whatever the regex captured
    defaults: Dict[str, str] = field(default_factory=dict)
    _compiled: List[Pattern[str]] = field(default_factory=list, repr=False)

    def compile(self) -> "Rule":
        self._compiled = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in self.patterns]
        return self

    def match(self, text: str) -> Optional[Intent]:
        for pattern in self._compiled:
            m = pattern.search(text)
            if not m:
                continue
            slots = dict(self.defaults)
            slots.update({k: v.strip() for k, v in (m.groupdict() or {}).items() if v})
            return Intent(self.name, self.skill, self.action, slots, text)
        return None


# Fragments reused across rules -------------------------------------------- #
_OPEN = r"(?:open|launch|start|run)"
_CLOSE = r"(?:close|quit|exit|kill)"
_INCREASE = r"(?:up|increase|raise)"
_DECREASE = r"(?:down|decrease|lower|reduce)"
_WHAT = r"(?:what(?:'s| is)?|tell\s*me)"


RULES: List[Rule] = [
    # ---------------------------------------------------------------- control
    Rule("cancel", "control", "cancel", [
        r"^\s*(?:stop|cancel|nevermind|never\s*mind|forget\s*it|abort)\s*$",
    ]),
    Rule("sleep", "control", "sleep", [
        r"^\s*(?:go\s*to\s*sleep|sleep\s*now|stop\s*listening|mute\s*yourself)\s*$",
    ]),
    Rule("help", "control", "help", [
        r"\b(?:help|what\s+can\s+you\s+do|commands?\s+list|how\s+do\s+i\s+use)\b",
    ]),
    Rule("affirm", "control", "affirm", [
        r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|do\s*it|confirm|go\s*ahead)\s*$",
    ]),
    Rule("deny", "control", "deny", [
        r"^\s*(?:no|nope|nah|don'?t|do\s*not|negative)\s*$",
    ]),

    # ----------------------------------------------------------------- system
    Rule("screenshot", "system", "screenshot", [
        r"\b(?:take\s+a\s+)?screenshot\b",
    ]),
    Rule("lock_screen", "system", "lock", [
        r"\block\s+(?:the\s+)?(?:screen|computer|system|pc)\b",
    ]),
    Rule("shutdown", "system", "shutdown", [
        r"\b(?:shut\s*down|shutdown|power\s*off|turn\s+off\s+(?:the\s+)?(?:computer|system|pc))\b",
    ]),
    Rule("restart", "system", "restart", [
        r"\b(?:re\s*start|reboot)\s*(?:the\s+)?(?:computer|system|pc)?\b",
    ]),
    Rule("logout", "system", "logout", [
        r"\b(?:log\s*out|sign\s*out|log\s*off)\b",
    ]),
    Rule("sleep_pc", "system", "suspend", [
        r"\b(?:suspend|sleep\s+(?:the\s+)?(?:computer|system|pc)|hibernate)\b",
    ]),

    Rule("volume_set", "system", "volume_set", [
        r"\b(?:set\s+)?volume\s*(?:to|at|=)?\s*(?P<value>\d{1,3})\s*(?:percent|%)?\b",
    ]),
    Rule("volume_mute", "system", "volume_mute", [
        r"\b(?:mute|silence)\b(?!\s*yourself)",
    ]),
    Rule("volume_unmute", "system", "volume_unmute", [
        r"\bun\s*mute\b",
    ]),
    Rule("volume_up", "system", "volume_up", [
        rf"\b(?:volume|sound)\s*{_INCREASE}\b",
        rf"\b{_INCREASE}\s*(?:the\s*)?(?:volume|sound)\b",
        r"\b(?:louder)\b",
    ]),
    Rule("volume_down", "system", "volume_down", [
        rf"\b(?:volume|sound)\s*{_DECREASE}\b",
        rf"\b{_DECREASE}\s*(?:the\s*)?(?:volume|sound)\b",
        r"\b(?:quieter)\b",
    ]),

    Rule("brightness_set", "system", "brightness_set", [
        r"\b(?:set\s+)?brightness\s*(?:to|at|=)?\s*(?P<value>\d{1,3})\s*(?:percent|%)?\b",
    ]),
    Rule("brightness_up", "system", "brightness_up", [
        rf"\bbrightness\s*{_INCREASE}\b",
        rf"\b{_INCREASE}\s*(?:the\s*)?brightness\b",
        r"\b(?:brighter)\b",
    ]),
    Rule("brightness_down", "system", "brightness_down", [
        rf"\bbrightness\s*{_DECREASE}\b",
        rf"\b{_DECREASE}\s*(?:the\s*)?brightness\b",
        r"\b(?:dimmer|dim\s+the\s+screen)\b",
    ]),

    Rule("wifi_toggle", "system", "wifi", [
        r"\b(?:turn\s+)?(?P<state>on|off)\s+(?:the\s+)?(?:wifi|wi-?fi)\b",
        r"\b(?:wifi|wi-?fi)\s*(?P<state>on|off)\b",
    ]),
    Rule("bluetooth_toggle", "system", "bluetooth", [
        r"\b(?:turn\s+)?(?P<state>on|off)\s+(?:the\s+)?bluetooth\b",
        r"\bbluetooth\s*(?P<state>on|off)\b",
    ]),

    Rule("battery", "system", "battery", [
        r"\bbattery\b.*\b(?:status|level|percent|percentage|left|remaining)?\b",
    ]),
    Rule("system_info", "system", "info", [
        r"\b(?:system|pc|computer)\s*(?:info|information|status|stats|details)\b",
        r"\b(?:cpu|ram|memory)\s*(?:usage|use|status)\b",
    ]),

    # ------------------------------------------------------------------ files
    Rule("disk_space", "files", "disk_space", [
        r"\b(?:disk|storage|drive)\s*(?:space|usage|free)\b",
        r"\bhow\s+much\s+(?:disk|storage|space)\b",
    ]),
    Rule("find_file", "files", "find", [
        r"\b(?:find|search\s+for|locate|look\s+for)\s+(?:a\s+|the\s+)?(?:file|folder|document)s?\s+(?:named\s+|called\s+)?(?P<query>.+?)\s*$",
    ]),
    Rule("open_folder", "files", "open_folder", [
        rf"^{_OPEN}\s+(?:my\s+|the\s+)?(?P<target>downloads?|documents?|desktop|pictures?|music|videos?|home|trash)(?:\s+(?:folder|directory))?\s*$",
    ]),
    Rule("create_folder", "files", "create_folder", [
        r"\b(?:create|make|new)\s+(?:a\s+)?(?:folder|directory)\s+(?:named\s+|called\s+)?(?P<name>.+?)\s*$",
    ]),

    # --------------------------------------------------------------- terminal
    Rule("run_command", "terminal", "run", [
        r"\b(?:run|execute)\s+(?:the\s+)?(?:command|cmd)\s+(?P<command>.+?)\s*$",
        r"^\s*(?:command|cmd)\s*[:\-]\s*(?P<command>.+?)\s*$",
    ]),

    # ---------------------------------------------------------------- utility
    Rule("time", "utils", "time", [
        rf"\b{_WHAT}\s*(?:the\s*)?time\b",
        r"^\s*time\s*$",
    ]),
    Rule("date", "utils", "date", [
        rf"\b{_WHAT}\s*(?:the\s*)?(?:date|day)\b",
        r"^\s*date\s*$",
    ]),
    Rule("weather", "utils", "weather", [
        r"\bweather\b(?:\s+(?:in|for|at)\s+(?P<city>.+?))?\s*$",
        r"\b(?:how(?:'s| is)\s+the\s+weather|is\s+it\s+(?:raining|hot|cold))\b",
    ]),
    Rule("timer", "utils", "timer", [
        r"\b(?:set\s+a\s+)?timer\s+for\s+(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr)s?\b",
        r"\b(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr)s?\s*(?:timer)\b",
    ]),
    Rule("reminder", "utils", "reminder", [
        r"\bremind\s+me\s+(?:to\s+)?(?P<what>.+?)\s+in\s+(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr)s?\s*$",
    ]),
    Rule("note_add", "utils", "note_add", [
        r"\b(?:take|make|write|add)\s+a\s+note\s*[:\-]?\s*(?P<text>.+?)\s*$",
        r"\bnote\s+(?:this\s+)?down\s*[:\-]?\s*(?P<text>.+?)\s*$",
    ]),
    Rule("note_read", "utils", "note_read", [
        r"\b(?:read|show|what\s+are)\s+(?:my\s+)?notes?\b",
    ]),
    Rule("search_web", "utils", "search", [
        r"\b(?:search|google|look\s+up)\s+(?:for\s+|the\s+web\s+for\s+)?(?P<query>.+?)\s*$",
    ]),
    Rule("media_playpause", "utils", "media", [
        r"\b(?:play|pause|resume)\s*(?:the\s*)?(?:music|song|video|media)?\s*$",
    ], defaults={"key": "playpause"}),
    Rule("media_next", "utils", "media", [
        r"\b(?:next|skip)\s*(?:the\s*)?(?:song|track|video)?\s*$",
    ], defaults={"key": "next"}),
    Rule("media_prev", "utils", "media", [
        r"\b(?:previous|prev|last)\s+(?:song|track|video)\b",
    ], defaults={"key": "previous"}),
    Rule("calculate", "utils", "calculate", [
        r"\b(?:calculate|compute|what(?:'s| is))\s+(?P<expression>[\d\s\.\+\-\*/x×÷%\(\)]+?)\s*$",
        r"^\s*(?P<expression>\d[\d\s\.\+\-\*/x×÷%\(\)]*\d)\s*$",
    ]),

    # ------------------------------------------------------------------- last
    # "open X" and "close X" swallow almost anything, so they must be tried
    # only after every specific rule above has had its chance - otherwise
    # "open downloads" never reaches the folder rule and "run command df -h"
    # is read as an application called "command df -h".
    Rule("open_app", "system", "open_app", [
        rf"^{_OPEN}\s+(?:the\s+)?(?P<target>.+?)\s*$",
        rf"^(?P<target>.+?)\s+{_OPEN}\s*$",
    ]),
    Rule("close_app", "system", "close_app", [
        rf"^{_CLOSE}\s+(?:the\s+)?(?P<target>.+?)\s*$",
        rf"^(?P<target>.+?)\s+{_CLOSE}\s*$",
    ]),
]

for _rule in RULES:
    _rule.compile()


#: Punctuation the rules themselves look at: arithmetic, times and contractions.
#: Everything else becomes a space.
_KEEP = set("_'.+-*/%()×÷:=")


def normalise(text: str) -> str:
    """Lower-case, strip punctuation noise and collapse whitespace."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("’", "'").replace("`", "'")
    kept = [
        ch
        if (ch.isalnum() or ch in _KEEP or unicodedata.category(ch).startswith("M"))
        else " "
        for ch in text
    ]
    return " ".join("".join(kept).split()).lower()
