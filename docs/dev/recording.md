# Recordings: audio in step with the notes

Record a lecture or a meeting while reading and writing; afterwards, play
it back and the handwriting and notes made meanwhile appear as they were
written, and a tap on any of them plays the moment it was written. The
survey behind the design, and how Notability, Goodnotes and OneNote do
the same things, is in
[research/audio-recording.md](../research/audio-recording.md). Code:
`gamma/recording.py` + `gamma/routers/recordings.py` (server),
`frontend/src/recording/` (the recorder, the pill, the card, the player)
and `frontend/src/replay/` (the one replay store every replay runs on);
tests `backend/tests/test_recordings.py`, `frontend/tests/replay.test.mjs`,
`frontend/tests/recording.test.mjs`, e2e `tests/e2e/scenarios/recording.mjs`.

## What the user sees

- **Record** is the microphone button in the viewer's zoom column, after
  the pen (the PDF and the notebook viewer alike). It asks for the
  microphone once, then a **pill** at the bottom of the viewer shows a red
  dot, the time recorded, the input level, Pause, Bookmark and Stop. A
  **recording block** appears at the end of the page's notes at once:
  "Recording · 15 Oct 2026 10:03" with a mic marker and, under the title,
  a card saying "Recording… 4:12". The title is the block's content,
  renamed like any note. Leaving the page stops the recording.
- Writing while recording needs nothing more: every stroke already
  carries the moment it was written, and every block made in the app
  carries `t0`, the same wall-clock moment. **Bookmark** drops an empty
  note "★ 2:14" stamped a few seconds back (the lead-in), so a point of
  the lecture is marked without drawing a star in the margin.
- **Pause** closes the current audio file and **Resume** starts a new one;
  the recorder also rolls over to a new file every five minutes on its
  own. Each finished file uploads as it ends and the card's count grows
  ("3 parts"), so a crash loses at most five minutes, and what a closed
  or crashed tab left behind is uploaded on the app's next start and the
  recording marked done, silently.
- **Stop** finishes the recording; the card shows its length and date, a
  play button and a menu with **Transcribe** and **Download audio**. On
  another tab or device the card says "Recording on another device" while
  the recording tab holds the microphone, and the mic button there does
  nothing until it lets go.
- **Play** docks the **player** at the bottom of the viewer (over the
  notes when no viewer is shown): play/pause, ±10 s, the time, a scrubber
  with a mark wherever something was written and the joins between the
  audio files, *Previous writing* / *Next writing* (skips between the
  marks, past the silence), then one button each, opening a menu, for
  the three ways the strokes not yet reached are drawn — **Spotlight**
  (faded, the default), **Reveal** (hidden, the stroke in progress growing
  as it was written) and **Static** (as they are) — and for the speed (0.7
  to 2×), then **Follow** (the page and the notes scroll to what is being
  written) and Done. Every control is an icon button of one height and
  the scrubber takes the width that is left. Space, ← → and Escape work
  while no editor has the focus.
- During playback, a note written during the recording is **shaded**
  until its moment and shown plainly after; a text box on the page the
  same; a stroke faded, hidden or drawn whole by the mode. **A tap on a
  stroke, a text box or a stamped note plays from the lead-in before the
  moment it was written** (3 s by default, Settings → Reading & editing ›
  Recordings). Outside playback, hovering a stamped note shows a small
  play glyph in its margin that does the same. A note written *during*
  playback is stamped with the moment being heard, so review notes attach
  to it; strokes drawn then keep their real time and are not linked.
- **Transcribe** (the card's menu, or every recording when Settings →
  Reading & editing › Recordings › *Transcribe recordings* is on) runs a
  background job that writes the spoken words under the recording, one
  note per stretch of speech, each stamped with its moment: a transcript
  is searchable, exportable, shaded and tappable during playback, and
  readable by the AI like any note. It uses the OpenAI-protocol connection
  dictation uses, and asks for `whisper-1`'s timestamps.
