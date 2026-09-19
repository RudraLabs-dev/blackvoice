"""Text helpers shared between the speech and AI-reply pipelines.

One regex decides what counts as the end of a sentence, used two different
ways: :mod:`blackvoice.audio.stt` strips it entirely from Whisper's already-
finished prose before the router ever sees it, while :mod:`blackvoice.skills.ai`
uses the same boundary to flush a streamed AI reply sentence by sentence as it
arrives, rather than making the user wait for the whole answer before hearing
anything.
"""

from __future__ import annotations

import re
from typing import List, Tuple

#: Sentence-ending punctuation, including the Devanagari danda. Requiring
#: whitespace or end-of-string after it is what leaves a decimal point like
#: "2.5" alone - arithmetic is a real command in this project, and Whisper
#: writes prose where Vosk writes bare words.
SENTENCE_PUNCT = re.compile(r"[.!?।]+(?=\s|$)")


def split_ready_sentences(buffer: str) -> Tuple[List[str], str]:
    """Split off every *confirmed* complete sentence at the front of ``buffer``.

    Meant to be called repeatedly as more text streams in: pass back whatever
    ``remainder`` came back last time, with the newly arrived text appended.
    A sentence boundary sitting at the very end of ``buffer`` is deliberately
    left unconfirmed rather than split off - the token after it has not
    arrived yet, so a period there might still turn out to be the "2" in
    "2.5" once it does. Call again once more text is in, or flush the
    remainder unconditionally once the stream itself has ended - at that
    point nothing more is coming, so there is no longer anything to confirm.

    A buffer with no confirmed boundary yet returns no sentences and the
    buffer unchanged, which is exactly how a short or unpunctuated reply
    (common in Hinglish, which this project's own AI system prompt
    deliberately allows for) degrades to being spoken as a single chunk once
    the stream ends, rather than never being flushed at all.
    """
    sentences: List[str] = []
    start = 0
    for match in SENTENCE_PUNCT.finditer(buffer):
        if match.end() == len(buffer):
            break
        piece = buffer[start:match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()
    remainder = buffer[start:].lstrip() if start else buffer
    return sentences, remainder
