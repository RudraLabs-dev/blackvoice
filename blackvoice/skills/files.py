"""File and folder operations."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

from ..nlu.intents import Intent
from .base import Reply, Skill

log = logging.getLogger(__name__)

#: spoken folder name -> XDG user dir key / relative path
FOLDERS = {
    "download": "Downloads",
    "downloads": "Downloads",
    "document": "Documents",
    "documents": "Documents",
    "desktop": "Desktop",
    "picture": "Pictures",
    "pictures": "Pictures",
    "music": "Music",
    "video": "Videos",
    "videos": "Videos",
    "home": "",
    "trash": ".local/share/Trash/files",
}

#: directories never worth walking for a voice search
SKIP_DIRS = {
    ".git", ".cache", "node_modules", "__pycache__", ".venv", "venv",
    ".local/share/Trash", "snap", ".steam", ".mozilla", ".config",
}

MAX_RESULTS = 8
MAX_DEPTH = 6


class FilesSkill(Skill):
    name = "files"

    def handle(self, intent: Intent) -> Reply:
        handler = getattr(self, f"_do_{intent.action}", None)
        if handler is None:
            return Reply.error("I do not know that file command.")
        return handler(intent)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _xdg_dir(name: str) -> Path:
        """Resolve a localised XDG user directory, falling back to ~/Name."""
        key = f"XDG_{name.upper()}_DIR"
        env = os.environ.get(key)
        if env:
            return Path(os.path.expandvars(env)).expanduser()

        user_dirs = Path.home() / ".config/user-dirs.dirs"
        if user_dirs.exists():
            try:
                for line in user_dirs.read_text(encoding="utf-8").splitlines():
                    if line.startswith(key + "="):
                        raw = line.split("=", 1)[1].strip().strip('"')
                        raw = raw.replace("$HOME", str(Path.home()))
                        return Path(raw).expanduser()
            except OSError:
                pass
        return Path.home() / name

    def _file_manager(self) -> Optional[str]:
        configured = self.config.skills.file_manager
        if configured and shutil.which(configured):
            return configured
        return self.which("xdg-open", "nautilus", "dolphin", "thunar", "nemo", "pcmanfm")

    # -------------------------------------------------------------- actions
    def _do_open_folder(self, intent: Intent) -> Reply:
        spoken = (intent.slots.get("target") or "").strip().lower()
        relative = FOLDERS.get(spoken)
        if relative is None:
            return Reply.error(f"I do not know a folder called {spoken}.")

        if relative in {"Downloads", "Documents", "Desktop", "Pictures", "Music", "Videos"}:
            path = self._xdg_dir(relative)
        else:
            path = Path.home() / relative if relative else Path.home()

        if not path.exists():
            return Reply.error(f"The {spoken} folder does not exist.")

        manager = self._file_manager()
        if manager and self.spawn([manager, str(path)]):
            return Reply(f"Opening {spoken}.", data={"path": str(path)})
        return Reply.error("I could not find a file manager to open that with.")

    def _do_find(self, intent: Intent) -> Reply:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            # "find a file", with no name given - asked for directly rather
            # than handed to the AI skill and hoping it calls a tool with
            # whatever comes back. Confirmed live that hope does not pay
            # off: asked to find a file, then given a real file name the
            # very next turn, qwen2.5:1.5b just acknowledged the name back
            # in conversation and never actually searched for anything.
            # Reply.needs/on_answer instead routes the next thing said
            # straight back here as the query, deterministically.
            return Reply(
                "What should I look for?",
                needs="What should I look for?",
                on_answer=self._find_with_query,
            )
        return self._find_with_query(query)

    def _find_with_query(self, query: str) -> Reply:
        query = (query or "").strip()
        if not query:
            return Reply.error("I did not catch a file name.")

        matches = self._search(query)
        if not matches:
            return Reply(f"I found nothing matching {query}.", ok=False)

        first = matches[0]
        speech = (
            f"Found {len(matches)} matches. The first is {first.name}."
            if len(matches) > 1
            else f"Found {first.name}."
        )
        display = f"Matches for “{query}”:\n" + "\n".join(
            f"  {p}" for p in matches[:MAX_RESULTS]
        )
        return Reply(speech, display=display, data={"matches": [str(p) for p in matches]})

    def _search(self, query: str) -> List[Path]:
        """Depth-limited walk of the home directory.

        ``locate`` is used when available because it is far faster; otherwise we
        walk, skipping the directories that are never interesting.
        """
        needle = query.lower()

        if self.which("locate") or self.which("plocate"):
            binary = self.which("plocate", "locate")
            result = self.run([binary, "-i", "-l", str(MAX_RESULTS * 3), needle], timeout=8)
            if result.returncode == 0 and result.stdout.strip():
                hits = [Path(line) for line in result.stdout.splitlines() if line.strip()]
                home = str(Path.home())
                ranked = [p for p in hits if str(p).startswith(home)] or hits
                return ranked[:MAX_RESULTS]

        matches: List[Path] = []
        home = Path.home()
        home_depth = len(home.parts)

        for root, dirs, files in os.walk(home, topdown=True, onerror=lambda _e: None):
            root_path = Path(root)
            if len(root_path.parts) - home_depth >= MAX_DEPTH:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for name in files + dirs:
                if needle in name.lower():
                    matches.append(root_path / name)
                    if len(matches) >= MAX_RESULTS:
                        return matches
        return matches

    def _do_create_folder(self, intent: Intent) -> Reply:
        raw = (intent.slots.get("name") or "").strip()
        if not raw:
            return Reply.error("What should the folder be called?")

        # Voice input must never escape the home directory.
        safe = "".join(ch for ch in raw if ch.isalnum() or ch in " -_").strip()
        if not safe:
            return Reply.error("That name has no characters I can use for a folder.")

        target = Path.home() / "Desktop" / safe
        if not target.parent.exists():
            target = Path.home() / safe

        if target.exists():
            return Reply(f"{safe} already exists.", ok=False)
        try:
            target.mkdir(parents=True)
        except OSError as exc:
            return Reply.error(f"I could not create that folder: {exc.strerror}")
        return Reply(f"Created the folder {safe}.", display=f"Created {target}",
                     data={"path": str(target)})

    def _do_disk_space(self, intent: Intent) -> Reply:
        try:
            usage = shutil.disk_usage(Path.home())
        except OSError as exc:
            return Reply.error(f"I could not read the disk usage: {exc.strerror}")

        free_gb = usage.free / 2**30
        total_gb = usage.total / 2**30
        percent = usage.used * 100 / usage.total if usage.total else 0
        return Reply(
            f"{free_gb:.0f} gigabytes free out of {total_gb:.0f}, "
            f"so the disk is {percent:.0f} percent full.",
            display=(
                f"Total  {total_gb:6.1f} GiB\n"
                f"Used   {usage.used / 2**30:6.1f} GiB  ({percent:.0f} %)\n"
                f"Free   {free_gb:6.1f} GiB"
            ),
            data={"free_gb": free_gb, "total_gb": total_gb},
        )
