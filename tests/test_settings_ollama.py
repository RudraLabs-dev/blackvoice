"""The ai.ollama_model dropdown: curated models, plus a way to type past them."""

from __future__ import annotations

import os

import pytest

if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 or its Qt libraries are unavailable")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from blackvoice import ollama_models  # noqa: E402
from blackvoice.config import Config  # noqa: E402
from blackvoice.ui.settings import SettingsWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    return SettingsWindow(Config())


def test_a_value_outside_the_curated_list_still_round_trips(window) -> None:
    """The default config ships "llama3.2", which is not in the curated list.

    An editable-but-restrictive combo can easily show the first curated item
    instead of the configured one the moment it does not match anything - this
    pins the fix down.
    """
    editor = window._editors["ai.ollama_model"]
    assert editor.value == "llama3.2"


def test_selecting_a_curated_model_reads_back_the_bare_name(window) -> None:
    """The dropdown labels the recommended entry; the stored value must not be."""
    editor = window._editors["ai.ollama_model"]
    editor.value = ollama_models.RECOMMENDED
    assert editor.value == ollama_models.RECOMMENDED
    assert "recommended" not in editor.value


def test_typing_a_model_not_on_the_list_is_kept_verbatim(window) -> None:
    editor = window._editors["ai.ollama_model"]
    editor.value = "mixtral:8x7b"
    assert editor.value == "mixtral:8x7b"


def test_every_curated_model_is_offered(window) -> None:
    box = window._editors["ai.ollama_model"].widget
    labels = {box.itemText(i) for i in range(box.count())}
    for name in ollama_models.LIGHTWEIGHT_MODELS:
        assert any(label.startswith(name) for label in labels), name


def test_the_collected_value_is_the_bare_model_name(window) -> None:
    """What _save() will actually write, without going through _save() itself.

    _save() writes straight to CONFIG_FILE and can pop a restart dialog, so the
    value that would be saved is checked one layer down, through _collect() -
    the same dictionary _save() applies to the config before calling
    config.save(). It must be the bare tag, not the "(recommended)" label.
    """
    editor = window._editors["ai.ollama_model"]
    editor.value = ollama_models.RECOMMENDED
    assert window._collect()["ai.ollama_model"] == ollama_models.RECOMMENDED
