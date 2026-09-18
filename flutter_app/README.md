# Black Voice — Flutter frontend (early scaffold)

A second UI for Black Voice, talking to the running engine over its local
control socket (`blackvoice/control_socket.py`) instead of calling into
`Engine` directly the way the PyQt6 tray and overlay do. This is the first
slice of what would eventually replace them, not a finished replacement —
see **What this is not**, below, before assuming more works than does.

## Status: written, not verified

This was written in an environment with no Flutter SDK installed, targeting
Linux desktop from a Windows machine that cannot build one. That means:

- The Dart code (`lib/*.dart`) has **not** been run through `flutter
  analyze`, `flutter pub get`, or `flutter build linux` — not even once.
- It is a careful best-effort draft against documented, stable Dart/Flutter
  APIs (`dart:io Socket` with `InternetAddressType.unix`, Material 3
  widgets), not tested working code.
- The Python side of this — `blackvoice/control_socket.py` and
  `blackvoice/ollama_models.py` — **is** tested: 31 tests run on every
  platform (`tests/test_control_socket.py`, `tests/test_ollama_models.py`)
  plus 7 more that only run where a Unix domain socket exists (skipped on
  Windows, real on Linux CI). The protocol this client speaks is solid; the
  client speaking it has not been compiled.

Budget the first session with this for fixing whatever `flutter analyze`
and the compiler flag — likely small (a typo, an API that moved between
Flutter versions) but real, and untriaged.

## Running it

```bash
# One-time, on a machine with the Flutter SDK and Linux desktop support:
flutter config --enable-linux-desktop

cd flutter_app
flutter create --platforms=linux .   # generates linux/ only; see note below
flutter pub get

# In another terminal, start the engine so the socket exists:
blackvoice run --no-ui

flutter run -d linux
```

`flutter create` run inside an existing project directory is documented to
add only the platform folders it's missing and leave an existing
`pubspec.yaml`/`lib/` alone — but that behaviour is a property of whatever
Flutter version does the generating, not of this repository, which is why
`linux/`, `android/`, and the rest are `.gitignore`d here rather than
committed: hand-writing that CMake/GTK scaffold without a way to verify it
would be worse than not having it, and regenerating it takes one command.
Check `git status`/`git diff` after running it before assuming nothing
changed outside `linux/`.

## What is actually here

- `lib/control_client.dart` — the protocol client: connects to the Unix
  socket, matches replies to requests by id, exposes unsolicited engine
  events (`state`, `heard`, `reply`) as a `Stream`.
- `lib/settings_screen.dart` — the concrete thing that was asked for: pick
  an Ollama model from the curated lightweight list, see which are already
  pulled, pull one with a tap. This is genuinely more capable here than the
  PyQt6 settings window, which only lets you *choose* a model — pulling one
  is still CLI-only there (`blackvoice setup --ollama --model <name>`).
- `lib/home_screen.dart` — a plain window: current engine state, the last
  thing heard and replied, a text box that calls `submit_text`. Enough to
  prove the event stream and request/response halves of the bridge both
  work; not a design.

## What this is not

The user asked for a full Flutter rewrite of the UI. This is the foundation
that makes one possible, not the rewrite itself — each of the following is
real, separate work, most of it needing an actual Linux machine to do
honestly:

- **No system tray icon.** Flutter has no first-party tray support; a
  community plugin (`tray_manager` or `system_tray`) would be the way in,
  and getting a tray icon to behave the same across GNOME, KDE and Xfce the
  way the existing PyQt6 tray already does (see `blackvoice/ui/tray.py`) is
  its own testing effort.
- **No overlay.** The PyQt6 overlay is a transient popup tied to the wake
  word — appears, shows the waveform and partial text, closes. `HomeScreen`
  here is an ordinary persistent window; it is not that popup and was not
  designed to become it.
- **No packaging.** `packaging/build-package.sh` stages a pure-Python tree
  into `/opt/blackvoice/lib`. A `flutter build linux` output is a compiled
  binary plus a bundle of shared libraries — a different shape entirely,
  needing its own staging step, its own desktop entry (or a change to the
  existing one), and a decision about whether it replaces the PyQt6 UI
  outright or the two coexist during a transition.
- **A small control-socket API.** Six operations, chosen to prove the
  architecture and to answer the one screen that was asked for — not a
  general-purpose remote-control surface. Missing on purpose for now:
  `activate`/`wake_up`, microphone level for a waveform, and progress events
  for a model pull (`pull_ollama_model` currently blocks until it finishes
  or fails, which is fine for one client and wrong for several).

## The protocol, briefly

One JSON object per line, in both directions, over
`$XDG_RUNTIME_DIR/blackvoice/control.sock`. A request carries `id` and `op`;
the server replies with the same `id` and either `"ok": true, "result":
...}` or `"ok": false, "error": "..."}`. Frames with no `id`, carrying
`"event"` instead, are pushed unprompted whenever the engine's bus fires.

The full op list and the reasoning behind the transport (a socket file, not
a port; hand-rolled JSON lines, not a library) are in
`blackvoice/control_socket.py`'s module docstring — read that before adding
another op, since both decisions were made for reasons that apply to any
future client, Flutter or not.
