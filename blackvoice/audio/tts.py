"""Text-to-speech.

Tries the best engine available on the machine, in this order:

1. ``piper``    - neural, natural sounding, needs a downloaded voice model
2. ``espeak-ng``- tiny, instant, no download needed
3. ``spd-say``  - speech-dispatcher, present on most desktop distros
4. ``pyttsx3``  - pure-Python wrapper, last resort

Speaking happens on a worker thread so a long sentence never blocks the audio
loop, and :meth:`Speaker.stop` cuts the current utterance off mid-word.
"""

from __future__ import annotations

import functools
import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Optional

from ..config import VoiceConfig

log = logging.getLogger(__name__)


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


@functools.lru_cache(maxsize=8)
def _piper_supports_espeak_data(binary: str) -> bool:
    """Whether this ``piper`` build understands ``--espeak_data`` and treats
    ``--output_file -`` as "write to stdout".

    Two unrelated projects both install a program called ``piper`` on PATH.
    The legacy `rhasspy/piper <https://github.com/rhasspy/piper>`_ binary -
    what :mod:`blackvoice.piper_install` fetches - takes both. The modern
    ``piper-tts`` PyPI package (``pip``/``pipx install piper-tts``, the
    ``piper1-gpl`` rewrite) does not: confirmed live against a real
    ``pipx``-installed copy, its ``--help`` has no ``--espeak_data`` at all,
    and ``--output-file`` is a real file path with no "-" convention - it
    defaults to stdout only when the flag is left out entirely. Get either
    of those wrong against that build and it does not error out cleanly; it
    still runs, just not on the text it was actually asked to speak - which
    is what made this so confusing to chase from a report of "it reads out
    a path" alone, with no indication the binary itself was the mismatch.

    Detected once per binary path by asking it, rather than assumed either
    way, since a future release of either project could change this.
    """
    try:
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell; keep today's behaviour rather than guess
    help_text = (result.stdout or "") + (result.stderr or "")
    return "--espeak_data" in help_text or "--espeak-data" in help_text


def detect_engine() -> str:
    """Pick the best engine present on this system.

    Piper is preferred because it is the only one that sounds like a person,
    but only when its binary exists - a configured voice is useless without it.
    """
    from .. import piper_install

    if piper_install.find_binary():
        return "piper"
    if _which("espeak-ng") or _which("espeak"):
        return "espeak"
    if _which("spd-say"):
        return "spd-say"
    try:
        import pyttsx3  # noqa: F401
        return "pyttsx3"
    except ImportError:
        return "none"


