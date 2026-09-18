"""The settings window.

The form is generated from the :class:`~blackvoice.config.Config` dataclass
tree rather than written out by hand. Adding a field to the config makes it
appear here automatically, which is the only way a settings dialog and a config
file stay in agreement over time.

Only presentation lives here: which fields get a dropdown rather than a text
box, what to call them in English, and the one-line explanations. The values,
their types and their defaults all come from the dataclasses.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import CONFIG_FILE, Config
from .icons import ACCENT, BLACK, GREY, app_icon

log = logging.getLogger(__name__)

#: Sections in the order they should appear, with the tab title.
SECTIONS: List[Tuple[str, str]] = [
    ("ai", "AI"),
    ("speech", "Recognition"),
    ("wake", "Wake word"),
    ("audio", "Microphone"),
    ("voice", "Speech output"),
    ("skills", "Applications"),
    ("safety", "Safety"),
    ("ui", "Appearance"),
]

#: Fields that are a choice, not free text.
CHOICES: Dict[str, List[str]] = {
    "speech.mode": ["hybrid", "offline", "online"],
    "speech.language": ["both", "en", "hi"],
    "voice.engine": ["auto", "piper", "espeak", "spd-say", "pyttsx3", "none"],
    "ai.provider": ["ollama", "anthropic", "openai", "none"],
    "ui.theme": ["light", "dark"],
}

# Filled in lazily so the voice catalogue lives in one place.
try:
    from .. import voices as _voices

    CHOICES["voice.piper_voice_en"] = [
        n for n, (lang, _p, _d) in _voices.VOICES.items() if lang == "en"
    ]
    CHOICES["voice.piper_voice_hi"] = [
        n for n, (lang, _p, _d) in _voices.VOICES.items() if lang == "hi"
    ]
except Exception:  # pragma: no cover - the window still works without them
    pass

#: One line under each field. Kept here because it is interface copy.
HELP: Dict[str, str] = {
    "ai.provider": "Where open questions are answered. Every voice command works without one.",
    "ai.ollama_url": "Address of a running Ollama. Local by default.",
    "ai.ollama_model": "Pull it first: blackvoice setup --ollama --model <name>. "
                       "The dropdown lists models known to run well on ordinary hardware; "
                       "type any other tag if you know your machine can take it.",
    "ai.auto_setup": "If Ollama is already installed, wake it and fetch this model on first "
                     "run automatically - no setup --ollama needed. Never installs Ollama itself.",
    "ai.anthropic_model": "Needs ANTHROPIC_API_KEY in the environment.",
    "ai.openai_model": "Needs OPENAI_API_KEY in the environment.",
    "ai.api_key": "Leave empty. The environment variable is the safer place — this file is world-readable.",
    "ai.max_tokens": "Spoken answers should be short.",
    "ai.timeout": "Seconds to wait before giving up on the backend.",
    "ai.system_prompt": "Instructions sent with every question.",
    "speech.mode": "offline never uses the network. hybrid only when Vosk is unsure.",
    "speech.language": "both runs two models over the same audio — this is what makes Hinglish work.",
    "speech.fallback_confidence": "Below this score, hybrid mode retries online.",
    "speech.auto_download": "Fetch the speech models on first run.",
    "speech.model_en": "A bare name resolves under the models directory; an absolute path is used as-is.",
    "speech.model_hi": "A bare name resolves under the models directory; an absolute path is used as-is.",
    "speech.online_timeout": "Seconds to wait on the cloud recogniser.",
    "wake.enabled": "Turn off to use only the tray icon and typed commands.",
    "wake.phrases": "Comma separated. Two words — 'hey black' — trigger far less by accident.",
    "wake.hotkey": "Shown for reference. Bind it in your desktop's own keyboard settings.",
    "wake.chime": "Short beep when it starts listening.",
    "audio.input_device": "Which microphone to use.",
    "audio.silence_threshold": "Loudness below this counts as silence. Run 'blackvoice mic' to find yours.",
    "audio.silence_timeout": "Seconds of silence that end a command.",
    "audio.max_command_seconds": "Hard limit on one utterance.",
    "audio.sample_rate": "What the speech models expect. Changing it breaks recognition.",
    "audio.block_size": "Samples read at a time.",
    "voice.engine": "auto picks the best installed: piper, then espeak-ng, then spd-say.",
    "voice.rate": "Words per minute.",
    "voice.volume": "0 to 1.",
    "voice.voice_en": "espeak voice id for English.",
    "voice.voice_hi": "espeak voice id for Hindi.",
    "voice.piper_voice_en": "Neural English voice. Downloaded on first use (~60 MB).",
    "voice.piper_voice_hi": "Neural Hindi voice. Downloaded on first use (~60 MB).",
    "voice.piper_auto_download": "Fetch the Piper voice the first time it is needed.",
    "voice.piper_model": "An explicit .onnx path, which overrides the two voices above.",
    "safety.confirm_shell": "Ask before running anything that is not read-only. Leave this on.",
    "safety.blocked_patterns": "Never run, confirmation or not. One pattern per line.",
    "safety.shell_timeout": "Kill a command that runs longer than this.",
    "safety.max_output_chars": "Truncate output shown back to you.",
    "ui.enabled": "Tray icon and popup overlay.",
    "ui.auto_close": "Close the popup by itself after a reply. Off means it stays until you close it.",
    "ui.overlay_timeout": "Seconds the card stays after a reply.",
    "ui.show_notifications": "Desktop notifications for timers and reminders.",
    "skills.weather_city": "Leave empty to locate by IP address.",
    "skills.search_url": "Keep the {query} placeholder.",
    "skills.browser": "Leave empty to auto-detect.",
    "skills.terminal": "Leave empty to auto-detect.",
    "skills.file_manager": "Leave empty to auto-detect.",
    "skills.editor": "Leave empty to auto-detect.",
}

#: Fields that need a restart before they take effect. Almost everything does,
#: because the engine reads the config once at startup.
_LIVE_FIELDS = {"ui.overlay_timeout", "ui.auto_close", "ui.show_notifications"}


def _label_for(name: str) -> str:
    return name.replace("_", " ").capitalize()


class _FieldEditor:
    """Wraps one widget so the dialog can read and write it uniformly."""

    def __init__(self, key: str, widget: QWidget, getter, setter) -> None:
        self.key = key
        self.widget = widget
        self._get = getter
        self._set = setter

    @property
    def value(self) -> Any:
        return self._get()

    @value.setter
    def value(self, new: Any) -> None:
        self._set(new)


class SettingsWindow(QDialog):
    """Edit every configuration value without opening a text editor."""

    saved = pyqtSignal()

    def __init__(self, config: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self._editors: Dict[str, _FieldEditor] = {}

        self.setWindowTitle("Black Voice — Settings")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(660, 560)

        self._build()
        self._load_from_config()

    # ------------------------------------------------------------- building
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)

        heading = QLabel("Settings")
        font = QFont()
        font.setPointSize(15)
        font.setWeight(QFont.Weight.ExtraBold)
        heading.setFont(font)
        layout.addWidget(heading)

        where = QLabel(str(CONFIG_FILE))
        where.setStyleSheet(f"color: {GREY.name()}; font-size: 11px;")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(where)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        for section, title in SECTIONS:
            if hasattr(self.config, section):
                self.tabs.addTab(self._build_section(section), title)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {GREY.name()}; font-size: 11px;")
        layout.addWidget(self.note)

        buttons = QDialogButtonBox()
        reset = QPushButton("Restore defaults")
        reset.clicked.connect(self._restore_defaults)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton(QDialogButtonBox.StandardButton.Save)
        save.setDefault(True)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_section(self, section: str) -> QWidget:
        sub = getattr(self.config, section)

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(6, 10, 14, 10)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        scroll.setWidget(inner)

        # The AI tab leads with a plain on/off, because that is the question
        # people actually come here to answer.
        if section == "ai":
            self.ai_enabled = QCheckBox("Answer open questions with a language model")
            self.ai_enabled.setToolTip(
                "Off means unrecognised phrases simply say so. "
                "Every voice command works either way."
            )
            self.ai_enabled.toggled.connect(self._on_ai_toggled)
            form.addRow(self.ai_enabled)
            form.addRow(self._separator())

        for field in dataclasses.fields(sub):
            key = f"{section}.{field.name}"
            editor = self._editor_for(key, field, getattr(sub, field.name))
            if editor is None:
                continue
            self._editors[key] = editor

            label = QLabel(_label_for(field.name))
            label.setToolTip(HELP.get(key, ""))
            form.addRow(label, editor.widget)

            hint = HELP.get(key)
            if hint:
                note = QLabel(hint)
                note.setWordWrap(True)
                note.setStyleSheet(f"color: {GREY.name()}; font-size: 10px;")
                form.addRow("", note)

        return page

    @staticmethod
    def _separator() -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #E0E0E0;")
        return line

    def _editor_for(self, key: str, field, current: Any) -> Optional[_FieldEditor]:
        if key == "ai.ollama_model":
            return self._ollama_model_editor(key, current)

        # A dropdown wherever the value is one of a known set.
        if key in CHOICES:
            box = QComboBox()
            box.addItems(CHOICES[key])
            return _FieldEditor(
                key, box,
                lambda b=box: b.currentText(),
                lambda v, b=box: b.setCurrentText(str(v)),
            )

        if key == "audio.input_device":
            return self._device_editor(key)

        if isinstance(current, bool):
            box = QCheckBox()
            return _FieldEditor(key, box, box.isChecked, box.setChecked)

        if isinstance(current, int):
            spin = QSpinBox()
            spin.setRange(0, 1_000_000)
            return _FieldEditor(key, spin, spin.value, lambda v, s=spin: s.setValue(int(v)))

        if isinstance(current, float):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 100_000.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            return _FieldEditor(key, spin, spin.value, lambda v, s=spin: s.setValue(float(v)))

        if isinstance(current, list):
            edit = QLineEdit()
            edit.setPlaceholderText("comma separated")
            return _FieldEditor(
                key, edit,
                lambda e=edit: [x.strip() for x in e.text().split(",") if x.strip()],
                lambda v, e=edit: e.setText(", ".join(str(x) for x in (v or []))),
            )

        if isinstance(current, str):
            edit = QLineEdit()
            if "api_key" in key:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                edit.setPlaceholderText("prefer the environment variable")
            return _FieldEditor(key, edit, edit.text, lambda v, e=edit: e.setText(str(v)))

        log.debug("no editor for %s (%r)", key, type(current))
        return None

    def _ollama_model_editor(self, key: str, current: Any) -> _FieldEditor:
        """A dropdown of small models, editable for anyone who wants a bigger one.

        A plain text field here invites typing a model sized for a workstation
        onto a laptop that will then take thirty seconds to answer, or not
        answer at all. This offers models known to run acceptably instead,
        without refusing a name that is not on the list - editable, not
        restricted, because whether a bigger model is worth the wait is the
        user's call once they know what they are choosing.
        """
        from .. import ollama_models

        box = QComboBox()
        box.setEditable(True)
        # A label -> real model name map, rather than Qt's item data: on an
        # editable combo, typing a value that matches no item leaves
        # currentIndex() (and so currentData()) pointing at whatever it was
        # before, while currentText() correctly shows what was typed. Reading
        # back through currentData() would silently return the wrong model.
        names_by_label: Dict[str, str] = {}
        for name in ollama_models.LIGHTWEIGHT_MODELS:
            label = name + ("  (recommended)" if name == ollama_models.RECOMMENDED else "")
            box.addItem(label)
            names_by_label[label] = name

        def _get() -> str:
            text = box.currentText()
            return names_by_label.get(text, text)

        def _set(value: Any) -> None:
            value = str(value)
            label = next((l for l, n in names_by_label.items() if n == value), value)
            box.setCurrentText(label)

        return _FieldEditor(key, box, _get, _set)

    def _device_editor(self, key: str) -> _FieldEditor:
        """The microphone list, read from the system rather than typed in."""
        box = QComboBox()
        box.addItem("System default", None)
        try:
            from ..audio.mic import list_devices

            for dev in list_devices():
                box.addItem(f"[{dev['index']}] {dev['name']}", dev["index"])
        except Exception as exc:
            log.debug("could not list input devices: %s", exc)
            box.addItem("(could not read the device list)", None)

        def _set(value):
            index = box.findData(value)
            box.setCurrentIndex(index if index >= 0 else 0)

        return _FieldEditor(key, box, lambda b=box: b.currentData(), _set)

    # -------------------------------------------------------------- values
    def _load_from_config(self) -> None:
        for key, editor in self._editors.items():
            section, name = key.split(".", 1)
            editor.value = getattr(getattr(self.config, section), name)

        provider = self.config.ai.provider
        self.ai_enabled.setChecked(provider != "none")
        self._on_ai_toggled(provider != "none")

    def _on_ai_toggled(self, enabled: bool) -> None:
        """Grey out the AI fields when it is switched off."""
        for key, editor in self._editors.items():
            if key.startswith("ai."):
                editor.widget.setEnabled(enabled)
        if not enabled:
            self.note.setText(
                "Open questions are switched off. Voice commands are unaffected."
            )
        else:
            self.note.setText("")

    def _collect(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for key, editor in self._editors.items():
            values[key] = editor.value

        # The checkbox is the real control; the provider dropdown records which
        # backend to return to when it is switched back on.
        if not self.ai_enabled.isChecked():
            values["ai.provider"] = "none"
        elif values.get("ai.provider") == "none":
            values["ai.provider"] = "ollama"
        return values

    # --------------------------------------------------------------- saving
    def _save(self) -> None:
        values = self._collect()
        changed: List[str] = []

        for key, value in values.items():
            section, name = key.split(".", 1)
            sub = getattr(self.config, section)
            if getattr(sub, name) != value:
                setattr(sub, name, value)
                changed.append(key)

        try:
            path = self.config.save()
        except OSError as exc:
            QMessageBox.critical(
                self, "Could not save",
                f"Writing {CONFIG_FILE} failed:\n\n{exc.strerror}",
            )
            return

        log.info("settings saved to %s (%d changed)", path, len(changed))
        self.saved.emit()

        if changed and not set(changed) <= _LIVE_FIELDS:
            QMessageBox.information(
                self, "Saved",
                "Settings saved.\n\nRestart Black Voice for them to take effect:\n"
                "quit from the tray icon and start it again, or\n\n"
                "    systemctl --user restart blackvoice",
            )
        self.accept()

    def _restore_defaults(self) -> None:
        answer = QMessageBox.question(
            self, "Restore defaults",
            "Reset every setting to its default?\n\n"
            "This also restores the safety deny-list, which is not updated "
            "automatically when Black Voice is upgraded.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        fresh = Config()
        for key, editor in self._editors.items():
            section, name = key.split(".", 1)
            editor.value = getattr(getattr(fresh, section), name)
        self.ai_enabled.setChecked(fresh.ai.provider != "none")
