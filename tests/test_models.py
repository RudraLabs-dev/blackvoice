"""First-run model setup: what is needed, what is missing, what gets fetched."""

from __future__ import annotations

import pytest

from blackvoice import models
from blackvoice.config import Config


# ------------------------------------------------------------------ wanted
def test_hybrid_or_offline_mode_needs_the_english_model() -> None:
    cfg = Config()
    assert models.wanted(cfg) == ["en"]


def test_online_mode_needs_no_models() -> None:
    """Nothing should be downloaded for a configuration that never uses Vosk."""
    cfg = Config()
    cfg.speech.mode = "online"
    assert models.wanted(cfg) == []
    assert models.missing(cfg) == []


# ----------------------------------------------------------------- missing
def test_missing_reports_absent_models(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("blackvoice.models.MODELS_DIR", tmp_path)
    cfg = Config()
    monkeypatch.setattr(Config, "model_path", lambda self: tmp_path / "en")

    assert models.missing(cfg) == ["en"]

    (tmp_path / "en").mkdir()
    assert models.missing(cfg) == []


# ------------------------------------------------------------------ ensure
def test_ensure_is_a_no_op_when_models_are_present(monkeypatch) -> None:
    monkeypatch.setattr(models, "missing", lambda cfg: [])
    called = []
    monkeypatch.setattr(models, "download", lambda *a, **k: called.append(a) or True)

    assert models.ensure(Config()) is True
    assert not called, "nothing should be downloaded when nothing is missing"


def test_ensure_downloads_what_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(models, "missing", lambda cfg: ["en"])
    fetched = []

    def _fake_download(lang, on_progress=None, timeout=60.0):
        fetched.append(lang)
        return True

    monkeypatch.setattr(models, "download", _fake_download)

    messages = []
    assert models.ensure(Config(), on_message=messages.append) is True
    assert fetched == ["en"]
    assert any("downloading" in m.lower() for m in messages)
    assert any("ready" in m.lower() for m in messages)


def test_ensure_respects_the_opt_out(monkeypatch) -> None:
    monkeypatch.setattr(models, "missing", lambda cfg: ["en"])
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("must not download"))

    cfg = Config()
    cfg.speech.auto_download = False

    messages = []
    assert models.ensure(cfg, on_message=messages.append) is False
    assert any("blackvoice setup" in m for m in messages)


def test_ensure_survives_a_failed_download(monkeypatch) -> None:
    """No network on first run must not stop the assistant from starting."""
    monkeypatch.setattr(models, "missing", lambda cfg: ["en"])
    monkeypatch.setattr(models, "download", lambda *a, **k: False)

    messages = []
    assert models.ensure(Config(), on_message=messages.append) is False
    assert any("could not be downloaded" in m for m in messages)


def test_download_rejects_an_unknown_language() -> None:
    assert models.download("klingon") is False


# ----------------------------------------------------------------- catalogue
def test_every_wanted_language_has_a_url() -> None:
    cfg = Config()
    for lang in models.wanted(cfg):
        assert lang in models.MODEL_URLS
        name, url = models.MODEL_URLS[lang]
        assert name and url.startswith("https://")
        assert url.endswith(".zip")


def test_auto_download_defaults_to_on() -> None:
    assert Config().speech.auto_download is True
