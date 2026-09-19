"""Config loading, merging and environment overrides."""

from __future__ import annotations

import json

import pytest

from blackvoice.config import Config


def test_defaults_are_sane() -> None:
    cfg = Config()
    assert cfg.audio.sample_rate == 16000
    assert cfg.speech.mode == "hybrid"
    assert cfg.wake.enabled is True
    assert "black" in cfg.wake.phrases
    assert cfg.safety.confirm_shell is True


def test_round_trip(tmp_path) -> None:
    path = tmp_path / "config.json"
    original = Config()
    original.voice.rate = 200
    original.ai.provider = "openai"
    original.save(path)

    loaded = Config.load(path)
    assert loaded.voice.rate == 200
    assert loaded.ai.provider == "openai"


def test_load_creates_the_file(tmp_path) -> None:
    path = tmp_path / "nested" / "config.json"
    Config.load(path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["audio"]["sample_rate"] == 16000


def test_partial_file_keeps_other_defaults(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"voice": {"rate": 120}}), encoding="utf-8")

    cfg = Config.load(path)
    assert cfg.voice.rate == 120
    assert cfg.voice.volume == 0.9           # untouched default
    assert cfg.speech.mode == "hybrid"       # untouched section


def test_unknown_keys_are_ignored(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"voice": {"rate": 130, "nonsense": 1}, "bogus": {"x": 2}}),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.voice.rate == 130
    assert not hasattr(cfg, "bogus")


def test_broken_file_falls_back_to_defaults(tmp_path, capsys) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")

    cfg = Config.load(path)
    # Compare against the dataclass default rather than a literal, so tuning a
    # default does not fail a test that is about broken-file handling.
    assert cfg.voice.rate == Config().voice.rate
    assert "unreadable" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env_key,env_value,attr_path,expected",
    [
        ("BLACKVOICE_SPEECH_MODE", "offline", ("speech", "mode"), "offline"),
        ("BLACKVOICE_VOICE_RATE", "210", ("voice", "rate"), 210),
        ("BLACKVOICE_VOICE_VOLUME", "0.5", ("voice", "volume"), 0.5),
        ("BLACKVOICE_WAKE_ENABLED", "false", ("wake", "enabled"), False),
        ("BLACKVOICE_UI_ENABLED", "0", ("ui", "enabled"), False),
    ],
)
def test_env_overrides(tmp_path, monkeypatch, env_key, env_value, attr_path, expected) -> None:
    monkeypatch.setenv(env_key, env_value)
    cfg = Config.load(tmp_path / "config.json")
    section, key = attr_path
    assert getattr(getattr(cfg, section), key) == expected


def test_env_list_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BLACKVOICE_WAKE_PHRASES", "jarvis, friday")
    cfg = Config.load(tmp_path / "config.json")
    assert cfg.wake.phrases == ["jarvis", "friday"]


def test_bad_env_value_is_ignored(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BLACKVOICE_VOICE_RATE", "not-a-number")
    cfg = Config.load(tmp_path / "config.json")
    assert cfg.voice.rate == Config().voice.rate
    assert "ignoring bad env value" in capsys.readouterr().out


def test_model_path_resolution(tmp_path) -> None:
    """A bare name resolves under the models dir; an absolute path is used as-is."""
    cfg = Config()
    assert cfg.model_path().name == cfg.speech.model_en

    custom = tmp_path / "custom-en"
    cfg.speech.model_en = str(custom)
    assert cfg.model_path() == custom
