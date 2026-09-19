# Configuration

Every setting below is editable from the tray icon → **Settings**, which writes
the same file. The window is generated from the configuration itself, so it
never falls behind what the program understands.

Configuration lives at `~/.config/blackvoice/config.json`. It is created with
defaults on first run.

```bash
blackvoice config           # print the current configuration
blackvoice config --path    # print the file path
blackvoice config --reset   # restore the defaults
```

> **After upgrading:** your existing config is never overwritten, so new default
> settings — including new entries in `safety.blocked_patterns` — do not reach
> it. Merge them by hand or run `blackvoice config --reset`.

## Environment overrides

Any value can be overridden by an environment variable named
`BLACKVOICE_<SECTION>_<KEY>`:

```bash
BLACKVOICE_SPEECH_MODE=offline BLACKVOICE_AI_PROVIDER=none blackvoice
```

Useful in the systemd unit:

```ini
[Service]
Environment=BLACKVOICE_SPEECH_MODE=offline
Environment=BLACKVOICE_UI_ENABLED=false
```

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`. Lists are
comma-separated. A value that will not parse is ignored with a warning rather
than crashing the assistant.

---

## `speech` — recognition

```jsonc
"speech": {
  "mode": "hybrid",
  "engine": "auto",
  "model_en": "vosk-model-small-en-us-0.15",
  "model_hi": "vosk-model-small-hi-0.22",
  "language": "both",
  "whisper_binary": "",
  "whisper_model": "ggml-base-q5_1.bin",
  "whisper_language": "auto",
  "fallback_confidence": 0.55,
  "online_timeout": 6.0,
  "auto_download": true
}
```

Three offline tiers are tried in order, cloud only behind all of them:

1. **whisper.cpp**, when `engine` allows it and both the binary and model are
   present — one vocabulary covering Hindi and English, so it can transcribe a
   sentence that switches language halfway instead of racing two guesses.
2. **Vosk**, always loaded — the fallback when whisper.cpp is absent or came
   back with nothing, and the only one of the three that streams, which is why
   it also does wake-word detection on its own.
3. **The cloud**, only in `hybrid` mode and only when the offline pass was
   unsure.

| Key | Default | What it does |
|---|---|---|
| `mode` | `hybrid` | `offline` — never touches the network. `online` — cloud only. `hybrid` — offline first, cloud only when unsure. |
| `engine` | `auto` | `auto` uses whisper.cpp when it is installed and quietly falls back to Vosk otherwise. `whisper` or `vosk` pin one. |
| `language` | `both` | Which Vosk model(s) to load — `en`, `hi`, or `both`. Only matters for the Vosk tier; whisper.cpp's `whisper_language` is separate. |
| `whisper_binary` | *(blank)* | Path to `whisper-cli`. Blank searches `PATH`, then the copy a `.deb`/`.rpm` bundles. |
| `whisper_model` | `ggml-base-q5_1.bin` | GGML model file — see [Speech models](Installation#speech-models). A bare name resolves under `~/.local/share/blackvoice/models`. |
| `whisper_language` | `auto` | `auto` detects per utterance — the setting to leave alone, since pinning a language is exactly what breaks a sentence that switches halfway. `en` or `hi` force one. |
| `fallback_confidence` | `0.55` | Below this Vosk score, `hybrid` retries online. Only reached when whisper.cpp did not answer. |
| `model_en` / `model_hi` | small models | A bare name resolves under `~/.local/share/blackvoice/models`; an absolute path is used as-is. |
| `online_timeout` | `6.0` | Seconds to wait on the cloud recogniser. |
| `auto_download` | `true` | Fetch the Vosk models on first run when they are missing. whisper.cpp is opt-in and not part of this — see below. |

Nothing is downloaded when `mode` is `"online"` — that configuration never uses
a local model.

**Privacy:** set `mode` to `"offline"` and no audio is ever sent anywhere. In
`hybrid`, audio only leaves the machine when the offline pass is unsure *and*
you are online.

**Installing whisper.cpp** is opt-in because it is a compiled binary rather
than a Python package, and downloading one automatically on first run is a
different kind of decision than fetching a Vosk model:

```bash
blackvoice setup --whisper                        # base model, ~57 MB
blackvoice setup --whisper --model ggml-small-q5_1.bin --force
```

`blackvoice doctor` reports whether the binary and the model are both present
— either one missing and `engine: "auto"` silently uses Vosk instead.

**Using a larger Vosk model** — the small models are ~50 MB and tuned for
commands. For better accuracy without whisper.cpp, download a larger one and
point at it:

```jsonc
"model_en": "/home/you/models/vosk-model-en-us-0.22"
```

Loading two large models doubles the memory cost, so consider
`"language": "en"` if you do.

**Is it working well on your voice?** `blackvoice eval record` walks through a
set of prompts covering English, Hindi and Hinglish and records your voice
saying them; `blackvoice eval run` scores every backend you have installed
against the same recordings — word error rate, and intent accuracy, which is
the number that matters: whether a mishearing actually changed which command
ran.

## `wake` — the wake word

```jsonc
"wake": {
  "enabled": true,
  "phrases": ["black", "blek", "blak"],
  "hotkey": "Ctrl+Alt+Space",
  "chime": true,
  "language": ""
}
```

| Key | Default | What it does |
|---|---|---|
| `enabled` | `true` | Turn off to use only the tray icon and text input. |
| `phrases` | three spellings of “black” | Anything in this list activates it. The extra spellings catch how the recogniser writes the word. |
| `hotkey` | `Ctrl+Alt+Space` | **Display only.** Black Voice does not grab keys — bind this in your desktop's keyboard settings. |
| `chime` | `true` | Short beep when it starts listening. |
| `language` | *(blank)* | Which Vosk model spots the wake word — `en` or `hi`. Blank follows `speech.language` (`"both"` maps to `en`). Set this only if the wake word itself is said in Hindi on a Hindi-primary install; before this existed it was silently always English regardless of `speech.language`. |

### False triggers

A single short word is a weak wake word. “black” is close enough to **back**,
**block**, **blank**, **lack** and **slack** that ordinary conversation can set
it off. A two-word trigger is far more reliable:

```jsonc
"phrases": ["hey black", "ok black"]
```

## `audio` — the microphone

```jsonc
"audio": {
  "sample_rate": 16000,
  "block_size": 8000,
  "input_device": null,
  "silence_threshold": 0.012,
  "silence_timeout": 0.8,
  "max_command_seconds": 12.0,
  "calibrate_noise": true,
  "calibration_seconds": 1.0,
  "calibration_margin": 1.6
}
```

| Key | Default | What it does |
|---|---|---|
| `input_device` | `null` | System default. Use `blackvoice devices` to find an index. |
| `silence_threshold` | `0.012` | A floor, not the last word: with `calibrate_noise` on, the level actually used while listening is raised above this to match the room. Run `blackvoice mic` to see both numbers for yours. |
| `silence_timeout` | `0.8` | Seconds of silence that end a command. Lower than it used to be — endpointing now tests loudness several times a second instead of once per half-second block, so this no longer carries a hidden rounding delay on top of it. Raise it if it still cuts you off mid-sentence. |
| `max_command_seconds` | `12.0` | Hard cap on one utterance. |
| `sample_rate` | `16000` | What the Vosk models expect. Changing it breaks recognition. |
| `calibrate_noise` | `true` | Measure this room's ambient noise for about a second at the start of each command and raise the effective silence threshold to clear it, instead of trusting one fixed number for every room — the fix for "keeps listening long after I stop talking" in a room with any background noise. |
| `calibration_seconds` | `1.0` | How much initial audio is used to measure the noise floor. |
| `calibration_margin` | `1.6` | The effective threshold is the measured noise floor times this much headroom, capped well above the configured floor so one loud noise during calibration cannot deafen the rest of the utterance. |

`blackvoice mic` shows both the configured floor and the live calibrated
threshold for the room you are actually in — the first thing to run if
listening still feels wrong after a config change.

## `voice` — speech output

```jsonc
"voice": {
  "engine": "auto",
  "rate": 145,
  "volume": 0.9,
  "voice_en": "en-us",
  "voice_hi": "hi",
  "piper_voice_en": "en_US-lessac-medium",
  "piper_voice_hi": "hi_IN-pratham-medium",
  "piper_auto_download": true,
  "piper_auto_install": true,
  "piper_model": ""
}
```

`engine` is `auto` by default and picks the best available: **piper** (neural,
sounds like a person), then **espeak-ng** (tiny, instant, speaks Hindi), then
**spd-say**, then **pyttsx3**. Set it explicitly to force one, or `"none"` to
print replies instead of speaking them.

Hindi is detected two ways, not just by Devanagari script: an AI reply written
in Hinglish (romanised Hindi, no Devanagari at all) is recognised by a short
list of common Hindi words, so it is spoken with `voice_hi` too rather than an
English voice guessing at pronunciations it was never trained on.

```bash
blackvoice say "testing one two three"
```

**Piper is installed automatically.** `piper_auto_install` (on by default)
fetches the Piper *program* itself on first run if it is not found anywhere —
a private, per-user install, no root, about 25 MB, the same size class as the
speech models this project already downloads without asking. `piper_auto_download`
(also on by default) separately fetches the *voice* (`piper_voice_en` /
`piper_voice_hi`, ~60 MB each) the first time it is actually needed. Turn
either off and Black Voice falls back to espeak-ng, which is always available
but sounds noticeably more robotic. Fetch the program by hand, or check
whether it is already there:

```bash
blackvoice setup --piper
blackvoice voice           # shows the installed binary and voices
```

For a specific `.onnx` voice file, set `piper_model` to its absolute path —
this overrides `piper_voice_en`/`piper_voice_hi`.

## `ai` — the question-answering backend

```jsonc
"ai": {
  "provider": "ollama",
  "ollama_url": "http://localhost:11434",
  "ollama_model": "llama3.2",
  "auto_setup": true,
  "auto_install": false,
  "anthropic_model": "claude-opus-5",
  "openai_model": "gpt-4o-mini",
  "api_key": "",
  "max_tokens": 512,
  "timeout": 30.0,
  "system_prompt": "..."
}
```

| Key | Default | What it does |
|---|---|---|
| `auto_setup` | `true` | With `provider: "ollama"`: on first run, wake an already-installed Ollama if it is stopped, and pull `ollama_model` if it is not there yet. |
| `auto_install` | `false` | On first run, if Ollama is not found *anywhere*, fetch a private copy for this user and run it as a background service. Off by default - see why below. |

### Ollama (default, local)

**If Ollama is already on the machine, there is nothing to do.** On first
run (`auto_setup: true`, the default), Black Voice wakes the service if it
is stopped and pulls `ollama_model` if that is not already fetched, and
questions just work.

**If it is not on the machine at all, that stays your call.** Set
`auto_install: true` and Black Voice fetches a private copy for just this
user on first run and runs it as a `systemctl --user` service - genuinely
no manual step after that, ever, for anyone who turns it on. It is off by
default for one concrete reason: there is no small build. Upstream publishes
one general Linux build per architecture and it bundles CUDA support
already; the smallest one for x86_64 is over a gigabyte. That is a
meaningfully different bandwidth and disk commitment than the ~90 MB of
speech models this project already fetches automatically, and a default
should not make that choice on your behalf.

What `auto_install` never does, on or off, is run Ollama's own installer
(`curl -fsSL https://ollama.com/install.sh | sh`) - that is precisely the
shape `safety.blocked_patterns` already refuses when a *user* asks the
terminal skill to run it, and root is not a place this project's own
automatic code paths get to make an exception for themselves. It downloads
the plain release archive instead, extracts it for just this user, and
writes its own `~/.config/systemd/user/ollama.service` - no root at any
point, and a pre-existing system install always takes precedence over this
private one (see `ollama_models.find_binary`).

