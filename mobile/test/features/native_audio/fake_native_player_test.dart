import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/features/native_audio/providers.dart';

void main() {
  group('FakeNativePlayer', () {
    late FakeNativePlayer player;

    setUp(() {
      player = FakeNativePlayer(authenticated: false);
    });

    tearDown(() async {
      await player.dispose();
    });

    test('play before authenticate returns Failure.auth', () async {
      final result = await player.play('spotify:track:abc');
      expect(result.isErr, isTrue);
      expect(result.failureOrNull, isA<AuthFailure>());
    });

    test('authenticate then play emits PlayerState with uri', () async {
      final events = <PlayerState>[];
      final sub = player.stateStream.listen(events.add);

      final auth = await player.authenticate();
      expect(auth.isOk, isTrue);

      final play = await player.play('spotify:track:xyz');
      expect(play.isOk, isTrue);

      // let broadcast deliver
      await Future<void>.delayed(Duration.zero);

      expect(events, hasLength(1));
      expect(events.single.uri, 'spotify:track:xyz');
      expect(events.single.isPaused, isFalse);

      await sub.cancel();
    });

    test('pause / resume toggle isPaused on the stream', () async {
      await player.authenticate();
      await player.play('spotify:track:xyz');

      final events = <PlayerState>[];
      final sub = player.stateStream.listen(events.add);

      await player.pause();
      await player.resume();
      await Future<void>.delayed(Duration.zero);

      expect(events.map((s) => s.isPaused), <bool>[true, false]);
      await sub.cancel();
    });

    test('seek updates positionMs on the emitted state', () async {
      await player.authenticate();
      await player.play('spotify:track:xyz');

      final events = <PlayerState>[];
      final sub = player.stateStream.listen(events.add);

      await player.seek(const Duration(milliseconds: 12345));
      await Future<void>.delayed(Duration.zero);

      expect(events.single.positionMs, 12345);
      await sub.cancel();
    });

    test('nextFailure short-circuits the next call and clears', () async {
      await player.authenticate();
      player.nextFailure =
          const Failure.network(message: 'boom', statusCode: 503);

      final failed = await player.play('spotify:track:xyz');
      expect(failed.isErr, isTrue);
      expect(failed.failureOrNull, isA<NetworkFailure>());

      // Cleared — next call succeeds.
      final ok = await player.play('spotify:track:xyz');
      expect(ok.isOk, isTrue);
    });

    test('disconnect resets state and emits idle', () async {
      await player.authenticate();
      await player.play('spotify:track:xyz');

      final events = <PlayerState>[];
      final sub = player.stateStream.listen(events.add);

      await player.disconnect();
      await Future<void>.delayed(Duration.zero);

      expect(events.single.uri, isNull);
      expect(events.single.isPaused, isTrue);

      // After disconnect, play should fail auth again.
      final replay = await player.play('spotify:track:xyz');
      expect(replay.failureOrNull, isA<AuthFailure>());

      await sub.cancel();
    });

    test('remoteCommandController surfaces on remoteCommandStream', () async {
      final commands = <RemoteCommand>[];
      final sub = player.remoteCommandStream.listen(commands.add);

      player.remoteCommandController
        ..add(const PlayCommand())
        ..add(const NextCommand())
        ..add(const PauseCommand())
        ..add(const PreviousCommand());
      await Future<void>.delayed(Duration.zero);

      expect(commands, hasLength(4));
      expect(commands[0], isA<PlayCommand>());
      expect(commands[1], isA<NextCommand>());
      expect(commands[2], isA<PauseCommand>());
      expect(commands[3], isA<PreviousCommand>());

      await sub.cancel();
    });

    test('isSpotifyInstalled reflects constructor flag', () async {
      final installed = FakeNativePlayer();
      final missing = FakeNativePlayer(spotifyInstalled: false);
      expect(await installed.isSpotifyInstalled(), isTrue);
      expect(await missing.isSpotifyInstalled(), isFalse);
      await installed.dispose();
      await missing.dispose();
    });
  });
}
