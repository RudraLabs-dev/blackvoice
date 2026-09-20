"""Hybrid speech-to-text.

Three offline tiers, tried in order, with the cloud only behind all of them:

``whisper.cpp``  a more accurate model, at the cost of a subprocess per
                 utterance. It is a native binary driven over a pipe, exactly
                 as Piper is on the output side - no Python extension module,
                 so nothing here has to be rebuilt when the system interpreter
                 changes.
``vosk``         a small, fast English model. Kept as the fallback, and still
                 the only thing cheap enough to sit on the microphone all day
                 for the wake word.
``online``       a cloud recogniser, when the offline pass came back unsure and
                 the configuration allows it.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..config import BUNDLED_BIN_DIR, AudioConfig, SpeechConfig
from ..text import SENTENCE_PUNCT
from .mic import Endpointer, Microphone

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    confidence: float = 0.0
    language: str = "en"
    source: str = "vosk"

    def __bool__(self) -> bool:
        return bool(self.text.strip())


# --------------------------------------------------------------------------- #
# Offline: Vosk
# --------------------------------------------------------------------------- #
class VoskRecognizer:
    """One loaded Vosk model plus its streaming recogniser."""

    def __init__(self, model_path: Path, sample_rate: int, language: str) -> None:
        self.language = language
        self.model_path = model_path
        self.sample_rate = sample_rate
        self._rec = None
        self._model = None

    def load(self) -> bool:
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError:
            log.warning("vosk is not installed; offline recognition disabled")
            return False

        if not self.model_path.exists():
            log.warning(
                "Vosk model missing at %s - run 'blackvoice setup' to download it",
                self.model_path,
            )
            return False

        SetLogLevel(-1)  # Vosk is extremely noisy on stdout otherwise
        try:
            self._model = Model(str(self.model_path))
            self._rec = KaldiRecognizer(self._model, self.sample_rate)
            self._rec.SetWords(True)
        except Exception:
            log.exception("could not load Vosk model at %s", self.model_path)
            return False

        log.info("loaded Vosk model (%s) from %s", self.language, self.model_path.name)
        return True

    @property
    def ready(self) -> bool:
        return self._rec is not None

    def reset(self) -> None:
        if self._rec is not None:
            # Draining the final result clears the internal state.
            self._rec.FinalResult()

    def accept(self, block: bytes) -> bool:
        """Feed audio; True means an utterance boundary was detected."""
        if self._rec is None:
            return False
        return bool(self._rec.AcceptWaveform(block))

    def partial(self) -> str:
        if self._rec is None:
            return ""
        try:
            return json.loads(self._rec.PartialResult()).get("partial", "")
        except (json.JSONDecodeError, TypeError):
            return ""

    def final(self) -> Transcript:
        if self._rec is None:
            return Transcript("", 0.0, self.language)
        try:
            data = json.loads(self._rec.FinalResult())
        except (json.JSONDecodeError, TypeError):
            return Transcript("", 0.0, self.language)

        text = (data.get("text") or "").strip()
        words = data.get("result") or []
        if words:
            confidence = sum(w.get("conf", 0.0) for w in words) / len(words)
        else:
            confidence = 0.0
        return Transcript(text, confidence, self.language, source="vosk")


# --------------------------------------------------------------------------- #
# Offline: whisper.cpp
# --------------------------------------------------------------------------- #
#: Executable names to try on PATH, in order. Upstream renamed the binary from
#: ``main`` to ``whisper-cli``; the old name is accepted only inside our own
#: bundle directory, because finding a bare "main" on someone's PATH and
#: executing it would be reckless.
_WHISPER_NAMES = ("whisper-cli", "whisper-cpp", "whisper")
_WHISPER_BUNDLED_NAMES = _WHISPER_NAMES + ("main",)

#: whisper.cpp prints one line per segment. With --no-timestamps there is no
#: prefix, but a build that does not know the flag still emits them, so they are
#: stripped defensively rather than trusted away.
_TIMESTAMP = re.compile(r"^\[[\d:.,\s>-]+\]\s*")

#: Non-speech annotations - [BLANK_AUDIO], (music playing) and friends. In a
#: spoken command anything in brackets is Whisper describing the audio rather
#: than transcribing it, so it never belongs in the text handed to the router.
_BRACKETED = re.compile(r"[\[(][^\])]*[\])]")

#: Whisper reports no usable per-utterance confidence, so results carry a
#: nominal one. It is never compared against a Vosk score - the tiering in
#: :class:`HybridSTT` chooses between the engines instead - and exists only so
#: that the number reaching the log and the overlay is not a bare zero.
_WHISPER_CONFIDENCE = 0.9


def find_whisper_binary(configured: str = "") -> Optional[str]:
    """Locate the whisper.cpp executable, or ``None``.

    An explicit path in the configuration wins. Otherwise PATH is searched
    before the copy the distribution packages bundle - the same precedence the
    launcher gives PYTHONPATH, on the same reasoning: a build the user's own
    distribution installed should beat the one we shipped.
    """
    if configured:
        path = Path(configured).expanduser()
        if path.is_absolute():
            return str(path) if path.exists() else None
        return shutil.which(configured)

    for name in _WHISPER_NAMES:
        found = shutil.which(name)
        if found:
            return found

    for name in _WHISPER_BUNDLED_NAMES:
        candidate = BUNDLED_BIN_DIR / name
        if candidate.exists():
            return str(candidate)
    return None


def _runtime_dir() -> Optional[str]:
    """Directory for the scratch WAV, or ``None`` for the platform default.

    XDG_RUNTIME_DIR is the right home for a transient per-user file, and that
    matters here beyond tidiness: the systemd unit runs with
    ``ProtectSystem=strict``, which leaves few writable places.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return runtime if runtime and os.path.isdir(runtime) else None


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw mono 16-bit PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def _tail(stream: Optional[bytes], limit: int = 200) -> str:
    """The end of a subprocess's stderr, for a one-line log message."""
    if not stream:
        return ""
    text = " ".join(stream.decode("utf-8", "replace").split())
    return text[-limit:]


