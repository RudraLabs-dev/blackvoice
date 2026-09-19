"""Command line entry point for Black Voice."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import APP_TITLE, CONFIG_FILE, LOG_FILE, MODELS_DIR, Config, ensure_dirs
from .core.logs import setup_logging


def _unicode_console() -> bool:
    """True when stdout can actually render the box-drawing banner.

    A terminal under ``LANG=C`` (or a Windows console on cp1252) raises
    UnicodeEncodeError on the nice glyphs, which is a silly way for a diagnostic
    command to die - so fall back to ASCII there.
    """
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


_UNICODE = _unicode_console()

OK = "✓" if _UNICODE else "[ok]"
BAD = "✗" if _UNICODE else "[--]"
DOT = "·" if _UNICODE else "-"
ARROW = "→" if _UNICODE else "->"
BULLET = "•" if _UNICODE else "*"

_BANNER_UNICODE = r"""
  ██████  ██       █████   ██████ ██   ██     ██    ██  ██████  ██  ██████ ███████
  ██   ██ ██      ██   ██ ██      ██  ██      ██    ██ ██    ██ ██ ██      ██
  ██████  ██      ███████ ██      █████       ██    ██ ██    ██ ██ ██      █████
  ██   ██ ██      ██   ██ ██      ██  ██       ██  ██  ██    ██ ██ ██      ██
  ██████  ███████ ██   ██  ██████ ██   ██       ████    ██████  ██  ██████ ███████
"""

_BANNER_ASCII = r"""
  ####  #      ###   ####  #  #    #  #  ###  #  ####  ###
  #   # #     #   # #      # #     #  # #   # # #     #
  ####  #     ##### #      ##      #  # #   # # #     ###
  #   # #     #   # #      # #      ##  #   # # #     #
  ####  ##### #   #  ####  #  #     ##   ###  #  #### ###
