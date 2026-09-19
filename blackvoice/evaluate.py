"""Measuring how well the speech front end actually works.

A voice assistant cannot be tuned by ear. Swapping a model changes hundreds of
utterances at once and the only honest way to compare two of them is to run both
over the same recordings and count. This module records that corpus and scores
it.

Two numbers come out, and the second one matters more:

**Word error rate** is the usual measure - edits per reference word, pooled over
the corpus rather than averaged per sample, so a long sentence weighs more than a
two-word one, as it should.

**Intent accuracy** is the number to optimise. This assistant does not need a
perfect transcript, it needs the right skill to run: if *"volume chalis karo"*
comes back as *"volume 40 caro"* the router still reaches ``set_volume`` and the
user cannot tell anything went wrong. A backend with the worse WER can easily be
the better one to ship, and only this column will say so.

The expected intent is derived by routing the *reference* text, which is ground
truth, so a recording does not have to be labelled by hand. That also frames the
question precisely: did the recognition error change the routing decision? A
reference that does not route to a command is reported separately, because such a
sample says nothing about command accuracy.
"""

from __future__ import annotations

import json
import logging
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .audio.stt import (
    OnlineRecognizer,
    Transcript,
    VoskRecognizer,
    WhisperCppRecognizer,
)
from .audio.wake import strip_wake_word
from .config import DATA_DIR, Config
from .nlu.intents import normalise
from .nlu.router import Router

log = logging.getLogger(__name__)

#: Default home for a corpus, alongside the models it is used to compare.
EVAL_DIR = DATA_DIR / "eval"
MANIFEST_NAME = "manifest.jsonl"

#: The intent the router falls back to when nothing matched. A reference that
#: lands here is a question, not a command, and is scored separately.
FALLBACK_INTENT = "ask"

#: Backends the harness can score, in the order a report lists them.
BACKENDS = ("whisper", "vosk", "online")


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
@dataclass
class Sample:
    """One recording and what was actually said in it."""

    audio: str
    reference: str
    #: Set only to override what routing the reference produces.
    intent: str = ""
    note: str = ""

    def path(self, root: Path) -> Path:
        p = Path(self.audio).expanduser()
        return p if p.is_absolute() else root / self.audio


def load_corpus(root: Path) -> List[Sample]:
    """Read ``manifest.jsonl``. Missing file means an empty corpus."""
    manifest = root / MANIFEST_NAME
    if not manifest.exists():
        return []

    samples: List[Sample] = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("%s line %d is not valid JSON: %s", manifest, number, exc)
            continue
        if not raw.get("audio") or not raw.get("reference"):
            log.warning("%s line %d has no audio or reference", manifest, number)
            continue
        samples.append(
            Sample(
                audio=str(raw["audio"]),
                reference=str(raw["reference"]),
                intent=str(raw.get("intent") or ""),
                note=str(raw.get("note") or ""),
            )
        )
    return samples


