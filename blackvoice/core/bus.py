"""A tiny thread-safe publish/subscribe bus.

The audio loop, the skills and the Qt UI all run on different threads. Rather
than wiring them to each other, everything talks through this bus: the engine
publishes state changes, the UI subscribes to them.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, Dict, List

log = logging.getLogger(__name__)

Handler = Callable[["Event"], None]


class Topic:
    """Well-known event names. Kept as constants so typos fail loudly."""

    STATE = "state"                 # idle / listening / thinking / speaking
    HEARD = "heard"                 # transcript of what the user said
    REPLY = "reply"                 # assistant's answer
    ERROR = "error"
    CONFIRM = "confirm"             # a skill needs a yes/no from the user
    ASK = "ask"                     # a skill needs one piece of free-text info
    LEVEL = "level"                 # mic level, 0..1, for the waveform
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    topic: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register ``handler`` for ``topic``; returns an unsubscribe callable."""
        with self._lock:
            self._subscribers[topic].append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                if handler in self._subscribers[topic]:
                    self._subscribers[topic].remove(handler)

        return _unsubscribe

    def publish(self, topic: str, **payload: Any) -> None:
        with self._lock:
            handlers = list(self._subscribers.get(topic, ()))
        event = Event(topic, payload)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # One bad subscriber must not take down the audio loop.
                log.exception("subscriber for %r raised", topic)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
