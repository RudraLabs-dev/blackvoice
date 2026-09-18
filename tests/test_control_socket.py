"""The control socket: op handlers directly, and the real socket end to end.

Every op handler is tested by calling it directly - no socket involved - so
this suite proves the actual logic everywhere, including on a platform with no
Unix domain sockets at all. A second block opens a real socket and drives the
whole thing as a client would; it is skipped where the platform cannot make
one, and is the part that should be re-run on Linux before trusting this file.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

from blackvoice import control_socket as cs
from blackvoice.config import Config
from blackvoice.core.bus import Topic


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# apply_config / get_schema - pure logic, no server needed
# --------------------------------------------------------------------------- #
def test_apply_config_sets_and_saves(tmp_path) -> None:
    config = Config()
    config.save(tmp_path / "config.json")
    cs.apply_config(config, "ai.ollama_model", "qwen2.5:1.5b")
    assert config.ai.ollama_model == "qwen2.5:1.5b"


def test_apply_config_rejects_an_unknown_section() -> None:
    with pytest.raises(cs.ControlError, match="unknown section"):
        cs.apply_config(Config(), "nonsense.field", 1)


def test_apply_config_rejects_an_unknown_field() -> None:
    with pytest.raises(cs.ControlError, match="unknown field"):
        cs.apply_config(Config(), "ai.nonsense", 1)


def test_apply_config_rejects_a_path_with_no_dot() -> None:
    with pytest.raises(cs.ControlError, match="not a section.field path"):
        cs.apply_config(Config(), "ollama_model", "x")


def test_apply_config_rejects_a_type_mismatch() -> None:
    with pytest.raises(cs.ControlError, match="expects bool"):
        cs.apply_config(Config(), "safety.confirm_shell", "yes")


def test_apply_config_lets_an_int_stand_in_for_a_float() -> None:
    """JSON has no separate "this int is really a float" - 1 must work for 0.9."""
    config = Config()
    cs.apply_config(config, "voice.volume", 1)
    assert config.voice.volume == 1.0
    assert isinstance(config.voice.volume, float)


def test_apply_config_does_not_let_a_bool_stand_in_for_an_int() -> None:
    """bool is an int subclass in Python; that must not smuggle True into a count."""
    with pytest.raises(cs.ControlError, match="expects int"):
        cs.apply_config(Config(), "voice.rate", True)


def test_apply_config_does_not_let_an_int_stand_in_for_a_bool() -> None:
    """The other direction of the same subclass relationship."""
    with pytest.raises(cs.ControlError, match="expects bool"):
        cs.apply_config(Config(), "safety.confirm_shell", 1)


def test_get_schema_covers_every_dataclass_section() -> None:
    schema = cs.get_schema(Config())
    assert set(schema) == {
        "audio", "speech", "wake", "voice", "ai", "safety", "ui", "skills", "control",
    }
    assert schema["ai"]["ollama_model"]["value"] == "llama3.2"
    assert schema["ai"]["ollama_model"]["type"] == "str"
    assert schema["voice"]["rate"]["default"] == 145


def test_encode_ends_with_one_newline() -> None:
    line = cs._encode({"a": 1})
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert json.loads(line) == {"a": 1}


# --------------------------------------------------------------------------- #
# op handlers, called directly - the real dispatch logic, no socket needed
# --------------------------------------------------------------------------- #
class _FakeServer:
    """Just enough of ControlServer for an op handler to run against."""

    def __init__(self, engine):
        self.engine = engine


@pytest.fixture
def engine(monkeypatch):
    from blackvoice.app import Engine

    monkeypatch.setattr("blackvoice.app.ensure_dirs", lambda: None)
    eng = Engine(Config())
    yield eng
    eng.stop()


def test_op_ping(engine) -> None:
    assert _run(cs._op_ping(_FakeServer(engine), {})) == {"pong": True}


def test_op_get_state(engine) -> None:
    assert _run(cs._op_get_state(_FakeServer(engine), {}))["state"] == engine.state


def test_op_get_config_matches_the_real_config(engine) -> None:
    result = _run(cs._op_get_config(_FakeServer(engine), {}))
    assert result["ai"]["ollama_model"] == engine.config.ai.ollama_model


def test_op_set_config_changes_the_live_engine(engine) -> None:
    result = _run(
        cs._op_set_config(_FakeServer(engine), {"path": "ai.ollama_model", "value": "qwen2.5:1.5b"})
    )
    assert result == {"path": "ai.ollama_model", "value": "qwen2.5:1.5b"}
    assert engine.config.ai.ollama_model == "qwen2.5:1.5b"


def test_op_set_config_requires_path_and_value(engine) -> None:
    with pytest.raises(cs.ControlError, match="'path'"):
        _run(cs._op_set_config(_FakeServer(engine), {"value": 1}))
    with pytest.raises(cs.ControlError, match="'value'"):
        _run(cs._op_set_config(_FakeServer(engine), {"path": "ai.ollama_model"}))


def test_op_submit_text_routes_through_the_engine(engine) -> None:
    result = _run(cs._op_submit_text(_FakeServer(engine), {"text": "what time is it"}))
    assert "speech" in result and "display" in result and "ok" in result


def test_op_submit_text_requires_nonempty_text(engine) -> None:
    with pytest.raises(cs.ControlError, match="'text'"):
        _run(cs._op_submit_text(_FakeServer(engine), {"text": "   "}))


def test_op_list_ollama_models_reports_unreachable(engine, monkeypatch) -> None:
    monkeypatch.setattr("blackvoice.ollama_models.pulled_models", lambda url: None)
    result = _run(cs._op_list_ollama_models(_FakeServer(engine), {}))
    assert result["reachable"] is False
    assert all(m["pulled"] is None for m in result["models"])
    assert any(m["recommended"] for m in result["models"])


def test_op_list_ollama_models_marks_what_is_pulled(engine, monkeypatch) -> None:
    monkeypatch.setattr(
        "blackvoice.ollama_models.pulled_models", lambda url: ["qwen2.5:1.5b"]
    )
    result = _run(cs._op_list_ollama_models(_FakeServer(engine), {}))
    by_name = {m["name"]: m for m in result["models"]}
    assert by_name["qwen2.5:1.5b"]["pulled"] is True
    assert by_name["llama3.2:1b"]["pulled"] is False


def test_op_pull_ollama_model_requires_a_name(engine) -> None:
    with pytest.raises(cs.ControlError, match="'name'"):
        _run(cs._op_pull_ollama_model(_FakeServer(engine), {}))


def test_op_pull_ollama_model_wraps_ollama_errors(engine, monkeypatch) -> None:
    import blackvoice.ollama_models as om

    def _boom(name, url, on_progress=None):
        raise om.OllamaError("not installed")

    monkeypatch.setattr(om, "pull", _boom)
    with pytest.raises(cs.ControlError, match="not installed"):
        _run(cs._op_pull_ollama_model(_FakeServer(engine), {"name": "qwen2.5:0.5b"}))


# --------------------------------------------------------------------------- #
# ControlServer lifecycle - platform gating, no socket needed
# --------------------------------------------------------------------------- #
def test_start_is_false_when_disabled(engine) -> None:
    engine.config.control.enabled = False
    assert engine.control.start() is False
    assert engine.control.running is False


def test_start_is_false_when_the_platform_has_no_unix_sockets(engine, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_is_supported", lambda: False)
    assert engine.control.start() is False


def test_stop_before_start_does_not_raise(engine) -> None:
    engine.control.stop()  # must be a no-op, not an AttributeError on _loop


# --------------------------------------------------------------------------- #
# the client-facing protocol, driven without a real socket
# --------------------------------------------------------------------------- #
# _handle_client/_dispatch touch nothing but the reader and writer passed in -
# no bound event loop, no thread - so the line-reading and dispatch logic gets
# real coverage everywhere, including a platform with no Unix domain sockets.
# What is left untested below this point is asyncio.start_unix_server itself
# and the cross-thread event hop, which is what the skipped block further down
# covers on a platform that actually has one.
class _FakeWriter:
    def __init__(self):
        self.writes: list = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    @property
    def lines(self):
        return [json.loads(w) for w in b"".join(self.writes).splitlines() if w]


class _FakeReader:
    def __init__(self, *lines: bytes):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


def test_dispatch_a_single_line(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    _run(server._handle_client(_FakeReader(b'{"id":1,"op":"ping"}\n'), writer))
    assert writer.lines == [{"id": 1, "ok": True, "result": {"pong": True}}]
    assert writer.closed


def test_dispatch_several_lines_on_one_connection(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    reader = _FakeReader(
        b'{"id":1,"op":"ping"}\n',
        b'{"id":2,"op":"get_state"}\n',
    )
    _run(server._handle_client(reader, writer))
    ids = [line["id"] for line in writer.lines]
    assert ids == [1, 2]
    assert all(line["ok"] for line in writer.lines)


def test_dispatch_bad_json_does_not_stop_the_connection(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    reader = _FakeReader(b"{not json}\n", b'{"id":2,"op":"ping"}\n')
    _run(server._handle_client(reader, writer))
    assert writer.lines[0]["ok"] is False
    assert writer.lines[1] == {"id": 2, "ok": True, "result": {"pong": True}}


def test_dispatch_an_unknown_op(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    _run(server._handle_client(_FakeReader(b'{"id":1,"op":"fly_to_the_moon"}\n'), writer))
    assert writer.lines[0]["ok"] is False
    assert "unknown op" in writer.lines[0]["error"]


def test_dispatch_a_line_over_the_limit_is_rejected(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    huge = b'{"id":1,"op":"ping","pad":"' + b"x" * (cs._MAX_LINE + 1) + b'"}\n'
    _run(server._handle_client(_FakeReader(huge), writer))
    assert writer.lines[0]["ok"] is False
    assert "too long" in writer.lines[0]["error"]


def test_dispatch_a_handler_that_raises_is_an_internal_error_not_a_crash(engine, monkeypatch) -> None:
    async def _boom(server, params):
        raise RuntimeError("surprise")

    monkeypatch.setitem(cs._OPS, "ping", _boom)
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    _run(server._handle_client(_FakeReader(b'{"id":1,"op":"ping"}\n'), writer))
    assert writer.lines[0] == {"id": 1, "ok": False, "error": "internal error"}


def test_the_client_is_dropped_from_the_registry_on_disconnect(engine) -> None:
    server = cs.ControlServer(engine)
    writer = _FakeWriter()
    _run(server._handle_client(_FakeReader(b'{"id":1,"op":"ping"}\n'), writer))
    assert writer not in server._clients


# --------------------------------------------------------------------------- #
# the real socket, end to end - only where the platform actually has one
# --------------------------------------------------------------------------- #
pytestmark_e2e = pytest.mark.skipif(
    not (hasattr(socket, "AF_UNIX") and cs._is_supported()),
    reason="this platform has no Unix domain sockets (expected on Windows)",
)


class _Client:
    """A synchronous test double for whatever a real frontend would be."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect(str(path))
        self._buf = b""

    def send(self, op, params=None, id_=1):
        line = json.dumps({"id": id_, "op": op, "params": params or {}}) + "\n"
        self.sock.sendall(line.encode("utf-8"))

    def recv_line(self):
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed the connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line)

    def call(self, op, params=None, id_=1):
        self.send(op, params, id_)
        return self.recv_line()

    def close(self):
        self.sock.close()