def append_sample(root: Path, sample: Sample) -> None:
    """Add one line to the manifest, creating it if need be."""
    root.mkdir(parents=True, exist_ok=True)
    record = {"audio": sample.audio, "reference": sample.reference}
    if sample.intent:
        record["intent"] = sample.intent
    if sample.note:
        record["note"] = sample.note
    with (root / MANIFEST_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #
class WavMismatch(RuntimeError):
    """A recording is not in the format the recognisers expect."""


def read_wav(path: Path, sample_rate: int) -> bytes:
    """Load mono 16-bit PCM at ``sample_rate``.

    Nothing is resampled on purpose. Resampling needs a dependency this project
    does not carry, and quietly scoring a backend on audio that has been through
    a conversion the live microphone path never applies would make the numbers
    mean something other than what they claim.
    """
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if (channels, width, rate) != (1, 2, sample_rate):
        raise WavMismatch(
            f"{path.name} is {channels}ch/{width * 8}-bit/{rate} Hz; "
            f"this corpus needs 1ch/16-bit/{sample_rate} Hz"
        )
    return frames


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


def capture_utterance(mic, audio_cfg, on_level: Optional[Callable] = None) -> bytes:
    """Record until the speaker stops, and return the raw PCM.

    This deliberately repeats the *loop* in :meth:`HybridSTT.listen_once`
    rather than calling it: that method transcribes as it goes and drives the
    overlay's partial text, and neither belongs in a recording tool. But the
    endpointing decision itself - :class:`~blackvoice.audio.mic.Endpointer` -
    is shared, not duplicated: a corpus recorded with different endpointing
    than the live assistant uses would predict nothing about it.
    """
    from .audio.mic import Endpointer

    chunks: List[bytes] = []
    elapsed = 0.0
    endpointer = Endpointer(audio_cfg)

    while elapsed < audio_cfg.max_command_seconds:
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

        if endpointer.should_stop:
            break

    return b"".join(chunks) if endpointer.heard_speech else b""


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    """Edit distance between two word sequences."""
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(
                min(
                    previous[j] + 1,                       # deletion
                    current[j - 1] + 1,                    # insertion
                    previous[j - 1] + (left != right),     # substitution
                )
            )
        previous = current
    return previous[-1]


def word_edits(reference: str, hypothesis: str) -> Tuple[int, int]:
    """``(edits, reference_words)`` after the router's own normalisation.

    Scoring on :func:`normalise` is the fair comparison: it is exactly the text
    the router will see, so punctuation and casing that no rule looks at are not
    counted as errors against a backend that happens to emit them.
    """
    ref = normalise(reference).split()
    hyp = normalise(hypothesis).split()
    return _levenshtein(ref, hyp), len(ref)


@dataclass
class SampleResult:
    sample: Sample
    hypothesis: str
    seconds: float
    edits: int
    ref_words: int
    expected_intent: str
    got_intent: str
    error: str = ""

    @property
    def wer(self) -> float:
        if not self.ref_words:
            return 0.0
        return self.edits / self.ref_words

    @property
    def exact(self) -> bool:
        return normalise(self.sample.reference) == normalise(self.hypothesis)

    @property
    def is_command(self) -> bool:
        """False when the reference itself does not route to a command."""
        return self.expected_intent != FALLBACK_INTENT

    @property
    def intent_ok(self) -> bool:
        return self.expected_intent == self.got_intent


@dataclass
class BackendResult:
    backend: str
    results: List[SampleResult] = field(default_factory=list)
    unavailable: str = ""

    @property
    def wer(self) -> float:
        """Corpus word error rate: pooled edits over pooled reference words."""
        words = sum(r.ref_words for r in self.results)
        if not words:
            return 0.0
        return sum(r.edits for r in self.results) / words

    @property
    def exact_match(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.exact) / len(self.results)

    @property
    def commands(self) -> List[SampleResult]:
        return [r for r in self.results if r.is_command]

    @property
    def intent_accuracy(self) -> float:
        rows = self.commands
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.intent_ok) / len(rows)

    @property
    def median_seconds(self) -> float:
        times = sorted(r.seconds for r in self.results)
        if not times:
            return 0.0
        middle = len(times) // 2
        if len(times) % 2:
            return times[middle]
        return (times[middle - 1] + times[middle]) / 2

    @property
    def failures(self) -> List[SampleResult]:
        """Command samples the router got wrong - the rows worth reading."""
        return [r for r in self.commands if not r.intent_ok]


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
#: A backend takes one utterance's PCM and returns a transcript.
Runner = Callable[[bytes], Transcript]


