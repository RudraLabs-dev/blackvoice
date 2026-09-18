#!/usr/bin/env bash
#
# Build a .deb or .rpm.
#
#   ./packaging/build-package.sh deb
#   ./packaging/build-package.sh rpm
#
# Black Voice runs on the system Python. Anything the distributions package —
# numpy, cffi, PyQt6, requests, psutil — is declared as a dependency, so it
# follows whatever interpreter the host has.
#
# Only the libraries no distribution ships are bundled, into /opt/blackvoice/lib.
# Every one of them is a py3-none or abi3 wheel, carrying no CPython ABI tag, so
# the directory keeps working across Python upgrades:
#
#   vosk               py3-none-manylinux   not packaged anywhere
#   sounddevice        py3-none-any         not packaged in Ubuntu
#   SpeechRecognition  py3-none-any         not packaged in Ubuntu
#   pyttsx3            py3-none-any         not packaged in Ubuntu
#
# The earlier design bundled a whole virtualenv. That was wrong: a venv symlinks
# the system interpreter and pins itself to its version, so a package built on
# Debian 12 (Python 3.11) failed on Ubuntu with 3.13 — "No module named
# blackvoice", because the venv looked in lib/python3.13 and the files were in
# lib/python3.11. This layout has no such coupling, and is a third of the size.
#
set -euo pipefail

TYPE="${1:?usage: build-package.sh deb|rpm}"
case "$TYPE" in
    deb|rpm) ;;
    *) echo "unknown package type: $TYPE" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/blackvoice
STAGE="$ROOT/build/stage-$TYPE"
LIB="$STAGE$PREFIX/lib"
OUT="$ROOT/dist"

#: Libraries with no distribution package. All ABI-independent.
BUNDLED=(vosk sounddevice SpeechRecognition pyttsx3)

# ------------------------------------------------------------------ version
# A quoted heredoc keeps the shell out of the Python entirely, which matters
# because the pattern is full of quote characters.
VERSION="$(python3 - "$ROOT/blackvoice/__init__.py" <<'PY'
import pathlib
import re
import sys

src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
if not match:
    sys.exit("no __version__ found")
print(match.group(1))
PY
)"
[ -n "$VERSION" ] || { echo "could not read the version" >&2; exit 1; }

echo "==> Black Voice $VERSION  ($TYPE)"

# ------------------------------------------------------------------- stage
echo "==> staging"
rm -rf "$STAGE"
mkdir -p "$LIB" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" \
         "$STAGE/usr/lib/systemd/user" \
         "$STAGE/usr/share/doc/blackvoice"

# --no-deps throughout: everything these would pull in is a distribution
# dependency instead, and letting pip resolve them would drag ABI-tagged
# wheels such as numpy and cffi back into the package.
echo "==> collecting the unpackaged libraries"
python3 -m pip install --quiet --no-deps --target "$LIB" "${BUNDLED[@]}"

echo "==> adding Black Voice itself"
python3 -m pip install --quiet --no-deps --target "$LIB" "$ROOT"

echo "==> trimming"
find "$LIB" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$LIB" -type f -name '*.pyc' -delete 2>/dev/null || true
# pip --target leaves the console scripts behind; we ship our own launcher.
rm -rf "$LIB/bin" 2>/dev/null || true

# SpeechRecognition ships 37 MB of PocketSphinx model data for an offline
# engine we never call — Vosk is what does offline recognition here — plus flac
# encoders for four platforms. Only recognize_google() is used, and it needs the
# Linux x86-64 encoder alone.
rm -rf "$LIB/speech_recognition/pocketsphinx-data" 2>/dev/null || true
for unused in flac-win32.exe flac-mac flac-linux-x86; do
    rm -f "$LIB/speech_recognition/$unused" 2>/dev/null || true
done
[ -f "$LIB/speech_recognition/flac-linux-x86_64" ] || \
    echo "    warning: the flac encoder is gone; the online fallback will not work" >&2

echo "==> bundled:"
du -sh "$LIB" | sed 's/^/    /'
find "$LIB" -maxdepth 1 -mindepth 1 -type d -printf '    %f\n' | sort | head -20

# Fail loudly rather than shipping a package that cannot import itself.
for required in blackvoice vosk; do
    [ -d "$LIB/$required" ] || { echo "$required is missing from $LIB" >&2; exit 1; }
done

# ------------------------------------------------------------------- files
install -m 0755 "$ROOT/packaging/blackvoice-launcher.sh" "$STAGE/usr/bin/blackvoice"

