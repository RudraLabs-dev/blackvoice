# Black Voice

**An offline-first voice assistant for Linux that speaks Hindi, English and Hinglish.**

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
| **[Voice Commands](Voice-Commands)** | Every command, in both languages |

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

Most people do not speak one language at a time, and the software should keep
up. With [whisper.cpp](https://github.com/ggml-org/whisper.cpp) installed
(`blackvoice setup --whisper`), one model transcribes the whole sentence in
whichever script it was actually said in — that is what makes a sentence that
switches from Hindi to English mid-way work. Without it, [Vosk](https://alphacephei.com/vosk/)
falls back to two models racing on the same audio, English and Hindi, and the
more confident transcript wins — a weaker trick, since neither model can
produce a sentence that needs words from both, but it needs nothing installed
beyond what ships already.

Set `speech.mode` to `"offline"` and nothing ever leaves the machine, either way.

## Requirements

| | |
|---|---|
| OS | Linux (GNOME, KDE, Xfce, or a tiling window manager) |
| Python | 3.9 or newer |
| Audio | PortAudio, plus PulseAudio, PipeWire or ALSA |
| Disk | ~90 MB for the two offline speech models |
| Network | Optional — only for the cloud fallback and the AI backend |

## Project status

Version 0.3.0. The command routing, safety guard, configuration and skill layers
are covered by 285 tests. The audio path — microphone capture, recognition,
wake word and speech output — needs a real Linux machine with a microphone to
exercise, so treat it as the least-tested part of the system and report what
breaks. `blackvoice eval` exists for exactly that: recording your own voice
against a set of prompts and scoring what each recognition backend actually
gets right, rather than trusting a claim about accuracy in the abstract.

---

<sub>Black Voice is a [Rudra Labs](https://rudralabs.dev) product · MIT licensed</sub>
