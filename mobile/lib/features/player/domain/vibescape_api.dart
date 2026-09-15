import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/player/domain/player_view_state.dart';

/// Slice of the Vibescape API surface the player uses. Agent A owns the full
/// client — this interface exists so the player controller can be tested
/// without pulling in the whole api layer.
abstract class VibescapeApi {
  /// Fetch the next recommended track for a given vibe (0-100).
  Future<Result<PlayerTrack>> nextTrackForVibe(int vibe);
}
