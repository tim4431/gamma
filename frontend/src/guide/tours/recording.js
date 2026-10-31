import { T } from "../../shared/i18n/i18n.js";
// Offered when the first recording stops, its card in the notes. What is
// worth teaching is what the recording keeps: every stroke and note made
// while it ran carries its moment, so Play brings them back in order and a
// tap on one plays what was being said (docs/dev/recording.md). The player
// is on screen only while it plays, so its step is `optional`; the last
// card names Transcribe, in the card's menu.
export default {
  id: "recording",
  version: 1,
  title: T("Recording"),
  requires: { editable: true },
  trigger: { event: "recording.stopped" },
  offerAnchor: "recording.card",
  offerPlacement: "left",
  offer: { title: T("You recorded audio"), line: T("Play it back with your writing as it was made.") },
  steps: [
    { id: "rec-play", anchor: "recording.card", placement: "left", media: "record-replay",
      title: T("Play brings back what you wrote"),
      body: T("Notes and strokes return at the moment you made them."),
      advanceOn: { event: "recording.played" } },
    { id: "rec-tap", anchor: "recording.player", placement: "top", optional: true,
      title: T("Tap any stroke or note to hear that moment"),
      body: T("**Next writing** skips past the silence.") },
    { id: "rec-transcribe", anchor: "recording.card", placement: "left",
      title: T("**Transcribe**, in its menu, writes out the words"),
      body: T("Each paragraph plays from its moment."), next: T("Done") },
  ],
};
