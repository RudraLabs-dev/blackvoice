"""System control: apps, volume, brightness, power, radios, hardware status.

Linux desktops disagree on which tool is installed, so every action probes a
list of candidates (PipeWire before PulseAudio before ALSA, and so on) and uses
the first one that is actually present.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..nlu.intents import Intent
from .base import Reply, Skill

log = logging.getLogger(__name__)


#: Spoken name -> the programs to try, in order of preference.
APP_ALIASES = {
    "browser": ["firefox", "chromium", "google-chrome", "brave-browser", "vivaldi", "epiphany"],
    "firefox": ["firefox"],
    "chrome": ["google-chrome", "chromium", "chrome"],
    "chromium": ["chromium", "chromium-browser"],
    "terminal": ["gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "xterm"],
    "files": ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm"],
    "file manager": ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm"],
    "editor": ["code", "gedit", "kate", "gnome-text-editor", "mousepad", "nano"],
    "code": ["code", "codium"],
    "vs code": ["code", "codium"],
    "calculator": ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk"],
    "settings": ["gnome-control-center", "systemsettings5", "xfce4-settings-manager"],
    "music": ["rhythmbox", "spotify", "audacious", "clementine", "lollypop"],
    "spotify": ["spotify"],
    "video": ["vlc", "mpv", "totem"],
    "vlc": ["vlc"],
    "camera": ["cheese", "guvcview"],
    "screenshot tool": ["gnome-screenshot", "spectacle", "flameshot"],
    "system monitor": ["gnome-system-monitor", "ksysguard", "htop"],
    "libre office": ["libreoffice"],
    "libreoffice": ["libreoffice"],
    "telegram": ["telegram-desktop", "telegram"],
    "discord": ["discord"],
    "slack": ["slack"],
    "whatsapp": ["whatsapp-for-linux", "whatsdesk"],
    "internet": ["firefox", "chromium", "google-chrome", "brave-browser", "vivaldi", "epiphany"],
}


class SystemSkill(Skill):
    name = "system"

    def handle(self, intent: Intent) -> Reply:
        action = intent.action
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return Reply.error("I do not know how to do that yet.")
        return handler(intent)

    # ------------------------------------------------------------ launching
    def _resolve_app(self, spoken: str) -> Tuple[Optional[str], str]:
        """Map a spoken app name to an executable. Returns (path, pretty name)."""
        spoken = (spoken or "").strip().lower()
        if not spoken:
            return None, spoken

        candidates = APP_ALIASES.get(spoken)
        if candidates is None:
            # Try the configured preference for the generic words.
            preferred = {
                "browser": self.config.skills.browser,
                "terminal": self.config.skills.terminal,
                "files": self.config.skills.file_manager,
                "editor": self.config.skills.editor,
            }.get(spoken, "")
            candidates = [preferred] if preferred else []
            # Finally, treat the spoken words as the binary itself.
            candidates += [spoken, spoken.replace(" ", "-"), spoken.replace(" ", "")]

        for candidate in candidates:
            if candidate and shutil.which(candidate):
                return candidate, spoken
        return None, spoken

    def _do_open_app(self, intent: Intent) -> Reply:
        target = intent.slots.get("target", "")
        binary, pretty = self._resolve_app(target)

        if binary is None:
            # Maybe it is a desktop entry rather than a bare binary.
            if self.which("gtk-launch") and self._launch_desktop(target):
                return Reply(f"Opening {pretty}.")
            install = self.suggest_install(pretty)
            if install:
                return Reply.error(f"{pretty} is not installed. To install it: {install}")
            return Reply.error(f"I could not find {pretty} on this system.")

        if self.spawn([binary]):
            return Reply(f"Opening {pretty}.")
        return Reply.error(f"{pretty} failed to start.")

    def _launch_desktop(self, name: str) -> bool:
        slug = name.strip().lower().replace(" ", "-")
        for directory in (
            Path("/usr/share/applications"),
            Path.home() / ".local/share/applications",
        ):
            if not directory.is_dir():
                continue
            for entry in directory.glob("*.desktop"):
                if slug in entry.stem.lower():
                    return self.spawn(["gtk-launch", entry.stem])
        return False

    def _do_close_app(self, intent: Intent) -> Reply:
        target = (intent.slots.get("target") or "").strip().lower()
        if not target:
            return Reply.error("Which application should I close?")

        names = APP_ALIASES.get(target, [target, target.replace(" ", "-")])
        killed: List[str] = []
        try:
            import psutil
        except ImportError:
            result = self.run(["pkill", "-f", names[0]])
            if result.returncode == 0:
                return Reply(f"Closed {target}.")
            return Reply.error(f"{target} does not seem to be running.")

        for proc in psutil.process_iter(["name", "pid"]):
            pname = (proc.info.get("name") or "").lower()
            if any(pname == n.lower() or n.lower() in pname for n in names):
                try:
                    proc.terminate()
                    killed.append(pname)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        if killed:
            return Reply(f"Closed {target}.", data={"killed": killed})
        return Reply.error(f"{target} does not seem to be running.")

    # -------------------------------------------------------------- volume
    def _volume_backend(self) -> Optional[str]:
        if self.which("wpctl"):
            return "wpctl"
        if self.which("pactl"):
            return "pactl"
        if self.which("amixer"):
            return "amixer"
        return None

    def _volume_change(self, delta: int) -> Reply:
        backend = self._volume_backend()
        sign = "+" if delta > 0 else "-"
        step = abs(delta)

        if backend == "wpctl":
            self.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{step}%{sign}"])
        elif backend == "pactl":
            self.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{step}%"])
        elif backend == "amixer":
            self.run(["amixer", "-q", "sset", "Master", f"{step}%{sign}"])
        else:
            return Reply.error("No volume control tool found. Install pulseaudio-utils.")

        current = self._volume_get()
        word = "up" if delta > 0 else "down"
        if current is None:
            return Reply(f"Volume {word}.")
        return Reply(f"Volume {word}, now {current} percent.", data={"volume": current})

    def _volume_get(self) -> Optional[int]:
        backend = self._volume_backend()
        if backend == "wpctl":
            out = self.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).stdout
            m = re.search(r"([\d.]+)", out)
            return int(float(m.group(1)) * 100) if m else None
        if backend == "pactl":
            out = self.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]).stdout
            m = re.search(r"(\d+)%", out)
            return int(m.group(1)) if m else None
        if backend == "amixer":
            out = self.run(["amixer", "get", "Master"]).stdout
            m = re.search(r"\[(\d+)%\]", out)
            return int(m.group(1)) if m else None
        return None

    def _do_volume_up(self, intent: Intent) -> Reply:
        return self._volume_change(+10)

    def _do_volume_down(self, intent: Intent) -> Reply:
        return self._volume_change(-10)

    def _do_volume_set(self, intent: Intent) -> Reply:
        try:
            value = max(0, min(100, int(intent.slots.get("value", "50"))))
        except ValueError:
            return Reply.error("I did not catch the volume level.")

        backend = self._volume_backend()
        if backend == "wpctl":
            self.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}"])
        elif backend == "pactl":
            self.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"])
        elif backend == "amixer":
            self.run(["amixer", "-q", "sset", "Master", f"{value}%"])
        else:
            return Reply.error("No volume control tool found.")
        return Reply(f"Volume set to {value} percent.", data={"volume": value})

    def _do_volume_mute(self, intent: Intent) -> Reply:
        return self._set_mute(True)

    def _do_volume_unmute(self, intent: Intent) -> Reply:
        return self._set_mute(False)

    def _set_mute(self, muted: bool) -> Reply:
        backend = self._volume_backend()
        flag = "1" if muted else "0"
        if backend == "wpctl":
            self.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag])
        elif backend == "pactl":
            self.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag])
        elif backend == "amixer":
            self.run(["amixer", "-q", "sset", "Master", "mute" if muted else "unmute"])
        else:
            return Reply.error("No volume control tool found.")
        return Reply("Muted." if muted else "Unmuted.")

    # ---------------------------------------------------------- brightness
    _BACKLIGHT = Path("/sys/class/backlight")

    def _backlight_device(self) -> Optional[Path]:
        if not self._BACKLIGHT.is_dir():
            return None
        devices = sorted(self._BACKLIGHT.iterdir())
        return devices[0] if devices else None

    def _brightness_get(self) -> Optional[int]:
        if self.which("brightnessctl"):
            out = self.run(["brightnessctl", "-m", "i"]).stdout
            m = re.search(r"(\d+)%", out)
            if m:
                return int(m.group(1))
        device = self._backlight_device()
        if device is None:
            return None
        try:
            current = int((device / "brightness").read_text().strip())
            maximum = int((device / "max_brightness").read_text().strip())
            return round(current * 100 / maximum) if maximum else None
        except (OSError, ValueError):
            return None

    def _brightness_set(self, percent: int) -> bool:
        percent = max(5, min(100, percent))  # never let voice blank the screen
        if self.which("brightnessctl"):
            return self.run(["brightnessctl", "set", f"{percent}%"]).returncode == 0
        if self.which("light"):
            return self.run(["light", "-S", str(percent)]).returncode == 0
        device = self._backlight_device()
        if device is None:
            return False
        try:
            maximum = int((device / "max_brightness").read_text().strip())
            (device / "brightness").write_text(str(round(maximum * percent / 100)))
            return True
        except (OSError, ValueError, PermissionError):
            return False

    def _do_brightness_up(self, intent: Intent) -> Reply:
        current = self._brightness_get() or 50
        return self._brightness_reply(current + 15)

    def _do_brightness_down(self, intent: Intent) -> Reply:
        current = self._brightness_get() or 50
        return self._brightness_reply(current - 15)

    def _do_brightness_set(self, intent: Intent) -> Reply:
        try:
            return self._brightness_reply(int(intent.slots.get("value", "50")))
        except ValueError:
            return Reply.error("I did not catch the brightness level.")

    def _brightness_reply(self, target: int) -> Reply:
        target = max(5, min(100, target))
        if self._brightness_set(target):
            return Reply(f"Brightness set to {target} percent.", data={"brightness": target})
        return Reply.error(
            "I could not change the brightness. Install brightnessctl and add "
            "yourself to the 'video' group."
        )

    # -------------------------------------------------------------- screen
    def _do_screenshot(self, intent: Intent) -> Reply:
        pictures = Path.home() / "Pictures"
        pictures.mkdir(parents=True, exist_ok=True)
        target = pictures / f"blackvoice-{datetime.now():%Y%m%d-%H%M%S}.png"

        attempts = [
            (["gnome-screenshot", "-f", str(target)], None),
            (["spectacle", "-b", "-n", "-o", str(target)], None),
            (["grim", str(target)], None),
            (["scrot", str(target)], None),
            (["import", "-window", "root", str(target)], None),
            (["maim", str(target)], None),
        ]
        for argv, _ in attempts:
            if not self.which(argv[0]):
                continue
            # run_gui, not run: confirmed live that a screenshot tool found
            # on PATH could still silently fail here - same cgroup/session
            # mismatch as launching Firefox, for a command this skill has to
            # wait on and check the result of rather than just fire and
            # forget, so spawn() alone was not an option either.
            result = self.run_gui(argv, timeout=15)
            if result.returncode == 0 and target.exists():
                return Reply(
                    "Screenshot saved to your Pictures folder.",
                    display=f"Screenshot saved:\n{target}",
                    data={"path": str(target)},
                )

        install = self.suggest_install("a screenshot tool such as gnome-screenshot")
        if install:
            return Reply.error(f"No screenshot tool found. To install one: {install}")
        return Reply.error(
            "No screenshot tool found. Install gnome-screenshot, grim or scrot."
        )

    def _do_lock(self, intent: Intent) -> Reply:
        attempts = [
            ["loginctl", "lock-session"],
            ["xdg-screensaver", "lock"],
            ["gnome-screensaver-command", "-l"],
            ["swaylock"],
            ["i3lock"],
            ["dm-tool", "lock"],
        ]
        for argv in attempts:
            if self.which(argv[0]) and self.spawn(argv):
                return Reply("Locking the screen.")
        return Reply.error("I could not find a screen locker.")

    # --------------------------------------------------------------- power
    def _power(self, verb: str, spoken: str) -> Reply:
        """Power actions always ask first - a misheard word here is expensive."""

        def _commit() -> Reply:
            attempts = {
                "poweroff": [["systemctl", "poweroff"], ["shutdown", "-h", "now"]],
                "reboot": [["systemctl", "reboot"], ["shutdown", "-r", "now"]],
                "suspend": [["systemctl", "suspend"]],
                "logout": [
                    ["gnome-session-quit", "--logout", "--no-prompt"],
                    ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")],
                    ["qdbus", "org.kde.ksmserver", "/KSMServer", "logout", "0", "0", "0"],
                ],
            }[verb]
            for argv in attempts:
                if argv[-1] == "" or not self.which(argv[0]):
                    continue
                if self.spawn(argv):
                    return Reply(f"{spoken.capitalize()} now.")
            return Reply.error(f"I could not {spoken} this machine.")

        return Reply(
            speech=f"Should I really {spoken}? Say yes to confirm.",
            confirm=f"{spoken.capitalize()} the computer?",
            on_confirm=_commit,
        )

    def _do_shutdown(self, intent: Intent) -> Reply:
        return self._power("poweroff", "shut down")

    def _do_restart(self, intent: Intent) -> Reply:
        return self._power("reboot", "restart")

    def _do_suspend(self, intent: Intent) -> Reply:
        return self._power("suspend", "suspend")

    def _do_logout(self, intent: Intent) -> Reply:
        return self._power("logout", "log out")

    # -------------------------------------------------------------- radios
    @staticmethod
    def _wants_on(state: str) -> bool:
        return (state or "").strip().lower() in {"on", "enable", "start"}

    def _do_wifi(self, intent: Intent) -> Reply:
        on = self._wants_on(intent.slots.get("state", "on"))
        if self.which("nmcli"):
            result = self.run(["nmcli", "radio", "wifi", "on" if on else "off"])
            if result.returncode == 0:
                return Reply(f"Wi-Fi turned {'on' if on else 'off'}.")
        if self.which("rfkill"):
            result = self.run(["rfkill", "unblock" if on else "block", "wifi"])
            if result.returncode == 0:
                return Reply(f"Wi-Fi turned {'on' if on else 'off'}.")
        return Reply.error("I could not change the Wi-Fi state. Is NetworkManager installed?")

    def _do_bluetooth(self, intent: Intent) -> Reply:
        on = self._wants_on(intent.slots.get("state", "on"))
        if self.which("bluetoothctl"):
            result = self.run(["bluetoothctl", "power", "on" if on else "off"])
            if result.returncode == 0:
                return Reply(f"Bluetooth turned {'on' if on else 'off'}.")
        if self.which("rfkill"):
            result = self.run(["rfkill", "unblock" if on else "block", "bluetooth"])
            if result.returncode == 0:
                return Reply(f"Bluetooth turned {'on' if on else 'off'}.")
        return Reply.error("I could not change the Bluetooth state.")

    # ------------------------------------------------------------ hardware
    def _do_battery(self, intent: Intent) -> Reply:
        try:
            import psutil
        except ImportError:
            return Reply.error("psutil is not installed, so I cannot read the battery.")

        battery = psutil.sensors_battery()
        if battery is None:
            return Reply("This machine has no battery - it looks like a desktop.")

        percent = round(battery.percent)
        if battery.power_plugged:
            return Reply(f"Battery at {percent} percent and charging.",
                         data={"percent": percent, "charging": True})

        parts = [f"Battery at {percent} percent"]
        secs = battery.secsleft
        if secs and secs > 0 and secs != psutil.POWER_TIME_UNLIMITED:
            hours, minutes = divmod(int(secs) // 60, 60)
            if hours:
                parts.append(f"about {hours} hours {minutes} minutes left")
            else:
                parts.append(f"about {minutes} minutes left")
        return Reply(", ".join(parts) + ".", data={"percent": percent, "charging": False})

    def _do_info(self, intent: Intent) -> Reply:
        try:
            import psutil
        except ImportError:
            return Reply.error("psutil is not installed, so I cannot read system stats.")

        cpu = psutil.cpu_percent(interval=0.4)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_seconds = int(datetime.now().timestamp() - psutil.boot_time())
        hours, minutes = divmod(uptime_seconds // 60, 60)
        days, hours = divmod(hours, 24)

        speech = (
            f"CPU is at {cpu:.0f} percent, memory at {mem.percent:.0f} percent, "
            f"and the root disk is {disk.percent:.0f} percent full."
        )
        display = "\n".join([
            f"CPU       {cpu:5.1f} %",
            f"Memory    {mem.percent:5.1f} %   ({mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} GiB)",
            f"Disk /    {disk.percent:5.1f} %   ({disk.used / 2**30:.0f} / {disk.total / 2**30:.0f} GiB)",
            f"Uptime    {days}d {hours}h {minutes}m",
        ])
        return Reply(speech, display=display, data={"cpu": cpu, "memory": mem.percent})
