"""Turn a transcript into an :class:`Intent`.

Rules are tried in order. Anything that matches nothing becomes an ``ai.ask``
intent, so the assistant answers the question instead of saying "I didn't
understand that".
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .intents import RULES, Intent, Rule, normalise

log = logging.getLogger(__name__)

#: Words that make a sentence a question rather than a command.
_QUESTION_HINTS = (
    "who", "what", "why", "how", "when", "where", "which", "explain", "tell me about",
)


class Router:
    def __init__(self, rules: Optional[List[Rule]] = None) -> None:
        self.rules = rules if rules is not None else RULES

    def route(self, text: str) -> Intent:
        cleaned = normalise(text)
        if not cleaned:
            return Intent("empty", "control", "noop", text=text)

        for rule in self.rules:
            intent = rule.match(cleaned)
            if intent is None:
                continue
            if self._is_bad_match(intent, cleaned):
                log.debug("rejecting %s for %r (looks like a question)", rule.name, cleaned)
                continue
            log.info("intent %s -> %s.%s %s", rule.name, intent.skill, intent.action, intent.slots)
            intent.text = text
            return intent

        log.info("no rule matched %r; sending it to the AI skill", cleaned)
        return Intent("ask", "ai", "ask", {"question": text.strip()}, text=text, score=0.4)

    @staticmethod
    def _is_bad_match(intent: Intent, cleaned: str) -> bool:
        """Reject over-eager matches.

        The generic ``open X`` / ``search X`` rules are broad enough to swallow
        real questions like "what is the weather like" - those belong to the AI.
        """
        if intent.name not in {"open_app", "close_app", "search_web", "calculate"}:
            return False

        if intent.name == "calculate":
            expression = intent.slots.get("expression", "")
            # Needs at least one operator to actually be arithmetic.
            return not any(op in expression for op in "+-*/x×÷%")

        if intent.name in {"open_app", "close_app"}:
            target = intent.slots.get("target", "")
            if not target or len(target.split()) > 4:
                return True
            return any(cleaned.startswith(hint) for hint in _QUESTION_HINTS)

        return False
