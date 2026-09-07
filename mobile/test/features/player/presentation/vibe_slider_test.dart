import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/player/presentation/widgets/vibe_slider.dart';

import '../../../helpers/pump.dart';

void main() {
  testWidgets('VibeSlider updates value on horizontal drag', (tester) async {
    var current = 50;
    var lastEnd = -1;
    await pumpWithProviders(
      tester,
      StatefulBuilder(
        builder: (context, setState) => SizedBox(
          width: 300,
          child: VibeSlider(
            value: current,
            onChanged: (v) => setState(() => current = v),
            onChangeEnd: (v) => lastEnd = v,
          ),
        ),
      ),
    );

    final finder = find.byType(VibeSlider);
    expect(finder, findsOneWidget);

    // Drag to roughly the far right (should push value up toward 100).
    await tester.timedDrag(
      finder,
      const Offset(120, 0),
      const Duration(milliseconds: 200),
    );
    await tester.pumpAndSettle();

    expect(current, greaterThan(50));
    expect(lastEnd, greaterThan(50));
  });

  testWidgets('VibeSlider releasing settles via spring animation',
      (tester) async {
    var current = 50;
    var endedAt = 0;
    await pumpWithProviders(
      tester,
      StatefulBuilder(
        builder: (context, setState) => SizedBox(
          width: 300,
          child: VibeSlider(
            value: current,
            onChanged: (v) => setState(() => current = v),
            onChangeEnd: (v) => endedAt = v,
          ),
        ),
      ),
    );

    await tester.timedDrag(
      find.byType(VibeSlider),
      const Offset(-90, 0),
      const Duration(milliseconds: 150),
    );
    // Pump long enough for spring to settle.
    await tester.pump(const Duration(milliseconds: 600));
    await tester.pumpAndSettle(const Duration(seconds: 1));

    expect(current, lessThan(50));
    expect(endedAt, current);
  });
}
