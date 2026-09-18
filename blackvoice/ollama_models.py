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
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

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

    This never installs Ollama - that stays a manual, documented step (see
    :func:`not_running_message`); the line this project does not cross is an
    automatic code path fetching and running a third-party installer as root,
    which is exactly the ``curl | sh`` shape :data:`SafetyConfig.blocked_patterns`
    already refuses when a *user* asks the terminal skill to run it.

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
