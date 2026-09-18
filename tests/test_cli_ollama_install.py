"""blackvoice setup --ollama --install: the CLI's on-demand path to the same
code ai.auto_install would otherwise run on the next first run.
"""

from __future__ import annotations

import pytest

from blackvoice import cli, ollama_models as om
from blackvoice.config import Config


def test_install_does_nothing_when_ollama_is_already_there(monkeypatch, capsys) -> None:
    monkeypatch.setattr(om, "find_binary", lambda: "/usr/bin/ollama")
    called = []
    monkeypatch.setattr(om, "download_and_install", lambda **k: called.append(True))

    assert cli._install_ollama(Config()) == 0
    assert called == []
    assert "already available" in capsys.readouterr().out


def test_install_downloads_and_enables_the_service(monkeypatch, capsys) -> None:
    monkeypatch.setattr(om, "find_binary", lambda: None)
    monkeypatch.setattr(om, "download_and_install", lambda on_progress=None: "/priv/bin/ollama")
    written = []
    monkeypatch.setattr(om, "write_user_service", lambda binary: written.append(binary))
    monkeypatch.setattr(om, "enable_and_start_user_service", lambda: True)

    assert cli._install_ollama(Config()) == 0
    out = capsys.readouterr().out
    assert written == ["/priv/bin/ollama"]
    assert "installed to /priv/bin/ollama" in out
    assert "running as a --user systemd service" in out


def test_install_reports_when_the_service_could_not_be_enabled(monkeypatch, capsys) -> None:
    monkeypatch.setattr(om, "find_binary", lambda: None)
    monkeypatch.setattr(om, "download_and_install", lambda on_progress=None: "/priv/bin/ollama")
    monkeypatch.setattr(om, "write_user_service", lambda binary: None)
    monkeypatch.setattr(om, "enable_and_start_user_service", lambda: False)

    assert cli._install_ollama(Config()) == 0  # the binary is still usable
    out = capsys.readouterr().out
    assert "could not enable the systemd service" in out
    assert "/priv/bin/ollama serve" in out


def test_install_failure_returns_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(om, "find_binary", lambda: None)

    def _boom(on_progress=None):
        raise om.OllamaError("no build for this machine")

    monkeypatch.setattr(om, "download_and_install", _boom)
    written = []
    monkeypatch.setattr(om, "write_user_service", lambda binary: written.append(binary))

    assert cli._install_ollama(Config()) == 1
    assert written == []
    assert "no build for this machine" in capsys.readouterr().out


def test_setup_ollama_dashinstall_routes_to_install_ollama(monkeypatch) -> None:
    import argparse

    called = []
    monkeypatch.setattr(cli, "_install_ollama", lambda config: called.append(config) or 0)
    args = argparse.Namespace(install=True, model=None, set_default=False)

    assert cli._setup_ollama(args) == 0
    assert len(called) == 1


def test_setup_ollama_with_no_model_and_nothing_installed_says_so(monkeypatch, capsys) -> None:
    import argparse

    # _setup_ollama loads the real config on disk by default; give it a
    # throwaway one instead of reading (or creating) the developer's own.
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda *a, **k: Config()))
    monkeypatch.setattr(om, "find_binary", lambda: None)
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: False)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: None)
    args = argparse.Namespace(install=False, model=None, set_default=False)

    assert cli._setup_ollama(args) == 1
    out = capsys.readouterr().out
    assert "not installed anywhere" in out
    assert "--install" in out
