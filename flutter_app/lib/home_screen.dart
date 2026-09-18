/// The main window: current state, the last thing heard and said, and a text
/// box so this works before any of the audio-loop-facing ops exist on the
/// bridge. Not a replacement for the PyQt6 overlay - that is a transient
/// popup tied to the wake word, not a window someone leaves open - see the
/// README for what a real equivalent still needs.
library home_screen;

import 'dart:async';

import 'package:flutter/material.dart';

import 'control_client.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  final ControlClient client;
  const HomeScreen({super.key, required this.client});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  String _state = 'idle';
  String _lastHeard = '';
  String _lastReply = '';
  bool _sending = false;

  final _controller = TextEditingController();
  late final StreamSubscription<Map<String, dynamic>> _subscription;

  @override
  void initState() {
    super.initState();
    _subscription = widget.client.events.listen(_onEvent);
    widget.client.callOk('get_state').then((result) {
      if (!mounted) return;
      setState(() => _state = (result as Map)['state'] as String? ?? _state);
    }).catchError((_) {});
  }

  void _onEvent(Map<String, dynamic> event) {
    if (!mounted) return;
    switch (event['event']) {
      case 'state':
        setState(() => _state = event['state'] as String? ?? _state);
        break;
      case 'heard':
        if (event['partial'] != true) {
          setState(() => _lastHeard = event['text'] as String? ?? '');
        }
        break;
      case 'reply':
        final text = (event['display'] as String?)?.trim();
        if (text != null && text.isNotEmpty) {
          setState(() => _lastReply = text);
        }
        break;
    }
  }

  @override
  void dispose() {
    _subscription.cancel();
    _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      final result = await widget.client.callOk('submit_text', {'text': text}) as Map;
      final display = (result['display'] as String?)?.trim();
      if (display != null && display.isNotEmpty) {
        setState(() => _lastReply = display);
      }
      _controller.clear();
    } on ControlException catch (e) {
      setState(() => _lastReply = 'Error: $e');
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Black Voice'),
        actions: [
          IconButton(
            icon: const Icon(Icons.smart_toy_outlined),
            tooltip: 'AI model',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => SettingsScreen(client: widget.client)),
            ),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Chip(label: Text('State: $_state')),
            ),
            const SizedBox(height: 20),
            if (_lastHeard.isNotEmpty)
              Text('You said: "$_lastHeard"', style: const TextStyle(color: Colors.grey)),
            if (_lastReply.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(_lastReply, style: Theme.of(context).textTheme.bodyLarge),
              ),
            const Spacer(),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _controller,
                    decoration: const InputDecoration(
                      hintText: 'Type a command...',
                      border: OutlineInputBorder(),
                    ),
                    onSubmitted: (_) => _send(),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  onPressed: _sending ? null : _send,
                  icon: _sending
                      ? const SizedBox(
                          width: 18, height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
