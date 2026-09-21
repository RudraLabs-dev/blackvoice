# Contributing

## Setup

```bash
git clone https://github.com/RudraLabs-dev/blackvoice.git
cd blackvoice
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
pytest -q
```

465 tests should pass in under a second. None of them need a microphone, a
display or a network connection.

## Working without a microphone

Most of the system can be exercised from the terminal:

```bash
blackvoice text                       # interactive
blackvoice text "open firefox"        # one command
blackvoice -v text "volume 40"        # with debug logging
```

Text mode runs the same router, the same skills and the same safety guard as the
voice path — only the microphone and speaker are skipped. Use it for anything
that is not specifically an audio problem.

For the UI without a desktop:

```bash
QT_QPA_PLATFORM=offscreen python -c "
from PyQt6.QtWidgets import QApplication
from blackvoice.ui.overlay import Overlay
app = QApplication([])
ov = Overlay()
ov.show_reply('hello')
print('ok')
"
```

## Tests

```bash
pytest -q                        # everything
pytest tests/test_router.py -v   # one file
pytest -k "router or wake"       # by name
pytest --cov=blackvoice          # coverage
```

| File | Covers | Tests |
|---|---|---|
| `test_router.py` | Intent routing | 48 |
| `test_safety.py` | The shell guard | 31 |
| `test_skills.py` | Calculator, notes, timers, registry, AI fallbacks | 25 |
| `test_config.py` | Loading, merging, environment overrides | 14 |
| `test_engine.py` | Confirmations, event bus, sleep | 9 |
| `test_models.py` | First-run model setup | 12 |
| `test_ui.py` | Overlay, run under the offscreen platform | 15 |
| `test_settings.py` | Settings window and config round-trip | 11 |

### What to test

**A new command** needs a routing case in `test_router.py`:

```python
("i had a coffee", "coffee", "add", {}),
```

**A new skill** needs its own tests. Skills are plain objects — see
[Writing Skills → Testing](Writing-Skills#testing).

**A safety bypass** needs a case in `test_safety.py` proving it is closed.

### What not to break

`tests/test_safety.py` is the one file where a failing test means something
serious. If a change there fails, do not adjust the test to match — the guard is
the point.

## Style

Match the surrounding code. Specifically:

**Comments explain why, not what.**

```python
# Endpointing is done on the RMS level rather than on Vosk's own utterance
# boundaries, so it behaves the same way when only the online path exists.
```

Not `# loop over the blocks`.

**Probe for tools, never assume.**

```python
tool = self.which("gnome-screenshot", "spectacle", "grim", "scrot")
if tool is None:
    return Reply.error("No screenshot tool found. Install grim or scrot.")
```

This is why it works across desktops. A hard-coded binary name is a bug.

**Degrade, do not crash.** Optional dependencies are imported lazily and their
absence is handled with a message naming the fix.

**Error messages should be actionable.** `Install playerctl so I can control
media playback.` — not `Error 3`.

**Type hints on public functions.** `from __future__ import annotations` is at
the top of every module.

## Commits

Explain why the change was needed, not just what changed:

```
Point the project URLs at the real repository

The clone URL, the pyproject homepage and the systemd unit's Documentation
line all pointed at a repository that was never created.
```

## Pull requests

1. Branch from `main`
2. Make the change, with tests
3. `pytest -q` passes
4. `blackvoice text "..."` still behaves for anything you touched
5. Open the PR, describing what broke or what was missing

Small and focused beats large and sweeping. A PR that fixes one thing and
explains why is easier to accept than one that rewrites a module.

## Documentation

This wiki lives in the repository, in `wiki/`. Editing the wiki on GitHub
directly does not work — a workflow overwrites it from `wiki/` on every push, so
your edit would be lost. Change the files in `wiki/` and open a pull request
instead.

The sync only runs when something under `wiki/` actually changed.

## Cutting a release

Versions follow [SemVer](https://semver.org). The version lives in exactly one
place — `blackvoice/__init__.py` — and `pyproject.toml` derives it. The release
workflow refuses to build when the tag and the code disagree, so the two cannot
drift apart silently.

```bash
# 1. bump the version
vim blackvoice/__init__.py          # __version__ = "0.2.0"
pytest -q

# 2. commit it
git commit -am "Release 0.2.0"
git push

# 3. tag it — this is what triggers the release
git tag v0.2.0
git push origin v0.2.0
```

The workflow then verifies the tag against the code, runs the tests, and builds
four things:

| Artifact | Built on |
|---|---|
| `blackvoice_X.Y.Z-1_amd64.deb` | a Debian container |
| `blackvoice-X.Y.Z-1.x86_64.rpm` | a Fedora container |
| `blackvoice-X.Y.Z.tar.gz` | the source tree |
| wheel and sdist | for `pip install` |

It publishes them to a GitHub Release with a `SHA256SUMS` file.

The `.deb` and `.rpm` run on the system Python and depend on the packages the
distribution already provides. Only the four libraries no distribution ships -
vosk, sounddevice, SpeechRecognition and pyttsx3 - are bundled, and none of them
carries a CPython ABI tag, so one build serves every distribution.

An earlier design bundled a whole virtualenv instead. That failed on any host
whose Python differed from the build machine's, which is most of them.

To test the packaging without releasing anything, run the workflow manually from
the Actions tab — it builds and checks every artifact but publishes nothing.
Locally:

```bash
./packaging/build-package.sh deb     # on Debian or Ubuntu
./packaging/build-package.sh rpm     # on Fedora
```

### Version policy

- **Patch** (`0.1.0` → `0.1.1`) — fixes only
- **Minor** (`0.1.0` → `0.2.0`) — new commands, new skills, new settings
- **Major** (`0.x` → `1.0.0`) — when the audio path has been verified on real
  hardware and the configuration format is settled

No pre-release tags for now. Debian writes them `0.2.0~rc1` and RPM writes them
`0.2.0-0.1.rc1`, and the two sort by different rules — get it wrong and the
package manager will not offer the upgrade from a release candidate to the final
version. Support for them can be added when there is a reason to need it.

## Adding a language

Recognition is English-only today — `SpeechConfig` has a single `model_en`
field, and `Config.model_path()` resolves it directly with no language
argument. Bringing back a second language means re-introducing the
per-language plumbing an earlier version of this project had:

1. Find a [Vosk model](https://alphacephei.com/vosk/models) for it
2. Add it to `MODEL_URLS` in `blackvoice/models.py`
3. Give `SpeechConfig` a field for it, and teach `Config.model_path()` (or a
   replacement) which one to load
4. Extend `HybridSTT` to load and race it alongside the English model
5. Add patterns to `intents.py` in that language
6. Add routing tests

This is more than a content change — the single-model design removed the
multi-model loading and racing logic itself, not just the extra-language
patterns.

## Where to start

Good first contributions:

- **More command phrasings.** The rules cover common ways of saying things, not
  every way. If something natural is not understood, add the pattern.
- **A new skill.** Clipboard, screen recording, window management, notes
  search — see [Writing Skills](Writing-Skills).
- **Desktop coverage.** If a tool your desktop uses is not in the probe lists,
  add it.
- **Real-hardware testing.** The audio path is the least-tested part of the
  system. Bug reports from an actual Linux machine with a microphone are
  genuinely valuable.

## Reporting bugs

Include the output of `blackvoice doctor`, your distribution and desktop, and —
for a command problem — the exact phrase plus what `blackvoice text "that
phrase"` does. That one line separates a recognition problem from a routing
problem immediately.

## Licence

MIT. Contributions are accepted under the same terms.
