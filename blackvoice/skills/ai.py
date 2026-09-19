"""Free-form question answering.

Anything the rule-based router could not turn into a command lands here. Three
backends are supported and the choice lives in the config:

``ollama``     local models, nothing leaves the machine (the default)
``anthropic``  the Claude API, through the official ``anthropic`` SDK
``openai``     the OpenAI chat completions endpoint

Replies are kept short on purpose - this is a voice assistant, and nobody wants
six paragraphs read aloud.

When ``ai.tools_enabled`` is on and the provider is ``ollama``, a question that
needs something actually *done* rather than explained can run a shell command
through the ``run_command`` tool - the same guarded path ``TerminalSkill``
uses. Every command still goes through :class:`~blackvoice.core.safety.ShellGuard`:
a handful of destructive patterns are refused outright, anything else that
could change the system pauses for a spoken "yes", and nothing runs as root.
This is what lets a request the fixed regex router does not recognise ("clean
up my downloads folder") actually happen instead of only being talked about.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Iterator, List, Optional, Tuple, Union

from ..core.safety import ShellGuard
from ..nlu.intents import Intent
from ..text import split_ready_sentences
from .base import Reply, Skill, SkillContext

log = logging.getLogger(__name__)

#: how many previous turns to send along for context
HISTORY_TURNS = 6


class _ToolCall:
    """A tool invocation the model asked for, pulled out of an Ollama stream."""

    __slots__ = ("name", "arguments")

    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


#: The only tool offered to the model. One tool rather than one per skill
#: because ShellGuard already knows how to tell a safe command from a
#: dangerous one - reimplementing that distinction as a menu of narrower
#: tools would just be a second, less battle-tested copy of the same guard.
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on the user's own Linux machine, as the "
                "logged-in user - never as root. Use this for anything that "
                "needs to actually happen rather than just be explained: "
                "checking or changing system state, managing files, "
                "installing something, running a script. A command that "
                "could change the system pauses and asks the user to say "
                "yes before it runs; a short list of destructive patterns "
                "(rm -rf, mkfs, dd to a device, and so on) is refused "
                "outright and never runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run, exactly as it should be typed.",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


class AISkill(Skill):
    name = "ai"

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self.ai = ctx.config.ai
        self.guard = ShellGuard(ctx.config.safety)
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
        short reply with no sentence-final punctuation - the whole answer is
        spoken as one chunk instead, exactly today's non-streaming behaviour.
        That is a deliberate graceful degradation, not a bug: such a reply
        never sounded any different before, it just does not get the latency
        win either.

        A tool call short-circuits all of this: nothing has been spoken yet,
        so it hands straight off to :meth:`_handle_tool_call` instead of
        treating the call as text.
        """
        tools = _TOOLS if self.ai.tools_enabled else None
        stream = self._stream_ollama(self._messages(question), tools=tools)
        first_sentence, rest, buffer, full_text, tool_call = self._consume_until_sentence(stream)

        if tool_call is not None:
            return self._handle_tool_call(tool_call, question, generation)

        return self._finish_reply(
            first_sentence, rest, buffer, full_text, stream, question, generation, "ai-stream"
        )

    def _consume_until_sentence(
        self, stream: Iterator[Union[str, "_ToolCall"]]
    ) -> Tuple[Optional[str], List[str], str, str, Optional["_ToolCall"]]:
        """Pull from ``stream`` until a full sentence is confirmed, it ends, or
        a tool call arrives. Returns
        ``(first_sentence, rest_sentences, buffer, full_text, tool_call)``,
        where exactly one of ``first_sentence`` and ``tool_call`` is set once
        a tool call is seen, and both may be ``None`` if the stream ended
        without ever confirming a sentence boundary.
        """
        buffer = ""
        full_text = ""
        for piece in stream:
            if isinstance(piece, _ToolCall):
                return None, [], buffer, full_text, piece
            full_text += piece
            buffer += piece
            sentences, buffer = split_ready_sentences(buffer)
            if sentences:
                return sentences[0], sentences[1:], buffer, full_text, None
        return None, [], buffer, full_text, None

    def _finish_reply(
        self,
        first_sentence: Optional[str],
        rest: List[str],
        buffer: str,
        full_text: str,
        stream: Iterator[Union[str, "_ToolCall"]],
        question: str,
        generation: int,
        thread_name: str,
        fallback: str = "",
    ) -> Reply:
        if first_sentence is None:
            answer = (full_text or buffer).strip() or fallback
            if not answer:
                return Reply.error("The AI backend returned an empty answer.")
            self._remember(question, answer)
            return Reply(speech=answer, display=answer, data={"provider": "ollama"})

        threading.Thread(
            target=self._speak_the_rest,
            args=(stream, buffer, full_text, rest, question, generation),
            name=thread_name,
            daemon=True,
        ).start()
        return Reply(
            speech=first_sentence, display=first_sentence, data={"provider": "ollama"}
        )

    def _speak_the_rest(
        self,
        stream: Iterator[Union[str, "_ToolCall"]],
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
                if isinstance(piece, _ToolCall):
                    # Already committed to a text answer - a stray second
                    # tool call this far in is ignored rather than acted on.
                    continue
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

    def _stream_ollama(
        self, messages: List[Dict[str, str]], tools: Optional[List[dict]] = None
    ) -> Iterator[Union[str, "_ToolCall"]]:
        """Yield each incremental piece of content as Ollama streams the reply,
        or a single :class:`_ToolCall` in place of text when the model asks to
        call one.

        NDJSON, one JSON object per line - parsed the same way
        ``ollama_models.pull()`` already parses ``/api/pull``'s stream, this
        project's established shape for a streaming Ollama endpoint rather
        than a second one invented just for chat. A tool call arrives whole
        in one message rather than incrementally, so it is yielded once and
        the stream ends there - there is nothing left to keep reading.
        """
        import json as _json

        import requests

        payload = {
            "model": self.ai.ollama_model,
            "stream": True,
            "messages": [{"role": "system", "content": self.ai.system_prompt}] + messages,
            "options": {"num_predict": self.ai.max_tokens},
        }
        if tools:
            payload["tools"] = tools
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
                message = data.get("message") or {}
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    fn = tool_calls[0].get("function") or {}
                    yield _ToolCall(fn.get("name", ""), fn.get("arguments") or {})
                    return
                piece = message.get("content", "")
                if piece:
                    yield piece
                if data.get("done"):
                    return

    # ------------------------------------------------------------ tool use
    def _handle_tool_call(self, call: "_ToolCall", question: str, generation: int) -> Reply:
        if call.name != "run_command":
            log.warning("AI backend asked for an unknown tool %r", call.name)
            return Reply.error("The AI backend tried to use a tool I do not have.")

        command = str(call.arguments.get("command") or "").strip()
        if not command:
            return Reply.error("The AI backend asked to run a command but did not say which one.")

        decision = self.guard.check(command)
        if decision.blocked:
            log.warning("AI tool call refused %r: %s", command, decision.reason)
            return Reply.error(decision.reason)

        if decision.needs_confirmation:
            return Reply(
                speech=f"Run {command}? {decision.reason} Say yes to confirm.",
                display=f"$ {command}\n\n{decision.reason}",
                confirm=f"Run: {command}",
                on_confirm=lambda: self._run_tool_command(command, question, generation),
            )

        return self._run_tool_command(command, question, generation)

    def _run_tool_command(self, command: str, question: str, generation: int) -> Reply:
        """Execute an already-guarded command, then let the model narrate the
        result in a sentence or two instead of reading raw output aloud.
        """
        try:
            argv = shlex.split(command)
        except ValueError:
            return Reply.error("I could not parse that command safely.")

        use_shell = len(argv) != len(command.split()) or any(ch in command for ch in ";|&><")
        timeout = self.config.safety.shell_timeout
        try:
            result = subprocess.run(
                command if use_shell else argv,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            return Reply.error(
                f"That command took longer than {timeout:.0f} seconds, so I stopped it."
            )
        except FileNotFoundError:
            return Reply.error(f"{argv[0]} is not installed.")
        except OSError as exc:
            return Reply.error(f"I could not run that: {exc.strerror}")

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        output = stdout or stderr or f"(exit code {result.returncode}, no output)"
        return self._narrate_tool_result(
            command, self.guard.truncate(output), result.returncode, question, generation
        )

    def _narrate_tool_result(
        self, command: str, output: str, returncode: int, question: str, generation: int
    ) -> Reply:
        messages = self._messages(question) + [
            {"role": "assistant", "content": f"(ran: {command})"},
            {
                "role": "user",
                "content": (
                    f"That command exited with code {returncode} and printed:\n{output}\n\n"
                    "Tell me what happened in one or two spoken sentences, plainly - "
                    "do not show me the raw command or its output."
                ),
            },
        ]
        # No `tools` this time: one tool call per question, so a model that
        # tries to chain another one just gets whatever text it produces.
        stream = self._stream_ollama(messages, tools=None)
        first_sentence, rest, buffer, full_text, _tool_call = self._consume_until_sentence(stream)
        return self._finish_reply(
            first_sentence,
            rest,
            buffer,
            full_text,
            stream,
            question,
            generation,
            "ai-tool-stream",
            fallback=f"Done. Exit code {returncode}.",
        )

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
