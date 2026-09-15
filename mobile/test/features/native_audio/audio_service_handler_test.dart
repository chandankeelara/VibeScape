import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/native_audio/providers.dart';

void main() {
  group('VibescapeAudioHandler', () {
    late VibescapeAudioHandler handler;

    setUp(() {
      handler = VibescapeAudioHandler();
    });

    tearDown(() async {
      await handler.dispose();
    });

    test('play() emits PlayCommand on commandStream', () async {
      final commands = <RemoteCommand>[];
      final sub = handler.commandStream.listen(commands.add);

      await handler.play();
      await Future<void>.delayed(Duration.zero);

      expect(commands, hasLength(1));
      expect(commands.single, isA<PlayCommand>());
      await sub.cancel();
    });

    test('pause() emits PauseCommand', () async {
      final commands = <RemoteCommand>[];
      final sub = handler.commandStream.listen(commands.add);

      await handler.pause();
      await Future<void>.delayed(Duration.zero);

      expect(commands.single, isA<PauseCommand>());
      await sub.cancel();
    });

    test('skipToNext / skipToPrevious emit Next / Previous commands',
        () async {
      final commands = <RemoteCommand>[];
      final sub = handler.commandStream.listen(commands.add);

      await handler.skipToNext();
      await handler.skipToPrevious();
      await Future<void>.delayed(Duration.zero);

      expect(commands, hasLength(2));
      expect(commands[0], isA<NextCommand>());
      expect(commands[1], isA<PreviousCommand>());
      await sub.cancel();
    });

    test('commandStream is broadcast — multiple listeners receive events',
        () async {
      final a = <RemoteCommand>[];
      final b = <RemoteCommand>[];
      final subA = handler.commandStream.listen(a.add);
      final subB = handler.commandStream.listen(b.add);

      await handler.play();
      await handler.skipToNext();
      await Future<void>.delayed(Duration.zero);

      expect(a, hasLength(2));
      expect(b, hasLength(2));
      await subA.cancel();
      await subB.cancel();
    });

    test('debugEmit routes a synthesised command through the stream',
        () async {
      final commands = <RemoteCommand>[];
      final sub = handler.commandStream.listen(commands.add);

      handler.debugEmit(const NextCommand());
      await Future<void>.delayed(Duration.zero);

      expect(commands.single, isA<NextCommand>());
      await sub.cancel();
    });
  });
}
