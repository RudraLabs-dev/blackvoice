"""Installing Ollama itself: finding a binary, fetching one, running it as a
--user systemd service. Everything that touches the network or a real
subprocess is mocked - this is Linux-only, opt-in behaviour this suite must
still exercise fully on a platform (and without the bandwidth) to run it for
real on.
"""

from __future__ import annotations

import subprocess

import pytest

from blackvoice import ollama_models as om


# --------------------------------------------------------------------------- #
# find_binary: precedence
# --------------------------------------------------------------------------- #
def test_find_binary_prefers_path_over_a_private_copy(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "bin" / "ollama"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("private copy")
    monkeypatch.setattr(om, "bundled_binary_path", lambda: bundled)
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/ollama")

    assert om.find_binary() == "/usr/bin/ollama"


def test_find_binary_falls_back_to_the_private_copy(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "bin" / "ollama"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("private copy")
    monkeypatch.setattr(om, "bundled_binary_path", lambda: bundled)
    monkeypatch.setattr(om.shutil, "which", lambda name: None)

    assert om.find_binary() == str(bundled)


def test_find_binary_is_none_when_neither_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(om, "bundled_binary_path", lambda: tmp_path / "bin" / "ollama")
    monkeypatch.setattr(om.shutil, "which", lambda name: None)

    assert om.find_binary() is None


# --------------------------------------------------------------------------- #
# _asset_name_for_this_machine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "machine, expected",
    [
        ("x86_64", "ollama-linux-amd64.tar.zst"),
        ("amd64", "ollama-linux-amd64.tar.zst"),
        ("aarch64", "ollama-linux-arm64.tar.zst"),
        ("arm64", "ollama-linux-arm64.tar.zst"),
        ("X86_64", "ollama-linux-amd64.tar.zst"),  # case-insensitive
    ],
)
def test_asset_name_for_known_machines(monkeypatch, machine, expected) -> None:
    import platform as real_platform

    monkeypatch.setattr(real_platform, "machine", lambda: machine)
    assert om._asset_name_for_this_machine() == expected


def test_asset_name_for_an_unknown_machine_raises(monkeypatch) -> None:
    import platform as real_platform

    monkeypatch.setattr(real_platform, "machine", lambda: "riscv64")
    with pytest.raises(om.OllamaError, match="riscv64"):
        om._asset_name_for_this_machine()


# --------------------------------------------------------------------------- #
# _latest_release_asset_url
# --------------------------------------------------------------------------- #
class _FakeGhResponse:
    def __init__(self, assets):
        self._assets = assets

    def raise_for_status(self):
        pass

    def json(self):
        return {"assets": self._assets}


def test_latest_release_asset_url_finds_the_matching_asset(monkeypatch) -> None:
    assets = [
        {"name": "ollama-linux-amd64.tar.zst", "browser_download_url": "https://x/amd64.tar.zst"},
        {"name": "ollama-linux-arm64.tar.zst", "browser_download_url": "https://x/arm64.tar.zst"},
    ]
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, headers=None: _FakeGhResponse(assets)
    )
    url = om._latest_release_asset_url("ollama-linux-amd64.tar.zst", timeout=5.0)
    assert url == "https://x/amd64.tar.zst"


def test_latest_release_asset_url_raises_when_the_asset_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, headers=None: _FakeGhResponse([])
    )
    with pytest.raises(om.OllamaError, match="no ollama-linux-amd64.tar.zst asset|has no"):
        om._latest_release_asset_url("ollama-linux-amd64.tar.zst", timeout=5.0)


def test_latest_release_asset_url_wraps_a_network_failure(monkeypatch) -> None:
    def _raise(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("requests.get", _raise)
    with pytest.raises(om.OllamaError, match="could not look up"):
        om._latest_release_asset_url("ollama-linux-amd64.tar.zst", timeout=5.0)


# --------------------------------------------------------------------------- #
# _extract_zst_tar
# --------------------------------------------------------------------------- #
def test_extract_prefers_tar_with_builtin_zstd(monkeypatch, tmp_path) -> None:
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(om.subprocess, "run", _run)
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/zstd")  # must not be used

    om._extract_zst_tar(tmp_path / "a.tar.zst", tmp_path / "out")

    assert len(calls) == 1
    assert calls[0][:2] == ["tar", "--zstd"]


def test_extract_falls_back_to_piping_standalone_zstd(monkeypatch, tmp_path) -> None:
    def _run(argv, **kwargs):
        if argv[:2] == ["tar", "--zstd"]:
            return subprocess.CompletedProcess(argv, 1, stderr=b"unrecognized option --zstd")
        return subprocess.CompletedProcess(argv, 0, stderr=b"")

    monkeypatch.setattr(om.subprocess, "run", _run)
    monkeypatch.setattr(om.shutil, "which", lambda name: "/usr/bin/zstd")

    class _FakeStdout:
        def close(self):
            pass

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout = _FakeStdout()
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(om.subprocess, "Popen", _FakePopen)

    om._extract_zst_tar(tmp_path / "a.tar.zst", tmp_path / "out")  # must not raise


def test_extract_raises_a_clear_error_when_nothing_can_unpack_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        om.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 1, stderr=b"no such option"),
    )
    monkeypatch.setattr(om.shutil, "which", lambda name: None)  # no standalone zstd either

    with pytest.raises(om.OllamaError, match="zstd"):
        om._extract_zst_tar(tmp_path / "a.tar.zst", tmp_path / "out")