- Constraints: the microphone needs a secure context (HTTPS, localhost,
  or the desktop app), so a server reached over plain HTTP on a LAN cannot
  record and the status pill says so; iPad Safari suspends capture when
  the tab is hidden or the screen locks, so recording there is a
  foreground activity. Read-only viewers and view shares can play; writers
  record.

## Model

A recording is a block with a `recording` property, among the page's notes
(no schema change beyond the kind rule):

| key | value |
|---|---|
| `recording.started` | wall-clock ms when the first file's audio began |
| `recording.segments[]` | `{url, started, dur, bytes, mime}` in order: the uploaded file (`/api/uploads/<hash>.webm` or `.m4a`), the wall-clock ms its audio began, its measured length in ms, its size, its container type |
| `recording.state` | `recording`, `paused` or `done`; while not done, `by` is the recording tab's id and `at` when it last wrote the block (its lease: stale after a segment's length plus a minute, `recording.js LEASE_STALE_MS`) |
| `recording.dur` | the segments' total |
| content | the title ("Recording · <date>" by default); children allowed, and a transcript's paragraphs are children with `transcript: true` and their `t0` |

`t0` on any block is the wall-clock ms it was made, stamped by the client
on every block it creates (`shared/model/blockModel.js makeNewBlock`,
`addHighlightAsBlock`, a text box's and a sheet's factories) from
`shared/model/wallClock.js` — the real time, or the moment being heard
while a recording plays. The server stores it like any property and the
kind rule ignores it. A stroke needs no stamp: its `t0` and per-sample
`t` are the same clock ([handwriting.md](handwriting.md) "The stroke
file"). The iPad app's own blocks carry no `t0` yet.

The kind: `json_type(properties, '$.recording') = 'object'` → `recording`,
after `sheet` in `db.BLOCK_HOT_COLUMNS` and `blocks_store.block_kind`
(migration step 38 rebuilds the generated column). The files are named in
the block's properties, so the orphan collector, the share scoping, the
mirror, the backup and the quota handle them with
no code of their own ([user_db.md](user_db.md) "Stored files").

Why wall-clock time and not an event stream: Notability and the upstream
fork record stroke and page events against the audio clock; here every
stroke already says when it was written, so a recording that remembers
when *it* began places everything by subtraction, including ink the iPad
or a second browser drew during it. The recording's own time is the
audio's: the segments laid end to end, pauses left out; a moment inside a
pause maps to the resume point.

## One clock for every replay (`frontend/src/replay/`)

A group's replay button, a sheet's and a recording's player all run on
one store, `replay/playback.js`: what is replaying (`scope`: `{group}`,
`{sheet}` or `{recording}`), on which clock, how far along (`t`), playing
or not, the rate, the display mode and the lead-in. A **clock**
(`replay/clock.js`) has its own time up to `duration` and maps it to the
strokes' wall clock and back (`toWall(t)`, `toT(wall)`):

- `compressedClock(ink)` is a group's or a sheet's own replay:
  `ink.js inkTimeline`'s compressed time, pauses shrunk to 400 ms and the
  whole scaled into 15 s. Its `times` table places each stroke's samples
  on it.
- `recordingClock(recording)` is the audio's time: `segments` with their
  `start` on it, `span` the wall-clock stretch covered, `segmentAt(t)` the
  file to play and the offset into it.

A **source** keeps the time: a stopwatch for a compressed clock, the
audio for a recording (`recording/audioSource.js`: two `<audio>` elements
take turns so the next segment is loaded before the join). The store ticks
on animation frames and publishes `t`; `seek`, `seekWall`, `seekBefore`
(the lead-in before a written moment), `setRate`, `setMode` and `stop(id)`
are the same calls for every clock. While a recording plays the store
sets the stamp clock (`wallClock.js`) to the moment heard.

Surfaces draw themselves *as of* the instant: `ink.js inkUntil(ink, at,
times)` is the drawing at an instant — the strokes begun by then in the
order begun, the one in progress cut to its samples so far, the ids not
yet reached — on a clock's table or, without one, the strokes' own wall
clock; `inkAtTime` is `inkUntil` on the compressed clock. React reads the
store through `replay/useFrame.js`: `useGroupFrame(groups, surface)`
gives a layer the state only while one of its groups (or its sheet) takes
part, or a recording's span crosses its handwriting (`inkWallSpan`);
`useBlockFrame(t0)` tells a row or a text box "pending" or "reached";
`useOwnReplay(id)` a button whether its own replay plays; `frameOf(state)`
hands `InkLayer`'s `ReplayStrokes` the instant, the table and the mode.
`writingTicks(clock, groups, blocks)` lists when something was written on
the clock, for the scrubber's marks, the skips and Follow.

So the layer is never replaced during a replay (NoteSheet used to swap in a
static SVG): it stays interactive, a tap can seek and the pen can write.
What a user sees of the group and sheet replays is unchanged.

## Client (`frontend/src/recording/`)

- `recording.js` (pure): the shape (`normalizeRecording`,
  `isRecordingBlock`, `heldElsewhere`, `recordingAt`, `extensionFor`), the
  segment length and the lease's staleness.
- `recorder.js`: a `MediaRecorder` over the stream at 48 kbps mono, no
  echo cancellation (a lecture, not a call), in segments: `start(1000)`
  delivers a chunk a second (`onChunk`, for the spool), a segment ends on
  a pause, a stop or the five-minute rollover (`onSegment` with the
  wall-clock start, the measured `dur` and the container type), a level
  meter from an `AudioContext` analyser. The container is the browser's
  (`pickMime`): Opus in WebM on Chrome, Edge and Firefox, AAC in MP4 on
  Safari, named `.webm` / `.m4a` by `extensionFor`.
- `webm.js` (pure): a WebM from `MediaRecorder` carries no length, so an
  `<audio>` element reports Infinity and seeks poorly; `withDuration`
  writes the measured length into the file's Info element before upload
  (EBML surgery as far as needed, the bytes left alone when the file is
  not what is expected).
- `spool.js`: IndexedDB, one row per chunk under the recording's block
  id and segment number, plus one per segment saying where it belongs;
  dropped once the segment is uploaded and the block knows it. Tolerates a
  browser without IndexedDB (chunks stay in memory).
- `recorderStore.js`: the recording in progress, module state with
  listeners like `playback.js`, so the pill re-renders on its own and App
  only hears of the coarse changes (`coarseNow`). `start(pageId)` asks for
  the microphone, inserts the block through App's `insertBlock` (the
  tree's own save path, like an ink group's first stroke) and runs the
  recorder; each finished segment goes up through `POST /api/upload-file`
  (reported in the tasks tray like any file) and the block's property is
  rewritten through `PUT /api/blocks/{id}` — a server-side writer, so the
  change fans out over the page socket and reaches every tab and device
  as a remote op. A failed upload stays spooled and retries with backoff;
  a 404 (the insert still queued) retries in two seconds. `pause`,
  `resume`, `stop` (the block marked `done` once the last segment is up),
  `bookmark`; `leftBehind` and `recover` for a crashed tab's segments
  (their length from decoding the file, else a second per chunk).
  `configure` takes App's `setStatus`, `insertBlock`, the lead-in and
  `onDone` (the automatic transcription).
