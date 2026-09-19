"""Installing the Piper binary: finding one, fetching one, extracting the
archive it comes in. Everything that touches the network or the filesystem
beyond a tmp_path is mocked or real-but-scoped - this is Linux-only, on-by-
default behaviour this suite must still exercise fully on any platform.
"""

from __future__ import annotations

import tarfile

import pytest

from blackvoice import piper_install as pi


# --------------------------------------------------------------------------- #
# find_binary: precedence
# --------------------------------------------------------------------------- #
def test_find_binary_prefers_path_over_a_private_copy(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "piper" / "piper"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("private copy")
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: bundled)
    monkeypatch.setattr(pi.shutil, "which", lambda name: "/usr/bin/piper")

    assert pi.find_binary() == "/usr/bin/piper"


def test_find_binary_falls_back_to_the_private_copy(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "piper" / "piper"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("private copy")
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: bundled)
    monkeypatch.setattr(pi.shutil, "which", lambda name: None)

    assert pi.find_binary() == str(bundled)


def test_find_binary_is_none_when_neither_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: tmp_path / "piper" / "piper")
    monkeypatch.setattr(pi.shutil, "which", lambda name: None)

    assert pi.find_binary() is None


# --------------------------------------------------------------------------- #
# espeak_data_dir
# --------------------------------------------------------------------------- #
def test_espeak_data_dir_present(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "piper" / "piper"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("x")
    (bundled.parent / "espeak-ng-data").mkdir()
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: bundled)

    assert pi.espeak_data_dir() == bundled.parent / "espeak-ng-data"


def test_espeak_data_dir_absent(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "piper" / "piper"
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: bundled)
    assert pi.espeak_data_dir() is None


# --------------------------------------------------------------------------- #
# _asset_name_for_this_machine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "machine, expected",
    [
        ("x86_64", "piper_linux_x86_64.tar.gz"),
        ("amd64", "piper_linux_x86_64.tar.gz"),
        ("aarch64", "piper_linux_aarch64.tar.gz"),
        ("arm64", "piper_linux_aarch64.tar.gz"),
        ("armv7l", "piper_linux_armv7l.tar.gz"),
        ("X86_64", "piper_linux_x86_64.tar.gz"),  # case-insensitive
    ],
)
def test_asset_name_for_known_machines(monkeypatch, machine, expected) -> None:
    import platform as real_platform

    monkeypatch.setattr(real_platform, "machine", lambda: machine)
    assert pi._asset_name_for_this_machine() == expected


def test_asset_name_for_an_unknown_machine_raises(monkeypatch) -> None:
    import platform as real_platform

    monkeypatch.setattr(real_platform, "machine", lambda: "riscv64")
    with pytest.raises(pi.PiperError, match="riscv64"):
        pi._asset_name_for_this_machine()


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
        {"name": "piper_linux_x86_64.tar.gz", "browser_download_url": "https://x/x86_64.tar.gz"},
        {"name": "piper_linux_aarch64.tar.gz", "browser_download_url": "https://x/aarch64.tar.gz"},
    ]
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, headers=None: _FakeGhResponse(assets)
    )
    url = pi._latest_release_asset_url("piper_linux_x86_64.tar.gz", timeout=5.0)
    assert url == "https://x/x86_64.tar.gz"


def test_latest_release_asset_url_raises_when_the_asset_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, headers=None: _FakeGhResponse([])
    )
    with pytest.raises(pi.PiperError, match="has no"):
        pi._latest_release_asset_url("piper_linux_x86_64.tar.gz", timeout=5.0)


def test_latest_release_asset_url_wraps_a_network_failure(monkeypatch) -> None:
    def _raise(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("requests.get", _raise)
    with pytest.raises(pi.PiperError, match="could not look up"):
        pi._latest_release_asset_url("piper_linux_x86_64.tar.gz", timeout=5.0)


# --------------------------------------------------------------------------- #
# _extract_tar
# --------------------------------------------------------------------------- #
def _make_tar(tmp_path, members: dict) -> "pathlib.Path":
    archive = tmp_path / "piper.tar.gz"
    src = tmp_path / "_src"
    src.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in members.items():
            path = src / name.replace("/", "_")
            path.write_text(content)
            tar.add(path, arcname=name)
    return archive


def test_extract_tar_unpacks_the_real_layout(tmp_path) -> None:
    archive = _make_tar(tmp_path, {"piper/piper": "binary", "piper/README.md": "hi"})
    dest = tmp_path / "out"

    pi._extract_tar(archive, dest)

    assert (dest / "piper" / "piper").read_text() == "binary"
    assert (dest / "piper" / "README.md").read_text() == "hi"


def test_extract_tar_refuses_a_path_traversal_member(tmp_path) -> None:
    archive = tmp_path / "evil.tar.gz"
    src = tmp_path / "_src"
    src.mkdir()
    evil = src / "evil"
    evil.write_text("gotcha")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(evil, arcname="../../evil")

    with pytest.raises(pi.PiperError, match="outside the install directory"):
        pi._extract_tar(archive, tmp_path / "out")


# --------------------------------------------------------------------------- #
# download_and_install: the orchestration, each piece mocked
# --------------------------------------------------------------------------- #
def test_download_and_install_refuses_off_linux(monkeypatch) -> None:
    monkeypatch.setattr(pi.sys, "platform", "win32")
    with pytest.raises(pi.PiperError, match="Linux-only"):
        pi.download_and_install()


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
    monkeypatch.setattr(pi.sys, "platform", "linux")
    monkeypatch.setattr(pi, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: tmp_path / "piper" / "piper")
    monkeypatch.setattr(pi, "_asset_name_for_this_machine", lambda: "piper_linux_x86_64.tar.gz")
    monkeypatch.setattr(pi, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.gz")
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeStreamingResponse(b"fake archive bytes"))

    def _fake_extract(archive, dest):
        target = tmp_path / "piper" / "piper"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\n")

    monkeypatch.setattr(pi, "_extract_tar", _fake_extract)

    progress = []
    binary = pi.download_and_install(on_progress=lambda *a: progress.append(a))

    assert binary == str(tmp_path / "piper" / "piper")
    assert progress and progress[0][0] == "downloading"
    assert not (tmp_path / "piper_linux_x86_64.tar.gz").exists()


def test_download_and_install_raises_when_the_archive_has_no_binary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pi.sys, "platform", "linux")
    monkeypatch.setattr(pi, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(pi, "bundled_binary_path", lambda: tmp_path / "piper" / "piper")
    monkeypatch.setattr(pi, "_asset_name_for_this_machine", lambda: "piper_linux_x86_64.tar.gz")
    monkeypatch.setattr(pi, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.gz")
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeStreamingResponse(b"bytes"))
    monkeypatch.setattr(pi, "_extract_tar", lambda archive, dest: None)  # extracts nothing

    with pytest.raises(pi.PiperError, match="did not contain"):
        pi.download_and_install()


def test_download_and_install_wraps_a_download_failure(monkeypatch, tmp_path) -> None:
    import requests

    monkeypatch.setattr(pi.sys, "platform", "linux")
    monkeypatch.setattr(pi, "default_install_dir", lambda: tmp_path)
    monkeypatch.setattr(pi, "_asset_name_for_this_machine", lambda: "piper_linux_x86_64.tar.gz")
    monkeypatch.setattr(pi, "_latest_release_asset_url", lambda name, timeout: "https://x/a.tar.gz")

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("requests.get", _raise)

    with pytest.raises(pi.PiperError, match="could not download"):
        pi.download_and_install()
    assert list(tmp_path.glob("*.tar.gz")) == []