class Speaker:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self.engine = cfg.engine if cfg.engine != "auto" else detect_engine()
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._proc: Optional[subprocess.Popen] = None
        self._pyttsx = None
        self._lock = threading.Lock()
        self.speaking = threading.Event()
        # Started last: the worker touches every attribute above.
        self._worker = threading.Thread(target=self._run, name="tts", daemon=True)
        self._worker.start()
        log.info("text-to-speech engine: %s", self.engine)

    # ------------------------------------------------------------- public
    def say(self, text: str) -> None:
        """Queue ``text`` to be spoken. Returns immediately."""
        text = (text or "").strip()
        if text and self.engine != "none":
            self._queue.put(text)

    def stop(self) -> None:
        """Interrupt whatever is being said and clear the queue."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass
            if self._pyttsx is not None:
                try:
                    self._pyttsx.stop()
                except Exception:
                    log.debug("pyttsx3 stop failed", exc_info=True)

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)

    def wait_until_idle(self, timeout: float = 30.0) -> bool:
        """Block until the queue is empty and nothing is being spoken."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.empty() and not self.speaking.is_set():
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------- worker
    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            log.info("speaking (%s): %r", self.engine, text)
            self.speaking.set()
            try:
                self._speak_now(text)
            except Exception:
                log.exception("speech failed for %r", text[:60])
            finally:
                self.speaking.clear()
                try:
                    self._queue.task_done()
                except ValueError:
                    pass

    def _speak_now(self, text: str) -> None:
        if self.engine == "piper":
            self._speak_piper(text)
        elif self.engine == "espeak":
            self._speak_espeak(text)
        elif self.engine == "spd-say":
            self._speak_spd(text)
        elif self.engine == "pyttsx3":
            self._speak_pyttsx3(text)
        else:
            print(f"[Black Voice] {text}")

    def _run_proc(self, argv, stdin_text: Optional[str] = None) -> None:
        with self._lock:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        proc = self._proc
        try:
            proc.communicate(
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            with self._lock:
                self._proc = None

    def _piper_voice_for(self) -> Optional[str]:
        """Which .onnx to speak this text with, fetching it if need be."""
        if self.cfg.piper_model:
            return self.cfg.piper_model

        from .. import voices

        name = self.cfg.piper_voice_en

        if voices.installed(name):
            return str(voices.voice_path(name))

        log.warning("piper voice %r not found installed; may re-fetch it", name)
        if not self.cfg.piper_auto_download:
            return None

        path = voices.ensure("en", name, on_message=lambda m: log.info("%s", m))
        return str(path) if path else None

    def _speak_piper(self, text: str) -> None:
        model = self._piper_voice_for()
        if not model:
            log.warning("no piper voice available; falling back to espeak-ng")
            self.engine = "espeak" if (_which("espeak-ng") or _which("espeak")) else "none"
            return self._speak_now(text)

        from .. import piper_install

        binary = piper_install.find_binary()
        if not binary:
            log.warning("no piper binary available; falling back to espeak-ng")
            self.engine = "espeak" if (_which("espeak-ng") or _which("espeak")) else "none"
            return self._speak_now(text)

        player = _which("aplay") or _which("paplay") or _which("pw-play")
        if not player:
            log.warning("no audio player found for piper output")
            return

        argv = [binary, "--model", model]
        if _piper_supports_espeak_data(binary):
            # The legacy rhasspy/piper CLI: "-" for stdout is its own
            # convention, and phonemising anything but the plainest English
            # needs --espeak_data - a relative lookup would depend on the
            # assistant's current working directory, which nothing here
            # guarantees.
            argv += ["--output_file", "-"]
            data_dir = piper_install.espeak_data_dir()
            if data_dir:
                argv += ["--espeak_data", str(data_dir)]
        # else: the modern piper-tts (piper1-gpl) CLI - leaving
        # --output-file out entirely is how *it* asks for stdout, and it has
        # no --espeak_data equivalent to pass.

        log.debug("piper argv: %s", argv)
        started = time.monotonic()
        piper = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # --output_file - still writes a complete WAV file (RIFF header and
        # all) to stdout, not headerless raw PCM - confirmed by inspecting
        # the actual bytes. Telling aplay to expect raw S16_LE at a hardcoded
        # 22050 Hz used to make it play the 44-byte header as if it were
        # audio, and would have played the wrong pitch/speed entirely for any
        # Piper voice whose native rate is not 22050 Hz. Every one of these
        # players auto-detects a WAV stream from its header, so the fix is to
        # simply stop telling them what to expect.
        play_args = [player, "-"]
        with self._lock:
            self._proc = subprocess.Popen(
                play_args,
                stdin=piper.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if piper.stdout:
            piper.stdout.close()
        piper.stdin.write(text.encode("utf-8"))
        piper.stdin.close()

        # Waited on separately (piper first, then the player) rather than
        # only timing the pair together, so a slow reply's log says which
        # half is actually slow: piper synthesising the WAV, or the player
        # writing it to the audio device - "12 seconds either way" gave no
        # way to tell those apart.
        try:
            piper.wait(timeout=30)
        except subprocess.TimeoutExpired:
            piper.kill()
        synthesised = time.monotonic()
        log.debug(
            "piper synthesis took %.2fs (exit code %s)",
            synthesised - started, piper.returncode,
        )

        try:
            self._proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        finally:
            with self._lock:
                self._proc = None
            log.debug(
                "playback took %.2fs (%.2fs total)",
                time.monotonic() - synthesised, time.monotonic() - started,
            )

    def _speak_espeak(self, text: str) -> None:
        binary = _which("espeak-ng") or _which("espeak")
        voice = self.cfg.voice_en
        argv = [
            binary,
            "-v", voice,
            "-s", str(self.cfg.rate),
            "-a", str(int(max(0.0, min(1.0, self.cfg.volume)) * 200)),
            "--stdin",
        ]
        self._run_proc(argv, stdin_text=text)

    def _speak_spd(self, text: str) -> None:
        # spd-say maps rate to -100..100; our config is words per minute.
        rate = max(-100, min(100, int((self.cfg.rate - 175) / 1.5)))
        self._run_proc(["spd-say", "--wait", "--rate", str(rate), text])

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            import pyttsx3
        except ImportError:
            self.engine = "none"
            return print(f"[Black Voice] {text}")

        # pyttsx3 engines are not reusable across threads on every backend, so
        # a fresh one per utterance is the reliable option.
        engine = pyttsx3.init()
        engine.setProperty("rate", self.cfg.rate)
        engine.setProperty("volume", self.cfg.volume)
        with self._lock:
            self._pyttsx = engine
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            with self._lock:
                self._pyttsx = None
            try:
                engine.stop()
            except Exception:
                pass
