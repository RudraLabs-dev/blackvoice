# Troubleshooting

Start here:

```bash
blackvoice doctor
```

It lists every Python package, system tool, model and microphone, marks what is
missing, and prints the exact command to fix each gap. Most problems on this page
are visible in its output.

If the symptom is that **speaking does nothing**, run this as well. It answers
the one question `doctor` cannot — whether audio actually reaches the program:

```bash
blackvoice mic
```

The bar should move when you talk. If it does not, the fault is below Black
Voice, and the output names where to look.

For more detail:

```bash
blackvoice -v run --no-ui               # debug logging in the terminal
tail -f ~/.cache/blackvoice/blackvoice.log
```

---

## It will not start

### `could not open microphone`

PortAudio is missing or no input device is available.

```bash
sudo apt install portaudio19-dev     # then reinstall the Python package
pip install --force-reinstall sounddevice
blackvoice devices
```

If `devices` lists nothing, the problem is below Black Voice — check
`arecord -l` and your desktop's sound settings.

### `PyQt6 is not available`

It falls back to headless automatically. To get the tray back:

```bash
pip install PyQt6
```

### No tray icon, but it is running

Some GNOME setups need the AppIndicator extension. The overlay and voice both
still work — trigger it with the wake word or `blackvoice text`.

---

## It does not hear me

### The wake word never fires

**Models missing.** `doctor` will say so.

```bash
blackvoice setup
```

**Wrong microphone.**

```bash
blackvoice devices
```

Then set the index:

```jsonc
"audio": { "input_device": 2 }
```

**Speaking too quietly.** Lower the threshold:

```jsonc
"audio": { "silence_threshold": 0.008 }
```

**Or skip it** — click the tray icon, which needs no wake word at all.

### It triggers when I am not talking to it

“black” is close to **back**, **block**, **blank**, **lack** and **slack**. A
two-word trigger is much more reliable:

```jsonc
"wake": { "phrases": ["hey black", "ok black"] }
```

### It cuts me off mid-sentence

```jsonc
"audio": { "silence_timeout": 2.0 }
```

### It keeps listening after I stop

`audio.calibrate_noise` (on by default) should already fix most of this by
itself: it measures your room's actual ambient noise for about a second at
the start of each command and raises the effective threshold to clear it,
rather than trusting one fixed number for every room. See what it computed
for your room:

```bash
blackvoice mic
```

That prints the configured floor next to the live calibrated threshold. If
listening still runs long after that:

- Lower `silence_timeout` — recognition now tests loudness several times a
  second rather than once per half-second block, so it no longer needs the
  padding older versions did:
  ```jsonc
  "audio": { "silence_timeout": 0.6 }
  ```
- A genuinely loud, constant background noise (a fan, traffic) can still
  raise the calibrated threshold enough to miss quiet speech. Raise
  `calibration_margin` if it is over-correcting, or turn calibration off and
  set a fixed floor yourself if your room's noise is unusually inconsistent:
  ```jsonc
  "audio": { "calibrate_noise": false, "silence_threshold": 0.02 }
  ```

---

## Recognition is poor

First find out whether it is recognition or routing:

```bash
blackvoice text "the exact phrase"
```

If text mode does the right thing, recognition is the problem. If it does the
wrong thing, the routing rules are — open an issue with the phrase.

**Install whisper.cpp.** It is more accurate than Vosk, and is the single
biggest fix for recognition quality generally. `blackvoice doctor` calls out
whisper.cpp as missing when it applies to your setup. Fix it with:

```bash
blackvoice setup --whisper
```

