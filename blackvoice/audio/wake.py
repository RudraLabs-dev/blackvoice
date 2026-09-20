"""Wake-word detection ("Black ...").

Vosk can be given a small grammar, which turns the general recogniser into a
cheap keyword spotter: it only has to decide between the wake phrases and
``[unk]``. That keeps idle CPU use low, which matters for something meant to sit
in the tray all day.

If the grammar mode is unavailable the detector degrades to fuzzy matching over
the normal partial results.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path
from typing import List, Optional

from ..config import WakeConfig

log = logging.getLogger(__name__)

#: how close a heard word must be to a wake phrase (0..1)
_FUZZY_RATIO = 0.78


class WakeWordDetector:
    def __init__(self, cfg: WakeConfig, model_path: Path, sample_rate: int) -> None:
        self.cfg = cfg
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.phrases: List[str] = [p.lower().strip() for p in cfg.phrases if p.strip()]
        self._rec = None
        self._grammar_mode = False

    # -------------------------------------------------------------- loading
    def load(self) -> bool:
        if not self.cfg.enabled:
            return False
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError:
            log.warning("vosk missing; wake word disabled (use the hotkey instead)")
            return False

        if not self.model_path.exists():
            log.warning("wake word needs a Vosk model at %s", self.model_path)
            return False

        SetLogLevel(-1)
        try:
            model = Model(str(self.model_path))
        except Exception:
            log.exception("could not load the wake-word model")
            return False

        # Restricted grammar keeps the search tiny.
        grammar = json.dumps(self.phrases + ["[unk]"])
        try:
            self._rec = KaldiRecognizer(model, self.sample_rate, grammar)
            self._grammar_mode = True
        except Exception:
            log.debug("grammar mode unsupported by this model; using fuzzy matching")
            try:
                self._rec = KaldiRecognizer(model, self.sample_rate)
                self._grammar_mode = False
            except Exception:
                log.exception("could not create the wake-word recogniser")
                return False

        log.info(
            "wake word ready: %s (%s mode)",
            ", ".join(self.phrases),
            "grammar" if self._grammar_mode else "fuzzy",
        )
        return True

    @property
    def ready(self) -> bool:
        return self._rec is not None

    def reset(self) -> None:
        if self._rec is not None:
            self._rec.FinalResult()

    # ------------------------------------------------------------ detection
    def feed(self, block: bytes) -> bool:
        """Feed one audio block; True when a wake phrase was heard."""
        if self._rec is None:
            return False

        if self._rec.AcceptWaveform(block):
            text = self._text_of(self._rec.Result(), "text")
            if self._matches(text):
                self.reset()
                return True
            return False

        partial = self._text_of(self._rec.PartialResult(), "partial")
        if self._matches(partial):
            self.reset()
            return True
        return False

    @staticmethod
    def _text_of(raw: str, key: str) -> str:
        try:
            return (json.loads(raw).get(key) or "").lower().strip()
        except (json.JSONDecodeError, TypeError, AttributeError):
            return ""

    def _matches(self, heard: str) -> bool:
        if not heard:
            return False
        words = heard.split()
        for phrase in self.phrases:
            if phrase in words:
                return True
            # Multi-word phrases, and near-misses like "blak" for "black".
            if " " in phrase and phrase in heard:
                return True
            for word in words:
                if difflib.SequenceMatcher(None, word, phrase).ratio() >= _FUZZY_RATIO:
                    return True
        return False


def strip_wake_word(text: str, phrases: List[str]) -> str:
    """Remove a wake phrase, and everything said before its last repeat.

    Ordinarily this just strips a leading "black open firefox" ->
    "open firefox". But someone who gets no response to a first "Black,
    open firefox" often repeats the whole thing again without pausing long
    enough to end the recording, and the endpointer then captures both
    attempts as one utterance - confirmed live: whisper transcribed "open
    firefox Black, open firefox" as a single command, and the word-salad
    that reached the router ("firefox black open firefox") matched nothing.
    Cutting after the *last* wake-phrase occurrence rather than only a
    leading one keeps just the final attempt, the same way a person
    listening would mentally discard an abandoned first try.

    Matched on a word boundary rather than as a bare prefix, so "blackboard
    is here" is left alone instead of losing its first syllable to "black" -
    a real prior gap this closes as a side effect, not just the repeat case.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    boundary_words = [re.escape(p.lower()) for p in phrases if p.strip()]
    if not boundary_words:
        return cleaned
    pattern = re.compile(r"\b(?:" + "|".join(boundary_words) + r")\b", re.IGNORECASE)

    last_end = None
    for match in pattern.finditer(cleaned):
        last_end = match.end()
    if last_end is None:
        return cleaned

    return cleaned[last_end:].lstrip(" ,.-:!?").strip()