Turn it on and fetch it right now, rather than waiting for the next run:

```bash
blackvoice setup --ollama --install
```

**Pick a model,** whichever way Ollama got there:

```bash
blackvoice setup --ollama
```

with no `--model` reports what is installed and running, and lists models
known to run acceptably on ordinary hardware — smallest first, one marked
recommended. Pull one and switch to it in the same step:

```bash
blackvoice setup --ollama --model qwen2.5:1.5b --set-default
```

The default, `ollama_model: "llama3.2"`, is not on that curated list — it
predates it and is left as-is rather than silently changed under you. The
tray icon → Settings → AI shows the same models as an editable dropdown, so
typing in a name that is not on the list still works if you know your
machine can take it.

Nothing leaves the machine. If Ollama is not installed at all, you get a
clear message with the install link the first time a question needs it.

**Replies are spoken as they are generated,** not after the whole answer has
finished — the first sentence is spoken the moment it is complete, while the
rest keeps streaming in and is spoken sentence by sentence behind it. A short
reply with no sentence-ending punctuation (common in Hinglish) is still
spoken as one chunk once the stream ends, exactly as before; only longer,
punctuated answers get the head start. This applies to Ollama only — Claude
and OpenAI answer in one blocking call, as they always have.

### Claude

```jsonc
"ai": { "provider": "anthropic", "anthropic_model": "claude-opus-5" }
```

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

