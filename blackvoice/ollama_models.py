"""A curated list of small Ollama models, and the calls to manage them.

The AI skill's ``ollama_model`` field has always taken any string - whatever
name ``ollama pull`` would accept - which is right for someone who knows
exactly which model they want. It is the wrong default for the settings
screen: a free-text field next to "which language model answers your
questions" invites someone to type a 70B model and wonder why the assistant
now takes thirty seconds to answer or does not answer at all on a laptop with
8 GB of RAM.

This module is the other half of that field: a short list of models small
enough to run acceptably on ordinary hardware, so the common path is a choice
between things that will actually work rather than a blank box.

Sizes are approximate - Ollama's library re-quantises and updates tags over
time - and are here only to be printed, the same spirit as the estimates next
to the Whisper models in :mod:`blackvoice.models`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger(__name__)

#: Called with (status_line, bytes_done, bytes_total); total is 0 when the
#: server has not reported one yet for the current layer.
ProgressFn = Callable[[str, int, int], None]


@dataclass(frozen=True)
class OllamaModel:
    #: The tag exactly as ``ollama pull`` and the config field expect it.
    name: str
    #: For display only: "0.5B", "1.5B", ...
    params: str
    #: Approximate download size in GB.
    download_gb: float
    #: Approximate resident memory while answering, in GB.
    ram_gb: float
    note: str = ""


#: Ordered smallest first, which is also the order a dropdown should offer
#: them in: the point of this list is to make the light end easy to reach.
LIGHTWEIGHT_MODELS: Dict[str, OllamaModel] = {
    m.name: m
    for m in (
        OllamaModel("qwen2.5:0.5b", "0.5B", 0.4, 1.0, "fastest; short answers only"),
        OllamaModel("tinyllama", "1.1B", 0.6, 1.0, "very fast; weak Hindi"),
        OllamaModel("qwen2.5:1.5b", "1.5B", 1.0, 2.0, "the best balance for a voice assistant"),
        OllamaModel("llama3.2:1b", "1B", 1.3, 2.0),
        OllamaModel("gemma2:2b", "2B", 1.6, 3.0),
        OllamaModel("llama3.2:3b", "3B", 2.0, 4.0, "noticeably slower to start speaking"),
    )
}

#: What "auto" picks: quick enough not to make the assistant feel slow, and
#: capable enough to hold up its end of a Hindi/English conversation.
RECOMMENDED = "qwen2.5:1.5b"


def describe(name: str) -> str:
    """One line for a model, known or not: "1.5B, ~1.0 GB download, ~2 GB RAM"."""
    model = LIGHTWEIGHT_MODELS.get(name)
    if model is None:
        return f"{name} (not in the curated list - size unknown)"
    line = f"{model.params}, ~{model.download_gb:g} GB download, ~{model.ram_gb:g} GB RAM"
    return f"{line} - {model.note}" if model.note else line


# --------------------------------------------------------------------------- #
# Talking to a running Ollama
# --------------------------------------------------------------------------- #
def _base_url(url: str) -> str:
    return (url or "http://localhost:11434").rstrip("/")


def is_reachable(url: str, timeout: float = 2.0) -> bool:
    """Whether an Ollama server is answering at ``url`` right now."""
    try:
        import requests

        requests.get(f"{_base_url(url)}/api/tags", timeout=timeout).raise_for_status()
        return True
    except Exception:
        return False


def pulled_models(url: str, timeout: float = 5.0) -> Optional[List[str]]:
    """Model tags already on disk, or ``None`` if the server could not be asked."""
    try:
        import requests

        response = requests.get(f"{_base_url(url)}/api/tags", timeout=timeout)
        response.raise_for_status()
        return [m.get("name", "") for m in response.json().get("models", []) if m.get("name")]
    except Exception as exc:
        log.debug("could not list Ollama models: %s", exc)
        return None


def not_running_message() -> str:
    """The same install-vs-start distinction :class:`AISkill` gives at answer time.

    Told here rather than imported from there: the AI skill's version is one
    branch of a larger error-classifying function tied to the exception it
    just caught, and this needs the same words before any request is made.
    """
    if shutil.which("ollama"):
        return "Ollama is installed but not running. Start it with: ollama serve"
    return (
        "Ollama is not installed. Get it from https://ollama.com/download, "
        "then run: ollama serve"
    )


def try_start_service(timeout: float = 10.0) -> bool:
    """Best-effort attempt to start an already-installed Ollama service.

    This never installs anything - see :func:`download_and_install` for the
    opt-in code path that does. The line this function itself stays on the
    right side of is running a third-party installer *as root*: Ollama's own
    is ``curl -fsSL https://ollama.com/install.sh | sh``, exactly the shape
    :data:`SafetyConfig.blocked_patterns` already refuses when a *user* asks
    the terminal skill to run it, which is why installing Ollama here goes
    through its plain release archive instead, as an ordinary user, never
    through that script.

    What this does is smaller and safer: nudge a service that is already on
    the machine but not currently running. Most desktop installs of Ollama
    register a systemd unit and start it immediately, so by the time Black
    Voice runs it is usually already up - this exists for the case where it
    was stopped, or set up as a user unit rather than a system one. Silent
    failure (no systemd, no such unit, no permission for a system unit
    started by an unprivileged process) is the expected outcome on plenty of
    machines and is not an error to surface - :func:`is_reachable` is what
    actually decides whether it worked.
    """
    if not shutil.which("systemctl"):
        return False
    for argv in (
        ["systemctl", "--user", "start", "ollama"],
        ["systemctl", "start", "ollama"],
    ):
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


class OllamaError(RuntimeError):
    """Raised when a model cannot be pulled - message is meant to be shown as-is."""


def pull(
    name: str,
    url: str,
    on_progress: Optional[ProgressFn] = None,
    timeout: float = 15.0,
) -> None:
    """Fetch a model through Ollama's own streaming pull API.

    Going through the HTTP API rather than shelling out to the ``ollama``
    binary means this works the same way whether Ollama runs natively, in a
    container, or on another host entirely - anywhere ``ai.ollama_url`` already
    points for answering questions. It also gives real progress: each line of
    the response is one JSON object with a status and, while a layer is
    downloading, byte counts for it.

    Raises :class:`OllamaError` with a message fit to show the user; never lets
    a ``requests`` exception escape.
    """
    try:
        import requests
    except ImportError as exc:
        raise OllamaError("the requests library is needed to install a model") from exc

    if not name.strip():
        raise OllamaError("no model name was given")

    try:
        response = requests.post(
            f"{_base_url(url)}/api/pull",
            json={"name": name, "stream": True},
            stream=True,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise OllamaError(not_running_message()) from exc
    except requests.exceptions.RequestException as exc:
        raise OllamaError(f"could not reach Ollama: {exc}") from exc

    import json as _json

    last_status = ""
    with response:
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            try:
                data = _json.loads(raw_line)
            except _json.JSONDecodeError:
                continue

            if data.get("error"):
                raise OllamaError(str(data["error"]))

            status = data.get("status", "")
            if status:
                last_status = status
            if on_progress:
                on_progress(status or last_status, data.get("completed", 0), data.get("total", 0))

    log.info("pulled Ollama model %s", name)


# --------------------------------------------------------------------------- #
# Installing Ollama itself - opt-in, per-user, no root
# --------------------------------------------------------------------------- #
#: Upstream publishes one general-purpose Linux build per architecture,
#: alongside GPU-vendor-specific variants (rocm, mlx, jetpack...) this project
#: does not attempt to pick between - the general build already includes CUDA
#: support, and hardware-specific tuning beyond that is exactly the kind of
#: judgement call Ollama's *own* installer makes and this one does not try to
#: second-guess. There is no small or CPU-only option upstream publishes for
#: x86_64: the smallest general Linux build is itself well over a gigabyte.
_RELEASES_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
_LINUX_ASSETS = {
    "x86_64": "ollama-linux-amd64.tar.zst",
    "amd64": "ollama-linux-amd64.tar.zst",
    "aarch64": "ollama-linux-arm64.tar.zst",
    "arm64": "ollama-linux-arm64.tar.zst",
}


def default_install_dir() -> Path:
    """Where a copy this project fetched itself would live - never where a
    system-wide or user-run install puts one; see :func:`find_binary`."""
    return DATA_DIR / "ollama"


def bundled_binary_path() -> Path:
    return default_install_dir() / "bin" / "ollama"


def find_binary() -> Optional[str]:
    """PATH first, then whatever this project may have installed for itself.

    The precedence matters: a system-wide or user-run install should always
    win over a private copy this project fetched, the same reasoning
    :func:`blackvoice.audio.stt.find_whisper_binary` already applies to
    whisper.cpp - a build the user (or their distribution) chose is trusted
    ahead of the one shipped here.
    """
    found = shutil.which("ollama")
    if found:
        return found
    bundled = bundled_binary_path()
    return str(bundled) if bundled.exists() else None


def _asset_name_for_this_machine() -> str:
    import platform

    machine = platform.machine().lower()
    name = _LINUX_ASSETS.get(machine)
    if not name:
        raise OllamaError(
            f"no Ollama build is published for this machine ({machine or 'unknown'})"
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
        raise OllamaError(f"could not look up the latest Ollama release: {exc}") from exc

    for asset in assets:
        if asset.get("name") == asset_name:
            return asset["browser_download_url"]
    raise OllamaError(f"the latest Ollama release has no {asset_name} asset")


def _extract_zst_tar(archive: Path, dest: Path) -> None:
    """Unpack a .tar.zst - a format Python's stdlib tarfile cannot read.

    Tries GNU tar's own --zstd support first (built in since tar 1.31, which
    is what every mainstream distribution from the last several years ships),
    then falls back to piping a standalone zstd binary into tar for anything
    older. Raising a clear, actionable error when neither is present is the
    point of trying both before giving up - "install zstd" is something a
    user can actually act on.
    """
    dest.mkdir(parents=True, exist_ok=True)
    errors = []

    try:
        result = subprocess.run(
            ["tar", "--zstd", "-xf", str(archive), "-C", str(dest)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180,
        )
        if result.returncode == 0:
            return
        errors.append(result.stderr.decode("utf-8", "replace").strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(str(exc))

    if shutil.which("zstd"):
        try:
            zstd_proc = subprocess.Popen(
                ["zstd", "-dc", str(archive)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            tar_result = subprocess.run(
                ["tar", "-x", "-C", str(dest)],
                stdin=zstd_proc.stdout, stderr=subprocess.PIPE, timeout=180,
            )
            zstd_proc.stdout.close()
            zstd_returncode = zstd_proc.wait(timeout=30)
            if tar_result.returncode == 0 and zstd_returncode == 0:
                return
            errors.append(tar_result.stderr.decode("utf-8", "replace").strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))

    raise OllamaError(
        "could not extract the Ollama archive - this needs a tar built with "
        "zstd support, or a standalone zstd binary (try: sudo apt install "
        "zstd, or sudo dnf install zstd)" + (f": {errors[-1]}" if errors else "")
    )


def download_and_install(
    on_progress: Optional[ProgressFn] = None, timeout: float = 30.0
) -> str:
    """Fetch Ollama's own release build into a private, per-user directory.

    No root, and no third-party install script executed - a plain archive,
    streamed and extracted, the same risk shape as every model file this
    project already fetches for Vosk, whisper.cpp and Piper. What makes this
    one worth pausing over is size: there is no small build to reach for, so
    this is a very different amount of bandwidth and disk than any of those -
    which is exactly why, unlike them, it is opt-in (``ai.auto_install``)
    rather than on by default.

    Linux only, and only for the two architectures upstream publishes a
    general build for. Raises :class:`OllamaError` with a message fit to show
    the user on anything else going wrong; never lets a lower-level exception
    escape.
    """
    if sys.platform != "linux":
        raise OllamaError("automatic Ollama installation is Linux-only")

    try:
        import requests
    except ImportError as exc:
        raise OllamaError("the requests library is needed to install Ollama") from exc

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
        raise OllamaError(f"could not download Ollama: {exc}") from exc

    if on_progress:
        on_progress("extracting", 0, 0)
    try:
        _extract_zst_tar(archive, install_dir)
    finally:
        archive.unlink(missing_ok=True)

    binary = bundled_binary_path()
    if not binary.exists():
        raise OllamaError("the Ollama archive did not contain a bin/ollama binary")
    binary.chmod(0o755)
    log.info("installed Ollama to %s", binary)
    return str(binary)


# --------------------------------------------------------------------------- #
# Running it as a --user systemd service - still no root
# --------------------------------------------------------------------------- #
_USER_SERVICE = """[Unit]
Description=Ollama (installed privately by Black Voice)
After=network-online.target