- `RecordingPill.jsx`, `RecordingCard.jsx` (the row's card; reads the
  store for the live state, `useOwnReplay` for the playing one, and
  `RecordingContext` for `play` and `transcribe`), `PlayerBar.jsx` (the
  player: `useReplay`, the marks from `writingTicks`, Follow through App's
  `showTick`, the keys), `RecordingContext.js` (`{play, transcribe,
  leadIn, covers(t0), playAt(wall)}`, what the notes rows need), and
  `recording.css` (the pill, the card, the bar, the shading classes
  `replayPending` / `replayReached` / `inkPending`, the margin glyph).
- `app/App.jsx` owns the wiring: the lists `recordingBlocks` and
  `stampedBlocks` (kept while unchanged), `playRecording` (the audio
  source, the prefs, tools disarmed so taps seek), `toggleRecording`,
  `transcribeRecording` (`tasks.start("transcribe", {block_id, model,
  language})`), the recovery at start, the mic button, the pill and the
  bar in the viewer (or the `notesReplayDock` over the notes), and the
  context value. `BlockTree.jsx` renders the marker, the card, the shading
  and the glyph; `TextBoxLayer.jsx` the shading and the seeking click;
  `InkLayer.jsx` the faded or hidden strokes and the seeking tap.
- Preferences ([settings.md](settings.md)): per browser `replayMode`,
  `replayRate`, `replayFollow`; the account's `replayLeadIn` (seconds) and
  `recordingTranscribe` (Settings → Reading & editing › Recordings).

