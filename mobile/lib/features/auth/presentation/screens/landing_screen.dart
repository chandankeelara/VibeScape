import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/auth/application/auth_controller.dart';
import 'package:vibescape/features/auth/application/spotify_auth_controller.dart';
import 'package:vibescape/features/auth/domain/auth_state.dart';
import 'package:vibescape/features/auth/presentation/widgets/auth_modal.dart';
import 'package:vibescape/features/auth/presentation/widgets/vibe_hero_demo.dart';

/// Landing / login page. Mirrors `frontend/login.html` — a hero copy block,
/// three primary CTAs (Log in / Just listen), and a live vibe-slider demo.
class LandingScreen extends ConsumerWidget {
  const LandingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final busy = auth.value is Busy;
    final failure = auth.value is AuthFailed
        ? (auth.value! as AuthFailed).failure.toString()
        : null;

    return Scaffold(
      backgroundColor: VibeTokens.bg,
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, cx) {
            final wide = cx.maxWidth >= 900;
            final content = [
              const Expanded(child: _CopyColumn()),
              if (wide) const SizedBox(width: VibeTokens.s32),
              if (wide) const Expanded(child: VibeHeroDemo()),
            ];
            return SingleChildScrollView(
              padding: const EdgeInsets.all(VibeTokens.s24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const _NavRow(),
                  const SizedBox(height: VibeTokens.s32),
                  wide
                      ? Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: content,
                        )
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: const [
                            _CopyColumn(),
                            SizedBox(height: VibeTokens.s24),
                            VibeHeroDemo(),
                          ],
                        ),
                  const SizedBox(height: VibeTokens.s24),
                  _CtaStack(busy: busy),
                  if (failure != null) ...[
                    const SizedBox(height: VibeTokens.s16),
                    Text(
                      failure,
                      style: const TextStyle(color: VibeTokens.danger),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ],
              ),
            );
          },
        ),
      ),
    );
  }
}

class _NavRow extends StatelessWidget {
  const _NavRow();
  @override
  Widget build(BuildContext context) {
    return Row(
      children: const [
        _Brand(),
        Spacer(),
        // Nav links intentionally omitted — the mobile port collapses to
        // just the brand + CTAs. Add if a marketing surface is needed.
      ],
    );
  }
}

class _Brand extends StatelessWidget {
  const _Brand();
  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: const BoxDecoration(
            color: VibeTokens.moodChill,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: VibeTokens.s8),
        Text(
          'VibeScape',
          style: Theme.of(context).textTheme.titleLarge?.copyWith(
                fontWeight: FontWeight.w700,
                letterSpacing: -0.3,
              ),
        ),
      ],
    );
  }
}

class _CopyColumn extends StatelessWidget {
  const _CopyColumn();

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(
            horizontal: VibeTokens.s12,
            vertical: VibeTokens.s4,
          ),
          decoration: BoxDecoration(
            color: VibeTokens.surface,
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: VibeTokens.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: const [
              Icon(Icons.circle, size: 8, color: VibeTokens.moodSteady),
              SizedBox(width: VibeTokens.s8),
              Text(
                'mood-based music player',
                style: TextStyle(fontSize: 12, color: VibeTokens.textSecondary),
              ),
            ],
          ),
        ),
        const SizedBox(height: VibeTokens.s16),
        Text(
          'Play music in chill mode.',
          style: t.displaySmall?.copyWith(
            fontWeight: FontWeight.w700,
            height: 1.05,
            letterSpacing: -1,
          ),
        ),
        const SizedBox(height: VibeTokens.s16),
        Text(
          'Every song, scored for energy and mood. Drag the slider — '
          'your queue shifts with it. Sign in with Spotify to sort your '
          'own library, or start listening in one tap.',
          style: t.bodyLarge?.copyWith(color: VibeTokens.textSecondary),
        ),
      ],
    );
  }
}

class _CtaStack extends ConsumerWidget {
  const _CtaStack({required this.busy});
  final bool busy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _CtaButton(
          title: 'Log in',
          subtitle: 'Spotify, email — more providers soon',
          icon: Icons.login,
          primary: true,
          enabled: !busy,
          onTap: () => showDialog<void>(
            context: context,
            barrierColor: Colors.black87,
            builder: (_) => const AuthModal(),
          ),
        ),
        const SizedBox(height: VibeTokens.s12),
        _CtaButton(
          title: 'Just listen',
          subtitle: 'No signup — curated library, streamed from YouTube',
          icon: Icons.play_arrow_rounded,
          enabled: !busy,
          onTap: () => ref.read(authControllerProvider.notifier).signInGuest(),
        ),
        const SizedBox(height: VibeTokens.s16),
        const Text(
          'No passwords required when you use Spotify. Guest sessions are ephemeral.',
          style: TextStyle(color: VibeTokens.textMuted, fontSize: 12),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

class _CtaButton extends StatelessWidget {
  const _CtaButton({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.onTap,
    this.primary = false,
    this.enabled = true,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final VoidCallback onTap;
  final bool primary;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final bg = primary ? VibeTokens.accent : VibeTokens.surface;
    final fg = primary ? Colors.black : VibeTokens.textPrimary;
    return Opacity(
      opacity: enabled ? 1 : 0.5,
      child: Material(
        color: bg,
        borderRadius: BorderRadius.circular(VibeTokens.rLg),
        child: InkWell(
          borderRadius: BorderRadius.circular(VibeTokens.rLg),
          onTap: enabled ? onTap : null,
          child: Padding(
            padding: const EdgeInsets.all(VibeTokens.s20),
            child: Row(
              children: [
                Icon(icon, color: fg),
                const SizedBox(width: VibeTokens.s16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: TextStyle(
                          color: fg,
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        subtitle,
                        style: TextStyle(
                          color: fg.withValues(alpha: 0.7),
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ),
                ),
                Icon(Icons.arrow_forward_rounded, color: fg),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
