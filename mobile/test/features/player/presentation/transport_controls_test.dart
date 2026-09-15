import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/player/presentation/widgets/transport_controls.dart';

import '../../../helpers/pump.dart';

void main() {
  testWidgets('tapping play button invokes onPlayPause', (tester) async {
    var plays = 0;
    var prevs = 0;
    var nexts = 0;
    await pumpWithProviders(
      tester,
      TransportControls(
        isPlaying: false,
        accentColor: VibeTokens.moodSteady,
        onPlayPause: () => plays += 1,
        onPrev: () => prevs += 1,
        onNext: () => nexts += 1,
      ),
    );

    await tester.tap(find.byIcon(Icons.play_arrow_rounded));
    await tester.pumpAndSettle();
    expect(plays, 1);

    await tester.tap(find.byIcon(Icons.skip_previous_rounded));
    await tester.pumpAndSettle();
    expect(prevs, 1);

    await tester.tap(find.byIcon(Icons.skip_next_rounded));
    await tester.pumpAndSettle();
    expect(nexts, 1);
  });

  testWidgets('play button shows pause icon when isPlaying=true',
      (tester) async {
    await pumpWithProviders(
      tester,
      TransportControls(
        isPlaying: true,
        accentColor: VibeTokens.moodHype,
        onPlayPause: () {},
        onPrev: () {},
        onNext: () {},
      ),
    );
    expect(find.byIcon(Icons.pause_rounded), findsOneWidget);
    expect(find.byIcon(Icons.play_arrow_rounded), findsNothing);
  });
}
