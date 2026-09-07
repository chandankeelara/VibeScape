import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// URL paste input for the "Add public playlist" tab.
class PlaylistUrlInput extends StatefulWidget {
  const PlaylistUrlInput({
    super.key,
    required this.onChanged,
    this.initial,
  });

  final ValueChanged<String?> onChanged;
  final String? initial;

  @override
  State<PlaylistUrlInput> createState() => _PlaylistUrlInputState();
}

class _PlaylistUrlInputState extends State<PlaylistUrlInput> {
  late final TextEditingController _text =
      TextEditingController(text: widget.initial ?? '');

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  bool get _looksValid {
    final v = _text.text.trim();
    if (v.isEmpty) return false;
    return v.contains('open.spotify.com/playlist/');
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          decoration: BoxDecoration(
            color: VibeTokens.surfaceHi,
            borderRadius: BorderRadius.circular(VibeTokens.rMd),
            border: Border.all(color: VibeTokens.border),
          ),
          padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s12),
          child: TextField(
            controller: _text,
            keyboardType: TextInputType.url,
            style: const TextStyle(color: VibeTokens.textPrimary),
            decoration: const InputDecoration(
              isCollapsed: true,
              contentPadding: EdgeInsets.symmetric(vertical: 12),
              border: InputBorder.none,
              hintText: 'https://open.spotify.com/playlist/…',
              hintStyle: TextStyle(color: VibeTokens.textMuted),
            ),
            onChanged: (v) {
              setState(() {});
              widget.onChanged(v.trim().isEmpty ? null : v.trim());
            },
          ),
        ),
        const SizedBox(height: VibeTokens.s8),
        Text(
          _text.text.isEmpty
              ? 'Paste a playlist link to continue'
              : (_looksValid
                  ? 'Looks good — hit sync to import.'
                  : 'That doesn\'t look like a Spotify playlist URL.'),
          style: TextStyle(
            color: _looksValid
                ? VibeTokens.accent
                : (_text.text.isEmpty
                    ? VibeTokens.textMuted
                    : VibeTokens.warning),
            fontSize: 11,
          ),
        ),
      ],
    );
  }
}