@pytest.fixture
def running_server(engine, tmp_path):
    engine.config.control.socket_path = str(tmp_path / "control.sock")
    assert engine.control.start() is True
    # start() only returns once the socket exists, but give the loop a beat
    # before a client dials in on a slower CI runner.
    deadline = time.monotonic() + 2.0
    while not (tmp_path / "control.sock").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    yield engine
    engine.control.stop()


@pytestmark_e2e
def test_ping_over_the_real_socket(running_server, tmp_path) -> None:
    client = _Client(tmp_path / "control.sock")
    try:
        assert client.call("ping") == {"id": 1, "ok": True, "result": {"pong": True}}
    finally:
        client.close()


@pytestmark_e2e
def test_set_then_get_config_round_trips(running_server, tmp_path) -> None:
    client = _Client(tmp_path / "control.sock")
    try:
        client.call("set_config", {"path": "ai.ollama_model", "value": "qwen2.5:1.5b"})
        result = client.call("get_config", id_=2)
        assert result["result"]["ai"]["ollama_model"] == "qwen2.5:1.5b"
    finally:
        client.close()


@pytestmark_e2e
def test_an_unknown_op_is_a_clean_error(running_server, tmp_path) -> None:
    client = _Client(tmp_path / "control.sock")
    try:
        result = client.call("nonsense_op")
        assert result["ok"] is False
        assert "unknown op" in result["error"]
    finally:
        client.close()