class WhisperCppRecognizer:
    """whisper.cpp, run once per utterance.

    The model is loaded on every invocation, which costs a few hundred
    milliseconds before decoding starts. That is the price of the subprocess
    design, and it is worth paying to keep a compiled extension out of
    ``/opt/blackvoice/lib``: every wheel bundled there has to work on whatever
    Python the host upgrades to, and ctranslate2 and onnxruntime ship one wheel
    per interpreter version. whisper.cpp also has an HTTP server mode that holds
    the model open, which is how to remove the reload cost once the measurements
    say it is worth supervising another process.
    """

    def __init__(
        self,
        speech: SpeechConfig,
        model_path: Path,
        sample_rate: int,
        binary: Optional[str] = None,
    ) -> None:
        self.speech = speech
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.binary = binary

    # -------------------------------------------------------------- loading
    def load(self) -> bool:
        """Check that the binary and the model are both present.

        Nothing is read into memory - whisper.cpp does that per run - so this is
        a lookup on PATH and a pair of stat calls.
        """
        if self.binary is None:
            self.binary = find_whisper_binary(self.speech.whisper_binary)

        if not self.binary:
            log.info(
                "whisper.cpp was not found; install it or set "
                "speech.whisper_binary. Using Vosk instead."
            )
            return False

        if not self.model_path.exists():
            log.info(
                "whisper model missing at %s - run 'blackvoice setup --whisper' "
                "to download it. Using Vosk instead.",
                self.model_path,
            )
            self.binary = None
            return False

        log.info("whisper.cpp ready: %s with %s", self.binary, self.model_path.name)
        return True

    @property
    def ready(self) -> bool:
        return bool(self.binary) and self.model_path.exists()

    # --------------------------------------------------------- transcribing
    def _argv(self, wav_path: str) -> List[str]:
        argv = [
            str(self.binary),
            "--model", str(self.model_path),
            "--file", wav_path,
            "--language", self.speech.whisper_language or "en",
            "--no-timestamps",
        ]
        if self.speech.whisper_threads > 0:
            argv += ["--threads", str(self.speech.whisper_threads)]
        prompt = self.speech.whisper_prompt.strip()
        if prompt:
            # Whisper conditions its decoding on this text, which pulls
            # ambiguous audio towards the vocabulary the router can act on.
            # It is not a grammar - anything may still be transcribed.
            argv += ["--prompt", prompt]
        return argv

    def transcribe(self, pcm: bytes) -> Transcript:
        if not self.ready or not pcm:
            return Transcript("", 0.0, source="whisper")

        handle, wav_path = tempfile.mkstemp(suffix=".wav", dir=_runtime_dir())
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(_wav_bytes(pcm, self.sample_rate))

            started = time.monotonic()
            try:
                completed = subprocess.run(
                    self._argv(wav_path),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.speech.whisper_timeout,
                )
            except subprocess.TimeoutExpired:
                log.warning(
                    "whisper.cpp did not finish within %.0fs; using the fallback",
                    self.speech.whisper_timeout,
                )
                return Transcript("", 0.0, source="whisper")
            except OSError as exc:
                log.warning("could not run whisper.cpp (%s); using the fallback", exc)
                # Stop retrying something that cannot be executed at all,
                # rather than paying for the failure on every utterance.
                self.binary = None
                return Transcript("", 0.0, source="whisper")

            if completed.returncode != 0:
                log.warning(
                    "whisper.cpp exited %d: %s",
                    completed.returncode,
                    _tail(completed.stderr),
                )
                return Transcript("", 0.0, source="whisper")

            text = self._clean(completed.stdout.decode("utf-8", "replace"))
            log.debug(
                "whisper.cpp took %.2fs for %r", time.monotonic() - started, text
            )
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                log.debug("could not remove %s", wav_path, exc_info=True)

        if not text:
            return Transcript("", 0.0, source="whisper")
        return Transcript(text, _WHISPER_CONFIDENCE, "en", source="whisper")

    @staticmethod
    def _clean(stdout: str) -> str:
        """Turn whisper.cpp's stdout into a single line of plain text."""
        parts = []
        for line in stdout.splitlines():
            line = _TIMESTAMP.sub("", line.strip()).strip()
            if line:
                parts.append(line)
        text = _BRACKETED.sub(" ", " ".join(parts))
        # The router's normalise() keeps a full stop on purpose - arithmetic
        # needs "2.5" - so a trailing one from Whisper's prose survives into
        # the rules and defeats every pattern anchored with $ unless it is
        # stripped here first.
        text = SENTENCE_PUNCT.sub("", text)
        return " ".join(text.split())


