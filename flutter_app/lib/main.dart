import 'package:flutter/material.dart';

import 'control_client.dart';
import 'home_screen.dart';

void main() {
  runApp(const BlackVoiceApp());
}

class BlackVoiceApp extends StatefulWidget {
  const BlackVoiceApp({super.key});

  @override
  State<BlackVoiceApp> createState() => _BlackVoiceAppState();
}

class _BlackVoiceAppState extends State<BlackVoiceApp> {
  late final ControlClient _client;
  late final Future<void> _connecting;

  @override
  void initState() {
    super.initState();
    _client = ControlClient();
    _connecting = _client.connect();
  }

  @override
  void dispose() {
    _client.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Black Voice',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.black),
      home: FutureBuilder<void>(
        future: _connecting,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const _Message(text: 'Connecting to Black Voice...');
          }
          if (snapshot.hasError) {
            return _Message(
              text: 'Could not reach Black Voice at ${_client.socketPath}\n\n'
                  'Is it running? Start it with:  blackvoice run\n\n'
                  '${snapshot.error}',
            );
          }
          return HomeScreen(client: _client);
        },
      ),
    );
  }
}

class _Message extends StatelessWidget {
  final String text;
  const _Message({required this.text});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(text, textAlign: TextAlign.center),
        ),
      ),
    );
  }
}