def test_extract_survives_tar_missing_entirely(monkeypatch, tmp_path) -> None:
    def _raise(argv, **k):
        raise OSError("no such file: tar")

    monkeypatch.setattr(om.subprocess, "run", _raise)
    monkeypatch.setattr(om.shutil, "which", lambda name: None)

    with pytest.raises(om.OllamaError):
        om._extract_zst_tar(tmp_path / "a.tar.zst", tmp_path / "out")


# --------------------------------------------------------------------------- #
# download_and_install: the orchestration, each piece mocked
# --------------------------------------------------------------------------- #
def test_download_and_install_refuses_off_linux(monkeypatch) -> None:
    monkeypatch.setattr(om.sys, "platform", "win32")
    with pytest.raises(om.OllamaError, match="Linux-only"):
        om.download_and_install()


class _FakeStreamingResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def raise_for_status(self):
        pass

    @property
    def headers(self):
        return {"content-length": str(len(self._payload))}

    def iter_content(self, chunk_size):
        yield self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_and_install_happy_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(om.sys, "platform", "linux")
    monkeypatch.setattr(om, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(om, "bundled_binary_path", lambda: tmp_path / "bin" / "ollama")
    monkeypatch.setattr(om, "_asset_name_for_this_machine", lambda: "ollama-linux-amd64.tar.zst")
    monkeypatch.setattr(om, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.zst")
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeStreamingResponse(b"fake archive bytes"))

    def _fake_extract(archive, dest):
        # Simulate the archive actually containing bin/ollama.
        target = tmp_path / "bin" / "ollama"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\n")

    monkeypatch.setattr(om, "_extract_zst_tar", _fake_extract)

    progress = []
    binary = om.download_and_install(on_progress=lambda *a: progress.append(a))

    assert binary == str(tmp_path / "bin" / "ollama")
    assert progress and progress[0][0] == "downloading"
    # The archive is cleaned up either way.
    assert not (tmp_path / "ollama-linux-amd64.tar.zst").exists()


def test_download_and_install_raises_when_the_archive_has_no_binary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(om.sys, "platform", "linux")
    monkeypatch.setattr(om, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(om, "bundled_binary_path", lambda: tmp_path / "bin" / "ollama")
    monkeypatch.setattr(om, "_asset_name_for_this_machine", lambda: "ollama-linux-amd64.tar.zst")
    monkeypatch.setattr(om, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.zst")
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeStreamingResponse(b"bytes"))
    monkeypatch.setattr(om, "_extract_zst_tar", lambda archive, dest: None)  # extracts nothing

    with pytest.raises(om.OllamaError, match="did not contain"):
        om.download_and_install()


def test_download_and_install_wraps_a_download_failure(monkeypatch, tmp_path) -> None:
    import requests

    monkeypatch.setattr(om.sys, "platform", "linux")
    monkeypatch.setattr(om, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(om, "_asset_name_for_this_machine", lambda: "ollama-linux-amd64.tar.zst")
    monkeypatch.setattr(om, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.zst")

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("requests.get", _raise)

    with pytest.raises(om.OllamaError, match="could not download"):
        om.download_and_install()
    # Nothing left behind from the failed attempt.
    assert list(tmp_path.glob("*.tar.zst")) == []


# --------------------------------------------------------------------------- #
# the --user systemd service
# --------------------------------------------------------------------------- #
def test_user_service_path_respects_xdg_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert om.user_service_path() == tmp_path / "systemd" / "user" / "ollama.service"


def test_write_user_service_contains_the_binary_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = om.write_user_service("/priv/bin/ollama")
    text = path.read_text(encoding="utf-8")
    assert "/priv/bin/ollama serve" in text
    assert "[Service]" in text and "[Install]" in text


def test_enable_and_start_service_without_systemctl(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: None)
    assert om.enable_and_start_user_service() is False


def test_enable_and_start_service_success(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(om.subprocess, "run", _run)
    assert om.enable_and_start_user_service() is True
    assert ["systemctl", "--user", "enable", "--now", "ollama"] in calls
    assert any(c[0] == "loginctl" for c in calls)


def test_enable_and_start_service_reports_the_enable_failure(monkeypatch) -> None:
    monkeypatch.setattr(om.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _run(argv, **kwargs):
        if argv[:4] == ["systemctl", "--user", "enable", "--now"]:
            return subprocess.CompletedProcess(argv, 1)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(om.subprocess, "run", _run)
    assert om.enable_and_start_user_service() is False


def test_enable_and_start_service_survives_a_missing_loginctl(monkeypatch) -> None:
    monkeypatch.setattr(
        om.shutil, "which",
        lambda name: None if name == "loginctl" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        om.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0)
    )
    assert om.enable_and_start_user_service() is True  # lingering is best-effort only