# --------------------------------------------------------------------------- #
# Online fallback
# --------------------------------------------------------------------------- #
class OnlineRecognizer:
    """Cloud recogniser used only when the offline pass is unsure."""

    #: tried in order; the first non-empty result wins
    LANG_CODES = ["en-IN", "en-US"]

    def __init__(self, cfg: SpeechConfig, sample_rate: int) -> None:
        self.cfg = cfg
        self.sample_rate = sample_rate
        self._sr = None

    def _engine(self):
        if self._sr is None:
            try:
                import speech_recognition as sr
            except ImportError:
                log.debug("SpeechRecognition is not installed; online fallback disabled")
                return None
            self._sr = sr
        return self._sr

    def transcribe(self, pcm: bytes) -> Optional[Transcript]:
        sr = self._engine()
        if sr is None or not pcm:
            return None

        recogniser = sr.Recognizer()
        recogniser.operation_timeout = self.cfg.online_timeout
        audio = sr.AudioData(pcm, self.sample_rate, 2)

        for code in self.LANG_CODES:
            try:
                text = recogniser.recognize_google(audio, language=code)
            except sr.UnknownValueError:
                continue
            except sr.RequestError as exc:
                log.warning("online recognition unavailable: %s", exc)
                return None
            except Exception:
                log.debug("online recognition failed for %s", code, exc_info=True)
                continue

            if text and text.strip():
                # The free endpoint returns no score; treat a hit as fairly good.
                return Transcript(text.strip(), 0.85, "en", source="online")
        return None


_LAST_NET_CHECK = (0.0, False)


def is_online(timeout: float = 1.0, cache_seconds: float = 20.0) -> bool:
    """Cheap connectivity probe, cached so we do not hammer it per utterance."""
    global _LAST_NET_CHECK
    now = time.monotonic()
    checked_at, result = _LAST_NET_CHECK
    if now - checked_at < cache_seconds:
        return result
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
            result = True
    except OSError:
        result = False
    _LAST_NET_CHECK = (now, result)
    return result


