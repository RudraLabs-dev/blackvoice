"""A local control socket, for anything that is not in this process.

The PyQt6 tray and overlay call straight into :class:`~blackvoice.app.Engine`
because they share its address space. A frontend written in anything else -
Flutter above all - cannot do that, and needs a channel across the process
boundary instead.

That channel is a Unix domain socket, speaking one JSON object per line in
each direction. Two decisions are worth explaining:

**A socket file, not a TCP port.** The socket lives under the user's own
``XDG_RUNTIME_DIR`` (mode 0600, in a 0700 directory), so the filesystem's own
permissions are the access control - no token to generate, hand to a client,
store, or leak. Nothing here is meant to be reachable from another machine,
and a socket file cannot be, by construction, whereas a loopback TCP port
usually can be reasoned about but is one misconfigured container network away
from being wrong.

**Hand-rolled JSON lines, not a library.** ``websockets`` and ``aiohttp``
would be the obvious choices and both are ruled out for the reason
``ctranslate2`` was: they ship one wheel per CPython version, and every
library this project bundles into ``/opt/blackvoice/lib`` has to be a
``py3-none`` or ``abi3`` wheel so the package keeps working when the host
upgrades its system Python - see ``packaging/build-package.sh``. A socket,
``asyncio`` and ``json`` are the standard library; nothing here can go out of
date the way a bundled C extension can.

POSIX only. ``asyncio.start_unix_server`` does not exist on Windows, which is
not a loss this project takes anywhere else either - it has never targeted
anything but Linux. :meth:`ControlServer.start` simply does not open a socket
when the platform lacks one, and logs why.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Set

from .core.bus import Topic

if TYPE_CHECKING:  # pragma: no cover - import cycle only at type-check time
    from .app import Engine

log = logging.getLogger(__name__)

#: Topics forwarded to every connected client as {"event": topic, ...payload}.
#: LEVEL is left out - it fires many times a second and no client needs it yet.
_FORWARDED_TOPICS = (Topic.STATE, Topic.HEARD, Topic.REPLY, Topic.CONFIRM)

#: Longest line accepted from a client, to bound memory from a runaway sender.
_MAX_LINE = 1 << 20


class ControlError(Exception):
    """Raised by an operation handler; its message is sent back to the client."""


def _is_supported() -> bool:
    return hasattr(asyncio, "start_unix_server")


# --------------------------------------------------------------------------- #
# config get/set
# --------------------------------------------------------------------------- #
def _config_sections(config) -> Dict[str, Any]:
    return {f.name: getattr(config, f.name) for f in dataclasses.fields(config)}


def get_schema(config) -> Dict[str, Any]:
    """Enough about each field for a generic form to render one.

    Deliberately thinner than the PyQt6 settings window's ``CHOICES``/``HELP``
    tables: those are presentation text for one particular UI toolkit, and
    belong to it. What every client needs regardless of toolkit is here - the
    field's current value, its default, and its type - and a future client is
    free to keep its own labels and hints keyed by the same dotted paths this
    already uses, the same way the PyQt6 window does.
    """
    schema: Dict[str, Any] = {}
    for section_name, section in _config_sections(config).items():
        if not dataclasses.is_dataclass(section):
            continue
        fields: Dict[str, Any] = {}
        defaults = type(section)()
        for f in dataclasses.fields(section):
            value = getattr(section, f.name)
            fields[f.name] = {
                "value": value,
                "default": getattr(defaults, f.name),
                "type": type(value).__name__,
            }
        schema[section_name] = fields
    return schema


def apply_config(config, path: str, value: Any) -> Any:
    """Set ``section.field`` to ``value`` and save. Returns the value stored.

    Unlike an environment-variable override, JSON already distinguishes bool,
    int, float, str and list, so there is no string to coerce - only a type to
    check against what is already there, the same guard
    :func:`blackvoice.config._merge` applies when loading the file itself.
    """
    if path.count(".") != 1:
        raise ControlError(f"not a section.field path: {path!r}")
    section_name, field_name = path.split(".", 1)

    sub = getattr(config, section_name, None)
    if sub is None or not dataclasses.is_dataclass(sub):
        raise ControlError(f"unknown section {section_name!r}")

    known = {f.name for f in dataclasses.fields(sub)}
    if field_name not in known:
        raise ControlError(f"unknown field {path!r}")

    current = getattr(sub, field_name)
    if current is not None:
        # bool is a subclass of int in Python, so a plain isinstance(value,
        # type(current)) would let True/False slip into an int field, and an
        # int slip into a bool one - checked separately, in that order, before
        # the general case.
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise ControlError(f"{path} expects bool, got {type(value).__name__}")
        elif isinstance(current, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ControlError(f"{path} expects float, got {type(value).__name__}")
            value = float(value)
        elif isinstance(current, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ControlError(f"{path} expects int, got {type(value).__name__}")
        elif not isinstance(value, type(current)):
            raise ControlError(
                f"{path} expects {type(current).__name__}, got {type(value).__name__}"
            )

    setattr(sub, field_name, value)
    config.save()
    return value


# --------------------------------------------------------------------------- #
# the server
# --------------------------------------------------------------------------- #
Handler = Callable[["ControlServer", Dict[str, Any]], Awaitable[Any]]


class ControlServer:
    """Owns the socket and the private event loop it runs on.

    Everything in :class:`~blackvoice.app.Engine` already has to be safe to
    call from a thread other than its own audio loop - the Qt tray does so on
    the Qt thread today - so handlers here call straight into it from the
    control loop's thread rather than marshalling through yet another queue.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: Set[asyncio.StreamWriter] = set()
        self._unsubscribe: list = []
        self._path: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        """Open the socket in a dedicated thread. False means it did not start.

        Never raising: a frontend that cannot be reached is a smaller problem
        than a voice assistant that fails to start because of one.
        """
        if not self.engine.config.control.enabled:
            log.debug("control socket disabled in configuration")
            return False
        if not _is_supported():
            log.info("control socket needs a Unix domain socket; not available here")
            return False
        if self.running:
            return True

        ready = threading.Event()
        outcome: Dict[str, Any] = {}

        def _thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._async_start())
                outcome["ok"] = True
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("control socket failed to start")
                outcome["ok"] = False
                outcome["error"] = str(exc)
            finally:
                ready.set()
            if outcome.get("ok"):
                loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=_thread_main, name="control", daemon=True)
        self._thread.start()
        ready.wait(timeout=5.0)
        return bool(outcome.get("ok"))

    def stop(self) -> None:
        if self._loop is None:
            return
        loop, self._loop = self._loop, None

        def _shutdown() -> None:
            if self._server is not None:
                self._server.close()
            for unsub in self._unsubscribe:
                unsub()
            self._unsubscribe.clear()
            loop.stop()

        loop.call_soon_threadsafe(_shutdown)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._server = None
        if self._path:
            try:
                os.unlink(self._path)
            except OSError:
                pass

    async def _async_start(self) -> None:
        path = self.engine.config.control_socket_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.unlink()  # a stale socket from a previous run that crashed
        except FileNotFoundError:
            pass

        self._server = await asyncio.start_unix_server(self._handle_client, path=str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            log.debug("could not chmod the control socket", exc_info=True)
        self._path = str(path)

        for topic in _FORWARDED_TOPICS:
            self._unsubscribe.append(self.engine.bus.subscribe(topic, self._on_event(topic)))

        log.info("control socket listening at %s", path)

    def _on_event(self, topic: str) -> Callable[[Any], None]:
        """A bus subscriber. Runs on the engine's thread; hops to the loop's."""

        def _handler(event) -> None:
            loop = self._loop
            if loop is None:
                return
            payload = {"event": topic, **event.payload}
            loop.call_soon_threadsafe(self._broadcast_nowait, payload)

        return _handler

    def _broadcast_nowait(self, payload: Dict[str, Any]) -> None:
        line = _encode(payload)
        for writer in list(self._clients):
            try:
                writer.write(line)
            except Exception:
                self._clients.discard(writer)

    # ------------------------------------------------------------- per client
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.add(writer)
        try:
            while True:
                try:
                    raw = await reader.readline()
                except (ConnectionResetError, asyncio.IncompleteReadError):
                    break
                if not raw:
                    break
                if len(raw) > _MAX_LINE:
                    await self._send(writer, {"ok": False, "error": "line too long"})
                    continue
                await self._dispatch(raw, writer)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _dispatch(self, raw: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(writer, {"ok": False, "error": "invalid JSON"})
            return

        request_id = message.get("id")
        op = message.get("op")
        handler = _OPS.get(op)
        if handler is None:
            await self._send(writer, {"id": request_id, "ok": False, "error": f"unknown op {op!r}"})
            return

        try:
            result = await handler(self, message.get("params") or {})
            await self._send(writer, {"id": request_id, "ok": True, "result": result})
        except ControlError as exc:
            await self._send(writer, {"id": request_id, "ok": False, "error": str(exc)})
        except Exception:
            log.exception("control op %r raised", op)
            await self._send(
                writer, {"id": request_id, "ok": False, "error": "internal error"}
            )

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: Dict[str, Any]) -> None:
        try:
            writer.write(_encode(payload))
            await writer.drain()
        except Exception:
            log.debug("could not write to a control client", exc_info=True)


def _encode(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
_OPS: Dict[str, Handler] = {}


def _op(name: str) -> Callable[[Handler], Handler]:
    def _register(fn: Handler) -> Handler:
        _OPS[name] = fn
        return fn

    return _register


@_op("ping")
async def _op_ping(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"pong": True}


@_op("get_state")
async def _op_get_state(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": server.engine.state}


@_op("get_schema")
async def _op_get_schema(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    return get_schema(server.engine.config)


@_op("get_config")
async def _op_get_config(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    return server.engine.config.to_dict()


@_op("set_config")
async def _op_set_config(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    path = params.get("path")
    if not isinstance(path, str):
        raise ControlError("'path' is required")
    if "value" not in params:
        raise ControlError("'value' is required")
    value = apply_config(server.engine.config, path, params["value"])
    return {"path": path, "value": value}


@_op("submit_text")
async def _op_submit_text(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ControlError("'text' is required")
    reply = server.engine.submit_text(text)
    return {"speech": reply.speech, "display": reply.display, "ok": reply.ok}


@_op("list_ollama_models")
async def _op_list_ollama_models(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import ollama_models

    loop = asyncio.get_running_loop()
    url = server.engine.config.ai.ollama_url
    pulled = await loop.run_in_executor(None, ollama_models.pulled_models, url)

    models = []
    for model in ollama_models.LIGHTWEIGHT_MODELS.values():
        models.append(
            {
                "name": model.name,
                "params": model.params,
                "download_gb": model.download_gb,
                "ram_gb": model.ram_gb,
                "note": model.note,
                "recommended": model.name == ollama_models.RECOMMENDED,
                "pulled": None if pulled is None else model.name in pulled,
            }
        )
    return {"models": models, "reachable": pulled is not None}


@_op("pull_ollama_model")
async def _op_pull_ollama_model(server: ControlServer, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import ollama_models

    name = params.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ControlError("'name' is required")

    loop = asyncio.get_running_loop()
    url = server.engine.config.ai.ollama_url
    try:
        # Blocking (a pull is tens of seconds to a few minutes); running it in
        # the default executor keeps every other client's requests answered
        # in the meantime. A later version can stream progress as events once
        # a client exists to show it - see the roadmap notes.
        await loop.run_in_executor(None, ollama_models.pull, name, url, None)
    except ollama_models.OllamaError as exc:
        raise ControlError(str(exc)) from exc
    return {"name": name}
