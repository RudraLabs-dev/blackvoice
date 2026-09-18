"""Engine.ensure_ai_backend(): waking an already-installed Ollama, never
installing one - and the systemctl nudge it can optionally use.
"""

from __future__ import annotations

import subprocess

import pytest

from blackvoice import ollama_models as om
from blackvoice.app import Engine, State
from blackvoice.config import Config
from blackvoice.core.bus import Topic


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("blackvoice.app.ensure_dirs", lambda: None)
    eng = Engine(Config())
    yield eng
    eng.stop()


class _Recorder:
    """Captures every event published on the bus, in order."""

    def __init__(self, bus):
        self.events = []
        for topic in (Topic.REPLY, Topic.STATE):
            bus.subscribe(topic, lambda e, t=topic: self.events.append((t, e.payload)))

    def topics(self):
        return [t for t, _ in self.events]


# --------------------------------------------------------------------------- #
# Engine.ensure_ai_backend
# --------------------------------------------------------------------------- #
def test_does_nothing_for_a_non_ollama_provider(engine, monkeypatch) -> None:
    engine.config.ai.provider = "anthropic"
    monkeypatch.setattr("shutil.which", lambda name: (_ for _ in ()).throw(AssertionError("should not even check")))
    engine.ensure_ai_backend()  # must not raise from the monkeypatched which()


def test_does_nothing_when_auto_setup_is_off(engine, monkeypatch) -> None:
    engine.config.ai.auto_setup = False
    monkeypatch.setattr("shutil.which", lambda name: (_ for _ in ()).throw(AssertionError("should not check")))
    engine.ensure_ai_backend()


def test_does_nothing_when_ollama_is_not_installed(engine, monkeypatch) -> None:
    """The one thing this project will not do automatically: install it."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    called = []
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: called.append("reachable") or True)
    engine.ensure_ai_backend()
    assert called == []  # never even asked whether a server is running


def test_nudges_a_stopped_service_then_gives_up_if_it_stays_down(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: False)
    monkeypatch.setattr("blackvoice.app.time.sleep", lambda s: None)  # do not really wait 5s
    started = []
    monkeypatch.setattr(om, "try_start_service", lambda: started.append(True))
    pulled_called = []
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: pulled_called.append(True))

    engine.ensure_ai_backend()

    assert started == [True]
    assert pulled_called == []  # never got far enough to check, let alone pull


def test_starts_the_service_and_then_pulls_when_not_pulled(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    calls = {"reachable": 0}

    def _reachable(url, timeout=2.0):
        calls["reachable"] += 1
        return calls["reachable"] > 1  # down first time, up after the nudge

    monkeypatch.setattr(om, "is_reachable", _reachable)
    monkeypatch.setattr(om, "try_start_service", lambda: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [])

    pulled = []
    monkeypatch.setattr(om, "pull", lambda name, url, on_progress=None: pulled.append(name))

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()

    assert pulled == [engine.config.ai.ollama_model]
    assert Topic.REPLY in recorder.topics()
    # _set_state(SETUP)/_set_state(IDLE) publish state events on their own;
    # what matters here is that on_progress itself was never called.
    progress_events = [p for t, p in recorder.events if t == Topic.STATE and "percent" in p]
    assert progress_events == []


def test_does_not_pull_a_model_that_is_already_there(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [engine.config.ai.ollama_model])
    pulled = []
    monkeypatch.setattr(om, "pull", lambda name, url, on_progress=None: pulled.append(name))

    engine.ensure_ai_backend()

    assert pulled == []


def test_pulls_when_the_server_answers_but_the_model_list_is_unknown(engine, monkeypatch) -> None:
    """pulled_models() returning None means "could not ask" - try the pull anyway."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: None)
    pulled = []
    monkeypatch.setattr(om, "pull", lambda name, url, on_progress=None: pulled.append(name))

    engine.ensure_ai_backend()

    assert pulled == [engine.config.ai.ollama_model]


def test_progress_reaches_the_bus_as_state_events(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [])

    def _pull(name, url, on_progress=None):
        on_progress("downloading", 50, 100)
        on_progress("downloading", 100, 100)

    monkeypatch.setattr(om, "pull", _pull)

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()

    # SETUP/IDLE transitions publish their own state events with no percent;
    # only the progress ones (from on_progress) carry one.
    states = [p for t, p in recorder.events if t == Topic.STATE and "percent" in p]
    assert [s["percent"] for s in states] == [50, 100]
    assert engine.state == State.IDLE  # returned to idle once the pull finished


def test_a_pull_failure_is_reported_not_raised(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [])

    def _boom(name, url, on_progress=None):
        raise om.OllamaError("disk full")

    monkeypatch.setattr(om, "pull", _boom)

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()  # must not raise

    failures = [p for t, p in recorder.events if t == Topic.REPLY and not p.get("ok", True)]
    assert any("disk full" in f["display"] for f in failures)
    assert engine.state == State.IDLE


def test_a_stale_state_does_not_flood_the_bus_with_repeat_percentages(engine, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [])

    def _pull(name, url, on_progress=None):
        for _ in range(5):
            on_progress("downloading", 50, 100)  # same percent every time

    monkeypatch.setattr(om, "pull", _pull)

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()

    states = [p for t, p in recorder.events if t == Topic.STATE and "percent" in p]
    assert len(states) == 1