[Service]
ExecStart={binary} serve
Environment=OLLAMA_MODELS={models_dir}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def user_service_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config_home / "systemd" / "user" / "ollama.service"


def write_user_service(binary: str) -> Path:
    """A --user unit, not a system one: writable and runnable with no root.

    Deliberately named and described as installed "by Black Voice", so
    anyone who finds it with ``systemctl --user status`` is told where it
    came from rather than mistaking it for Ollama's own installer having
    been run.
    """
    path = user_service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _USER_SERVICE.format(binary=binary, models_dir=default_install_dir() / "models"),
        encoding="utf-8",
    )
    return path


def enable_and_start_user_service(timeout: float = 15.0) -> bool:
    """systemctl --user enable --now, plus a best-effort try at lingering.

    Lingering (``loginctl enable-linger``) is what lets a --user unit keep
    running without an active login session - the closest a user-level
    service gets to behaving like a real background service. A user can
    usually enable it for themselves with no elevated privilege, but not
    guaranteed to on every distribution's policy, so failure here is not
    treated as this having failed overall: the unit still runs for the rest
    of the current login session regardless.
    """
    if not shutil.which("systemctl"):
        return False

    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
        )
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "ollama"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if shutil.which("loginctl"):
        try:
            import getpass

            subprocess.run(
                ["loginctl", "enable-linger", getpass.getuser()],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass  # best-effort only; see the docstring above

    return result.returncode == 0
