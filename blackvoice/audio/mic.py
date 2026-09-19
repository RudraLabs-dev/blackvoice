"""Microphone capture.

Wraps ``sounddevice`` in a small blocking iterator that yields raw 16-bit mono
PCM blocks, plus helpers for level metering and silence detection. Import of
``sounddevice`` is deferred so that the rest of Black Voice (config, CLI help,
text mode) still works on a machine with no PortAudio installed.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Iterator, List, Optional, Tuple

from ..config import AudioConfig

log = logging.getLogger(__name__)


class MicrophoneUnavailable(RuntimeError):
    """Raised when no usable input device could be opened."""


def _import_sounddevice():
    try:
        import sounddevice as sd  # noqa: WPS433 - deliberate lazy import
    except (ImportError, OSError) as exc:
        raise MicrophoneUnavailable(
            "sounddevice/PortAudio is not available. "
            "Install it with:  sudo apt install portaudio19-dev && pip install sounddevice"
        ) from exc
    return sd


def list_devices() -> List[dict]:
    """Return the input devices PortAudio can see."""
    sd = _import_sounddevice()
    devices = []
    for index, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append(
                {
                    "index": index,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": int(dev.get("default_samplerate", 0)),
                }
            )
    return devices


def rms_level(block: bytes) -> float:
    """Root-mean-square loudness of an int16 block, normalised to 0..1."""
    try:
        import numpy as np
    except ImportError:
        return 0.0
    if not block:
        return 0.0
    samples = np.frombuffer(block, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2)) / 32768.0)


class Microphone:
    """A restartable microphone stream.

    Usage::

        with Microphone(cfg) as mic:
            for block in mic.blocks():
                ...
    """

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=64)
        self._stream = None
        self._stop = threading.Event()

    # ----------------------------------------------------------- lifecycle
    def open(self) -> "Microphone":
        sd = _import_sounddevice()
        self._stop.clear()

        def _callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("audio status: %s", status)
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                # Dropping a block is better than blocking the audio thread.
                log.debug("audio queue full, dropping a block")

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.cfg.sample_rate,
                blocksize=self.cfg.block_size,
                device=self.cfg.input_device,
                dtype="int16",
                channels=1,
                callback=_callback,
            )
            self._stream.start()
        except Exception as exc:
            raise MicrophoneUnavailable(f"could not open microphone: {exc}") from exc

        log.info(
            "microphone open (%d Hz, block %d, device %s)",
            self.cfg.sample_rate,
            self.cfg.block_size,
            self.cfg.input_device if self.cfg.input_device is not None else "default",
        )
        return self

    def close(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.debug("error while closing the audio stream", exc_info=True)
            self._stream = None
        self.drain()

    def __enter__(self) -> "Microphone":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -------------------------------------------------------------- reading
    def drain(self) -> None:
        """Throw away buffered audio, e.g. after the assistant finished speaking."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def read(self, timeout: float = 0.5) -> Optional[bytes]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def blocks(self) -> Iterator[bytes]:
        """Yield audio blocks until :meth:`close` is called."""
        while not self._stop.is_set():
            block = self.read()
            if block:
                yield block

    @property
    def seconds_per_block(self) -> float:
        return self.cfg.block_size / float(self.cfg.sample_rate)


