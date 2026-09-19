"""Downloading Piper voices.

espeak-ng is instant and tiny, which is why it is the fallback. It is also a
formant synthesiser from the 1990s lineage and many people simply cannot
follow it, which makes a voice assistant useless however well it hears.

Piper is a neural synthesiser that sounds like a person. Its voices are about
60 MB each and cannot ship in the distribution packages for the same reasons as
the speech models - a post-install script must not use the network, and it runs
as root while the voice belongs to a user - so they are fetched on demand.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .config import DATA_DIR

log = logging.getLogger(__name__)

VOICES_DIR = DATA_DIR / "voices"

_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

#: name -> (language, repository path without extension, human description)
VOICES: Dict[str, Tuple[str, str, str]] = {
    "en_US-lessac-medium": (
        "en", "en/en_US/lessac/medium/en_US-lessac-medium",
        "American English, clear and neutral",
    ),
    "en_US-amy-medium": (
        "en", "en/en_US/amy/medium/en_US-amy-medium",
        "American English, warmer",
    ),
    "en_GB-alba-medium": (
        "en", "en/en_GB/alba/medium/en_GB-alba-medium",
        "British English",
    ),
}

#: What to fetch when the user asks for a better voice and says nothing more.
DEFAULT_VOICES = {"en": "en_US-lessac-medium"}

ProgressFn = Callable[[str, int, int], None]


def voice_path(name: str) -> Path:
    """Where a voice's model file lives once installed."""
    return VOICES_DIR / f"{name}.onnx"


def installed(name: str) -> bool:
    model = voice_path(name)
    return model.exists() and model.with_suffix(".onnx.json").exists()


def available() -> Dict[str, Tuple[str, str, str]]:
    return dict(VOICES)


def download(
    name: str,
    on_progress: Optional[ProgressFn] = None,
    timeout: float = 120.0,
) -> bool:
    """Fetch one voice. Returns True when it is ready to use."""
    if name not in VOICES:
        log.error("unknown voice %r", name)
        return False
    if installed(name):
        return True

    try:
        import requests
    except ImportError:
        log.error("the requests library is needed to download voices")
        return False

    _lang, repo_path, _desc = VOICES[name]
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    # The config file is tiny; the model is the part worth reporting on.
    for suffix in (".onnx.json", ".onnx"):
        url = f"{_BASE}/{repo_path}{suffix}"
        target = VOICES_DIR / f"{name}{suffix}"
        if target.exists():
            continue

        partial = target.with_suffix(target.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                done = 0
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        handle.write(chunk)
                        done += len(chunk)
                        if on_progress and suffix == ".onnx":
                            on_progress(name, done, total)
            # Only put it in place once it arrived whole, so an interrupted
            # download never looks like an installed voice.
            partial.replace(target)
        except Exception as exc:
            log.error("could not download %s%s: %s", name, suffix, exc)
            partial.unlink(missing_ok=True)
            return False

    log.info("installed voice %s", name)
    return True


def ensure(
    language: str,
    name: Optional[str] = None,
    on_progress: Optional[ProgressFn] = None,
    on_message: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    """Make a voice available for ``language``; return its path, or None."""
    name = name or DEFAULT_VOICES.get(language)
    if not name:
        return None

    if installed(name):
        return voice_path(name)

    if on_message:
        on_message(f"Downloading the {name} voice (about 60 MB). This happens once.")

    if not download(name, on_progress=on_progress):
        if on_message:
            on_message(
                "The voice could not be downloaded. Black Voice will keep using "
                "espeak-ng."
            )
        return None

    if on_message:
        on_message("Voice installed.")
    return voice_path(name)