An `ant auth login` profile works too — leave the key unset and the SDK finds it.
Requests use `effort: "low"`, because a spoken answer should be quick and short.

### OpenAI

```jsonc
"ai": { "provider": "openai", "openai_model": "gpt-4o-mini" }
```

```bash
export OPENAI_API_KEY=sk-...
```

### Off

```jsonc
"ai": { "provider": "none" }
```

Unrecognised commands then say so instead of being answered.

> **Keep keys out of the config file.** `api_key` exists for awkward setups, but
> the environment variables above are read automatically and are the better
> place. The config file is world-readable on most systems.

The assistant remembers the last six turns for context. Clear it from the tray
menu → *Clear AI conversation*.

## `safety` — the shell guard

```jsonc
"safety": {
  "confirm_shell": true,
  "blocked_patterns": ["..."],
  "shell_timeout": 20.0,
  "max_output_chars": 2000
}
```

| Key | Default | What it does |
|---|---|---|
| `confirm_shell` | `true` | Ask before running anything that is not read-only. **Leave this on.** |
| `blocked_patterns` | 10 patterns | Regexes that are refused outright, confirmation or not. |
| `shell_timeout` | `20.0` | Kill a command that runs longer. |
| `max_output_chars` | `2000` | Truncate output shown back to you. |