whisper.cpp is a native binary and a separate download, not a Python package
— see [Configuration](Configuration#speech--recognition) for what
`blackvoice doctor` and `blackvoice setup --whisper` actually check.

**Use a bigger model.** The small model is ~50 MB and tuned for commands.
Download a larger one from
[alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) and point at
it:

```jsonc
"speech": { "model_en": "/home/you/models/vosk-model-en-us-0.22" }
```

**Lean on the cloud more.** Raise the threshold so hybrid mode falls back more
often:

```jsonc
"speech": { "fallback_confidence": 0.75 }
```

Or use it exclusively — `"mode": "online"`.

---

## It does not talk back

```bash
blackvoice say "testing"
```

The engine it picked is printed first. If it says `none`:

```bash
sudo apt install espeak-ng
```

**I cannot understand a word of it.** espeak-ng is a formant synthesiser; it is
tiny and instant, which is why it is the fallback, but plenty of people
cannot follow it. Piper sounds like a person:

```bash
pip install piper-tts
blackvoice voice --install
blackvoice voice --test
```

Then switch to it in Settings → Speech output, or:

```jsonc
"voice": { "engine": "piper" }
```

The voice is about 60 MB and is fetched on first use.

**Still hard to follow but not that bad.** Slow it down — the default is 145
words per minute and lower helps:

```jsonc
"voice": { "rate": 120 }
```

---

## Commands do not work

### Volume does nothing

```bash
sudo apt install pulseaudio-utils     # gives you pactl
```

PipeWire users get `wpctl` from `pipewire-utils` / `wireplumber`. Check which one
you have:

```bash
which wpctl pactl amixer
```

### Brightness is refused

You are probably not in the `video` group:

```bash
sudo usermod -aG video $USER
```

Log out and back in. Or install `brightnessctl`, which handles permissions
itself:

```bash
sudo apt install brightnessctl
```

### Screenshots fail

Install any one of these: `gnome-screenshot`, `spectacle`, `grim` (Wayland),
`scrot` (X11), `maim`, `imagemagick`.

### Media keys do nothing

```bash
sudo apt install playerctl
playerctl status      # must show a running player
```

### An application will not open

Check the name Black Voice would use:

```bash
which firefox
blackvoice -v text "open firefox"
```

The debug log shows what it resolved to. Set an explicit default if
auto-detection picks the wrong one:

```jsonc
"skills": { "browser": "firefox" }
```

**`blackvoice -v text` says "Opening firefox" but nothing appears** —
and only when the wake word triggers it, not from this debug command:
check `journalctl --user -u blackvoice.service` for `is not a snap cgroup`
or `capability cap_dac_override not found`. That combination means a
snap-packaged app (Firefox on Ubuntu, by default) is being refused by
snapd's own confinement, because the systemd service's `NoNewPrivileges`
hardening blocks the privilege snap-confine needs to start - the debug
command above never reproduces it, since it runs outside that service's
own cgroup. Fixed in 0.7.2 by launching apps through their own transient
`systemd-run --user` unit instead of as a direct child of the service; if
you still see it on a current install, `systemctl --user status
blackvoice.service` and confirm `NoNewPrivileges` and `ProtectSystem` in
`packaging/blackvoice.service` still match what actually shipped.

### “That command is on the blocked list”

Working as intended — see **[Security Model](Security-Model)**. If you are sure,
edit `safety.blocked_patterns` yourself. Note that upgrades never modify an
existing config, so your edits stay.

---

## AI answers do not work

### `Ollama is not running`

```bash
ollama serve          # in another terminal
ollama pull llama3.2
```

### `The <provider> API key is missing or invalid`

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or OPENAI_API_KEY
```

Set it in the systemd unit too if you run it as a service, otherwise the
variable will not be there:

```ini
Environment=ANTHROPIC_API_KEY=sk-ant-...
```

### Answers are too long

```jsonc
"ai": { "max_tokens": 256 }
```

The system prompt already asks for at most three sentences; a local model may
ignore it. Adjust `ai.system_prompt` directly.

---

## It runs but behaves oddly

### Changes to config.json do nothing

Restart it — the config is read once at startup.

```bash
systemctl --user restart blackvoice
```

Check the file parses:

```bash
python -m json.tool ~/.config/blackvoice/config.json
```

A broken config falls back to defaults with a message on stderr rather than
crashing.

### New safety patterns did not appear after upgrading

They never do. `blocked_patterns` was written to your config on first run and is
not touched again.

```bash
blackvoice config --reset
```

### It hears its own voice

It drains the microphone after speaking, so this should not happen. If it does,
you likely have a loop-back device selected — pick a real microphone with
`blackvoice devices`.

---

## The service will not stay up

```bash
systemctl --user status blackvoice
journalctl --user -u blackvoice -n 50
```

**Starts before audio is ready.** The unit already sleeps 3 seconds; raise it:

```ini
ExecStartPre=/bin/sleep 10
```

**No display.** The tray needs a graphical session. For headless operation:

```ini
Environment=BLACKVOICE_UI_ENABLED=false
```

**Stops when you log out.** That is by design — it is a user service tied to your
graphical session.

---

## Still stuck

Open an issue at
[RudraLabs-dev/blackvoice/issues](https://github.com/RudraLabs-dev/blackvoice/issues)
with:

```bash
blackvoice doctor          # paste the whole output
blackvoice --version
uname -a
echo "$XDG_CURRENT_DESKTOP $XDG_SESSION_TYPE"
tail -50 ~/.cache/blackvoice/blackvoice.log
```

For a command that is misheard or mishandled, include the exact phrase and what
`blackvoice text "that phrase"` does — that separates a recognition problem from
a routing one immediately.
