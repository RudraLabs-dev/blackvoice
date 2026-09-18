# Installation

Black Voice targets Linux. It needs Python 3.9+, PortAudio for the microphone,
and a speech engine for talking back.

There are two routes: a distribution package, or the source installer.

## From a package

Download from the
[releases page](https://github.com/RudraLabs-dev/blackvoice/releases).

**Debian, Ubuntu, Mint, Pop!\_OS**

```bash
sudo apt install ./blackvoice_0.3.0-1_amd64.deb
```

**Fedora, RHEL, openSUSE**

```bash
sudo dnf install ./blackvoice-0.3.0-1.x86_64.rpm
```

### What the package installs

| Path | Contents |
|---|---|
| `/opt/blackvoice/lib` | The few libraries no distribution packages |
| `/usr/bin/blackvoice` | The launcher |
| `/usr/share/applications/` | Desktop entry |
| `/usr/lib/systemd/user/` | The user service |
| `/usr/share/icons/hicolor/` | The icon |

Black Voice runs on your system Python. Anything your distribution already
packages — numpy, cffi, requests, PyQt6, psutil — is pulled in as a normal
dependency, so it tracks whatever interpreter you have.

Only four libraries are bundled, because no distribution ships them:

| Bundled | Why |
|---|---|
| `vosk` | Not in any distribution's repositories |
| `sounddevice` | Not packaged in Ubuntu |
| `SpeechRecognition` | Not packaged in Ubuntu |
| `pyttsx3` | Not packaged in Ubuntu |

All four are `py3-none` wheels — they carry no CPython ABI tag, so they keep
working when your Python is upgraded. That is what keeps the package to about
15 MB and lets one build serve every distribution.

### Verify the download

```bash
sha256sum -c SHA256SUMS
```

## From source

Works on any distribution, including those with no package above.

```bash
git clone https://github.com/RudraLabs-dev/blackvoice.git
cd blackvoice
./install.sh --system
```

Then check it:

```bash
blackvoice doctor
```

`doctor` prints what is installed, what is missing, and the exact command to fix
each gap. Run it first whenever something misbehaves.

## What the installer does

| Step | Where it lands |
|---|---|
| System packages (`--system` only) | via apt / dnf / pacman / zypper |
| Virtualenv + dependencies | `~/.local/share/blackvoice-venv` |
| `blackvoice` command | `~/.local/bin/blackvoice` |
| Desktop entry and icon | `~/.local/share/applications`, `~/.local/share/icons` |
| systemd user unit | `~/.config/systemd/user/blackvoice.service` |
| Speech models (~90 MB) | `~/.local/share/blackvoice/models` |

Nothing is installed system-wide except the distro packages, and those only when
you pass `--system`.

### Installer options

```bash
./install.sh              # Python side only; tells you what system packages are missing
./install.sh --system     # also installs the distro packages (needs sudo)
./install.sh --no-models  # skip the ~90 MB model download
./install.sh --uninstall  # remove everything the installer created
```

`--uninstall` leaves your config, models and notes alone. Delete those yourself
if you want them gone:

```bash
rm -rf ~/.config/blackvoice ~/.local/share/blackvoice ~/.cache/blackvoice
```

## System packages

`--system` installs these for you. If you would rather do it by hand:

**Debian / Ubuntu / Mint / Pop!\_OS**

```bash
sudo apt install portaudio19-dev python3-dev python3-venv espeak-ng \
  libnotify-bin playerctl brightnessctl pulseaudio-utils gnome-screenshot
```

**Fedora / RHEL**

```bash
sudo dnf install portaudio-devel python3-devel espeak-ng \
  libnotify playerctl brightnessctl pulseaudio-utils gnome-screenshot
```

**Arch / Manjaro**

```bash
sudo pacman -S --needed portaudio espeak-ng libnotify playerctl \
  brightnessctl libpulse gnome-screenshot
```

**openSUSE**

```bash
sudo zypper install portaudio-devel python3-devel espeak-ng \
  libnotify-tools playerctl brightnessctl pulseaudio-utils
```

### What each package is for

| Package | Without it |
|---|---|
| `portaudio19-dev` | **No microphone at all.** This one is not optional. |
| `python3-venv` | The installer cannot create its virtualenv |
| `espeak-ng` | No speech output — replies are printed, not spoken |
| `pulseaudio-utils` / PipeWire | Volume commands do nothing |
| `brightnessctl` | Brightness commands do nothing |
| `playerctl` | Media keys (play, next, previous) do nothing |
| `libnotify-bin` | No desktop notifications for timers and reminders |
| `gnome-screenshot` | Screenshots fail — `grim`, `scrot`, `maim` and `spectacle` also work |

## Manual install

If you would rather not use the installer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
blackvoice setup          # download the Vosk models
blackvoice doctor
```

The optional extras can be installed separately if you want a smaller footprint:

```bash
pip install -e ".[offline]"   # Vosk only, no cloud fallback
pip install -e ".[ui]"        # PyQt6 tray icon and overlay
pip install -e ".[online]"    # cloud fallback
pip install -e ".[tts]"       # pyttsx3 speech output
```

Without `[ui]` it runs headless (`blackvoice run --no-ui`), which is fine over
SSH or on a server.

## Speech models

**You do not normally have to do anything here.** On its first run Black Voice
notices the models are missing and downloads them (~90 MB), reporting progress
as it goes. It happens once.

They cannot ship inside the `.deb` or `.rpm`. A package's post-install script
must not use the network — installs have to work in chroots, containers and
offline mirrors — and that script runs as root, while the models belong to a
user's home directory. First run is the correct place for it: the right user,
the right directory, and a moment when a network connection is a reasonable
thing to expect.

To fetch them yourself, or to re-fetch them:

```bash
blackvoice setup                  # both languages
blackvoice setup --language en    # English only
blackvoice setup --language hi    # Hindi only
blackvoice setup --force          # re-download
```

To turn the automatic download off — for a metered connection, or to control
exactly when it happens:

```jsonc
"speech": { "auto_download": false }
```

Models come from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)
and land in `~/.local/share/blackvoice/models`. If the download fails behind a
proxy, fetch the zips by hand and extract them there — the folder names must stay
as they are:

```
~/.local/share/blackvoice/models/
├── vosk-model-small-en-us-0.15/
└── vosk-model-small-hi-0.22/
```

You can point at bigger models by setting `speech.model_en` / `speech.model_hi`
to an absolute path. See [Configuration](Configuration).

### Better Hinglish: whisper.cpp

The Vosk models above are enough to run. For a sentence that switches between
Hindi and English mid-way — which two Vosk models racing on the same audio
cannot really do, see [FAQ → Why not just
Vosk?](FAQ#why-not-just-vosk) — install
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) and fetch a model:

```bash
blackvoice setup --whisper                 # ggml-base-q5_1.bin, ~57 MB
blackvoice doctor                          # confirms both the binary and model are found
```

Opt-in rather than downloaded automatically, because it is a compiled binary
you install yourself (your package manager, or build it), not a Python
package `pip install -e ".[all]"` above already covers. `speech.engine:
"auto"` — the default — uses it once it is there and falls back to Vosk
silently if it is not, so nothing breaks either way.

### An AI backend sized for this machine

Answering open questions (anything that is not a recognised command) needs a
language model. The default, Ollama, runs locally:

```bash
blackvoice setup --ollama                                     # what's known to run acceptably
blackvoice setup --ollama --model qwen2.5:1.5b --set-default  # fetch and switch to it
```

See [Configuration → ai](Configuration#ai--the-question-answering-backend) for
Claude, OpenAI, or turning it off entirely — every voice command works
without any of this.

## Running on login

```bash
systemctl --user enable --now blackvoice
systemctl --user status blackvoice
journalctl --user -u blackvoice -f      # follow the logs
```

## Upgrading

```bash
cd blackvoice
git pull
./install.sh
```

One thing the upgrade does **not** do: your `~/.config/blackvoice/config.json`
was written on first run and is never overwritten, so new default settings — and
in particular new entries in `safety.blocked_patterns` — do not reach an existing
config. After an upgrade, either merge them by hand or reset:

```bash
blackvoice config --reset
```

## Next

→ **[Getting Started](Getting-Started)**