## Server

- `storage.py`: `.webm` (`audio/webm`), `.m4a` (`audio/mp4`), `.ogg` /
  `.opus` (`audio/ogg`) in `FILE_MEDIA_TYPES`; the audio extensions are
  served inline (`INLINE_EXTENSIONS`) so an `<audio>` element plays them,
  with the usual Range support and cache headers; `POST /api/upload-file`
  checks a recorder's container by its magic bytes (`is_audio`) as it
  checks `.ink` and `.pdf`.
- `gamma/recording.py`: `is_recording`, `recording_label` (what the
  agent's `read_block` says of the block), `fmt_duration`,
  `transcript_paragraphs` (segments joined on sentence ends to about 400
  characters), `mp4_duration_ms` (the length an `.m4a` states in its movie
  header, read from box headers only), and the transcription job.
- The Notability import ([import_export.md](import_export.md#notability-notes))
  makes a recording block per recording a note holds: one `audio/mp4`
  segment streamed into storage, `dur` from `mp4_duration_ms`, `started`
  when Notability made it, `state: "done"`. No stroke is placed on it:
  Notability times a stroke drawn during a recording as an event (seconds
  into it, and a duration), never its samples, and a stroke's place in a
  replay comes only from its own `t0` and `t`, which are never invented.
- `POST /api/jobs/transcribe` (`routers/recordings.py`, body `{block_id,
  model?, language?}`): a job per recording (`key` = the block id,
  [tasks.md](tasks.md)) that reads each segment's file from storage (25 MB
  at most per request), sends it to the connection `/api/ai/transcribe`
  would pick (`transcription_request(…, timestamps=True)`,
  `transcript_segments` on the protocol — `whisper-1`'s `verbose_json`
  segments, one segment without timestamps otherwise), and writes the
  paragraphs as the block's children in one batch (`client="transcribe"`),
  replacing the children a previous run left (`transcript: true`). Each
  child's `t0` is its segment's `started` plus the paragraph's offset into
  it. Not metered, like dictation.
- Exports ([import_export.md](import_export.md)): the Markdown export
  writes a "Recording · 12:34" line with one link per audio file, so the
  files are bundled into `assets/`; the Obsidian vault an embed per file;
  the notes PDF a muted line (the audio is not in a PDF); the annotated
  PDF ignores recordings. The Gamma backup needs nothing: it copies every
  referenced upload.

## Not built yet

Trim, split and merge (trim as `in` / `out` offsets on a segment would
need no re-encoding), importing an audio file as a recording (the
Notability import brings a note's own), strokes placed on a recording the
Notability import brought, transcribing a segment over 25 MB (an imported
lecture can be one), live
transcription, per-word reveal of a note's text (a block is shaded whole),
strokes drawn during playback linked to the moment heard, the iPad app's
native capture, a record button on a page without a viewer, re-encoding
for a browser that cannot play the other's container (Opus in WebM on an
old Safari, AAC on a codec-less Chromium), and the tours.
