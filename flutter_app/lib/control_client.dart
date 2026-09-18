/// A client for Black Voice's local control socket.
///
/// Mirrors `blackvoice/control_socket.py`: one JSON object per line over a
/// Unix domain socket, requests carrying an `id` that the matching response
/// echoes back, and unsolicited `{"event": ...}` frames pushed whenever the
/// engine's own event bus fires. See that file's module docstring for why a
/// hand-rolled protocol over `dart:io`'s `Socket` rather than a package: the
/// same reasoning applies on this side of the connection - one less
/// dependency whose version can drift out from under a small client.
library control_client;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// Raised when the server answered but reported `"ok": false`.
class ControlException implements Exception {
  final String message;
  ControlException(this.message);

  @override
  String toString() => message;
}

/// Where the socket lives when nothing overrides it - the same resolution
/// [blackvoice.config.Config.control_socket_path] performs in Python.
/// `XDG_RUNTIME_DIR` is preferred because it is already per-user and
/// per-login-session; the cache directory is the fallback for the rare
/// desktop that does not set it.
String defaultControlSocketPath() {
  final env = Platform.environment;
  final runtimeDir = env['XDG_RUNTIME_DIR'];
  if (runtimeDir != null && runtimeDir.isNotEmpty) {
    return '$runtimeDir/blackvoice/control.sock';
  }
  final cacheHome = env['XDG_CACHE_HOME'];
  final home = env['HOME'] ?? '';
  final base = (cacheHome != null && cacheHome.isNotEmpty) ? cacheHome : '$home/.cache';
  return '$base/blackvoice/control.sock';
}

class ControlClient {
  final String socketPath;

  Socket? _socket;
  StreamSubscription<String>? _subscription;
  int _nextId = 1;
  final Map<int, Completer<Map<String, dynamic>>> _pending = {};
  final StreamController<Map<String, dynamic>> _events =
      StreamController<Map<String, dynamic>>.broadcast();

  ControlClient([String? socketPath]) : socketPath = socketPath ?? defaultControlSocketPath();

  /// Events pushed by the engine that were not a reply to any request of
  /// ours - state changes, what was heard, the assistant's replies.
  Stream<Map<String, dynamic>> get events => _events.stream;

  Future<void> connect({Duration timeout = const Duration(seconds: 5)}) async {
    _socket = await Socket.connect(
      InternetAddress(socketPath, type: InternetAddressType.unix),
      0,
    ).timeout(timeout);

    _subscription = _socket!
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(_onLine, onDone: _onDone, onError: (_) => _onDone());
  }

  void _onLine(String line) {
    if (line.isEmpty) return;
    final Map<String, dynamic> message = jsonDecode(line) as Map<String, dynamic>;

    final id = message['id'];
    if (id is int) {
      _pending.remove(id)?.complete(message);
      return;
    }
    if (message.containsKey('event')) {
      _events.add(message);
    }
  }

  void _onDone() {
    final pending = List.of(_pending.values);
    _pending.clear();
    for (final completer in pending) {
      if (!completer.isCompleted) {
        completer.completeError(StateError('the control socket closed'));
      }
    }
  }

  /// Send one request; resolves with the raw `{"id", "ok", ...}` reply.
  ///
  /// Does not throw on `"ok": false` - that is a normal, expected outcome for
  /// some calls (an unpulled model, a bad path). Use [callOk] when a failure
  /// should simply become a thrown exception, which is the common case.
  Future<Map<String, dynamic>> call(String op, [Map<String, dynamic>? params]) {
    final socket = _socket;
    if (socket == null) {
      throw StateError('call connect() before using the control client');
    }
    final id = _nextId++;
    final completer = Completer<Map<String, dynamic>>();
    _pending[id] = completer;

    final line = jsonEncode({'id': id, 'op': op, 'params': params ?? const {}});
    socket.add(utf8.encode('$line\n'));
    return completer.future;
  }

  /// Like [call], but returns just `result` and throws [ControlException]
  /// when the server reported `"ok": false`.
  Future<dynamic> callOk(String op, [Map<String, dynamic>? params]) async {
    final response = await call(op, params);
    if (response['ok'] != true) {
      throw ControlException((response['error'] ?? 'unknown error').toString());
    }
    return response['result'];
  }

  Future<void> close() async {
    await _subscription?.cancel();
    await _socket?.close();
    if (!_events.isClosed) {
      await _events.close();
    }
  }
}
