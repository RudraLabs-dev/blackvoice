# FAQ

### Does it work without internet?

Yes, that is the point. Set `speech.mode` to `"offline"` and nothing ever leaves
the machine. In the default `hybrid` mode it only reaches the network when the
offline pass is unsure *and* you are online.

The AI question-answering skill needs a backend — the default, Ollama, is also
local.

### Is my voice sent anywhere?

In `offline` mode, no. In `hybrid`, only when Vosk's confidence falls below
`fallback_confidence` and a connectivity check succeeds. In `online`, always.

If this matters to you, set `"mode": "offline"` and stop wondering. See
[Security Model → Privacy](Security-Model#privacy).

### Does it really understand Hinglish?

Yes, and not by detecting the language first. When whisper.cpp is installed
(`blackvoice setup --whisper`) it transcribes the whole sentence with one
vocabulary that covers both scripts, so *"Black, firefox kholo"* comes back as
one sentence rather than two guesses to reconcile.

Without whisper.cpp, the fallback is two Vosk models racing on the same audio,
one English and one Hindi, and the more confident result wins. That is a
weaker trick — a Vosk model can only emit words from its own lexicon, so on a
sentence that switches language halfway neither model has the whole thing, and
picking the more confident wrong half is not a fix. It is why whisper.cpp is
worth installing if Hinglish is how you actually talk to it: see [Why not just
Vosk?](#why-not-just-vosk) below.

### Will it run on Windows or macOS?

No. It is Linux-only by design — the system commands, desktop integration and
packaging are all Linux. The core routing and skills are portable, but the parts
that make it useful are not.

### Why not just Vosk?

It still does one job on its own: the **wake word**. Listening for "black" all
day needs something that streams and costs almost nothing at idle, and a
restricted Vosk grammar — deciding only between the wake phrases and
`[unk]` — is exactly that.

**Transcribing the command itself** is a different job with a different best
tool. `blackvoice setup --whisper` fetches a small
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) model; once it is
there, `speech.engine: "auto"` (the default) uses it to transcribe whatever
was said between the wake word and the silence that ends the utterance, with
Vosk's two-model race kept as the fallback when whisper.cpp is not installed
or fails. It is a native binary run as a subprocess, the same arrangement
Piper already uses for speech output, rather than a Python package — the
libraries a Python Whisper binding would need (`ctranslate2`, `onnxruntime`)
ship one build per Python version, which does not survive a distribution
upgrading its system Python the way the `.deb`/`.rpm` packages need to.

So: Vosk for streaming keyword-spotting, whisper.cpp for one-shot
transcription of what was actually said. Using Vosk for both was the earlier
design, and the sentence above about why is still true of the wake word —
just not, any more, of the whole pipeline.

Want to know how well it actually works on your own voice, in your own
accent, before deciding whether to install it? `blackvoice eval record` walks
through a set of prompts and records them; `blackvoice eval run` scores every
available backend on the same recordings for word error rate and, more to the
point, whether the mistake actually changed which command ran.

### Can I change the wake word?

Yes, in `wake.phrases`. A two-word trigger is much more reliable than a single
short word:

```jsonc
"wake": { "phrases": ["hey black", "ok black"] }
```

“black” on its own is close enough to **back**, **block**, **blank** and **lack**
that ordinary conversation can set it off.

### Can I use it without a wake word?

Yes. Click the tray icon, or use `blackvoice text`. Set `wake.enabled` to `false`
to switch the listener off entirely.

### Why is there no global hotkey?

Because every desktop environment grabs keys differently and a hotkey that works
on GNOME but silently fails on i3 is worse than none. Bind
`~/.local/bin/blackvoice text` in your desktop's own keyboard settings.

### How much CPU does it use while idle?

The wake-word detector uses a restricted Vosk grammar, so it only has to decide
between the wake phrases and “not that”. That is much cheaper than full
recognition, which only starts once you have been heard.

### Can it run headless, on a server or over SSH?

Yes:

```bash
blackvoice run --no-ui
```

No Qt, no tray, no display needed. You still need a microphone on that machine.

### Will it run my commands as root?

Never. `sudo`, `doas`, `pkexec` and `su` are refused before anything else is
checked. If a task needs root, it tells you to run it yourself.

### Can I make it run shell commands without confirming?

`"safety": { "confirm_shell": false }`. The deny-list still applies —
`rm -rf`, `mkfs`, `dd` to a device and the rest stay refused. It is not
recommended; read [Security Model](Security-Model) first.

### I upgraded and my safety settings look old

They are. `blocked_patterns` is written into your config on first run and never
touched again, so new default patterns do not reach an existing config. Run
`blackvoice config --reset` or merge them by hand.

### Which AI backend should I use?

**Ollama** if you want everything local and private — this is the default.
**Claude** or **OpenAI** if you want better answers and do not mind the
questions leaving your machine. **None** if you only want commands.

If Ollama is already installed, there is nothing else to do — Black Voice
wakes it and pulls a model on first run by itself. If it is not installed at
all, set `ai.auto_install: true` and Black Voice fetches a private copy for
you too (about 1.3 GB — there is no small build, which is the whole reason
this is opt-in rather than the default). Either way, pick a model sized for
the machine it runs on rather than typing one in from memory:

```bash
blackvoice setup --ollama --install                           # only if Ollama itself is missing
blackvoice setup --ollama                                     # see what's known to be light
blackvoice setup --ollama --model qwen2.5:1.5b --set-default  # fetch it and switch to it
```

The tray icon → **Settings** → **AI** offers the same list as a dropdown,
editable if you want a model that is not on it. See [Configuration → AI
backend](Configuration#ai--the-question-answering-backend).

### Does it remember the conversation?

The AI skill keeps the last six turns for context. Clear it from the tray menu →
*Clear AI conversation*. Nothing is written to disk.

### Do timers survive a restart?

No. Timers and reminders live in memory only. Use `at` or a calendar for
anything that matters.

### Where are my notes stored?

`~/.local/share/blackvoice/notes.md`, as plain markdown with timestamps. Edit it
in any editor.

### Can I add my own commands?

Yes — a skill class, some regex patterns, one line to register it. See
[Writing Skills](Writing-Skills).

### It hears me but does the wrong thing

That is a routing problem, not a recognition one. Confirm it:

```bash
blackvoice text "the exact phrase"
```

If text mode also does the wrong thing, open an issue with the phrase. If text
mode is correct, the problem is recognition — see
[Troubleshooting](Troubleshooting#recognition-is-poor).

### Why does it sound robotic?

If you are hearing espeak-ng, it is because Piper — the natural-sounding
voice — was not available. It should install itself: on first run, if no
`piper` binary is found anywhere, Black Voice fetches a private, per-user
copy automatically (`voice.piper_auto_install`, on by default), and the
first Piper voice it actually needs downloads the same way
(`voice.piper_auto_download`). Check what actually happened with:

```bash
blackvoice voice
blackvoice setup --piper    # fetch the binary right now, rather than on next run
```

If Piper *is* the engine and it still sounds wrong, the likely cause is not
Piper itself: a Hinglish or Hindi reply written in Roman script (no Devanagari
at all — the AI backend does this deliberately, matching how the question was
asked) used to be routed to the English voice, which guesses at pronunciations
it was never trained on. That is fixed by detecting common romanised Hindi
words, not by anything voice-related — see [Configuration](Configuration#voice--speech-output).

### Is it production ready?

Version 0.6.1. The command routing, safety guard, configuration and skill layers
have 473 tests. The audio path needs a real machine with a microphone to
exercise properly, so treat that as the least-proven part and report what breaks.

### How do I uninstall it?

```bash
./install.sh --uninstall
rm -rf ~/.config/blackvoice ~/.local/share/blackvoice ~/.cache/blackvoice
```

The first command removes the program; the second removes your config, models
and notes.

### Who makes this?

[Rudra Labs](https://rudralabs.dev). MIT licensed — use it, fork it, ship it.
