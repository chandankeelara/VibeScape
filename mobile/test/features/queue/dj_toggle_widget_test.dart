import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/queue/presentation/widgets/dj_toggle.dart';

import '../../helpers/pump.dart';

void main() {
  testWidgets('DjToggle fires onChanged with inverted value', (tester) async {
    var value = false;
    await pumpWithProviders(
      tester,
      StatefulBuilder(
        builder: (context, setState) => DjToggle(
          active: value,
          onChanged: (v) => setState(() => value = v),
        ),
      ),
    );
    await tester.tap(find.text('DJ'));
    await tester.pumpAndSettle();
    expect(value, true);
    await tester.tap(find.text('DJ'));
    await tester.pumpAndSettle();
    expect(value, false);
  });
}
