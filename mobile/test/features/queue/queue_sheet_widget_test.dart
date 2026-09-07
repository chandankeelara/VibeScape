import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/queue/application/queue_api.dart';
import 'package:vibescape/features/queue/application/queue_controller.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';
import 'package:vibescape/features/queue/presentation/queue_sheet.dart';

import '../../helpers/pump.dart';
import '_fakes.dart';

void main() {
  testWidgets('renders both section headers with an empty queue',
      (tester) async {
    await pumpWithProviders(
      tester,
      const SizedBox(width: 400, height: 800, child: QueueSheet()),
      overrides: [queueApiProvider.overrideWithValue(FakeQueueApi())],
    );
    await tester.pumpAndSettle();
    expect(find.text('Up next'), findsOneWidget);
    expect(find.text('Recommended for this track'), findsOneWidget);
    expect(find.textContaining('queue is empty'), findsOneWidget);
  });

  testWidgets('populates queue list from controller', (tester) async {
    ProviderContainer? container;
    await pumpWithProviders(
      tester,
      Consumer(builder: (context, ref, _) {
        container = ProviderScope.containerOf(context, listen: false);
        return const SizedBox(width: 400, height: 800, child: QueueSheet());
      }),
      overrides: [queueApiProvider.overrideWithValue(FakeQueueApi())],
    );
    await tester.pumpAndSettle();
    container!
        .read(queueControllerProvider.notifier)
        .add(const QueueTrack(id: 'x', title: 'Foo Bar', artist: 'The Artist'));
    await tester.pumpAndSettle();
    expect(find.text('Foo Bar'), findsOneWidget);
    expect(find.text('The Artist'), findsOneWidget);
  });
}