# --------------------------------------------------------------------------- #
# Engine.ensure_ai_backend: the auto_install path (off by default)
# --------------------------------------------------------------------------- #
def test_auto_install_off_does_nothing_when_ollama_is_absent(engine, monkeypatch) -> None:
    """The current default: find nothing, do nothing, no download attempted."""
    assert engine.config.ai.auto_install is False
    monkeypatch.setattr(om, "find_binary", lambda: None)
    called = []
    monkeypatch.setattr(om, "download_and_install", lambda **k: called.append(True))
    engine.ensure_ai_backend()
    assert called == []


def test_auto_install_on_installs_wires_the_service_and_pulls(engine, monkeypatch) -> None:
    engine.config.ai.auto_install = True
    monkeypatch.setattr(om, "find_binary", lambda: None)
    monkeypatch.setattr(om, "download_and_install", lambda on_progress=None: "/priv/bin/ollama")

    service_written = []
    monkeypatch.setattr(om, "write_user_service", lambda binary: service_written.append(binary))
    service_started = []
    monkeypatch.setattr(om, "enable_and_start_user_service", lambda: service_started.append(True))

    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [])
    pulled = []
    monkeypatch.setattr(om, "pull", lambda name, url, on_progress=None: pulled.append(name))

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()

    assert service_written == ["/priv/bin/ollama"]
    assert service_started == [True]
    assert pulled == [engine.config.ai.ollama_model]
    installing = [p["display"] for t, p in recorder.events if t == Topic.REPLY]
    assert any("installing a local AI engine" in d for d in installing)


def test_auto_install_failure_is_reported_and_stops_there(engine, monkeypatch) -> None:
    engine.config.ai.auto_install = True
    monkeypatch.setattr(om, "find_binary", lambda: None)

    def _boom(on_progress=None):
        raise om.OllamaError("no build for this machine")

    monkeypatch.setattr(om, "download_and_install", _boom)
    service_written = []
    monkeypatch.setattr(om, "write_user_service", lambda binary: service_written.append(binary))
    pulled = []
    monkeypatch.setattr(om, "pull", lambda name, url, on_progress=None: pulled.append(name))

    recorder = _Recorder(engine.bus)
    engine.ensure_ai_backend()  # must not raise

    assert service_written == []  # never got as far as wiring up a service
    assert pulled == []
    failures = [p for t, p in recorder.events if t == Topic.REPLY and not p.get("ok", True)]
    assert any("no build for this machine" in f["display"] for f in failures)
    assert engine.state == State.IDLE


def test_a_preexisting_binary_is_preferred_over_installing_one(engine, monkeypatch) -> None:
    """find_binary() returning something at all means auto_install is never touched."""
    engine.config.ai.auto_install = True
    monkeypatch.setattr(om, "find_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(om, "is_reachable", lambda *a, **k: True)
    monkeypatch.setattr(om, "pulled_models", lambda *a, **k: [engine.config.ai.ollama_model])

    called = []
    monkeypatch.setattr(om, "download_and_install", lambda **k: called.append(True))

    engine.ensure_ai_backend()

    assert called == []


# --------------------------------------------------------------------------- #
# Engine._wait_for_ollama
# --------------------------------------------------------------------------- #
def test_wait_for_ollama_retries_then_succeeds(monkeypatch) -> None:
    from blackvoice.app import Engine

    monkeypatch.setattr("blackvoice.app.time.sleep", lambda s: None)
    calls = {"n": 0}

    def _reachable(url, timeout=1.0):
        calls["n"] += 1
        return calls["n"] >= 3

    fake_om = type("M", (), {"is_reachable": staticmethod(_reachable)})
    assert Engine._wait_for_ollama(fake_om, "http://x", tries=5, interval=0.01) is True
    assert calls["n"] == 3


def test_wait_for_ollama_gives_up_after_the_last_try(monkeypatch) -> None:
    from blackvoice.app import Engine

    monkeypatch.setattr("blackvoice.app.time.sleep", lambda s: None)
    fake_om = type("M", (), {"is_reachable": staticmethod(lambda url, timeout=1.0: False)})
    assert Engine._wait_for_ollama(fake_om, "http://x", tries=3, interval=0.01) is False


# --------------------------------------------------------------------------- #
# ollama_models.try_start_service
# --------------------------------------------------------------------------- #
def test_try_start_service_without_systemctl_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: None)
    ran = []
    monkeypatch.setattr(om.subprocess, "run", lambda *a, **k: ran.append(a))
    assert om.try_start_service() is False
    assert ran == []


def test_try_start_service_succeeds_on_the_user_unit(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/systemctl")
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(om.subprocess, "run", _run)
    assert om.try_start_service() is True
    assert calls == [["systemctl", "--user", "start", "ollama"]]  # never tried the system one


def test_try_start_service_falls_back_to_the_system_unit(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/systemctl")
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if argv[1] == "start" else 1)

    monkeypatch.setattr(om.subprocess, "run", _run)
    assert om.try_start_service() is True
    assert len(calls) == 2


def test_try_start_service_fails_quietly_when_neither_works(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(
        om.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 1),
    )
    assert om.try_start_service() is False


def test_try_start_service_survives_a_missing_binary_mid_call(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/systemctl")

    def _raise(argv, **k):
        raise OSError("no such file")

    monkeypatch.setattr(om.subprocess, "run", _raise)
    assert om.try_start_service() is False


def test_try_start_service_survives_a_timeout(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/systemctl")

    def _raise(argv, **k):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(om.subprocess, "run", _raise)
    assert om.try_start_service() is False
