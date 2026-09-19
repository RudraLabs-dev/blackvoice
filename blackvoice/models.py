"""Downloading and locating the offline speech model.

The model is ~40 MB and cannot ship inside the distribution packages, because
a package's post-install script must not touch the network — installs have to
work in chroots, containers and offline mirrors, and that script runs as root
while the models belong to a user.

So they are fetched on first run instead: as the actual user, into that user's
own data directory, at a moment when a network connection is a reasonable thing
to expect.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

from .config import MODELS_DIR, Config

log = logging.getLogger(__name__)

#: Vosk small model — tens of megabytes, tuned for command recognition.
MODEL_URLS = {
    "en": (
        "vosk-model-small-en-us-0.15",
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    ),
}

#: whisper.cpp models, as single GGML files - there is nothing to unpack.
#: The q5_1 quantisations are the useful ones here: they are a third of the
#: size of the float builds for a difference in accuracy that is hard to hear
#: on short commands, and the assistant sits in the tray all day.
WHISPER_URL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

#: name -> approximate size on disk, for what the CLI prints before it starts.
WHISPER_MODELS = {
    "ggml-tiny-q5_1.bin": 31,
    "ggml-base-q5_1.bin": 57,
    "ggml-small-q5_1.bin": 181,
    "ggml-medium-q5_0.bin": 514,
}

#: Called with (language, downloaded_bytes, total_bytes); total is 0 when the
#: server does not send a content length.
ProgressFn = Callable[[str, int, int], None]


def wanted(config: Config) -> List[str]:
    """Which languages this configuration needs on disk."""
    return [] if config.speech.mode == "online" else ["en"]


def missing(config: Config) -> List[str]:
    """Which of the wanted models are not installed."""
    return [lang for lang in wanted(config) if not config.model_path().exists()]


def download(
    lang: str,
    on_progress: Optional[ProgressFn] = None,
    timeout: float = 60.0,
) -> bool:
    """Fetch and extract one model. Returns True on success."""
    try:
        name, url = MODEL_URLS[lang]
    except KeyError:
        log.error("no model is known for language %r", lang)
        return False

    try:
        import requests
    except ImportError:
        log.error("the requests library is needed to download models")
        return False

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = MODELS_DIR / name
    archive = MODELS_DIR / f"{name}.zip"

    log.info("downloading %s", name)
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            with archive.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(lang, done, total)
    except Exception as exc:
        log.error("could not download %s: %s", name, exc)
        archive.unlink(missing_ok=True)
        return False

    try:
        if target.exists():
            shutil.rmtree(target)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(MODELS_DIR)
    except (zipfile.BadZipFile, OSError) as exc:
        log.error("could not extract %s: %s", name, exc)
        return False
    finally:
        archive.unlink(missing_ok=True)

    if not target.exists():
        log.error("the archive did not contain %s", name)
        return False

    log.info("installed %s", name)
    return True


def whisper_url(name: str) -> str:
    """Download URL for a GGML model name."""
    return WHISPER_URL_BASE + name


def download_whisper(
    name: str,
    dest: Optional[Path] = None,
    on_progress: Optional[ProgressFn] = None,
    timeout: float = 60.0,
) -> bool:
    """Fetch one whisper.cpp GGML model. Returns True on success.

    Unlike the Vosk models this is a single file, so it is streamed straight to
    its final name - through a ``.part`` sibling, because these run to hundreds
    of megabytes and an interrupted download that left a short file in place
    would be loaded by whisper.cpp as a corrupt model rather than as a missing
    one, which is a much more confusing failure.
    """
    try:
        import requests
    except ImportError:
        log.error("the requests library is needed to download models")
        return False

    target = dest or (MODELS_DIR / name)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")

    log.info("downloading %s", name)
    try:
        with requests.get(whisper_url(name), stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            with part.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(name, done, total)
    except Exception as exc:
        log.error("could not download %s: %s", name, exc)
        part.unlink(missing_ok=True)
        return False

    try:
        part.replace(target)
    except OSError as exc:
        log.error("could not install %s: %s", name, exc)
        part.unlink(missing_ok=True)
        return False

    log.info("installed %s", name)
    return True


def ensure(
    config: Config,
    on_progress: Optional[ProgressFn] = None,
    on_message: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download whatever is missing. True when everything needed is present.

    Never raises: a machine with no network should still start, fall back to
    the online recogniser if that is allowed, and say what is wrong.
    """
    absent = missing(config)
    if not absent:
        return True

    if not config.speech.auto_download:
        if on_message:
            on_message(
                "Speech models are missing and automatic download is switched "
                "off. Run 'blackvoice setup' to install them."
            )
        return False

    if on_message:
        on_message(
            "First run: downloading the offline speech model (about 40 MB). "
            "This happens once."
        )

    ok = True
    for lang in absent:
        if not download(lang, on_progress=on_progress):
            ok = False

    if on_message:
        if ok:
            on_message("Speech models installed. Black Voice is ready.")
        else:
            on_message(
                "Some models could not be downloaded. Black Voice will keep "
                "working with whatever is available — run 'blackvoice setup' "
                "to try again."
            )
    return ok
