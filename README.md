<div align="center">

<img src="assets/logo.svg" width="140" alt="Black Voice">

# Black Voice

**An offline-first voice assistant for Linux — speaks Hindi, English and Hinglish.**

<sub>A RUDRA LABS PRODUCT</sub>

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-black.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-black.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-linux-black.svg)](#requirements)
[![Tests](https://img.shields.io/badge/tests-285%20passing-black.svg)](tests/)

**[Documentation](https://github.com/RudraLabs-dev/blackvoice/wiki)** ·
[Installation](https://github.com/RudraLabs-dev/blackvoice/wiki/Installation) ·
[Commands](https://github.com/RudraLabs-dev/blackvoice/wiki/Voice-Commands) ·
[Configuration](https://github.com/RudraLabs-dev/blackvoice/wiki/Configuration)

</div>

---

Say **“Black”**, then tell it what to do.

```
  you    black, firefox kholo
  black  Opening firefox.

  you    black, volume 40
  black  Volume set to 40 percent.

  you    black, run command df -h
  black  Run df -h? Say yes to confirm.
```

Black Voice controls your desktop by voice — applications, volume, brightness,
files, timers, media — and answers questions through a language model of your
choosing. Speech recognition runs **on your own machine**. Nothing is uploaded
unless the offline pass is unsure *and* you have allowed a cloud fallback.

## Why it exists

Most voice assistants send your microphone to someone else's server, understand
one language at a time, and give a shell whatever they think they heard. Black
Voice takes the opposite position on all three.

**Offline first.** [Vosk](https://alphacephei.com/vosk/) runs locally on every
utterance. The network is something you opt into, not a dependency you inherit.
Set `speech.mode` to `"offline"` and nothing ever leaves the machine.

**Actually bilingual.** With `language: "both"`, the English and Hindi models
transcribe the *same audio* and the more confident transcript wins. There is no
language-detection step to get wrong — which is why *“Black, firefox kholo”*
works as well as *“Black, open firefox”*, and why a sentence that switches
halfway still lands.

**Safe with a shell.** Voice recognition mishears things; that is the normal
operating condition, not an edge case. Destructive commands are refused in code,
anything that changes the system asks first, and only a short read-only list runs
straight away. See the [security model](https://github.com/RudraLabs-dev/blackvoice/wiki/Security-Model).

**Runs on the desktop you actually have.** Every action probes for the tool that
is installed — PipeWire before PulseAudio before ALSA, `brightnessctl` before
`light` before raw sysfs, six different screenshot tools — so it works on GNOME,
KDE, Xfce and the tiling window managers without configuration.

## What it does

| | |
|---|---|
| **Applications** | Open and close programs by name, with aliases that resolve to whatever is installed |
| **System** | Volume, brightness, screenshots, screen lock, Wi-Fi, Bluetooth, power |
| **Files** | Search your home directory, open standard folders, create folders, check disk usage |
| **Terminal** | Run shell commands behind a three-tier safety guard |
| **Everyday** | Clock, weather, timers, reminders, notes, web search, media keys, arithmetic |
| **Questions** | Anything unrecognised goes to a local or hosted language model |
| **Interface** | Tray icon with a popup overlay, or fully headless for servers and SSH |

The full command reference, in both languages, is in the
**[wiki](https://github.com/RudraLabs-dev/blackvoice/wiki/Voice-Commands)**.

## Install

**Debian, Ubuntu, Mint, Pop!\_OS**

```bash
sudo apt install ./blackvoice_0.5.2-1_amd64.deb
```

**Fedora, RHEL, openSUSE**

```bash
sudo dnf install ./blackvoice-0.5.2-1.x86_64.rpm
```

Packages are on the
[releases page](https://github.com/RudraLabs-dev/blackvoice/releases). They run on
your system Python and pull in what your distribution already packages, bundling
only the four libraries no distribution ships — which is why they are about
15 MB rather than 150.

**From source — any distribution**

```bash
git clone https://github.com/RudraLabs-dev/blackvoice.git
cd blackvoice
./install.sh --system
```

That is the whole installation. On its first run Black Voice downloads the
offline speech models (~90 MB) into your home directory and says so while it
does; there is no setup step to remember.

```bash
blackvoice doctor    # optional: check what is installed
```

For the source route, `--system` installs the distro packages (PortAudio,
espeak-ng, playerctl and friends) and needs sudo. Without it only the Python
side is installed, and `doctor` tells you exactly what is missing.

→ Manual install, per-distro package lists and what each one is for:
**[Installation](https://github.com/RudraLabs-dev/blackvoice/wiki/Installation)**

### Requirements

| | |
|---|---|
| OS | Linux |
| Python | 3.9 or newer |
| Audio | PortAudio, plus PulseAudio, PipeWire or ALSA |
| Disk | ~90 MB for the offline speech models |
| Network | Optional |

## Use

```bash
blackvoice                 # tray icon and popup overlay
blackvoice run --no-ui     # terminal only — no display needed
blackvoice text            # type commands instead of speaking them
blackvoice text "open firefox"
blackvoice doctor          # what is installed, what is missing
blackvoice mic             # live level meter — is the microphone working?
blackvoice voice --install # a neural voice you can actually understand
blackvoice devices         # list microphones
blackvoice say "hello"     # test speech output
blackvoice setup --ollama  # see and fetch small local models for the AI skill
```

Every setting is editable from the tray icon → **Settings**, including whether
open questions go to a language model at all, which model, and its endpoint.
The config file is still there if you prefer it.

Start it on login:

```bash
systemctl --user enable --now blackvoice
```

There is no global hotkey built in, because every desktop grabs keys differently
and one that silently fails is worse than none. Bind
`~/.local/bin/blackvoice text` in your desktop's keyboard settings, or click the
tray icon.

## A taste of the commands

| | English | Hindi / Hinglish |
|---|---|---|
| Apps | `open firefox` | `firefox kholo` |
| Volume | `volume 40` · `mute` | `awaaz badhao` |
| Screen | `screenshot` · `lock screen` | `screenshot lo` |
| Files | `find file report.pdf` | `downloads kholo` |
| Shell | `run command df -h` | `terminal me ls chalao` |
| Time | `what time is it` | `kitne baje hain` |
| Timers | `set timer for 5 minutes` | `10 minute ka timer` |
| Notes | `take a note buy milk` | `mere notes padho` |
| Meta | `help` · `stop` · `go to sleep` | `madad` · `ruko` · `so jao` |

Mixing languages mid-sentence is fine. Anything matching none of the rules
becomes a question for the AI backend.

→ All 42 rules, with slots and matching order:
**[Voice Commands](https://github.com/RudraLabs-dev/blackvoice/wiki/Voice-Commands)**

## How it works

```
  microphone ──► wake word ──► hybrid STT ──► router ──► skill ──► speaker
                  (Vosk        (Vosk, then    (regex     (system, files,   (piper /
                   grammar)     cloud if       rules)     terminal, ai,     espeak-ng)
                                unsure)                   utils)
                        │                          │
                        └──────── event bus ───────┴──► tray icon + overlay
```

The wake word uses a restricted Vosk grammar, so idle CPU stays low — it only
has to decide between the wake phrases and “not that”. Recording ends on silence
rather than on the recogniser's own boundaries, so it behaves the same whichever
recognition path is active.

→ Threads, portability, graceful degradation:
**[Architecture](https://github.com/RudraLabs-dev/blackvoice/wiki/Architecture)**

## Safety

Shell access is deliberately narrow:

- **Refused outright** — `rm -rf`, `mkfs`, `dd` to a device, fork bombs,
  `curl … | sh`, anything under `sudo` or `pkexec`. These never run.
- **Asks first** — anything that could change the system, and anything chaining
  commands with `;`, `&&` or a pipe.
- **Runs immediately** — a short read-only list (`ls`, `df`, `cat`, `git status`, …).

Power actions always confirm. Arithmetic walks an AST rather than calling `eval`.
Brightness never drops below 5%. Folder names are stripped of anything that could
traverse a path.

→ The full model, including what it does *not* protect against:
**[Security Model](https://github.com/RudraLabs-dev/blackvoice/wiki/Security-Model)**

## Configuration

`~/.config/blackvoice/config.json`, created on first run.

```jsonc
{
  "speech": { "mode": "hybrid", "language": "both" },
  "wake":   { "phrases": ["black"] },
  "voice":  { "engine": "auto", "rate": 165 },
  "ai":     { "provider": "ollama", "ollama_model": "llama3.2" },
  "safety": { "confirm_shell": true },
  "skills": { "weather_city": "Jaipur" }
}
```

Every value can be overridden by an environment variable, which is handy in the
systemd unit:

```bash
BLACKVOICE_SPEECH_MODE=offline BLACKVOICE_AI_PROVIDER=none blackvoice
```

→ Every setting, with defaults and trade-offs:
**[Configuration](https://github.com/RudraLabs-dev/blackvoice/wiki/Configuration)**

## Documentation

The wiki is the full documentation. It is generated from the `wiki/` folder in
this repository, so it is reviewed alongside the code.

| | |
|---|---|
| [Installation](https://github.com/RudraLabs-dev/blackvoice/wiki/Installation) | Per-distro packages and what each one is for |
| [Getting Started](https://github.com/RudraLabs-dev/blackvoice/wiki/Getting-Started) | First run, the wake word, what to say first |
| [Voice Commands](https://github.com/RudraLabs-dev/blackvoice/wiki/Voice-Commands) | Every command in both languages |
| [Configuration](https://github.com/RudraLabs-dev/blackvoice/wiki/Configuration) | Every setting explained |
| [Architecture](https://github.com/RudraLabs-dev/blackvoice/wiki/Architecture) | How audio becomes an action |
| [Security Model](https://github.com/RudraLabs-dev/blackvoice/wiki/Security-Model) | The shell guard in detail |
| [Writing Skills](https://github.com/RudraLabs-dev/blackvoice/wiki/Writing-Skills) | Add your own commands |
| [Troubleshooting](https://github.com/RudraLabs-dev/blackvoice/wiki/Troubleshooting) | Symptom to fix |
| [Contributing](https://github.com/RudraLabs-dev/blackvoice/wiki/Contributing) | Development setup and the test suite |
| [FAQ](https://github.com/RudraLabs-dev/blackvoice/wiki/FAQ) | Short answers |

## Development

```bash
pip install -e ".[all,dev]"
pytest -q                  # 165 tests, no microphone required
blackvoice text            # exercise the router without speaking
```

Adding a command is three steps: write a `Skill` subclass with a `handle`
method, add its patterns to `RULES` in `blackvoice/nlu/intents.py`, and register
it in `Engine.__init__`. Put specific rules before general ones — the router
returns the first match.

```
blackvoice/
  app.py            engine: audio loop, state machine, confirmations
  cli.py            command line
  config.py         dataclass config and environment overrides
  audio/            mic, hybrid STT, speech output, wake word
  nlu/              intent patterns (Hindi + English) and the router
  skills/           system, files, terminal, ai, utils, control
  ui/               tray icon, overlay, the painted logo
  core/             event bus, logging, shell safety
wiki/               documentation, published by a workflow
```

Documentation lives in `wiki/` and is published to the GitHub wiki by
`.github/workflows/wiki-sync.yml` on every push that touches it. Editing the
wiki on GitHub directly does not work — the next sync overwrites it. Change
`wiki/` and open a pull request instead.

## Project status

Version 0.1.0. The routing, safety guard, configuration and skill layers are
covered by 165 tests. The audio path — microphone capture, Vosk recognition,
wake word and speech output — needs a real Linux machine with a microphone to
exercise, so treat it as the least-proven part and please report what breaks.

Issues and pull requests are welcome:
[RudraLabs-dev/blackvoice/issues](https://github.com/RudraLabs-dev/blackvoice/issues)

## Licence

MIT © 2026 Rudra Labs. See [LICENSE](LICENSE).