def build_backend(name: str, config: Config) -> Tuple[Optional[Runner], str]:
    """Return ``(runner, reason_it_is_unavailable)`` for one backend.

    Each engine is driven directly rather than through :class:`HybridSTT`, whose
    whole job is to hide one behind another. Comparing them requires the
    opposite.
    """
    rate = config.audio.sample_rate

    if name == "whisper":
        rec = WhisperCppRecognizer(config.speech, config.whisper_model_path(), rate)
        if not rec.load():
            return None, "whisper.cpp binary or model not found"
        return rec.transcribe, ""

    if name == "vosk":
        loaded: List[VoskRecognizer] = []
        rec = VoskRecognizer(config.model_path(), rate, "en")
        if rec.load():
            loaded.append(rec)
        if not loaded:
            return None, "no Vosk model could be loaded"

        def run_vosk(pcm: bytes) -> Transcript:
            # The confidence race the live hybrid path uses, reproduced here so
            # the comparison is against what actually ships.
            best = Transcript("", 0.0)
            for rec in loaded:
                rec.reset()
                rec.accept(pcm)
                result = rec.final()
                if result and result.confidence > best.confidence:
                    best = result
            return best

        return run_vosk, ""

    if name == "online":
        online = OnlineRecognizer(config.speech, rate)

        def run_online(pcm: bytes) -> Transcript:
            return online.transcribe(pcm) or Transcript("", 0.0, source="online")

        return run_online, ""

    return None, f"unknown backend {name!r}"


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def score(
    samples: Iterable[Sample],
    root: Path,
    backend: str,
    config: Config,
    router: Optional[Router] = None,
    on_sample: Optional[Callable[[SampleResult], None]] = None,
) -> BackendResult:
    """Run one backend over the corpus."""
    runner, unavailable = build_backend(backend, config)
    if runner is None:
        return BackendResult(backend, unavailable=unavailable)

    router = router or Router()
    phrases = config.wake.phrases
    result = BackendResult(backend)

    for sample in samples:
        expected = router.route(strip_wake_word(sample.reference, phrases)).name
        if sample.intent:
            expected = sample.intent

        try:
            pcm = read_wav(sample.path(root), config.audio.sample_rate)
        except (WavMismatch, OSError, wave.Error) as exc:
            row = SampleResult(
                sample, "", 0.0, 0, 0, expected, "", error=str(exc)
            )
            result.results.append(row)
            if on_sample:
                on_sample(row)
            continue

        started = time.monotonic()
        transcript = runner(pcm)
        seconds = time.monotonic() - started

        heard = strip_wake_word(transcript.text, phrases)
        got = router.route(heard).name if heard else ""
        edits, ref_words = word_edits(sample.reference, transcript.text)

        row = SampleResult(
            sample=sample,
            hypothesis=transcript.text,
            seconds=seconds,
            edits=edits,
            ref_words=ref_words,
            expected_intent=expected,
            got_intent=got,
        )
        result.results.append(row)
        if on_sample:
            on_sample(row)

    return result


def compare(
    root: Path,
    backends: Sequence[str],
    config: Config,
    on_backend: Optional[Callable[[str], None]] = None,
    on_sample: Optional[Callable[[SampleResult], None]] = None,
) -> Dict[str, BackendResult]:
    """Score every backend over the same corpus."""
    samples = load_corpus(root)
    router = Router()
    out: Dict[str, BackendResult] = {}
    for backend in backends:
        if on_backend:
            on_backend(backend)
        out[backend] = score(
            samples, root, backend, config, router=router, on_sample=on_sample
        )
    return out


# --------------------------------------------------------------------------- #
# Prompts to record
# --------------------------------------------------------------------------- #
#: A starting corpus spanning the intent space this assistant handles.
DEFAULT_PROMPTS: List[str] = [
    # Commands
    "black open firefox",
    "black close the terminal",
    "black volume 40",
    "black mute the sound",
    "black brightness up",
    "black take a screenshot",
    "black lock the screen",
    "black turn off wifi",
    "black turn on bluetooth",
    "black open downloads",
    "black what is the time",
    "black set a timer for five minutes",
    "black search for python tutorials",
    "black next track",
    "black pause the music",
    "black how much disk space is left",
    "black take a note buy milk",
    # Questions, which should reach the AI rather than a command
    "black who wrote the mahabharata",
    "black what is the weather like today",
    "black explain what a kernel is",
    # Near misses worth having in the corpus
    "black open fire fox",
    "black volume forty percent",
    "black stop",
]


def load_prompts(path: Optional[Path]) -> List[str]:
    """Prompts from a file, one per line, or the built-in list."""
    if path is None:
        return list(DEFAULT_PROMPTS)
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def next_index(root: Path) -> int:
    """One past the highest numbered recording, so a corpus can be extended."""
    highest = 0
    for wav in root.glob("*.wav"):
        try:
            highest = max(highest, int(wav.stem))
        except ValueError:
            continue
    return highest + 1
