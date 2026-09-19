"""Installing the Piper binary itself.

This is the other half of what :mod:`blackvoice.voices` already does: that
module fetches a Piper *voice* (the ``.onnx`` model), but was never able to
fetch Piper itself - the program that actually reads the voice and produces
audio. Two shortcuts were checked and ruled out before writing this:

- The PyPI ``piper-tts`` package (the ``piper1-gpl`` rewrite) ships
  ``cp39-abi3`` wheels, which sounds promising, but it depends on
  ``onnxruntime`` - already established elsewhere in this project as
  unbundleable into ``/opt/blackvoice/lib``. It *does* register a ``piper``
  console script (``piper = piper.__main__:main``, confirmed against its
  published wheel's ``entry_points.txt``) - which is exactly why a bare
  ``pipx install piper-tts`` shadows this project's own fetch on PATH, and
  why :func:`find_binary` cannot just trust anything named ``piper`` it
  finds there: :mod:`blackvoice.audio.tts` has to tell the two CLIs apart by
  their ``--help`` output before building an argv either one will accept.
- The legacy `rhasspy/piper <https://github.com/rhasspy/piper>`_ repository,
  however, still publishes a plain binary archive per architecture as a real
  GitHub release asset - confirmed directly against the release API rather
  than assumed. That is what this module fetches: a private, per-user copy,
  the same shape as :mod:`blackvoice.ollama_models`, but simpler - a plain
  ``.tar.gz`` needs nothing beyond the standard library's own ``tarfile``,
  unlike Ollama's ``.tar.zst``.

Unlike Ollama's multi-gigabyte build, this archive is tens of megabytes - the
same size class as the Vosk models this project already downloads on first
run without asking - so installing it is on by default
(``voice.piper_auto_install``), not opt-in.
"""

from __future__ import annotations

import logging
import platform
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Callable, Optional

from .config import DATA_DIR

log = logging.getLogger(__name__)

#: Called with (status_line, bytes_done, bytes_total); total is 0 once the
#: status has moved past the download itself (e.g. "extracting").
ProgressFn = Callable[[str, int, int], None]

_RELEASES_API = "https://api.github.com/repos/rhasspy/piper/releases/latest"

#: Linux only - the same boundary :func:`blackvoice.ollama_models.download_and_install`
#: draws, and for the same reason: this is POSIX system integration, not a
#: cross-platform feature.
_LINUX_ASSETS = {
    "x86_64": "piper_linux_x86_64.tar.gz",
    "amd64": "piper_linux_x86_64.tar.gz",
    "aarch64": "piper_linux_aarch64.tar.gz",
    "arm64": "piper_linux_aarch64.tar.gz",
    "armv7l": "piper_linux_armv7l.tar.gz",
}


class PiperError(RuntimeError):
    """Raised when the Piper binary cannot be installed - message is meant to
    be shown to the user as-is."""


def default_install_dir() -> Path:
    """Where a copy this project fetched itself would live - never where a
    system-wide or user-run install puts one; see :func:`find_binary`."""
    return DATA_DIR / "piper"


def bundled_binary_path() -> Path:
    # The archive's own top-level directory is called "piper", same as the
    # binary inside it - see the docstring's confirmed layout.
    return default_install_dir() / "piper" / "piper"


def espeak_data_dir() -> Optional[Path]:
    """The ``espeak-ng-data`` directory Piper's archive ships beside the
    binary, when our own private copy is the one installed.

    Piper needs this to phonemise anything but the plainest English, and
    passing it explicitly with ``--espeak_data`` means playback does not
    depend on the assistant's current working directory happening to be the
    install directory - which nothing about this project guarantees.
    """
    candidate = bundled_binary_path().parent / "espeak-ng-data"
    return candidate if candidate.is_dir() else None


def find_binary() -> Optional[str]:
    """PATH first, then whatever this project may have installed for itself.

    The same precedence :func:`blackvoice.audio.stt.find_whisper_binary` and
    :func:`blackvoice.ollama_models.find_binary` already apply: a build the
    user or their distribution chose is trusted ahead of the one shipped here.
    """
    found = shutil.which("piper")
    if found:
        return found
    bundled = bundled_binary_path()
    return str(bundled) if bundled.exists() else None


def _asset_name_for_this_machine() -> str:
    machine = platform.machine().lower()
    name = _LINUX_ASSETS.get(machine)
    if not name:
        raise PiperError(
            f"no Piper build is published for this machine ({machine or 'unknown'})"
        )
    return name


def _latest_release_asset_url(asset_name: str, timeout: float) -> str:
    try:
        import requests

        response = requests.get(
            _RELEASES_API, timeout=timeout, headers={"User-Agent": "blackvoice"}
        )
        response.raise_for_status()
        assets = response.json().get("assets", [])
    except Exception as exc:
        raise PiperError(f"could not look up the latest Piper release: {exc}") from exc

    for asset in assets:
        if asset.get("name") == asset_name:
            return asset["browser_download_url"]
    raise PiperError(f"the latest Piper release has no {asset_name} asset")


def _extract_tar(archive: Path, dest: Path) -> None:
    """Unpack the plain ``.tar.gz`` Piper ships.

    Stdlib ``tarfile`` is enough here - unlike Ollama's ``.tar.zst``, nothing
    external has to be shelled out to. Every member's destination is checked
    before anything is written, so a maliciously crafted archive cannot place
    a file outside the install directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (resolved_dest / member.name).resolve()
            if target != resolved_dest and resolved_dest not in target.parents:
                raise PiperError(
                    f"refusing to extract {member.name!r} outside the install directory"
                )
        # "data" is the hardened filter (no device files, no absolute or
        # traversing paths, ownership stripped) that became the default only
        # in 3.12+ - passed explicitly so behaviour does not depend on which
        # patch level of Python happens to be running.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(resolved_dest, filter="data")
        else:
            tar.extractall(resolved_dest)


def download_and_install(
    on_progress: Optional[ProgressFn] = None, timeout: float = 30.0
) -> str:
    """Fetch Piper's own release build into a private, per-user directory.

    No root, and nothing but a plain archive is executed - the same risk
    shape as every model file this project already fetches for Vosk,
    whisper.cpp and Ollama. Linux only, and only for the architectures
    upstream publishes a build for. Raises :class:`PiperError` with a message
    fit to show the user on anything else going wrong; never lets a
    lower-level exception escape.
    """
    if sys.platform != "linux":
        raise PiperError("automatic Piper installation is Linux-only")

    try:
        import requests
    except ImportError as exc:
        raise PiperError("the requests library is needed to install Piper") from exc

    asset_name = _asset_name_for_this_machine()
    url = _latest_release_asset_url(asset_name, timeout=timeout)

    install_dir = default_install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    archive = install_dir / asset_name

    log.info("downloading %s", asset_name)
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            with archive.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress("downloading", done, total)
    except requests.exceptions.RequestException as exc:
        archive.unlink(missing_ok=True)
        raise PiperError(f"could not download Piper: {exc}") from exc

    if on_progress:
        on_progress("extracting", 0, 0)
    try:
        _extract_tar(archive, install_dir)
    finally:
        archive.unlink(missing_ok=True)

    binary = bundled_binary_path()
    if not binary.exists():
        raise PiperError("the Piper archive did not contain a piper/piper binary")
    binary.chmod(0o755)
    log.info("installed Piper to %s", binary)
    return str(binary)