# The shipped .desktop and .service carry an @BIN@ placeholder so the same
# files serve the tarball installer and the distribution packages.
sed 's|@BIN@|/usr/bin/blackvoice|g' "$ROOT/packaging/blackvoice.desktop" \
    > "$STAGE/usr/share/applications/blackvoice.desktop"
sed 's|@BIN@|/usr/bin/blackvoice|g' "$ROOT/packaging/blackvoice.service" \
    > "$STAGE/usr/lib/systemd/user/blackvoice.service"
chmod 0644 "$STAGE/usr/share/applications/blackvoice.desktop" \
           "$STAGE/usr/lib/systemd/user/blackvoice.service"

install -m 0644 "$ROOT/assets/logo.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/blackvoice.svg"
install -m 0644 "$ROOT/README.md" "$ROOT/LICENSE" "$STAGE/usr/share/doc/blackvoice/"

# ---------------------------------------------------------- maintainer hooks
mkdir -p "$ROOT/build"
cat > "$ROOT/build/after-install.sh" <<'HOOK'
#!/bin/sh
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true

cat <<'MSG'

  Black Voice is installed.

    blackvoice           start it
    blackvoice doctor    check the installation

  On its first run it downloads the offline speech models (~90 MB) into
  your home directory. Models are per-user, so this cannot happen here.

  Voice commands all work as they are. Answering open questions needs a
  language model - if Ollama is already on this machine, Black Voice wakes
  it and fetches a small one on first run automatically, nothing to do.
  Otherwise it is a separate, optional install:

    https://ollama.com/download

  Documentation: https://github.com/RudraLabs-dev/blackvoice/wiki

MSG
exit 0
HOOK

cat > "$ROOT/build/after-remove.sh" <<'HOOK'
#!/bin/sh
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
exit 0
HOOK

chmod +x "$ROOT/build/after-install.sh" "$ROOT/build/after-remove.sh"

# ------------------------------------------------------------------ build
mkdir -p "$OUT"

FPM_COMMON=(
    -s dir
    -t "$TYPE"
    -n blackvoice
    -v "$VERSION"
    --iteration 1
    --license MIT
    --vendor "Rudra Labs"
    --maintainer "Rudra Labs <connect@rudralabs.dev>"
    --url "https://github.com/RudraLabs-dev/blackvoice"
    --description "Offline-first voice assistant for Linux
Black Voice controls your desktop by voice in Hindi, English and Hinglish.
Speech recognition runs locally, so it works without a network connection.
Applications, volume, brightness, files, timers and guarded shell access,
plus question answering through a local or hosted language model."
    --after-install "$ROOT/build/after-install.sh"
    --after-remove "$ROOT/build/after-remove.sh"
    # Not architecture-independent: libvosk.so and the flac encoder are x86-64
    # binaries. "native" resolves to amd64 for the deb and x86_64 for the rpm.
    --architecture native
    -C "$STAGE"
    --package "$OUT/"
)

case "$TYPE" in
    deb)
        fpm "${FPM_COMMON[@]}" \
            --depends "python3 (>= 3.9)" \
            --depends "python3-numpy" \
            --depends "python3-cffi" \
            --depends "python3-requests" \
            --depends "libportaudio2" \
            --deb-recommends "python3-pyqt6" \
            --deb-recommends "python3-psutil" \
            --deb-recommends "espeak-ng" \
            --deb-recommends "pulseaudio-utils" \
            --deb-recommends "libnotify-bin" \
            --deb-suggests "playerctl" \
            --deb-suggests "brightnessctl" \
            --deb-suggests "gnome-screenshot" \
            --deb-priority optional \
            --category sound \
            .
        ;;
    rpm)
        fpm "${FPM_COMMON[@]}" \
            --depends "python3 >= 3.9" \
            --depends "python3-numpy" \
            --depends "python3-cffi" \
            --depends "python3-requests" \
            --depends "portaudio" \
            --rpm-summary "Offline-first voice assistant for Linux" \
            --category "Applications/Multimedia" \
            --rpm-tag "Recommends: python3-pyqt6" \
            --rpm-tag "Recommends: python3-psutil" \
            --rpm-tag "Recommends: espeak-ng" \
            --rpm-tag "Suggests: playerctl" \
            --rpm-tag "Suggests: brightnessctl" \
            .
        ;;
esac

echo
echo "==> built:"
ls -lh "$OUT"/*."$TYPE" | sed 's/^/    /'
