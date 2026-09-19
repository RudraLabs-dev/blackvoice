"""Free-form question answering.

Anything the rule-based router could not turn into a command lands here. Three
backends are supported and the choice lives in the config:

``ollama``     local models, nothing leaves the machine (the default)
``anthropic``  the Claude API, through the official ``anthropic`` SDK
``openai``     the OpenAI chat completions endpoint

Replies are kept short on purpose - this is a voice assistant, and nobody wants
six paragraphs read aloud.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import deque
from typing import Deque, Dict, Iterator, List, Optional

from ..nlu.intents import Intent
from ..text import split_ready_sentences
from .base import Reply, Skill, SkillContext

log = logging.getLogger(__name__)

#: how many previous turns to send along for context
HISTORY_TURNS = 6


class AISkill(Skill):
    name = "ai"

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self.ai = ctx.config.ai
        self._history: Deque[Dict[str, str]] = deque(maxlen=HISTORY_TURNS * 2)
        self._client = None  # lazily built Anthropic client
        #: bumped on every handle() call; a background stream from an older
        #: question checks this before each ctx.say() and stops quietly once
        #: it no longer matches, rather than talking over a newer answer.
        self._generation = 0

    def handle(self, intent: Intent) -> Reply:
        if intent.action != "ask":
            return Reply.error("I do not know that AI command.")

        question = (intent.slots.get("question") or intent.text or "").strip()
        if not question:
            return Reply.error("I did not catch the question.")

        provider = (self.ai.provider or "none").lower()
        if provider == "none":
            return Reply.error("I did not understand that, and the AI backend is switched off.")

        self._generation += 1
        generation = self._generation

        try:
            if provider == "ollama":
                return self._handle_streaming(question, generation)
            answer = self._ask(provider, question)
        except Exception as exc:
            log.exception("AI backend %s failed", provider)
            return Reply.error(self._friendly_error(provider, exc))

        if not answer:
            return Reply.error("The AI backend returned an empty answer.")

        self._remember(question, answer)
        return Reply(speech=answer, display=answer, data={"provider": provider})

    def reset(self) -> None:
        """Forget the conversation - bound to "new chat" in the tray menu."""
        self._history.clear()

    def _remember(self, question: str, answer: str) -> None:
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": answer})

    # -------------------------------------------------------------- routing
    def _ask(self, provider: str, question: str) -> str:
        if provider == "anthropic":
            return self._ask_anthropic(question)
        if provider == "openai":
            return self._ask_openai(question)
        raise ValueError(f"unknown AI provider {provider!r}")

    def _messages(self, question: str) -> List[Dict[str, str]]:
        return list(self._history) + [{"role": "user", "content": question}]

    # --------------------------------------------------------------- ollama
    def _handle_streaming(self, question: str, generation: int) -> Reply:
        """Speak the first sentence as soon as it exists; keep talking in the
        background instead of making the user wait for the whole answer.

        Everything through the first confirmed sentence happens
        synchronously, on the same thread that produces this method's return
        value, exactly like every other skill - so nothing about
        ``Engine._deliver`` or the SPEAKING state transition has to change.
        Only what comes after that first sentence moves to a background
        thread, using the same "call ctx.say() outside the normal Reply
        path" mechanism UtilsSkill's timers already rely on
        (``UtilsSkill._schedule``).

        If the stream ends before ever confirming a sentence boundary - a
        short reply, or Hinglish with no Western sentence-final punctuation,
        which this project's own AI system prompt explicitly allows for -
        the whole answer is spoken as one chunk instead, exactly today's
        non-streaming behaviour. That is a deliberate graceful degradation,
        not a bug: such a reply never sounded any different before, it just
        does not get the latency win either.
        """
        stream = self._stream_ollama(question)
        buffer = ""
        full_text = ""
        first_sentence: Optional[str] = None
        rest: List[str] = []

        for piece in stream:
            full_text += piece
            buffer += piece
            sentences, buffer = split_ready_sentences(buffer)
            if sentences:
                first_sentence, rest = sentences[0], sentences[1:]
                break

        if first_sentence is None:
            answer = (full_text or buffer).strip()
            if not answer:
                return Reply.error("The AI backend returned an empty answer.")
            self._remember(question, answer)
            return Reply(speech=answer, display=answer, data={"provider": "ollama"})

        threading.Thread(
            target=self._speak_the_rest,
            args=(stream, buffer, full_text, rest, question, generation),
            name="ai-stream",
            daemon=True,
        ).start()
        return Reply(
            speech=first_sentence, display=first_sentence, data={"provider": "ollama"}
        )

    def _speak_the_rest(
        self,
        stream: Iterator[str],
        buffer: str,
        full_text: str,
        rest: List[str],
        question: str,
        generation: int,
    ) -> None:
        try:
            for sentence in rest:
                if not self._say_if_current(sentence, generation):
                    return
            for piece in stream:
                full_text += piece
                buffer += piece
                sentences, buffer = split_ready_sentences(buffer)
                for sentence in sentences:
                    if not self._say_if_current(sentence, generation):
                        return
            tail = buffer.strip()
            if tail and not self._say_if_current(tail, generation):
                return
        except Exception:
            log.exception("streaming AI reply failed mid-stream")
            return

        if generation == self._generation:
            self._remember(question, full_text.strip())

    def _say_if_current(self, sentence: str, generation: int) -> bool:
        """Speak ``sentence`` unless a newer question has since taken over."""
        if generation != self._generation:
            return False
        self.ctx.say(sentence)
        return True

    def _stream_ollama(self, question: str) -> Iterator[str]:
        """Yield each incremental piece of content as Ollama streams the reply.

        NDJSON, one JSON object per line - parsed the same way
        ``ollama_models.pull()`` already parses ``/api/pull``'s stream, this
        project's established shape for a streaming Ollama endpoint rather
        than a second one invented just for chat.
        """
        import json as _json

        import requests

        payload = {
            "model": self.ai.ollama_model,
            "stream": True,
            "messages": [{"role": "system", "content": self.ai.system_prompt}]
            + self._messages(question),
            "options": {"num_predict": self.ai.max_tokens},
        }
        response = requests.post(
            f"{self.ai.ollama_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=self.ai.timeout,
            stream=True,
        )
        response.raise_for_status()

        with response:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                try:
                    data = _json.loads(raw_line)
                except _json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
                piece = (data.get("message") or {}).get("content", "")
                if piece:
                    yield piece
                if data.get("done"):
                    return

    # ------------------------------------------------------------ anthropic
    def _anthropic_client(self):
        if self._client is not None:
            return self._client

        import anthropic

        key = self.ai.api_key or os.environ.get("ANTHROPIC_API_KEY")
        # With no explicit key the SDK still resolves ANTHROPIC_AUTH_TOKEN or an
        # `ant auth login` profile, so do not force one in.
        self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def _ask_anthropic(self, question: str) -> str:
        client = self._anthropic_client()
        response = client.messages.create(
            model=self.ai.anthropic_model,
            max_tokens=self.ai.max_tokens,
            system=self.ai.system_prompt,
            # Spoken answers are short; low effort keeps the reply snappy.
            output_config={"effort": "low"},
            messages=self._messages(question),
        )

        if response.stop_reason == "refusal":
            return "I am not able to answer that one."

        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    # --------------------------------------------------------------- openai
    def _ask_openai(self, question: str) -> str:
        import requests

        key = self.ai.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.ai.openai_model,
                "max_tokens": self.ai.max_tokens,
                "messages": [{"role": "system", "content": self.ai.system_prompt}]
                + self._messages(question),
            },
            timeout=self.ai.timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "").strip()

    # ---------------------------------------------------------------- errors
    @staticmethod
    def _friendly_error(provider: str, exc: Exception) -> str:
        text = str(exc).lower()

        if provider == "ollama" and ("connection" in text or "refused" in text):
            # Nothing is listening on the port, which means one of two very
            # different things. Telling them apart saves the user a search.
            if shutil.which("ollama"):
                return "Ollama is installed but not running. Start it with: ollama serve"
            return (
                "Ollama is not installed - it is what answers questions. "
                "Get it from https://ollama.com/download, then run: "
                "ollama pull llama3.2. Every other command works without it."
            )

        if "api_key" in text or "authentication" in text or "401" in text:
            return f"The {provider} API key is missing or invalid."
        if "timeout" in text or "timed out" in text:
            return "The AI backend took too long to answer."
        if "rate" in text and "limit" in text:
            return "The AI backend is rate limiting me. Try again shortly."
        return "I could not reach the AI backend."
