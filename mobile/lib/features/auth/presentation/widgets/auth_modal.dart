import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/auth/application/auth_controller.dart';
import 'package:vibescape/features/auth/application/spotify_auth_controller.dart';
import 'package:vibescape/features/auth/domain/auth_state.dart';

/// The "Log in to VibeScape" dialog. Three providers (Spotify live, YouTube
/// Music / Apple Music / Amazon Music disabled with "Soon" badges), a
/// divider, then email tabs (Log in / Create account).
class AuthModal extends ConsumerStatefulWidget {
  const AuthModal({super.key});

  @override
  ConsumerState<AuthModal> createState() => _AuthModalState();
}

class _AuthModalState extends ConsumerState<AuthModal> {
  bool _signUp = false;
  final _email = TextEditingController();
  final _password = TextEditingController();

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authControllerProvider);
    final busy = auth.value is Busy;
    final failure = auth.value is AuthFailed
        ? (auth.value! as AuthFailed).failure.toString()
        : null;

    ref.listen(authControllerProvider, (prev, next) {
      if (next.value is Authenticated) Navigator.of(context).pop();
    });

    return Dialog(
      backgroundColor: VibeTokens.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(VibeTokens.rLg),
      ),
      insetPadding: const EdgeInsets.all(VibeTokens.s16),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(VibeTokens.s24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Log in to VibeScape',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
              const SizedBox(height: VibeTokens.s4),
              const Text(
                'Pick a provider — or use email.',
                style: TextStyle(color: VibeTokens.textSecondary),
              ),
              const SizedBox(height: VibeTokens.s20),
              _providerButton(
                label: 'Continue with Spotify',
                onTap: busy
                    ? null
                    : () async {
                        await ref
                            .read(spotifyAuthControllerProvider)
                            .start();
                      },
                icon: Icons.music_note,
                color: const Color(0xFF1DB954),
              ),
              const SizedBox(height: VibeTokens.s8),
              _providerButton(
                label: 'Continue with YouTube Music',
                icon: Icons.smart_display,
                soon: true,
              ),
              const SizedBox(height: VibeTokens.s8),
              _providerButton(
                label: 'Continue with Apple Music',
                icon: Icons.apple,
                soon: true,
              ),
              const SizedBox(height: VibeTokens.s8),
              _providerButton(
                label: 'Continue with Amazon Music',
                icon: Icons.shopping_bag,
                soon: true,
              ),
              const SizedBox(height: VibeTokens.s20),
              const _Divider(),
              const SizedBox(height: VibeTokens.s16),
              Row(
                children: [
                  _tab('Log in', !_signUp, () => setState(() => _signUp = false)),
                  const SizedBox(width: VibeTokens.s8),
                  _tab('Create account', _signUp, () => setState(() => _signUp = true)),
                ],
              ),
              const SizedBox(height: VibeTokens.s16),
              TextField(
                controller: _email,
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                enabled: !busy,
                decoration: const InputDecoration(
                  labelText: 'Email',
                  hintText: 'you@example.com',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: VibeTokens.s12),
              TextField(
                controller: _password,
                obscureText: true,
                autofillHints: [
                  _signUp
                      ? AutofillHints.newPassword
                      : AutofillHints.password,
                ],
                enabled: !busy,
                decoration: InputDecoration(
                  labelText: 'Password',
                  hintText:
                      _signUp ? 'at least 6 characters' : 'your password',
                  border: const OutlineInputBorder(),
                ),
              ),
              if (failure != null) ...[
                const SizedBox(height: VibeTokens.s12),
                Text(
                  failure,
                  style: const TextStyle(color: VibeTokens.danger, fontSize: 13),
                ),
              ],
              const SizedBox(height: VibeTokens.s16),
              FilledButton(
                onPressed: busy ? null : _submit,
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: VibeTokens.s16),
                  backgroundColor: VibeTokens.accent,
                  foregroundColor: Colors.black,
                ),
                child: Text(
                  busy
                      ? 'Please wait…'
                      : _signUp
                          ? 'Create account'
                          : 'Log in with email',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _submit() async {
    final ctrl = ref.read(authControllerProvider.notifier);
    final email = _email.text.trim();
    final password = _password.text;
    if (_signUp) {
      await ctrl.signUpEmail(email: email, password: password);
    } else {
      await ctrl.signInEmail(email: email, password: password);
    }
  }

  Widget _providerButton({
    required String label,
    required IconData icon,
    VoidCallback? onTap,
    Color? color,
    bool soon = false,
  }) {
    return Material(
      color: VibeTokens.surfaceHi,
      borderRadius: BorderRadius.circular(VibeTokens.rMd),
      child: InkWell(
        borderRadius: BorderRadius.circular(VibeTokens.rMd),
        onTap: soon ? null : onTap,
        child: Opacity(
          opacity: soon ? 0.5 : 1,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: VibeTokens.s16,
              vertical: VibeTokens.s12,
            ),
            child: Row(
              children: [
                Icon(icon, color: color ?? VibeTokens.textPrimary),
                const SizedBox(width: VibeTokens.s12),
                Expanded(child: Text(label)),
                if (soon)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: VibeTokens.border,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: const Text('Soon', style: TextStyle(fontSize: 11)),
                  )
                else
                  const Icon(Icons.arrow_forward_rounded, size: 16),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _tab(String label, bool active, VoidCallback onTap) {
    return Expanded(
      child: Material(
        color: active ? VibeTokens.accent : VibeTokens.surfaceHi,
        borderRadius: BorderRadius.circular(VibeTokens.rSm),
        child: InkWell(
          borderRadius: BorderRadius.circular(VibeTokens.rSm),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: VibeTokens.s12),
            child: Text(
              label,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: active ? Colors.black : VibeTokens.textPrimary,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();
  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Expanded(child: Divider(color: VibeTokens.border)),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s12),
          child: Text(
            'or',
            style: TextStyle(color: VibeTokens.textMuted.withValues(alpha: 0.8)),
          ),
        ),
        const Expanded(child: Divider(color: VibeTokens.border)),
      ],
    );
  }
}
