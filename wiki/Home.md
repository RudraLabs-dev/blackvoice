# Black Voice

**An offline-first voice assistant for Linux.**

Say **“Black”**, then tell it what to do. It opens applications, controls the
system, finds files, runs shell commands behind a safety guard, sets timers,
reads the weather and answers questions.

Speech recognition runs on your own machine by default. Nothing is uploaded
unless the offline pass is unsure *and* you have allowed a cloud fallback.

```
  you    black, firefox kholo
  black  Opening firefox.

  you    black, volume 40
  black  Volume set to 40 percent.

  you    black, run command df -h
  black  Run df -h? Say yes to confirm.
```

---

## Start here

| | |
|---|---|
| **[Installation](Installation)** | Install it, including the system packages most guides forget |
| **[Getting Started](Getting-Started)** | First run, the wake word, and what to say first |
| **[Voice Commands](Voice-Commands)** | Every command it understands |

## Go deeper

| | |
|---|---|
| **[Configuration](Configuration)** | Every setting in `config.json`, and the environment overrides |
| **[Architecture](Architecture)** | How audio becomes an action |
| **[Security Model](Security-Model)** | Why a misheard word cannot wipe your disk |
| **[Writing Skills](Writing-Skills)** | Add your own commands |
| **[Troubleshooting](Troubleshooting)** | When something does not work |
| **[Contributing](Contributing)** | Development setup and the test suite |
| **[FAQ](FAQ)** | Short answers to common questions |

---

## Why offline first

Most voice assistants send your microphone to someone else's server. Black Voice
does the opposite: recognition runs locally on every utterance, and the network
is something you opt into rather than depend on.

Recognition still has two tiers. With [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
installed (`blackvoice setup --whisper`), a more accurate model transcribes
the command. Without it, [Vosk](https://alphacephei.com/vosk/) — smaller,
faster, and what ships already — handles it, and also does the wake-word
detection either way.

Set `speech.mode` to `"offline"` and nothing ever leaves the machine, either way.

## Requirements

| | |
|---|---|
| OS | Linux (GNOME, KDE, Xfce, or a tiling window manager) |
| Python | 3.9 or newer |
| Audio | PortAudio, plus PulseAudio, PipeWire or ALSA |
| Disk | ~40 MB for the offline speech model |
| Network | Optional — only for the cloud fallback and the AI backend |

## Project status

Version 0.6.1. The command routing, safety guard, configuration and skill layers
are covered by 393 tests. The audio path — microphone capture, recognition,
wake word and speech output — needs a real Linux machine with a microphone to
exercise, so treat it as the least-tested part of the system and report what
breaks. `blackvoice eval` exists for exactly that: recording your own voice
against a set of prompts and scoring what each recognition backend actually
gets right, rather than trusting a claim about accuracy in the abstract.

Recent work focused on making it feel less like a command-line tool that
happens to listen and more like an assistant: calibrated, hysteresis-based
endpointing so it stops listening promptly instead of lingering in a noisy
room (`blackvoice mic` shows the live calibrated threshold), an automatic
private install of the Piper voice engine itself (not just its
voices) so a fresh install sounds natural rather than falling back to
espeak-ng, and Ollama replies that are spoken sentence by sentence as they
are generated instead of only after the whole answer finishes.

---

<sub>Black Voice is a [Rudra Labs](https://rudralabs.dev) product · MIT licensed</sub>
