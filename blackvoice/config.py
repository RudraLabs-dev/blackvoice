"""Configuration for Black Voice.

Config lives at ``~/.config/blackvoice/config.json`` and is created with sane
defaults on first run. Every value can also be overridden by an environment
variable of the form ``BLACKVOICE_<SECTION>_<KEY>`` (upper case), which is handy
for systemd units and quick experiments.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_NAME = "blackvoice"
APP_TITLE = "Black Voice"

#: Branding. Defined once here so the vendor line never drifts between the
#: README, the About dialog, the desktop entry and the package metadata.
APP_VENDOR = "Rudra Labs"
APP_ATTRIBUTION = f"A {APP_VENDOR} product"


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME

CONFIG_FILE = CONFIG_DIR / "config.json"
MODELS_DIR = DATA_DIR / "models"
NOTES_FILE = DATA_DIR / "notes.md"
HISTORY_FILE = DATA_DIR / "history.jsonl"
TIMERS_FILE = DATA_DIR / "timers.json"
LOG_FILE = CACHE_DIR / "blackvoice.log"

#: Where the distribution packages put the native helpers they bundle
#: (whisper.cpp, Piper). Searched only after PATH, so a build the user or
#: their distribution installed is always preferred to ours.
BUNDLED_BIN_DIR = Path("/opt/blackvoice/bin")


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    block_size: int = 8000
    input_device: Optional[int] = None       # None = system default mic
    #: RMS level (0..1) below which a block counts as silence. A floor, not
    #: the last word: when calibrate_noise is on, the effective threshold used
    #: while listening is raised above this to match the room, never lowered
    #: below it. See audio.mic.Endpointer.
    silence_threshold: float = 0.012
    #: stop capturing a command after this many seconds of silence. Lower
    #: than it looks: Endpointer tests loudness several times a second rather
    #: than once per 0.5s mic block, so this no longer carries a hidden
    #: rounding delay on top of it.
    silence_timeout: float = 0.8
    #: hard cap on a single command utterance
    max_command_seconds: float = 12.0
    #: measure the room's ambient noise at the start of each utterance and
    #: raise the effective silence threshold to clear it, instead of trusting
    #: one fixed number for every room
    calibrate_noise: bool = True
    #: seconds of initial audio used to measure the noise floor
    calibration_seconds: float = 1.0
    #: effective threshold = noise floor * this margin, never below
    #: silence_threshold and capped well above it - see Endpointer
    calibration_margin: float = 1.6


@dataclass
class SpeechConfig:
    """Offline speech-to-text, with the cloud only as a fallback.

    Two offline engines are supported. whisper.cpp is a native binary driven
    over a pipe - the same arrangement as Piper on the output side - so it adds
    no Python extension module and nothing that has to be rebuilt when the
    system interpreter changes. Vosk remains the fallback.
    """

    #: "hybrid" | "offline" | "online"
    mode: str = "hybrid"
    #: Vosk model folder, resolved under MODELS_DIR when not absolute
    model_en: str = "vosk-model-small-en-us-0.15"

    # ---------------------------------------------------------- whisper.cpp
    #: Which offline engine leads: "auto" | "whisper" | "vosk".
    #: "auto" takes whisper.cpp when its binary *and* model are both present
    #: and quietly uses Vosk otherwise, so an installation that has not fetched
    #: the model yet behaves exactly as it did before.
    engine: str = "auto"
    #: whisper.cpp executable. Blank probes PATH first and the bundled copy
    #: second - the same precedence the launcher gives PYTHONPATH, so a
    #: distribution's own build wins over the one we ship.
    whisper_binary: str = ""
    #: GGML model file, resolved under MODELS_DIR when not absolute
    whisper_model: str = "ggml-base-q5_1.bin"
    #: pinned to English - this assistant only understands English commands.
    whisper_language: str = "en"
    #: decoder threads; 0 lets whisper.cpp choose from the core count
    whisper_threads: int = 0
    #: seconds to wait for a transcript before giving up on the utterance
    whisper_timeout: float = 30.0
    #: Passed to whisper.cpp as --prompt. Whisper conditions its decoding on
    #: this text, so naming the command vocabulary pulls ambiguous audio
    #: towards the words this assistant can actually act on. It is not a
    #: grammar: anything may still be transcribed.
    whisper_prompt: str = (
        "Black, open firefox. Black, set volume to 40. Black, take a screenshot. "
        "Black, brightness up. Black, turn off wifi. Black, set a timer. "
        "Black, open chrome. Black, close the terminal. Black, lock the screen."
    )
    #: below this Vosk confidence the hybrid mode retries online
    fallback_confidence: float = 0.55
    #: seconds to wait on the online recogniser before giving up
    online_timeout: float = 6.0
    #: fetch the models on first run when they are not installed yet. They
    #: cannot ship in the distribution packages - a post-install script must
    #: not use the network, and it runs as root while the models belong to a
    #: user - so first run is where this legitimately happens.
    auto_download: bool = True


@dataclass
class WakeConfig:
    enabled: bool = True
    #: any of these spoken words activates the assistant
    phrases: List[str] = field(default_factory=lambda: ["black", "blek", "blak"])
    #: global hotkey shown in the UI (bound by your desktop environment)
    hotkey: str = "Ctrl+Alt+Space"
    #: play a short beep when activated
    chime: bool = True
    #: after replying, keep listening for a follow-up without needing the
    #: wake word said again - the same "conversation mode" Alexa/Google
    #: Assistant default to, rather than a fresh "Black" before every single
    #: thing said
    followup_enabled: bool = True
    #: how long to wait for that follow-up before giving up quietly and
    #: going back to waiting for the wake word. Deliberately shorter than
    #: AudioConfig.max_command_seconds: a wake word says "I am about to
    #: speak", so waiting the full command-length timeout is reasonable: a
    #: follow-up is only maybe coming, and holding the mic open that long
    #: on every single reply for a "maybe" would make the assistant look
    #: like it is still listening long after most people have moved on.
    followup_seconds: float = 6.0


@dataclass
class VoiceConfig:
    """Text-to-speech output."""

    #: "auto" | "piper" | "espeak" | "spd-say" | "pyttsx3" | "none"
    engine: str = "auto"
    #: Words per minute. 165 is brisk for a formant synthesiser like espeak-ng;
    #: 145 is markedly easier to follow, which matters more than speed when the
    #: listener is not a native English speaker.
    rate: int = 145
    volume: float = 0.9
    #: espeak voice id
    voice_en: str = "en-us"
    #: Piper neural voice, downloaded on demand. This is what makes the
    #: assistant sound like a person rather than a 1990s synthesiser.
    piper_voice_en: str = "en_US-lessac-medium"
    #: fetch the Piper voice the first time it is needed
    piper_auto_download: bool = True
    #: fetch the Piper *program* itself on first run if no copy is found
    #: anywhere - a private, per-user install, no root. On by default: unlike
    #: Ollama's multi-gigabyte build, this archive is tens of MB, the same
    #: size class as the Vosk models this project already downloads without
    #: asking. See blackvoice.piper_install.
    piper_auto_install: bool = True
    #: an explicit .onnx path, which overrides the named voices above
    piper_model: str = ""


@dataclass
class AIConfig:
    """LLM backend for free-form questions."""

    #: "ollama" | "anthropic" | "openai" | "none"
    provider: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    #: When provider is "ollama": on first run, wake an already-installed
    #: Ollama service if it is stopped, and fetch ollama_model if it is not
    #: pulled yet - no manual `setup --ollama` needed for whoever already has
    #: Ollama on the machine.
    auto_setup: bool = True
    #: Off by default, unlike auto_setup above: when Ollama is not found
    #: anywhere, fetch a private copy on first run and run it as a --user
    #: systemd service, no root involved. Off by default because there is no
    #: small build to fetch - upstream's smallest general Linux release is
    #: over a gigabyte - so this is a very different bandwidth and disk
    #: commitment than anything else this project downloads automatically,
    #: and defaults should not make that choice for someone silently.
    auto_install: bool = False
    #: Let a free-form question that the regex router could not classify
    #: actually run a shell command - via Ollama's tool calling - instead of
    #: only being talked about. The command goes through the exact same
    #: ShellGuard as a spoken "run command X": a handful of destructive
    #: patterns are refused outright, anything else that could change the
    #: system still pauses for a spoken "yes", and nothing ever runs as root.
    #: Only wired up for the ollama provider today.
    tools_enabled: bool = True
    anthropic_model: str = "claude-opus-5"
    openai_model: str = "gpt-4o-mini"
    #: left blank on purpose - read from ANTHROPIC_API_KEY / OPENAI_API_KEY
    api_key: str = ""
    max_tokens: int = 512
    timeout: float = 30.0
    system_prompt: str = (
        "You are Black Voice, a voice assistant on someone's Linux desktop. "
        "Your answers are spoken aloud, so write the way a person talks: plain "
        "sentences, no lists, no markdown, no headings, and no reading out "
        "symbols or code unless you are asked for them. "
        "Two or three sentences is usually right; one is often better. "
        "Answer the question that was asked and stop - do not offer follow-ups "
        "or ask whether they want more. "
        "If you do not know, say so plainly rather than guessing. "
        "When a request needs something actually done on this machine rather "
        "than explained, use the run_command tool instead of describing the "
        "command - the user is talking to you, not reading a terminal."
    )


@dataclass
class SafetyConfig:
    """Guard rails for the terminal skill."""

    #: run shell commands only after the user confirms
    confirm_shell: bool = True
    #: commands matching these patterns are refused outright
    blocked_patterns: List[str] = field(
        default_factory=lambda: [
            r"\brm\s+-[a-zA-Z]*[rf]",
            r"\bmkfs(\.|\s)",
            r"\bdd\s+.*of=/dev/",
            r">\s*/dev/sd",
            r":\s*\(\s*\)\s*\{.*\}\s*;\s*:",   # fork bomb, spaced or not
            r"\bchmod\s+-R\s+777\s+/",
            r"\b(shutdown|reboot|halt|poweroff)\b",
            r"\b(curl|wget)\b[^|]*\|\s*(ba|z|k)?sh",
            r"\bmv\s+[^\s]+\s+/dev/null",
            r"\buserdel\b|\bpasswd\b",
        ]
    )
    shell_timeout: float = 20.0
    #: truncate command output shown back to the user
    max_output_chars: int = 2000


@dataclass
class UIConfig:
    #: show the tray icon and popup overlay
    enabled: bool = True
    #: Close the popup by itself after a reply. Off by default: it used to
    #: vanish in the middle of a long spoken answer, and a card the user
    #: dismisses is more predictable than one that decides for itself.
    auto_close: bool = False
    #: seconds before an automatic close, when auto_close is on
    overlay_timeout: float = 8.0
    theme: str = "light"
    show_notifications: bool = True


@dataclass
class SkillsConfig:
    #: city for weather, e.g. "Jaipur"
    weather_city: str = ""
    search_url: str = "https://duckduckgo.com/?q={query}"
    #: preferred applications; blank means auto-detect
    browser: str = ""
    terminal: str = ""
    file_manager: str = ""
    editor: str = ""


@dataclass
class ControlConfig:
    """The local control socket: how any other UI talks to a running engine.

    PyQt6's tray and overlay call straight into :class:`Engine`, because they
    live in the same process. Anything that does not - a Flutter client above
    all - needs a channel across the process boundary, and this is it: a
    Unix domain socket under the user's own runtime directory, speaking one
    JSON object per line. A filesystem socket rather than a TCP port because
    the permissions on its containing directory are the access control - no
    token to generate, store or leak - and because nothing here has any
    business being reachable from another machine.

    POSIX only, like the rest of this project's system integration; on any
    other platform the socket is simply not opened.
    """

    enabled: bool = True
    #: blank resolves under XDG_RUNTIME_DIR; an absolute path overrides it,
    #: e.g. to run two instances side by side during development.
    socket_path: str = ""


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    wake: WakeConfig = field(default_factory=WakeConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    control: ControlConfig = field(default_factory=ControlConfig)

    # ------------------------------------------------------------------ paths
    @property
    def models_dir(self) -> Path:
        return MODELS_DIR

    def model_path(self) -> Path:
        """Absolute path to the Vosk model directory."""
        name = self.speech.model_en
        p = Path(name).expanduser()
        return p if p.is_absolute() else MODELS_DIR / name

    def whisper_model_path(self) -> Path:
        """Absolute path to the GGML file whisper.cpp should load."""
        p = Path(self.speech.whisper_model).expanduser()
        return p if p.is_absolute() else MODELS_DIR / self.speech.whisper_model

    def control_socket_path(self) -> Path:
        """Where the control socket listens, resolving the XDG default.

        XDG_RUNTIME_DIR is shared with every other app (``/run/user/1000``),
        so a subdirectory keeps this from colliding with anyone else's socket.
        CACHE_DIR is already ours alone (``~/.cache/blackvoice``), so the
        fallback used when a desktop somehow has no runtime dir does not
        repeat the name.
        """
        if self.control.socket_path:
            return Path(self.control.socket_path).expanduser()
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if runtime:
            return Path(runtime) / APP_NAME / "control.sock"
        return CACHE_DIR / "control.sock"

    # ------------------------------------------------------------------- i/o
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = path or CONFIG_FILE
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                cfg = _merge(cfg, raw)
            except (json.JSONDecodeError, OSError) as exc:
                # A broken config should never stop the assistant from starting.
                print(f"[blackvoice] config unreadable ({exc}); using defaults")
        else:
            cfg.save(path)
        _apply_env(cfg)
        return cfg


def _merge(obj: Any, raw: Dict[str, Any]) -> Any:
    """Recursively overlay ``raw`` onto a dataclass instance, ignoring junk keys."""
    known = {f.name for f in fields(obj)}
    for key, value in raw.items():
        if key not in known:
            continue
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            setattr(obj, key, _merge(current, value))
        else:
            setattr(obj, key, value)
    return obj


def _apply_env(cfg: Config) -> None:
    """Support BLACKVOICE_AI_PROVIDER=openai style overrides."""
    for section in fields(cfg):
        sub = getattr(cfg, section.name)
        if not is_dataclass(sub):
            continue
        for f in fields(sub):
            env_key = f"BLACKVOICE_{section.name.upper()}_{f.name.upper()}"
            if env_key not in os.environ:
                continue
            raw = os.environ[env_key]
            try:
                setattr(sub, f.name, _coerce(raw, getattr(sub, f.name)))
            except (TypeError, ValueError):
                print(f"[blackvoice] ignoring bad env value for {env_key}")


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
