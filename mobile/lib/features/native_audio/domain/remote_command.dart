/// Sealed union of remote transport commands issued by the OS
/// (iOS MPRemoteCommandCenter / Android MediaSession) via `audio_service`.
///
/// The [NativePlayer] surfaces these on `remoteCommandStream` so the player
/// controller can react (e.g. `NextCommand` -> `advanceToNext()` in the JS
/// equivalent).
sealed class RemoteCommand {
  const RemoteCommand();
}

final class PlayCommand extends RemoteCommand {
  const PlayCommand();
}

final class PauseCommand extends RemoteCommand {
  const PauseCommand();
}

final class NextCommand extends RemoteCommand {
  const NextCommand();
}

final class PreviousCommand extends RemoteCommand {
  const PreviousCommand();
}