"""

BANNER = _BANNER_UNICODE if _UNICODE else _BANNER_ASCII


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    from .app import Engine

    config = Config.load()
    engine = Engine(config)

    if args.no_ui or not config.ui.enabled:
        return _run_headless(engine)

    try:
        from .ui.tray import TrayApp
    except ImportError as exc:
        print(f"PyQt6 is not available ({exc}); falling back to headless mode.")
        print("Install it with:  pip install PyQt6")
        return _run_headless(engine)

    return TrayApp(engine).run()


def _run_headless(engine) -> int:
    from .core.bus import Topic

    engine.bus.subscribe(
        Topic.HEARD,
        lambda e: None if e.get("partial") else print(f"  you  {e.get('text', '')}"),
    )
    engine.bus.subscribe(
        Topic.REPLY,
        lambda e: print(f"  {APP_TITLE.split()[0].lower()}  {e.get('display') or e.get('speech')}\n"),
    )
    engine.bus.subscribe(Topic.ERROR, lambda e: print(f"  error  {e.get('message')}"))

    print(BANNER)
    print(engine.describe())
    print('\nListening. Say "Black" to wake me. Press Ctrl+C to quit.\n')

    try:
        engine.start(background=False)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        engine.stop()
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    """Type commands instead of speaking them - handy for testing the router."""
    from .app import Engine

    engine = Engine(Config.load())

    if args.command:
        reply = engine.process(" ".join(args.command))
        print(reply.display or reply.speech)
        engine.stop()
        return 0 if reply.ok else 1

    print(BANNER)
    print('Type a command, or "quit" to exit.\n')
    try:
        while True:
            try:
                line = input("  you  ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in {"quit", "exit", "q"}:
                break
            reply = engine.process(line)
            print(f"  black  {reply.display or reply.speech}\n")
    except KeyboardInterrupt:
        print()
    finally:
        engine.stop()
    return 0


def cmd_say(args: argparse.Namespace) -> int:
    """Check that text-to-speech works."""
    from .audio.tts import Speaker

    config = Config.load()
    speaker = Speaker(config.voice)
    text = " ".join(args.text) or "Black Voice is ready."
    print(f"Engine: {speaker.engine}")
    speaker.say(text)
    speaker.wait_until_idle(timeout=30)
    speaker.shutdown()
    return 0


def _download_progress():
    """A progress printer shared by the model downloads."""
    last = [-1]

    def _progress(_name: str, done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        if percent == last[0]:
            return
        last[0] = percent
        print(chr(13) + f"  {percent:3d}%  {done / 2**20:6.1f} MiB", end="", flush=True)

    return _progress


def cmd_setup(args: argparse.Namespace) -> int:
    """Download the offline speech models."""
    from . import models

    ensure_dirs()

    if args.whisper:
        return _setup_whisper(args)
    if args.piper:
        return _setup_piper(args)
    if args.ollama:
        return _setup_ollama(args)

    wanted = ["en", "hi"] if args.language == "both" else [args.language]
    failures = 0

    for lang in wanted:
        name = models.MODEL_URLS[lang][0]
        if (MODELS_DIR / name).exists() and not args.force:
            print(f"{OK} {name} already installed")
            continue

        print(f"{ARROW} downloading {name}")
        ok = models.download(lang, on_progress=_download_progress())
        print()
        if ok:
            print(f"{OK} {name} installed")
        else:
            failures += 1

    if failures:
        print()
        print("Some models could not be installed.")
        print(f"You can download them by hand into {MODELS_DIR} from")
        print("https://alphacephei.com/vosk/models")
        return 1

    print()
    print(f"Models are in {MODELS_DIR}")
    print("Run 'blackvoice doctor' to check everything is wired up.")
    return 0


def _setup_whisper(args: argparse.Namespace) -> int:
    """Download a whisper.cpp GGML model and say whether it can be used."""
    from . import models
    from .audio.stt import find_whisper_binary

    config = Config.load()
    name = args.model or config.speech.whisper_model
    if name not in models.WHISPER_MODELS:
        print(f"{BAD} unknown model {name!r}")
        print("  known models: " + ", ".join(sorted(models.WHISPER_MODELS)))
        return 2

    target = MODELS_DIR / name
    if target.exists() and not args.force:
        print(f"{OK} {name} already installed")
    else:
        size = models.WHISPER_MODELS[name]
        print(f"{ARROW} downloading {name} (about {size} MB)")
        ok = models.download_whisper(name, on_progress=_download_progress())
        print()
        if not ok:
            print(f"{BAD} {name} could not be downloaded")
            print(f"  Fetch it by hand into {MODELS_DIR} from")
            print(f"  {models.whisper_url(name)}")
            return 1
        print(f"{OK} {name} installed")

    # The model on its own does nothing: whisper.cpp is a separate binary, the
    # same way Piper is for speech output.
    binary = find_whisper_binary(config.speech.whisper_binary)
    print()
    if binary:
        print(f"{OK} whisper.cpp  {binary}")
    else:
        print(f"{BAD} whisper.cpp was not found on this system")
        print("  It is a native binary, not a Python package.")
        print()
        print("  Debian sid/forky package it directly:")
        print("    sudo apt install whisper.cpp")
        print()
        print("  Anywhere else, build it yourself:")
        print("    git clone https://github.com/ggml-org/whisper.cpp")
        print("    cmake -B build whisper.cpp && cmake --build build -j")
        print("  Then put whisper-cli on PATH, or set speech.whisper_binary.")

    if name != config.speech.whisper_model:
        print()
        print(f"{BULLET} to use it, set speech.whisper_model to {name!r} in")
        print(f"  {CONFIG_FILE}")

    return 0 if binary else 1


def _setup_piper(args: argparse.Namespace) -> int:
    """Fetch the Piper binary itself, right now, rather than waiting for
    voice.piper_auto_install to do it on the next run - same code path, same
    result, just on demand.
    """
    from . import piper_install

    existing = piper_install.find_binary()
    if existing is not None and not args.force:
        print(f"{OK} Piper is already available at {existing}")
        print("  nothing to install")
        return 0

    print(f"{ARROW} downloading Piper (about 25 MB)")
    last = [-1]

    def _progress(status: str, done: int, total: int) -> None:
        if status == "extracting":
            print(chr(13) + "  extracting..." + " " * 20)
            return
        percent = int(done * 100 / total) if total else 0
        if percent == last[0]:
            return
        last[0] = percent
        print(chr(13) + f"  {percent:3d}%  {done / 2**20:6.1f} MiB", end="", flush=True)

    try:
        binary = piper_install.download_and_install(on_progress=_progress)
    except piper_install.PiperError as exc:
        print()
        print(f"{BAD} {exc}")
        return 1

    print()
    print(f"{OK} installed to {binary}")
    print()
    print(f"{BULLET} fetch a voice next: blackvoice voice --install")
    print(f"{BULLET} then hear it:       blackvoice voice --test")
    return 0


def _install_ollama(config: Config) -> int:
    """Fetch a private copy of Ollama right now, instead of waiting for
    ai.auto_install to do it on the next run - same code path, same result,
    just on demand.
    """
    from . import ollama_models

    existing = ollama_models.find_binary()
    if existing is not None:
        print(f"{OK} Ollama is already available at {existing}")
        print("  nothing to install")
        return 0

    print(f"{ARROW} downloading Ollama (about 1.3 GB - no small build is published "
          "for this platform)")
    last = [-1]

    def _progress(status: str, done: int, total: int) -> None:
        if status == "extracting":
            print(chr(13) + "  extracting..." + " " * 20)
            return
        percent = int(done * 100 / total) if total else 0
        if percent == last[0]:
            return
        last[0] = percent
        print(chr(13) + f"  {percent:3d}%  {done / 2**20:7.1f} MiB", end="", flush=True)

    try:
        binary = ollama_models.download_and_install(on_progress=_progress)
    except ollama_models.OllamaError as exc:
        print()
        print(f"{BAD} {exc}")
        return 1

    print()
    print(f"{OK} installed to {binary}")

    ollama_models.write_user_service(binary)
    if ollama_models.enable_and_start_user_service():
        print(f"{OK} running as a --user systemd service (systemctl --user status ollama)")
    else:
        print(f"{DOT} could not enable the systemd service - start it yourself:")
        print(f"  {binary} serve &")

    print(f"\n{BULLET} pull a model next: blackvoice setup --ollama --model <name>")
    return 0


def _setup_ollama(args: argparse.Namespace) -> int:
    """Pull a small Ollama model, and optionally make it the active one.

    With no --model this only reports: what is installed, what is running,
    and which curated models exist to choose from. Nothing is downloaded by
    just asking what is available.
    """
    from . import ollama_models

    config = Config.load()

    if args.install:
        return _install_ollama(config)

    reachable = ollama_models.is_reachable(config.ai.ollama_url)
    pulled = ollama_models.pulled_models(config.ai.ollama_url) if reachable else None

    if not args.model:
        binary = ollama_models.find_binary()
        if binary is None:
            print(f"{BAD} Ollama is not installed anywhere")
            print(f"  {ollama_models.not_running_message()}")
            print("  or: blackvoice setup --ollama --install  (~1.3 GB, no root)")
            return 1
        print(f"{OK if reachable else BAD} Ollama at {config.ai.ollama_url}"
              f"{'' if reachable else ' - ' + ollama_models.not_running_message()}")
        print(f"\nCurrently configured: {config.ai.ollama_model}")
        if pulled is not None:
            already_have = ollama_models.has_model(pulled, config.ai.ollama_model)
            have = OK if already_have else DOT
            print(f"  {have} {'already pulled' if already_have else 'not pulled yet'}")

        print("\nLightweight models you can pull:")
        for name in ollama_models.LIGHTWEIGHT_MODELS:
            mark = OK if pulled and ollama_models.has_model(pulled, name) else DOT
            star = " (recommended)" if name == ollama_models.RECOMMENDED else ""
            print(f"  {mark} {name:<16} {ollama_models.describe(name)}{star}")
        print("\nPull one with: blackvoice setup --ollama --model <name>")
        print("Add --set-default to also make it the active model.")
        return 0

    name = args.model
    print(f"{ARROW} pulling {name}" + ("" if name in ollama_models.LIGHTWEIGHT_MODELS else " (not in the curated list)"))

    last = [-1]

    def _progress(status: str, done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else None
        if percent is not None:
            if percent == last[0]:
                return
            last[0] = percent
            print(chr(13) + f"  {percent:3d}%  {status}" + " " * 10, end="", flush=True)
        else:
            print(f"  {status}")

    try:
        ollama_models.pull(name, config.ai.ollama_url, on_progress=_progress)
    except ollama_models.OllamaError as exc:
        print()
        print(f"{BAD} {exc}")
        return 1

    print()
    print(f"{OK} {name} installed")

    if args.set_default:
        config.ai.ollama_model = name
        config.ai.provider = "ollama"
        config.save()
        print(f"{OK} set as the active model ({CONFIG_FILE})")
    elif name != config.ai.ollama_model:
        print(f"\n{BULLET} to use it, set ai.ollama_model to {name!r} in")
        print(f"  {CONFIG_FILE}")
        print("  or re-run this with --set-default")

    return 0


def cmd_mic(args: argparse.Namespace) -> int:
    """Show a live level meter, to answer "is the microphone working at all?".

    This is the first thing to run when nothing happens after speaking. It
    separates three failures that look identical from the outside: no device,
    a device that produces silence, and recognition that is not triggering.
    """
    from .audio.mic import Endpointer, Microphone, MicrophoneUnavailable, rms_level

    config = Config.load()

    try:
        from .audio.mic import list_devices

        devices = list_devices()
    except Exception as exc:
        print(f"{BAD} Could not query audio devices: {exc}")
        print()
        print("  On Debian/Ubuntu:  sudo apt install portaudio19-dev")
        return 1

    if not devices:
        print(f"{BAD} No input device found.")
        print()
        print("  Nothing is wrong with Black Voice - the system has no microphone")
        print("  it can see. In a virtual machine this usually means the host mic")
        print("  is not being passed through:")
        print()
        print("    VirtualBox   Settings -> Audio -> Enable Audio Input")
        print("    VMware       Removable Devices -> Sound Card -> Connect")
        print("    virt-manager Add Hardware -> Sound, then check the host mixer")
        print()
        print("  Confirm with:  arecord -l")
        return 1

    chosen = config.audio.input_device
    print("Input devices:")
    for dev in devices:
        mark = ARROW if chosen == dev["index"] else " " * len(ARROW)
        print(f"  {mark} [{dev['index']:2d}] {dev['name']}")
    if chosen is None:
        print(f"\nUsing the system default. Set audio.input_device to pin one.")
    print()

    seconds = max(3, min(60, args.seconds))
    print(f"Listening for {seconds} seconds - speak normally.")
    print(f"The bar should move when you talk. Ctrl+C to stop early.")
    print()

    threshold = config.audio.silence_threshold
    peak = 0.0
    heard = 0
    blocks = 0
    endpointer = Endpointer(config.audio)

    try:
        with Microphone(config.audio) as mic:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                block = mic.read(timeout=1.0)
                if block is None:
                    continue
                blocks += 1
                level = rms_level(block)
                peak = max(peak, level)
                if level >= threshold:
                    heard += 1
                endpointer.feed(block)

                # Scale for display the same way the overlay waveform does.
                filled = int(min(1.0, level * 8.0) * 40)
                bar = "#" * filled + "." * (40 - filled)
                flag = "  <- speech" if level >= threshold else ""
                print(f"{chr(13)}  [{bar}] {level * 100:5.1f}%{flag}   ",
                      end="", flush=True)
    except MicrophoneUnavailable as exc:
        print(f"\n{BAD} {exc}")
        return 1
    except KeyboardInterrupt:
        pass

    print()
    print()

    if blocks == 0:
        print(f"{BAD} The device opened but delivered no audio at all.")
        print("  Check the host mixer, and that the VM is not muted.")
        return 1

    print(f"Blocks captured   {blocks}")
    print(f"Peak level        {peak * 100:.1f}%")
    print(f"Silence threshold {threshold * 100:.1f}%  (audio.silence_threshold, the configured floor)")
    if config.audio.calibrate_noise:
        if endpointer.noise_floor is not None:
            print(f"Calibrated floor  {endpointer.noise_floor * 100:.1f}%  "
                  f"(from the first {config.audio.calibration_seconds:g}s of this room)")
            print(f"Live threshold    {endpointer.threshold * 100:.1f}%  "
                  "(what a real command would actually be judged against)")
        else:
            print(f"{DOT} not enough audio to calibrate in this run - try a longer --seconds")
    print()

    if peak < 0.002:
        print(f"{BAD} Effectively silence. The device exists but hears nothing.")
        print("  The microphone is muted, at zero gain, or not connected to the")
        print("  guest. Check the host mixer and 'alsamixer' inside the VM.")
        return 1

    if heard == 0:
        print(f"{BAD} Audio is arriving but never crosses the speech threshold.")
        print(f"  Lower it so quiet speech registers:")
        print()
        print(f'    "audio": {{ "silence_threshold": {max(0.002, peak * 0.4):.3f} }}')
        return 1

    print(f"{OK} The microphone works - {heard} of {blocks} blocks were speech.")
    print()
    print("  If commands still do nothing, the problem is recognition, not audio.")
    print("  Check that the speech models are installed:  blackvoice doctor")
    return 0


def cmd_voice(args: argparse.Namespace) -> int:
    """Install and try the neural voices.

    espeak-ng is the fallback because it is tiny and always available, but many
    people cannot follow it. Piper sounds like a person; this is the shortest
    path from one to the other.
    """
    from . import piper_install, voices
    from .audio.tts import Speaker, detect_engine

    config = Config.load()

    binary = piper_install.find_binary()
    print(f"piper binary      {binary or '(not installed)'}")
    print(f"current engine    {config.voice.engine} -> {detect_engine()}")
    print()

    print("Voices:")
    for name, (lang, _path, desc) in voices.VOICES.items():
        mark = OK if voices.installed(name) else DOT
        print(f"  {mark} {name:<24} {lang}   {desc}")
    print()
    print(f"Stored in {voices.VOICES_DIR}")
    print()

    if not args.install and not args.test:
        if binary is None:
            print("Piper is not installed. Without it these voices cannot be used:")
            print()
            print("    blackvoice setup --piper")
            print()
        print("To fetch the voices:   blackvoice voice --install")
        print("To hear the result:    blackvoice voice --test")
        return 0

    if args.install:
        wanted = [args.name] if args.name else list(voices.DEFAULT_VOICES.values())
        failed = 0
        for name in wanted:
            if voices.installed(name):
                print(f"{OK} {name} already installed")
                continue

            print(f"{ARROW} downloading {name}")
            last = [-1]

            def _progress(_n: str, done: int, total: int) -> None:
                percent = int(done * 100 / total) if total else 0
                if percent == last[0]:
                    return
                last[0] = percent
                bar = f"  {percent:3d}%  {done / 2**20:6.1f} MiB"
                print(chr(13) + bar, end="", flush=True)

            ok = voices.download(name, on_progress=_progress)
            print()
            if ok:
                print(f"{OK} {name} installed")
            else:
                failed += 1

        if failed:
            print()
            print("Some voices could not be downloaded.")
            return 1

        print()
        print("Now switch to them:")
        print()
        print('    "voice": { "engine": "piper" }')
        print()
        print("  or use the tray icon -> Settings -> Speech output.")

    if args.test:
        print()
        sample = args.text or "Black Voice is ready. Opening firefox."
        speaker = Speaker(config.voice)
        print(f"Speaking with: {speaker.engine}")
        speaker.say(sample)
        speaker.wait_until_idle(timeout=60)
        speaker.shutdown()

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what is installed and what is missing."""
    from .app import Engine

    print(BANNER)
    config = Config.load()
    problems: List[str] = []

    print("Python packages")
    for module, why, fatal in [
        ("sounddevice", "microphone capture", True),
        ("numpy", "audio buffers", True),
        ("vosk", "offline speech recognition", False),
        ("speech_recognition", "online fallback", False),
        ("PyQt6", "tray icon and overlay", False),
        ("requests", "AI and weather", True),
        ("psutil", "battery and system info", False),
    ]:
        try:
            __import__(module)
            print(f"  {OK} {module:<20} {why}")
        except ImportError:
            print(f"  {BAD} {module:<20} {why}")
            if fatal:
                problems.append(f"pip install {module}")

    print("\nSystem tools")
    for tool, why in [
        ("espeak-ng", "text to speech"),
        ("pactl", "volume (PulseAudio)"),
        ("wpctl", "volume (PipeWire)"),
        ("brightnessctl", "screen brightness"),
        ("playerctl", "media keys"),
        ("nmcli", "Wi-Fi"),
        ("notify-send", "desktop notifications"),
        ("gnome-screenshot", "screenshots"),
        ("ollama", "local AI answers (optional)"),
    ]:
        mark = OK if shutil.which(tool) else DOT
        print(f"  {mark} {tool:<20} {why}")

    print("\nAI backend")
    from . import ollama_models

    provider = config.ai.provider
    if provider == "none":
        print(f"  {DOT} switched off - unrecognised phrases just say so")
    elif provider == "ollama":
        binary = ollama_models.find_binary()

        if binary is None:
            print(f"  {BAD} Ollama is not installed anywhere")
            if config.ai.auto_install:
                print(f"      {DOT} ai.auto_install is on - the next run fetches a private "
                      "copy (~1.3 GB) and runs it as a background service, no root")
                problems.append("blackvoice run  # auto_install does the rest")
            else:
                print(f"      {ollama_models.not_running_message()}")
                print(f"      {DOT} or set ai.auto_install: true to fetch one automatically "
                      "(~1.3 GB, no root)")
                problems.append("https://ollama.com/download")
        else:
            private = binary == str(ollama_models.bundled_binary_path())
            label = "Ollama (private copy set up by Black Voice)" if private else "Ollama"
            reachable = ollama_models.is_reachable(config.ai.ollama_url)
            print(f"  {OK if reachable else BAD} {label} at {config.ai.ollama_url}")

            if not reachable:
                message = ("not running - try: systemctl --user start ollama" if private
                            else ollama_models.not_running_message())
                print(f"      {message}")
                problems.append("ollama serve")
            else:
                pulled = ollama_models.pulled_models(config.ai.ollama_url) or []
                have = ollama_models.has_model(pulled, config.ai.ollama_model)
                print(f"  {OK if have else BAD} model {config.ai.ollama_model!r} "
                      f"{'is pulled' if have else 'is not pulled yet'}")
                if not have:
                    problems.append(f"blackvoice setup --ollama --model {config.ai.ollama_model}")
                if config.ai.ollama_model not in ollama_models.LIGHTWEIGHT_MODELS:
                    print(f"      {DOT} not in the curated lightweight list - "
                          "make sure this machine can actually run it")
    elif provider in ("anthropic", "openai"):
        env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        has_key = bool(config.ai.api_key or os.environ.get(env_var))
        print(f"  {OK if has_key else BAD} {provider}: {env_var} "
              f"{'is set' if has_key else 'is not set'}")
        if not has_key:
            problems.append(f"export {env_var}=...")
    else:
        print(f"  {BAD} unknown provider {provider!r}")

    print("\nSpeech engine")
    from .audio.stt import find_whisper_binary

    whisper_binary = find_whisper_binary(config.speech.whisper_binary)
    whisper_model = config.whisper_model_path()
    print(f"  {OK if whisper_binary else DOT} whisper.cpp binary   "
          f"{whisper_binary or 'not found'}")
    print(f"  {OK if whisper_model.exists() else DOT} whisper.cpp model    "
          f"{whisper_model.name if whisper_model.exists() else 'not installed'}")

    if whisper_binary and whisper_model.exists():
        print(f"  {OK} Hinglish in one pass (whisper.cpp leads, Vosk backs it up)")
    elif config.speech.language == "both":
        # Vosk still works on its own - this is not fatal - but it is the
        # single biggest thing standing between this install and a sentence
        # that switches language halfway, so it earns a real problems-list
        # entry rather than being an aside nobody reads until something
        # already sounds wrong.
        print(f"  {BAD} Vosk only: a sentence mixing Hindi and English will")
        print("      lose half of itself.")
        problems.append("blackvoice setup --whisper")
    else:
        print(f"  {DOT} whisper.cpp is not installed, but speech.language is not "
              "'both' - no code-switched sentence to lose half of")

    print("\nModels")
    for lang in ("en", "hi"):
        path = config.model_path(lang)
        if path.exists():
            print(f"  {OK} {lang}  {path}")
        else:
            print(f"  {BAD} {lang}  missing ({path})")

    if not any(config.model_path(l).exists() for l in ("en", "hi")):
        problems.append("blackvoice setup")

    print("\nMicrophones")
    try:
        from .audio.mic import list_devices

        devices = list_devices()
        if devices:
            for dev in devices:
                print(f"  {DOT} [{dev['index']}] {dev['name']}")
        else:
            print(f"  {BAD} no input devices found")
            problems.append("check that a microphone is connected")
    except Exception as exc:
        print(f"  {BAD} {exc}")
        problems.append("install PortAudio: sudo apt install portaudio19-dev")

    print("\nEngine")
    try:
        print("  " + Engine(config).describe().replace("\n", "\n  "))
    except Exception as exc:
        print(f"  {BAD} could not start the engine: {exc}")

    print(f"\nConfig  {CONFIG_FILE}")
    print(f"Log     {LOG_FILE}")

    if problems:
        print("\nTo fix:")
        for item in problems:
            print(f"  {BULLET} {item}")
        return 1

    print("\nEverything looks good.")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Record a corpus of spoken commands, or score the backends over it."""
    from . import evaluate

    root = Path(args.dataset).expanduser() if args.dataset else evaluate.EVAL_DIR
    if args.action == "record":
        return _eval_record(args, root)
    if args.action == "list":
        return _eval_list(root)
    return _eval_run(args, root)


def _eval_list(root: Path) -> int:
    from . import evaluate

    samples = evaluate.load_corpus(root)
    if not samples:
        print(f"No corpus at {root}. Record one with 'blackvoice eval record'.")
        return 1

    print(f"{len(samples)} recordings in {root}\n")
    for sample in samples:
        missing = "" if sample.path(root).exists() else f"  {BAD} audio missing"
        print(f"  {sample.audio:<12} {sample.reference}{missing}")
    return 0


def _eval_record(args: argparse.Namespace, root: Path) -> int:
    """Walk through prompts, recording one utterance each."""
    from . import evaluate
    from .audio.mic import Microphone, MicrophoneUnavailable

    config = Config.load()
    prompts = evaluate.load_prompts(
        Path(args.prompts).expanduser() if args.prompts else None
    )
    if args.count:
        prompts = prompts[: args.count]
    if not prompts:
        print(f"{BAD} no prompts to record")
        return 2

    root.mkdir(parents=True, exist_ok=True)
    index = evaluate.next_index(root)
    existing = len(evaluate.load_corpus(root))

    print(BANNER)
    print(f"Recording into {root}")
    if existing:
        print(f"{existing} recordings are already there; these will be added.")
    print()
    print("Say each line as naturally as you would to the assistant - the point")
    print("is to capture how you actually talk, not a clean dictation. Record a")
    print("few in the room where you use it, with whatever noise is normally on.")
    print()

    try:
        mic = Microphone(config.audio).open()
    except MicrophoneUnavailable as exc:
        print(f"{BAD} {exc}")
        return 1

    saved = 0
    try:
        for number, prompt in enumerate(prompts, 1):
            print(f"[{number}/{len(prompts)}]  {prompt}")
            while True:
                choice = input("  [Enter] record  s skip  q quit > ").strip().lower()
                if choice == "q":
                    print(f"\n{OK} {saved} recordings saved to {root}")
                    return 0
                if choice == "s":
                    break

                mic.drain()
                print("  listening...", end="", flush=True)
                pcm = evaluate.capture_utterance(mic, config.audio)
                seconds = len(pcm) / 2 / config.audio.sample_rate
                if not pcm:
                    print(f"\r  {BAD} nothing was heard; try again")
                    continue
                print(f"\r  {DOT} {seconds:.1f}s captured      ")

                keep = input("  [Enter] keep  r redo  s skip > ").strip().lower()
                if keep == "r":
                    continue
                if keep == "s":
                    break

                name = f"{index:04d}.wav"
                evaluate.write_wav(root / name, pcm, config.audio.sample_rate)
                evaluate.append_sample(
                    root, evaluate.Sample(audio=name, reference=prompt)
                )
                index += 1
                saved += 1
                break
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        mic.close()

    print(f"\n{OK} {saved} recordings saved to {root}")
    print("Score the backends with: blackvoice eval run")
    return 0


def _eval_run(args: argparse.Namespace, root: Path) -> int:
    """Score every available backend over the corpus and print the comparison."""
    from . import evaluate

    config = Config.load()
    samples = evaluate.load_corpus(root)
    if not samples:
        print(f"No corpus at {root}.")
        print("Record one with: blackvoice eval record")
        return 1

    backends = list(evaluate.BACKENDS) if args.backend == "all" else [args.backend]

    print(BANNER)
    print(f"{len(samples)} recordings from {root}")
    print()

    results = evaluate.compare(
        root,
        backends,
        config,
        on_backend=lambda name: print(f"{ARROW} {name}", flush=True),
    )

    usable = {name: r for name, r in results.items() if not r.unavailable}
    for name, result in results.items():
        if result.unavailable:
            print(f"  {DOT} {name} skipped: {result.unavailable}")
    if not usable:
        print(f"\n{BAD} no backend could be run")
        return 1

    # Word error rate is the familiar number; intent accuracy is the one that
    # decides which engine to ship, because it measures whether the mistake
    # changed what the assistant did.
    print()
    print(f"  {'backend':<10} {'WER':>8} {'exact':>8} {'intent':>8} {'median':>9}")
    print(f"  {'-' * 10} {'-' * 8:>8} {'-' * 8:>8} {'-' * 8:>8} {'-' * 9:>9}")
    for name, result in usable.items():
        print(
            f"  {name:<10} {result.wer:>8.1%} {result.exact_match:>8.1%} "
            f"{result.intent_accuracy:>8.1%} {result.median_seconds:>8.2f}s"
        )

    first = next(iter(usable.values()))
    unrouted = [r for r in first.results if not r.is_command]
    print()
    print(f"  {len(first.commands)} of {len(first.results)} references route to a "
          "command; intent accuracy is over those.")
    if unrouted:
        # Worth reading rather than counting. A reference that reaches the AI
        # fallback is either a genuine question or a command the rules cannot
        # parse yet, and only the second kind is a bug - one this harness would
        # otherwise hide, because a hypothesis that also falls through scores
        # as a match.
        print(f"  {len(unrouted)} route to no command, so recognition is not")
        print("  measured on them. Check that each is really a question:")
        for row in unrouted[:8]:
            print(f"    {DOT} {row.sample.reference}")
        if len(unrouted) > 8:
            print(f"    {DOT} ... and {len(unrouted) - 8} more")
    if "whisper" in usable:
        print("  whisper's median includes loading the model, which happens per")
        print("  utterance until the server mode is wired up.")

    broken = [r for r in first.results if r.error]
    if broken:
        print()
        for row in broken[:5]:
            print(f"  {BAD} {row.sample.audio}: {row.error}")

    if args.failures:
        for name, result in usable.items():
            failures = result.failures
            if not failures:
                continue
            print(f"\n{name}: {len(failures)} routing failures")
            for row in failures[: args.failures]:
                print(f"  {DOT} said   {row.sample.reference}")
                print(f"    heard  {row.hypothesis or '(nothing)'}")
                print(f"    wanted {row.expected_intent}  got "
                      f"{row.got_intent or '(nothing)'}")

    return 0


def cmd_devices(args: argparse.Namespace) -> int:
    from .audio.mic import list_devices

    try:
        devices = list_devices()
    except Exception as exc:
        print(exc)
        return 1

    if not devices:
        print("No input devices found.")
        return 1

    print("Input devices:\n")
    for dev in devices:
        print(f"  [{dev['index']:2d}]  {dev['name']}  ({dev['channels']} ch, {dev['sample_rate']} Hz)")
    print("\nSet one with:  audio.input_device  in", CONFIG_FILE)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config = Config.load()
    if args.reset:
        path = Config().save()
        print(f"Configuration reset to defaults: {path}")
        return 0
    if args.path:
        print(CONFIG_FILE)
        return 0
    import json

    print(json.dumps(config.to_dict(), indent=2))
    return 0


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackvoice",
        description=f"{APP_TITLE} - an offline-first voice assistant for Linux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  blackvoice setup              download the offline models\n"
            "  blackvoice                    run with the tray icon\n"
            "  blackvoice run --no-ui        run in the terminal\n"
            "  blackvoice text 'open firefox'\n"
            "  blackvoice doctor             check the installation\n"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_TITLE} {__version__}",
        help="print the version and exit",
    )

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the assistant (default)")
    run.add_argument("--no-ui", action="store_true", help="terminal only, no tray icon")
    run.set_defaults(func=cmd_run)

    text = sub.add_parser("text", help="type commands instead of speaking")
    text.add_argument("command", nargs="*", help="run one command and exit")
    text.set_defaults(func=cmd_text)

    setup = sub.add_parser("setup", help="download the offline speech models")
    setup.add_argument(
        "--language", choices=["en", "hi", "both"], default="both",
        help="which models to fetch (default: both)",
    )
    setup.add_argument("--force", action="store_true", help="re-download even if present")
    setup.add_argument(
        "--whisper", action="store_true",
        help="download a whisper.cpp model instead of the Vosk ones",
    )
    setup.add_argument(
        "--piper", action="store_true",
        help="download the Piper binary itself (voices come from 'blackvoice voice --install')",
    )
    setup.add_argument(
        "--model",
        help="which whisper model (e.g. ggml-small-q5_1.bin) or Ollama model "
             "(e.g. qwen2.5:1.5b) to fetch",
    )
    setup.add_argument(
        "--ollama", action="store_true",
        help="pull a small Ollama model instead of speech models",
    )
    setup.add_argument(
        "--set-default", action="store_true",
        help="with --ollama, also make the pulled model the active one",
    )
    setup.add_argument(
        "--install", action="store_true",
        help="with --ollama and no --model: fetch Ollama itself if it is not "
             "installed anywhere (~1.3 GB, no root) and run it as a --user service",
    )
    setup.set_defaults(func=cmd_setup)

    ev = sub.add_parser("eval", help="measure recognition accuracy on your own voice")
    ev.add_argument(
        "action", nargs="?", default="run", choices=["run", "record", "list"],
        help="record a corpus, score the backends over it, or list it",
    )
    ev.add_argument("--dataset", help="corpus directory (default: the data dir)")
    ev.add_argument("--prompts", help="file of lines to read out, for 'record'")
    ev.add_argument("--count", type=int, help="stop after this many prompts")
    ev.add_argument(
        "--backend", default="all", choices=["all", "whisper", "vosk", "online"],
        help="which backend to score (default: all of them)",
    )
    ev.add_argument(
        "--failures", type=int, default=10, metavar="N",
        help="show up to N misrouted utterances per backend (0 for none)",
    )
    ev.set_defaults(func=cmd_eval)

    doctor = sub.add_parser("doctor", help="check the installation")
    doctor.set_defaults(func=cmd_doctor)

    devices = sub.add_parser("devices", help="list microphones")
    devices.set_defaults(func=cmd_devices)

    mic = sub.add_parser("mic", help="live microphone level meter")
    mic.add_argument("--seconds", type=int, default=15,
                     help="how long to listen (default: 15)")
    mic.set_defaults(func=cmd_mic)

    say = sub.add_parser("say", help="test text to speech")
    say.add_argument("text", nargs="*")
    say.set_defaults(func=cmd_say)

    voice = sub.add_parser("voice", help="install and try the neural voices")
    voice.add_argument("--install", action="store_true",
                       help="download the Piper voices")
    voice.add_argument("--test", action="store_true", help="speak a sample")
    voice.add_argument("--name", help="a specific voice rather than the defaults")
    voice.add_argument("--text", help="what to say for --test")
    voice.set_defaults(func=cmd_voice)

    config = sub.add_parser("config", help="show or reset the configuration")
    config.add_argument("--path", action="store_true", help="print the config file path")
    config.add_argument("--reset", action="store_true", help="restore the defaults")
    config.set_defaults(func=cmd_config)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Belt and braces: a stray non-ASCII character in a filename or an error
    # message must not kill the program on a non-UTF-8 console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose, quiet=args.quiet)

    if not getattr(args, "func", None):
        args.func = cmd_run
        args.no_ui = False

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
