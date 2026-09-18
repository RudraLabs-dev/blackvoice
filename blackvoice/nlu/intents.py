"""Intent patterns for Hindi, English and Hinglish.

Every rule is a regular expression with optional named groups; a named group
becomes a slot passed to the skill. Rules are matched in the order they appear
in :data:`RULES`, so put the specific ones before the general ones.

The Hindi Vosk model emits Devanagari while the English model romanises, so most
rules carry both spellings.
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
_OPEN = r"(?:open|launch|start|run|khol(?:o|do|iye)?|chalu\s*kar(?:o|do)?|shuru\s*kar(?:o|do)?|खोलो|खोल|चालू\s*करो|शुरू\s*करो)"
_CLOSE = r"(?:close|quit|exit|kill|band\s*kar(?:o|do)?|bandh\s*kar(?:o|do)?|बंद\s*करो|बन्द\s*करो)"
_INCREASE = r"(?:up|increase|raise|badha(?:o|do|iye)?|bada(?:o|do)?|tez\s*kar(?:o|do)?|बढ़ाओ|तेज\s*करो)"
_DECREASE = r"(?:down|decrease|lower|reduce|kam\s*kar(?:o|do)?|ghata(?:o|do)?|dhime\s*kar(?:o|do)?|कम\s*करो|घटाओ)"
_WHAT = r"(?:what(?:'s| is)?|kya|कया|क्या|batao|बताओ|tell\s*me)"


RULES: List[Rule] = [
    # ---------------------------------------------------------------- control
    Rule("cancel", "control", "cancel", [
        r"^\s*(?:stop|cancel|nevermind|never\s*mind|forget\s*it|abort)\s*$",
        r"^\s*(?:ruk(?:o|iye|ja)?|rehne\s*do|chhod\s*do|band\s*karo)\s*$",
        r"^\s*(?:रुको|रहने\s*दो|छोड़\s*दो)\s*$",
    ]),
    Rule("sleep", "control", "sleep", [
        r"^\s*(?:go\s*to\s*sleep|sleep\s*now|stop\s*listening|mute\s*yourself)\s*$",
        r"^\s*(?:so\s*ja(?:o|iye)?|sun(?:na)?\s*band\s*kar(?:o|do)?)\s*$",
        r"^\s*(?:सो\s*जाओ|सुनना\s*बंद\s*करो)\s*$",
    ]),
    Rule("help", "control", "help", [
        r"\b(?:help|what\s+can\s+you\s+do|commands?\s+list|how\s+do\s+i\s+use)\b",
        r"\b(?:madad|kya\s+kar\s+sakte\s+ho|मदद|क्या\s*कर\s*सकते\s*हो)\b",
    ]),
    Rule("affirm", "control", "affirm", [
        r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|do\s*it|confirm|go\s*ahead)\s*$",
        r"^\s*(?:haa?n(?:\s*ji)?|theek\s*hai|kar\s*do|हाँ|हां|ठीक\s*है|कर\s*दो)\s*$",
    ]),
    Rule("deny", "control", "deny", [
        r"^\s*(?:no|nope|nah|don'?t|do\s*not|negative)\s*$",
        r"^\s*(?:nahi(?:n)?|mat\s*karo|नहीं|नही|मत\s*करो)\s*$",
    ]),

    # ----------------------------------------------------------------- system
    Rule("screenshot", "system", "screenshot", [
        r"\b(?:take\s+a\s+)?screenshot\b",
        r"\b(?:screen\s*shot|screenshot)\s*(?:lo|le\s*lo|khich(?:o|lo)?)\b",
        r"\b(?:स्क्रीनशॉट)\b",
    ]),
    Rule("lock_screen", "system", "lock", [
        r"\block\s+(?:the\s+)?(?:screen|computer|system|pc)\b",
        r"\b(?:screen|system)\s*lock\s*kar(?:o|do)?\b",
        r"\b(?:स्क्रीन\s*लॉक)\b",
    ]),
    Rule("shutdown", "system", "shutdown", [
        r"\b(?:shut\s*down|shutdown|power\s*off|turn\s+off\s+(?:the\s+)?(?:computer|system|pc))\b",
        r"\b(?:computer|system)\s*band\s*kar(?:o|do)?\b",
        r"\b(?:शटडाउन|कंप्यूटर\s*बंद\s*करो)\b",
    ]),
    Rule("restart", "system", "restart", [
        r"\b(?:re\s*start|reboot)\s*(?:the\s+)?(?:computer|system|pc)?\b",
        r"\b(?:computer|system)\s*(?:restart|reboot)\s*kar(?:o|do)?\b",
        r"\b(?:रीस्टार्ट|रीबूट)\b",
    ]),
    Rule("logout", "system", "logout", [
        r"\b(?:log\s*out|sign\s*out|log\s*off)\b",
        r"\b(?:लॉग\s*आउट)\b",
    ]),
    Rule("sleep_pc", "system", "suspend", [
        r"\b(?:suspend|sleep\s+(?:the\s+)?(?:computer|system|pc)|hibernate)\b",
        r"\b(?:computer|system)\s*(?:sula\s*do|sleep\s*kar(?:o|do)?)\b",
    ]),

    Rule("volume_set", "system", "volume_set", [
        r"\b(?:set\s+)?volume\s*(?:ko\s*)?(?:to|at|=|par|पर)?\s*(?P<value>\d{1,3})\s*(?:percent|%|प्रतिशत)?\b",
        r"\b(?:awaaz|आवाज़|आवाज)\s*(?P<value>\d{1,3})\s*(?:percent|%)?\s*kar(?:o|do)?\b",
    ]),
    Rule("volume_mute", "system", "volume_mute", [
        r"\b(?:mute|silence)\b(?!\s*yourself)",
        r"\b(?:awaaz|आवाज़|आवाज|sound)\s*(?:band|bandh)\s*kar(?:o|do)?\b",
        r"\b(?:म्यूट)\b",
    ]),
    Rule("volume_unmute", "system", "volume_unmute", [
        r"\bun\s*mute\b",
        r"\b(?:awaaz|आवाज़|sound)\s*(?:wapas|chalu)\s*kar(?:o|do)?\b",
    ]),
    Rule("volume_up", "system", "volume_up", [
        rf"\b(?:volume|sound|awaaz|आवाज़|आवाज)\s*{_INCREASE}\b",
        rf"\b{_INCREASE}\s*(?:the\s*)?(?:volume|sound|awaaz)\b",
        r"\b(?:louder|zor\s*se)\b",
    ]),
    Rule("volume_down", "system", "volume_down", [
        rf"\b(?:volume|sound|awaaz|आवाज़|आवाज)\s*{_DECREASE}\b",
        rf"\b{_DECREASE}\s*(?:the\s*)?(?:volume|sound|awaaz)\b",
        r"\b(?:quieter|dhire\s*se)\b",
    ]),

    Rule("brightness_set", "system", "brightness_set", [
        r"\b(?:set\s+)?brightness\s*(?:to|at|=|par)?\s*(?P<value>\d{1,3})\s*(?:percent|%)?\b",
        r"\b(?:roshni|चमक|ब्राइटनेस)\s*(?P<value>\d{1,3})\s*(?:percent|%)?\s*kar(?:o|do)?\b",
    ]),
    Rule("brightness_up", "system", "brightness_up", [
        rf"\b(?:brightness|roshni|चमक|ब्राइटनेस)\s*{_INCREASE}\b",
        rf"\b{_INCREASE}\s*(?:the\s*)?(?:brightness|roshni)\b",
        r"\b(?:brighter|screen\s*tez)\b",
    ]),
    Rule("brightness_down", "system", "brightness_down", [
        rf"\b(?:brightness|roshni|चमक|ब्राइटनेस)\s*{_DECREASE}\b",
        rf"\b{_DECREASE}\s*(?:the\s*)?(?:brightness|roshni)\b",
        r"\b(?:dimmer|dim\s+the\s+screen)\b",
    ]),

    Rule("wifi_toggle", "system", "wifi", [
        r"\b(?:turn\s+)?(?P<state>on|off)\s+(?:the\s+)?(?:wifi|wi-?fi)\b",
        r"\b(?:wifi|wi-?fi|वाईफाई)\s*(?:ko\s*)?(?P<state>on|off|chalu|band|बंद|चालू)\s*(?:kar(?:o|do)?)?\b",
    ]),
    Rule("bluetooth_toggle", "system", "bluetooth", [
        r"\b(?:turn\s+)?(?P<state>on|off)\s+(?:the\s+)?bluetooth\b",
        r"\b(?:bluetooth|ब्लूटूथ)\s*(?:ko\s*)?(?P<state>on|off|chalu|band|बंद|चालू)\s*(?:kar(?:o|do)?)?\b",
    ]),

    Rule("battery", "system", "battery", [
        r"\bbattery\b.*\b(?:status|level|percent|percentage|left|remaining)?\b",
        r"\b(?:battery|बैटरी)\s*(?:kitni|kitna|कितनी)\b",
    ]),
    Rule("system_info", "system", "info", [
        r"\b(?:system|pc|computer)\s*(?:info|information|status|stats|details)\b",
        r"\b(?:cpu|ram|memory)\s*(?:usage|use|status|kitna|kitni)\b",
        r"\b(?:सिस्टम\s*जानकारी)\b",
    ]),

    # ------------------------------------------------------------------ files
    Rule("disk_space", "files", "disk_space", [
        r"\b(?:disk|storage|drive)\s*(?:space|usage|free|kitni|kitna)\b",
        r"\bhow\s+much\s+(?:disk|storage|space)\b",
        r"\b(?:डिस्क|स्टोरेज)\b",
    ]),
    Rule("find_file", "files", "find", [
        r"\b(?:find|search\s+for|locate|look\s+for)\s+(?:a\s+|the\s+)?(?:file|folder|document)s?\s+(?:named\s+|called\s+)?(?P<query>.+?)\s*$",
        r"\b(?P<query>.+?)\s*(?:naam\s*ki\s*)?(?:file|folder|फाइल|फ़ाइल)\s*(?:dhoond(?:o|do)?|khoj(?:o|do)?|ढूंढो|खोजो)\b",
    ]),
    Rule("open_folder", "files", "open_folder", [
        rf"^{_OPEN}\s+(?:my\s+|the\s+)?(?P<target>downloads?|documents?|desktop|pictures?|music|videos?|home|trash)(?:\s+(?:folder|directory))?\s*$",
        r"^(?P<target>downloads?|documents?|desktop|pictures?|music|videos?|home)\s*(?:folder\s*)?(?:khol(?:o|do)?|खोलो)\s*$",
    ]),
    Rule("create_folder", "files", "create_folder", [
        r"\b(?:create|make|new)\s+(?:a\s+)?(?:folder|directory)\s+(?:named\s+|called\s+)?(?P<name>.+?)\s*$",
        r"\b(?P<name>.+?)\s*(?:naam\s*ka\s*)?(?:folder|फोल्डर)\s*bana(?:o|do)?\b",
    ]),

    # --------------------------------------------------------------- terminal
    Rule("run_command", "terminal", "run", [
        r"\b(?:run|execute)\s+(?:the\s+)?(?:command|cmd)\s+(?P<command>.+?)\s*$",
        r"\b(?:terminal|shell)\s+(?:me(?:in)?\s+)?(?P<command>.+?)\s*(?:chala(?:o|do)?|run\s*kar(?:o|do)?)\s*$",
        r"^\s*(?:command|cmd)\s*[:\-]\s*(?P<command>.+?)\s*$",
    ]),

    # ---------------------------------------------------------------- utility
    Rule("time", "utils", "time", [
        rf"\b{_WHAT}\s*(?:the\s*)?time\b",
        r"\b(?:kitne?\s*baje|kitna\s*baja|समय\s*क्या|कितने\s*बजे)\b",
        r"^\s*time\s*$",
    ]),
    Rule("date", "utils", "date", [
        rf"\b{_WHAT}\s*(?:the\s*)?(?:date|day)\b",
        r"\b(?:aaj\s*(?:ki\s*)?(?:date|tareekh)|आज\s*की\s*तारीख|कौन\s*सा\s*दिन)\b",
        r"^\s*date\s*$",
    ]),
    Rule("weather", "utils", "weather", [
        r"\bweather\b(?:\s+(?:in|for|at)\s+(?P<city>.+?))?\s*$",
        r"\b(?:mausam|मौसम)\b(?:\s*(?P<city>.+?)\s*(?:ka|में|me))?",
        r"\b(?:how(?:'s| is)\s+the\s+weather|is\s+it\s+(?:raining|hot|cold))\b",
    ]),
    Rule("timer", "utils", "timer", [
        r"\b(?:set\s+a\s+)?timer\s+for\s+(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr)s?\b",
        r"\b(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr|मिनट|सेकंड|घंटा)s?\s*(?:ka\s*)?(?:timer|टाइमर)\b",
    ]),
    Rule("reminder", "utils", "reminder", [
        r"\bremind\s+me\s+(?:to\s+)?(?P<what>.+?)\s+in\s+(?P<amount>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr)s?\s*$",
        r"\b(?P<amount>\d+)\s*(?P<unit>minute|min|hour|hr|मिनट|घंटा)s?\s*(?:baad|बाद)\s*(?P<what>.+?)\s*(?:yaad\s*dila(?:na|o)?|याद\s*दिलाना)\b",
    ]),
    Rule("note_add", "utils", "note_add", [
        r"\b(?:take|make|write|add)\s+a\s+note\s*[:\-]?\s*(?P<text>.+?)\s*$",
        r"\bnote\s+(?:this\s+)?down\s*[:\-]?\s*(?P<text>.+?)\s*$",
        r"\b(?:note|नोट)\s*(?:me(?:in)?\s*)?(?P<text>.+?)\s*(?:likh(?:o|do|na)?|लिखो)\b",
    ]),
    Rule("note_read", "utils", "note_read", [
        r"\b(?:read|show|what\s+are)\s+(?:my\s+)?notes?\b",
        r"\b(?:mere\s*)?(?:note|नोट)s?\s*(?:padh(?:o|do)?|dikha(?:o|do)?|पढ़ो|दिखाओ)\b",
    ]),
    Rule("search_web", "utils", "search", [
        # "google par X dhoondo" must be tried before the bare "google X" form,
        # otherwise the query keeps the leading "par".
        r"\b(?:google|internet|web)\s+(?:par|pe|me(?:in)?)\s+(?P<query>.+?)\s*(?:dhoond(?:o|do)?|search\s*kar(?:o|do)?)?\s*$",
        r"\b(?P<query>.+?)\s*(?:ko\s*)?(?:search|google|सर्च)\s*kar(?:o|do)?\s*$",
        r"\b(?:search|google|look\s+up)\s+(?:for\s+|the\s+web\s+for\s+)?(?P<query>.+?)\s*$",
    ]),
    Rule("media_playpause", "utils", "media", [
        r"\b(?:play|pause|resume)\s*(?:the\s*)?(?:music|song|video|media)?\s*$",
        r"\b(?:gaana|गाना|music)\s*(?:chala(?:o|do)?|rok(?:o|do)?|बजाओ|रोको)\b",
    ], defaults={"key": "playpause"}),
    Rule("media_next", "utils", "media", [
        r"\b(?:next|skip)\s*(?:the\s*)?(?:song|track|video)?\s*$",
        r"\b(?:agla|अगला)\s*(?:gaana|गाना|song)\b",
    ], defaults={"key": "next"}),
    Rule("media_prev", "utils", "media", [
        r"\b(?:previous|prev|last)\s+(?:song|track|video)\b",
        r"\b(?:pichla|पिछला)\s*(?:gaana|गाना|song)\b",
    ], defaults={"key": "previous"}),
    Rule("calculate", "utils", "calculate", [
        r"\b(?:calculate|compute|what(?:'s| is))\s+(?P<expression>[\d\s\.\+\-\*/x×÷%\(\)]+?)\s*$",
        r"^\s*(?P<expression>\d[\d\s\.\+\-\*/x×÷%\(\)]*\d)\s*(?:kitna|कितना)?\s*$",
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
    r"""Lower-case, strip punctuation noise and collapse whitespace.

    Combining marks are kept, which a ``\w``-based character class would not do:
    they are Unicode category Mn, so every Devanagari vowel sign would be
    deleted and "खोलो" would reach the rules as "ख ल" - two bare consonants
    matching nothing. That silently made every Devanagari pattern in this file
    unreachable, so keep the filter working on categories rather than on ``\w``.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("’", "'").replace("`", "'")
    kept = [
        ch
        if (ch.isalnum() or ch in _KEEP or unicodedata.category(ch).startswith("M"))
        else " "
        for ch in text
    ]
    return " ".join("".join(kept).split()).lower()