Turning `confirm_shell` off does **not** disable the deny-list — destructive
commands are still refused. Read **[Security Model](Security-Model)** before
changing anything here.

## `control` — the local control socket

```jsonc
"control": {
  "enabled": true,
  "socket_path": ""
}
```

A Unix domain socket at `$XDG_RUNTIME_DIR/blackvoice/control.sock`, opened
whenever `blackvoice run` starts, for anything outside the process that wants
to talk to a running engine — the PyQt6 tray already calls into it directly,
so this exists for whatever does not: the early
[Flutter frontend](https://github.com/RudraLabs-dev/blackvoice/tree/main/flutter_app)
in the repository is the first thing using it.

| Key | Default | What it does |
|---|---|---|
| `enabled` | `true` | Turn off if nothing on this machine needs the socket. |
| `socket_path` | *(blank)* | Blank resolves the `XDG_RUNTIME_DIR` location above; set an absolute path to run two instances side by side, or if your desktop sets no runtime directory. |

Nothing here is reachable from another machine — access control is the
filesystem permissions on the socket file (`0600`, in a `0700` directory), not
a token to configure. POSIX only: on a platform with no Unix domain sockets
the engine simply does not open one, silently.

The protocol is one JSON object per line, documented in
`blackvoice/control_socket.py`'s module docstring alongside the two decisions
behind it — a socket file rather than a port, and no library dependency for
speaking it.

## `ui` — tray and overlay

```jsonc
"ui": {
  "enabled": true,
  "overlay_timeout": 8.0,
  "theme": "light",
  "show_notifications": true
}
```

Set `enabled` to `false` (or run `blackvoice run --no-ui`) for headless
operation. `overlay_timeout` is how long the popup stays after a reply.

## `skills` — per-skill preferences

```jsonc
"skills": {
  "weather_city": "",
  "search_url": "https://duckduckgo.com/?q={query}",
  "browser": "",
  "terminal": "",
  "file_manager": "",
  "editor": ""
}
```

`weather_city` pins the weather instead of geolocating by IP. The four
application fields override auto-detection, so `open browser` opens the one you
actually want:

```jsonc
"browser": "firefox",
"terminal": "alacritty",
"editor": "code"
```

For a different search engine, keep the `{query}` placeholder:

```jsonc
"search_url": "https://www.google.com/search?q={query}"
```

## Where things live

| Path | Contents |
|---|---|
| `~/.config/blackvoice/config.json` | this file |
| `~/.local/share/blackvoice/models/` | speech models — Vosk and, if installed, the whisper.cpp GGML file |
| `~/.local/share/blackvoice/notes.md` | your notes |
| `~/.cache/blackvoice/blackvoice.log` | rotating log, 1 MB × 3 |
| `$XDG_RUNTIME_DIR/blackvoice/control.sock` | the control socket, while running |

These follow `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and
`XDG_RUNTIME_DIR` when set.