# --------------------------------------------------------------------------- #
# The hybrid front end
# --------------------------------------------------------------------------- #
class HybridSTT:
    def __init__(self, speech: SpeechConfig, audio: AudioConfig) -> None:
        self.speech = speech
        self.audio = audio
        self.whisper: Optional[WhisperCppRecognizer] = None
        self.recognizers: List[VoskRecognizer] = []
        self.online = OnlineRecognizer(speech, audio.sample_rate)
        self._loaded = False

    # ------------------------------------------------------------- loading
    def load(self) -> None:
        if self._loaded or self.speech.mode == "online":
            self._loaded = True
            return

        from ..config import Config

        cfg = Config()  # only used for its model_path() helpers
        cfg.speech = self.speech

        # whisper.cpp first, when it is wanted and actually installed. "auto"
        # means "use it if it is there", so an installation that has not fetched
        # the GGML model yet carries on with Vosk and says nothing alarming.
        engine = (self.speech.engine or "auto").lower()
        if engine in {"auto", "whisper"}:
            whisper = WhisperCppRecognizer(
                self.speech, cfg.whisper_model_path(), self.audio.sample_rate
            )
            if whisper.load():
                self.whisper = whisper
            elif engine == "whisper":
                log.error(
                    "speech.engine is 'whisper' but whisper.cpp is not usable; "
                    "falling back to Vosk."
                )

        # Vosk is still loaded even when whisper leads: it backs whisper up when
        # an utterance comes back empty, and it is what feeds the live partial
        # text to the overlay, which whisper cannot do mid-utterance.
        rec = VoskRecognizer(cfg.model_path(), self.audio.sample_rate, "en")
        if rec.load():
            self.recognizers.append(rec)

        if not self.has_offline and self.speech.mode == "offline":
            log.error(
                "No offline recogniser could be loaded and mode is 'offline'. "
                "Run 'blackvoice setup' to download the models."
            )
        self._loaded = True

    @property
    def has_offline(self) -> bool:
        if self.whisper is not None and self.whisper.ready:
            return True
        return any(r.ready for r in self.recognizers)

    @property
    def offline_engine(self) -> str:
        """Which offline engine would actually be used: whisper, vosk or none."""
        if self.whisper is not None and self.whisper.ready:
            return "whisper"
        return "vosk" if any(r.ready for r in self.recognizers) else "none"

    # --------------------------------------------------------- transcribing
    def transcribe_pcm(self, pcm: bytes) -> Transcript:
        """Recognise a complete utterance held in memory."""
        self.load()
        best = Transcript("", 0.0)

        # Tier one. Whisper's answer is taken whole or not at all: it is the
        # only engine here that can write a code-switched sentence, so there is
        # nothing to be gained by scoring it against a monolingual guess.
        if self.speech.mode != "online" and self.whisper is not None:
            result = self.whisper.transcribe(pcm)
            if result:
                return result

        if self.speech.mode != "online":
            for rec in self.recognizers:
                rec.reset()
                rec.accept(pcm)
                result = rec.final()
                if result and result.confidence > best.confidence:
                    best = result

        if self._should_fall_back(best):
            log.debug(
                "falling back online (offline gave %r @ %.2f)", best.text, best.confidence
            )
            remote = self.online.transcribe(pcm)
            if remote:
                return remote

        return best

    def _should_fall_back(self, offline: Transcript) -> bool:
        if self.speech.mode == "offline":
            return False
        if self.speech.mode == "online":
            return True
        # hybrid
        if not self.has_offline:
            return is_online()
        unsure = (not offline) or offline.confidence < self.speech.fallback_confidence
        return unsure and is_online()

    # ------------------------------------------------------------ streaming
    def listen_once(
        self,
        mic: Microphone,
        on_partial=None,
        on_level=None,
        max_seconds: Optional[float] = None,
    ) -> Transcript:
        """Record until the speaker goes quiet, then transcribe the whole thing.

        Endpointing is done on the RMS level rather than on Vosk's own utterance
        boundaries, so it behaves the same way when only the online path exists.

        ``max_seconds`` overrides ``AudioConfig.max_command_seconds`` for this
        call only - used for the optional post-reply follow-up listen
        (WakeConfig.followup_seconds), which should give up quickly and
        quietly when nobody says anything more, rather than holding the mic
        open for a full command-length timeout after every single reply on
        the chance a follow-up might be coming.
        """
        self.load()
        for rec in self.recognizers:
            rec.reset()

        chunks: List[bytes] = []
        elapsed = 0.0
        last_partial = ""
        endpointer = Endpointer(self.audio)
        limit = self.audio.max_command_seconds if max_seconds is None else max_seconds

        while elapsed < limit:
            block = mic.read(timeout=1.0)
            if block is None:
                if endpointer.heard_speech:
                    break
                continue

            chunks.append(block)
            elapsed += mic.seconds_per_block
            levels = endpointer.feed(block)
            if on_level:
                for level in levels:
                    on_level(level)

            # Live partial text for the overlay, from the first model only.
            if on_partial and self.recognizers:
                primary = self.recognizers[0]
                primary.accept(block)
                partial = primary.partial()
                if partial and partial != last_partial:
                    last_partial = partial
                    on_partial(partial)

            if endpointer.should_stop:
                break

        if not endpointer.heard_speech:
            return Transcript("", 0.0)

        return self.transcribe_pcm(b"".join(chunks))