class Endpointer:
    """Calibrated, hysteresis-based end-of-utterance detection.

    Comparing one fixed threshold against a whole 0.5s block, the way capture
    used to work, has two problems. First, any stray noise at or above
    ``AudioConfig.silence_threshold`` resets the silence clock to zero rather
    than merely pausing it, so a room with intermittent ambient noise can keep
    "hearing speech" long after the user actually stopped - in the worst case
    all the way to the hard ``max_command_seconds`` cap. Second, testing only
    once per 0.5s block means the silence clock cannot even start until up to
    0.5s after the user stopped, which alone accounts for a large share of
    ``silence_timeout``'s old default.

    This fixes both without a new dependency (a real VAD library like
    ``webrtcvad`` ships no wheels at all - see the packaging constraint this
    project already lives under): calibrate a noise floor from the first
    moment of listening instead of trusting one number for every room,
    classify loudness into three bands instead of one line, and test more
    often than once per raw mic block.
    """

    #: Once speech has actually been heard, a block has to reach this multiple
    #: of the effective threshold to count as confidently still talking and
    #: reset the silence clock to zero. Anything from the threshold itself up
    #: to this line is ambiguous - the tail of a word decaying towards
    #: silence, or a stray ambient blip that happens to cross the line the
    #: same way the reported bug describes - and only pauses the clock rather
    #: than restarting it, so a handful of such blips delay the countdown
    #: instead of preventing it from ever completing. Before any speech has
    #: been heard the plain threshold decides on its own, so first-utterance
    #: sensitivity is unchanged.
    _CONFIRM_MULTIPLE = 1.5

    #: however loud the calibration window was, never raise the effective
    #: threshold more than this multiple of the configured floor - a single
    #: loud noise during calibration (a door, a bark) must not deafen the
    #: rest of the utterance
    _MAX_THRESHOLD_MULTIPLE = 5.0

    #: percentile of calibration-window levels taken as the noise floor. Low
    #: enough to see past a burst of early speech overlapping the window - if
    #: the user starts talking the instant the wake chime ends, most of the
    #: window is still ambient and a low percentile finds it anyway.
    _CALIBRATION_PERCENTILE = 0.35

    #: sub-block duration the mic's own block is sliced into, so the silence
    #: clock is not blind between one 0.5s read and the next
    _SUB_BLOCK_SECONDS = 0.1

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self.threshold = cfg.silence_threshold
        self.heard_speech = False
        self.silent_for = 0.0
        self.noise_floor: Optional[float] = None
        self._calibrating = cfg.calibrate_noise and cfg.calibration_seconds > 0
        self._calibration_remaining = cfg.calibration_seconds
        self._calibration_levels: List[float] = []

    @property
    def calibrated(self) -> bool:
        return not self._calibrating

    def _finish_calibration(self) -> None:
        self._calibrating = False
        if not self._calibration_levels:
            return
        levels = sorted(self._calibration_levels)
        index = min(int(len(levels) * self._CALIBRATION_PERCENTILE), len(levels) - 1)
        floor = levels[index]
        self.noise_floor = floor
        cap = self.cfg.silence_threshold * self._MAX_THRESHOLD_MULTIPLE
        self.threshold = min(
            cap, max(self.cfg.silence_threshold, floor * self.cfg.calibration_margin)
        )
        log.debug(
            "endpointer calibrated: floor=%.4f -> threshold=%.4f (configured %.4f)",
            floor, self.threshold, self.cfg.silence_threshold,
        )

    def _sub_blocks(self, block: bytes) -> Iterator[Tuple[bytes, float]]:
        bytes_per_sample = 2  # int16 mono, the only format Microphone captures
        sub_bytes = max(1, int(self.cfg.sample_rate * self._SUB_BLOCK_SECONDS)) * bytes_per_sample
        if sub_bytes >= len(block):
            yield block, len(block) / bytes_per_sample / self.cfg.sample_rate
            return
        for start in range(0, len(block), sub_bytes):
            chunk = block[start:start + sub_bytes]
            yield chunk, len(chunk) / bytes_per_sample / self.cfg.sample_rate

    def _update(self, level: float, seconds: float) -> None:
        if self._calibrating:
            self._calibration_levels.append(level)
            self._calibration_remaining -= seconds
            if self._calibration_remaining <= 1e-9:  # float accumulation, not a real remainder
                self._finish_calibration()
            # A calibration window still has to notice real speech - someone
            # who starts talking the instant the chime ends must not be timed
            # out while it happens, even though the floor computed afterwards
            # may end up partly informed by their own voice.
            if level >= self.threshold:
                self.heard_speech = True
                self.silent_for = 0.0
            return

        if not self.heard_speech:
            if level >= self.threshold:
                self.heard_speech = True
                self.silent_for = 0.0
            return

        if level >= self.threshold * self._CONFIRM_MULTIPLE:
            self.silent_for = 0.0
        elif level >= self.threshold:
            pass  # ambiguous: hold the clock rather than reset or advance it
        else:
            self.silent_for += seconds

    def feed(self, block: bytes) -> List[float]:
        """Process one raw mic block; return the level of each sub-block."""
        levels = []
        for sub, seconds in self._sub_blocks(block):
            level = rms_level(sub)
            levels.append(level)
            self._update(level, seconds)
        return levels

    @property
    def should_stop(self) -> bool:
        return self.heard_speech and self.silent_for >= self.cfg.silence_timeout