@pytestmark_e2e
def test_bad_json_is_a_clean_error_not_a_dropped_connection(running_server, tmp_path) -> None:
    client = _Client(tmp_path / "control.sock")
    try:
        client.sock.sendall(b"{not json\n")
        result = client.recv_line()
        assert result["ok"] is False
        # The connection must still be usable afterwards.
        assert client.call("ping", id_=2)["ok"] is True
    finally:
        client.close()


@pytestmark_e2e
def test_a_bus_event_reaches_a_connected_client(running_server, tmp_path) -> None:
    client = _Client(tmp_path / "control.sock")
    try:
        running_server.bus.publish(Topic.STATE, state="listening")
        # An unsolicited push, not a reply to any request - no "id" of ours.
        deadline = time.monotonic() + 2.0
        event = None
        while time.monotonic() < deadline:
            try:
                event = client.recv_line()
                break
            except ConnectionError:
                break
        assert event == {"event": "state", "state": "listening"}
    finally:
        client.close()


@pytestmark_e2e
def test_the_socket_file_is_removed_on_stop(running_server, tmp_path) -> None:
    path = tmp_path / "control.sock"
    assert path.exists()
    running_server.control.stop()
    assert not path.exists()


@pytestmark_e2e
def test_a_second_start_while_running_is_a_cheap_no_op(running_server) -> None:
    assert running_server.control.start() is True
