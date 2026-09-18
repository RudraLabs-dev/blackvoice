/// The one screen this scaffold actually delivers: choosing and fetching a
/// lightweight Ollama model, over the control socket rather than editing
/// config.json by hand.
///
/// Deliberately does not attempt the full settings surface the PyQt6 window
/// covers (every field of every config section) - that is a bigger, less
/// urgent rewrite than proving the architecture works for the thing that was
/// actually asked for: picking the model that answers questions, restricted
/// to sizes known to run acceptably, with the download itself one tap away
/// rather than a separate CLI command.
library settings_screen;

import 'package:flutter/material.dart';

import 'control_client.dart';

class SettingsScreen extends StatefulWidget {
  final ControlClient client;
  const SettingsScreen({super.key, required this.client});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  List<Map<String, dynamic>> _models = [];
  String? _current;
  bool _loading = true;
  bool _reachable = true;
  String? _pullingName;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final config = await widget.client.callOk('get_config') as Map<String, dynamic>;
      final list = await widget.client.callOk('list_ollama_models') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _current = (config['ai'] as Map<String, dynamic>)['ollama_model'] as String?;
        _models = (list['models'] as List).cast<Map<String, dynamic>>();
        _reachable = list['reachable'] == true;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _select(String name) async {
    final previous = _current;
    setState(() => _current = name);
    try {
      await widget.client.callOk('set_config', {'path': 'ai.ollama_model', 'value': name});
    } catch (e) {
      if (!mounted) return;
      setState(() => _current = previous);
      _showError('Could not save: $e');
    }
  }

  Future<void> _pull(String name) async {
    setState(() => _pullingName = name);
    try {
      await widget.client.callOk('pull_ollama_model', {'name': name});
      await _load();
    } catch (e) {
      _showError('Could not pull $name: $e');
    } finally {
      if (mounted) setState(() => _pullingName = null);
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('AI model'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _loading ? null : _load),
        ],
      ),
      body: _buildBody(context),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_error!, textAlign: TextAlign.center),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (!_reachable)
          const Card(
            color: Color(0xFFFFF3E0),
            child: Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'Ollama is not reachable right now. Models already pulled cannot be '
                'told apart from ones that are not - start it with "ollama serve" '
                'and reopen this screen to see which is which.',
              ),
            ),
          ),
        const Padding(
          padding: EdgeInsets.only(top: 8, bottom: 4),
          child: Text(
            'Models known to run acceptably on ordinary hardware. Pull one, '
            'then pick it - the assistant switches to it immediately.',
            style: TextStyle(color: Colors.grey),
          ),
        ),
        for (final model in _models) _ModelTile(
          model: model,
          selected: model['name'] == _current,
          pulling: _pullingName == model['name'],
          onSelect: () => _select(model['name'] as String),
          onPull: () => _pull(model['name'] as String),
        ),
      ],
    );
  }
}

class _ModelTile extends StatelessWidget {
  final Map<String, dynamic> model;
  final bool selected;
  final bool pulling;
  final VoidCallback onSelect;
  final VoidCallback onPull;

  const _ModelTile({
    required this.model,
    required this.selected,
    required this.pulling,
    required this.onSelect,
    required this.onPull,
  });

  @override
  Widget build(BuildContext context) {
    final bool? pulled = model['pulled'] as bool?;
    final String note = (model['note'] as String?) ?? '';
    final subtitle = StringBuffer()
      ..write('${model['params']}, ~${model['download_gb']} GB download, '
          '~${model['ram_gb']} GB RAM');
    if (note.isNotEmpty) subtitle.write(' - $note');

    return Card(
      child: ListTile(
        leading: Radio<bool>(
          value: true,
          groupValue: selected ? true : null,
          onChanged: pulled == true ? (_) => onSelect() : null,
        ),
        title: Row(
          children: [
            Text(model['name'] as String),
            if (model['recommended'] == true) ...[
              const SizedBox(width: 8),
              const Chip(
                label: Text('recommended', style: TextStyle(fontSize: 11)),
                visualDensity: VisualDensity.compact,
                materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
            ],
          ],
        ),
        subtitle: Text(subtitle.toString()),
        trailing: pulling
            ? const SizedBox(
                width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
            : pulled == true
                ? const Icon(Icons.check_circle, color: Colors.green)
                : TextButton(onPressed: onPull, child: const Text('Pull')),
      ),
    );
  }
}
