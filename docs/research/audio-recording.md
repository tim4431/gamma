# Audio recording synced with notes

Survey date: 2026-10-14. What Notability's audio recording does and how
it keeps the recording and the notes in step, what Goodnotes, OneNote,
Samsung Notes, Noted, Apple Notes and the upstream fork built around the
same idea, what this repository already has that a recording would
drive, how the existing handwriting replay and an audio replay become
one clock, and the design recommended. Findings
and a plan; nothing here is built. The timed replay the design extends is
described in [dev/handwriting.md](../dev/handwriting.md) "Replay".

## What Notability does

Sources: the help centre's [Recording and Playing Audio][n-rec],
[Importing Audio][n-import] and [Audio Transcripts][n-transcripts], the
blog's [How We Built Live Transcription][n-live] and [Become an Audio
Pro][n-pro], and two university guides ([Calgary][n-calgary],
[Colorado College][n-cc]). The feature has been in the app since 2010;
transcripts came in 14.4 (2023), live transcripts and the Learn tools in
15.0.

### Recording

- One **record button** in the toolbox. Pressed, an input meter and a
  timer appear in the toolbar; pressed again, the recording stops. From
  the library the same button makes a new note and starts recording at
  once. There is no pause button in the current help: a stop and a new
  recording take its place, and a note holds **any number of
  recordings**, listed in the player's menu.
- **Every edit is stamped against the recording clock.** The engineering
  post says it plainly: "every note edit and ink stroke gets a timestamp
  when created." That covers handwriting, typed text (per word), photos
  and stickers. An older university guide says only typed text synced;
  that describes the app of a decade ago.
- Recordings **split into one-hour segments**; the help recommends a
  fresh recording after an hour. The iOS app encodes with AVFoundation and
  keeps "only a few seconds in memory at a time", uploading as it goes
  when transcription is on.
- Recording keeps going while the user moves between pages of the note
  and, with the system's background audio permission, while the app is
  in the background (the lock screen shows the recording). It does not
  survive leaving the note.
- **Imported audio** (MP3, MP4, M4A, a Voice Memo shared into the app)
  joins the note as a recording. Anything written while it plays is
  stamped against its clock, so a student can play a lecture recording
  from another device and take synced notes afterwards.

### Playback

- **Play** opens the player bar and puts the note in **playback mode**:
  the ink that existed before the current recording time is drawn, the
  rest is hidden (or faded: "Note Replay" is a switch; off, the note
  stays static and only the tap-to-seek works). Typed text written during
  the recording is shaded as the audio reaches it. Playback follows the
  writing across pages of the note.
- **Tap to seek.** With the hand tool, a tap on "a word, sketch, or
  photo" plays the audio from the moment that thing was written. The
  blog's bookmark tip depends on it: draw a star in the margin, tap the
  star later.
- The player bar: a scrubber you drag (dragging away from the bar slows
  the scrub for fine control), ±10 s, speeds 0.7×, 1×, 1.25×, 1.5×, 2×.
- Anything written **during playback is stamped too**, so a review pass
  adds synced annotations.
- The player's menu lists the note's recordings: rename, delete, **merge**
  several into one (not imported audio), **trim** by dragging the ends,
  **split** at the playhead and name the pieces. **Tuning** adjusts the
  equaliser and **Voice Boost** amplifies a distant speaker.

### Transcripts

- Recordings are transcribed on Notability's servers (OpenAI's Whisper,
  audio resampled to 16 kHz; the smallest model that serves most users).
  Standard transcription runs in the background once the device is
  online and can be switched off in Settings → AI Customization. Plus
  subscribers get unlimited transcripts; others one free hour.
- The transcript is **time-stamped per segment** and lives in the note's
  Summary panel. Tapping a transcript paragraph seeks the audio, and the
  note's playback mode then shows what was being written at that moment;
  tapping handwriting shows what was being said. Transcript text is
  searchable from the library and copyable into the note. It is not
  editable in place.
- **Live transcription** (Pro) splits the stream into 3 to 15 s segments
  that overlap the previous one by a small window, transcribes each within
  a few hundred milliseconds, and keeps a "verified up to here" chunk
  index: audio after it is re-sliced with what has arrived since and sent
  again, so the tail self-corrects. Sticky sessions keep one user's audio
  on one server; diarization and timestamps for the live mode were
  "coming soon".
- **Smart Notes** summarise a recording; Learn makes flashcards, quizzes
  and a chat over recordings, handwriting and PDFs (Pro, 15.0).

### Sync, storage, export

- Recordings sync between devices through Notability Cloud. Backups to
  Dropbox, Google Drive, OneDrive, Box and WebDAV offer "PDF +
  Recording" (a zip with the PDF and a folder of `.m4a` files) and
  "RTF + Recording"; a plain PDF export drops the audio. Sharing a note
  in Notability's own format keeps the recordings and their sync.

## Goodnotes and the upstream fork

- **Goodnotes** ([help][g-audio], [feature page][g-feature]) records from
  a toolbar button, stamps handwriting written during the recording, and
  replays it in sync with the audio: "Replay" animates the strokes,
  "Static" leaves the page as it is, and a 2026 "Spotlight" mode fades the
  strokes not yet reached. Tapping a handwritten word seeks the audio.
  Replay works only in Notebook documents on iOS and macOS; transcription
  runs on the device with a proprietary model, and "Record and Summarize"
  writes a summary beside the transcript.
- **The upstream fork** (`amogadget/Gamma`, surveyed in
  [upstream-features.md](upstream-features.md) items 3 and 4) built the
  same shape on its iPad app: `AVAudioRecorder` writes AAC segments that
  roll over every five minutes and are finalised on pause, each uploaded
  as a private asset and listed in order in one `audio` block under the
  PDF page, with recovery metadata persisted before capture; a separate
  `replay_events` stream of stroke and page events against the audio
  clock drives a browser player that seeks across its segments, jumps
  pdf.js to the recorded page and reveals the final surviving strokes
  through per-stroke PNG masks exported from PencilKit. A click on a
  timed stroke seeks two seconds before it. Its own list of limits is
  instructive: erasures and undo are not replayed, text blocks keep only
  their final text, an in-progress stroke's reveal is approximate, old
  audio without events cannot be synchronised after the fact, and some
  Linux Chromium builds cannot decode AAC.

### Designs worth borrowing from elsewhere

- **Goodnotes' three display modes** ([help][g-audio]): *Spotlight* (the
  default: strokes ahead of the playhead faded, those played fully
  visible), *Real-time reveal* (strokes appear as they were written) and
  *Static* (nothing changes, only tap-to-seek works). One switch on the
  player, remembered per browser. Goodnotes links only the notes made on
  the page where the recording started; a whole-page clock does better.
- **OneNote's margin play glyph** ([support][o-rec]): hovering a
  paragraph written during a recording shows a small play button to its
  left, and clicking it plays from a few seconds before the paragraph
  (five by default, a setting). "See Playback" moves the cursor through
  the paragraphs as the audio reaches them. The glyph is how a reader
  discovers that notes are linked without entering playback mode, and the
  lead-in setting is the right answer to "how far back".
- **Samsung Notes** ([support][s-rec]) greys typed words and highlights
  each as the audio reaches the moment it was typed, and seeks when
  handwriting is selected: the same per-word shading as Notability.
- **Noted's margin timestamps and #TimeTag** ([Noted][noted]): a time
  stamp runs down the note's left margin as you type; a tag typed in the
  text is a tappable bookmark, and a *Time Offset* backdates it because a
  key point has passed before the hand moves. Notability's users draw a
  star for the same purpose; Samsung calls it an Audio Bookmark. A
  *Bookmark* button on the recording pill that drops a stamped, empty
  note ("★ 12:34") makes the trick a feature, and the offset applies to
  it too.
- **Apple Notes** ([support][a-rec]) keeps the transcript inside the
  note, tappable to play from that point, with a live transcript while
  recording. The transcript-as-child-blocks design below is the same
  shape.
- **Writing marks on the scrubber**, Gamma's own: the recording's
  timeline knows when every stroke and stamped block was written, so the
  scrubber can show ticks where writing happened and *Next* / *Previous
  writing* buttons can skip the silence between them. No surveyed app
  has it; Glean-style audio note-takers chunk the audio by pauses for a
  similar reason.

The behaviour to copy is Notability's; the storage to copy is neither.
Upstream's per-stroke rasters exist because PencilKit's drawing is opaque.
This repository's ink is a vector file whose samples already carry time,
so the audio can drive the replay that exists, and the recording needs
only one new kind of block and one clock.

## What Gamma already has

Source audit, 2026-10-14, of the pieces a recording would use. Mechanics
are in the dev docs named; this lists only what matters to the design.

- **Every stroke is timed, in wall-clock time.** A stroke's `t0` is
  `Date.now()` at pointer-down (`ink/InkLayer.jsx`), and each sample's `t`
  is its offset from there (`ink/inkInput.js`); the file keeps both
  (`ch: "xypt"`, [dev/handwriting.md](../dev/handwriting.md) "The stroke
  file"). The iPad writes the same, with an integer clock. Only ink
  imported from a foreign PDF's `/Ink` has no time. Moves, restyles and
  transforms keep a stroke's id and its timing; the partial eraser's
  pieces keep `t0` and their samples' `t`; a duplicate copies the timing.
  So the question "when was this stroke written" is already answered to
  the millisecond, by a clock the recording can share. The
  [handwriting research](handwriting.md) kept the per-sample offsets for
  exactly this ("what makes audio-synchronised replay possible later").
- **A replay exists, without audio.** `ink.js inkTimeline` orders a
  group's strokes by `t0 + t`, cuts pauses to 400 ms and compresses the
  whole thing to 15 s; `inkAtTime` slices a stroke's delta-coded `pts` to
  its first samples, so a half-written stroke is a prefix; `inkReplay.js`
  plays one replay on animation frames and `InkLayer.jsx useReplayOf`
  re-renders only the layer or card showing it. The audio-driven replay
  needs the same two functions over real time instead of compressed time.
- **Blocks carry no client time.** `created_at` / `updated_at` are the
  server's clock, set once per batch (`ops.py`) and never read by the
  notes tree. A note typed during a recording has no stamp the stroke
  clock could be compared with; the collab path debounces typing by
  350 ms and makes no per-keystroke record.
- **A block's kind is derived from its properties**, not a column:
  `unified_blocks.kind` is a generated SQL `CASE` (`db.py`) mirrored by
  `blocks_store.block_kind`, in the order page/folder/label, `ink_url` →
  ink, `text_box` → text box, `sheet` → sheet, link, `pdf_position` →
  highlight, else note. A new kind is one more `WHEN` in both, plus the
  row branch in `editor/BlockTree.jsx` ([dev/user_db.md](../dev/user_db.md)).
- **Stored files are content-addressed and found by regex.** Anything a
  block names as `/api/uploads/<hash>.<ext>` in its content or properties
  is kept alive by the orphan collector, copied by the mirror and the
  iPad's replica, bundled by the Gamma backup, readable through a share
  link, and metered against the quota (`storage.UPLOAD_REF_RE`,
  [dev/user_db.md](../dev/user_db.md) "Stored files"). `POST
  /api/upload-file` takes any non-executable file (50 MB per file by
  default, the whole body in memory), and `GET /api/uploads/<name>`
  answers Range requests (Starlette's `FileResponse`; a 206 is tested) with
  a month's immutable cache. Audio is half-known: `.mp3` and `.wav` have
  media types, `.webm`, `.m4a`, `.ogg` and `.opus` are served as
  `application/octet-stream`, and no audio type is in
  `INLINE_EXTENSIONS`, so every audio file downloads as an attachment.
- **The browser already records.** The chat composer's dictation
  (`chat/ChatDock.jsx`) asks `getUserMedia({audio: true})`, picks
  `audio/webm;codecs=opus`, `audio/webm` or `audio/mp4` by
  `MediaRecorder.isTypeSupported`, draws a live waveform from an
  `AudioContext` analyser, and tells the user the microphone needs HTTPS
  or localhost. `POST /api/ai/transcribe` sends the blob to an
  OpenAI-protocol connection (`gpt-4o-transcribe`, falling back to
  `whisper-1`, 25 MB cap) and returns plain text, no timestamps
  ([dev/ai.md](../dev/ai.md)). The problem reporter records the screen the
  same way. No `<audio>` element exists anywhere in the client, no header
  restricts the microphone, and the iPad app declares no microphone usage
  string.
- **Long work has a home.** A server job (`gamma/jobs.py`) runs in a
  worker thread, reports progress, survives the tab, and shows in the
  Background tasks tray; the metadata batch is the precedent for a job
  that writes into pages through `apply_ops` ([dev/tasks.md](../dev/tasks.md)
  "Adding a kind"). The tray also lists a tab's own work, such as an
  upload in flight.
- **Exports branch per kind.** The Markdown export bundles any upload a
  renderer writes out, the notes PDF and the Obsidian vault branch on
  link / highlight / ink / text box, and the annotated PDF collects marks,
  ink and text boxes ([dev/import_export.md](../dev/import_export.md)). A
  kind none of them know falls through as a note with its caption.

## Design

### The one idea: the recording shares the strokes' clock

Notability and the upstream fork both record a second stream of events
(stroke began, page turned) against the audio clock. Gamma does not need
one. Every stroke already says when it was written in wall-clock
milliseconds, so a recording that remembers when *it* started places
every stroke on its timeline by subtraction, and a stroke's start maps
back to an audio position the same way. Nothing per stroke is stored,
nothing is backfilled, and ink the iPad or a second browser drew during
the recording is synchronised too, to within the devices' clock skew. The
same stamp, added to the blocks typed during a recording, brings notes
and text boxes along.

The recording's time is the audio's own: the concatenated length of its
segments, pauses left out, as upstream and Notability do. A wall-clock
instant inside a pause maps to the moment the recording resumed.

### One clock for every replay

Today's replay is one store (`ink/inkReplay.js`) holding **one id and
one precomputed frame**: `inkTimeline` turns a drawing into per-sample
replay times on a compressed clock (pauses cut to 400 ms, the whole
capped at 15 s), `inkAtTime` slices the drawing to that clock, and the
store ticks on animation frames and publishes `{id, frame}`. A layer or
card asks `useReplayOf(ids)` whether the frame is one of its own.
NoteSheet's replay goes further: `MarkupLayers` **replaces** the
interactive layer with a static `<Strokes>` SVG of the frame while it
plays. Three things in that shape fight an audio replay: the frame is
one drawing (a recording spans every group on every surface, plus notes
and text boxes); the clock is synthetic and unseekable (audio is real
time and scrubbed); and the layer swap removes the thing a tap must hit.

The reconciliation is to make the replay a **clock that publishes a
wall-clock instant**, and to make every surface draw itself *as of* that
instant. The compressed group replay and the audio replay then differ
only in where the instant comes from:

- `replay/clock.js` (pure): a clock is `{duration, toWall(t), toT(wall)}`,
  a monotone map between its own time and the strokes' wall clock.
  `inkTimeline` already computes the breakpoints of the compressed
  clock (each sample's replay time beside its wall time); it returns
  them as a clock instead of as frames, with its synthetic placement of
  untimed strokes kept. A recording's clock is the segment table from
  the block (the `toWall` / `toPlayhead` pair described under Playback).
  A sheet's replay is the compressed clock over the union of its groups,
  as today.
- `ink.js inkUntil(ink, wall, starts?)`: the drawing as of a wall-clock
  instant: strokes begun by then, the one in progress sliced to its
  samples so far (the prefix slice `inkAtTime` does), and the ids not yet
  reached; `starts` is the clock's optional table of synthetic starts for
  untimed strokes. `inkAtTime(ink, timeline, t)` becomes
  `inkUntil(ink, clock.toWall(t), clock.starts)` and keeps its tests.
- `replay/playback.js` replaces `inkReplay.js`: one store with
  `{clock, scope, wall, playing, rate}`, where `scope` names what takes
  part (`{group: id}`, `{sheet: id}`, or `{recording: id, from, to}`,
  a wall span) and `wall` is the instant it publishes on animation frames:
  `performance.now()` through the compressed clock, or the current
  `<audio>` element's `currentTime` through the recording's. `play`,
  `pause`, `seekWall(wall)`, `seekT(t)` and `rate` are the same calls
  for every clock, so the group replay gains a scrubber and a seek for
  free, and the audio player is the same bar with a longer duration.
- `replay/useFrame.js` replaces `useReplayOf`: a layer, a card, a sheet,
  a notes row or a text box asks for the instant **if it is in scope**: a
  group or sheet replay reaches only the surfaces holding that id; a
  recording reaches every timed group whose span crosses `[from, to]`
  and every block with a `t0` in it. Everything else re-renders for
  nothing, as today. The consumer computes its own `inkUntil` from the
  instant, memoized per `(ink, wall)`, instead of receiving a frame: the
  store stops knowing about drawings at all.
- `InkLayer` takes a `frame` (the instant and the display mode) and draws
  in place: reached strokes as they are, the one in progress sliced,
  unreached ones faded (Spotlight), hidden (Real-time reveal) or
  untouched (Static); strokes without timing or outside the span stay
  untouched. `MarkupLayers` loses its `replay` prop and the `<Strokes>`
  swap; NoteSheet's replay becomes a sheet scope on the one store; the
  group card draws from the same instant. The layer stays interactive,
  so a tap during an audio replay can seek and the pen can still write.

What this changes about the existing replay: nothing a user sees. Its
clock stays compressed and capped, its button stays on the card and the
sheet, and the pinned tests keep passing through the wrapper. What it
removes: a store that holds frames, the layer swap, and one of two ways
to draw a replay. What it adds for the recording: a scope, a second kind
of clock, and the fade. The module moves from `ink/` to `replay/` because
notes rows and text boxes read it too, and `ink/` is imported by
`markup/` but never the other way; `replay/` sits under both.

### The recording block

A new kind, `recording`: a block among the page's notes, placed where the
notes' caret stood when recording began (or last among the children of
the page), holding one property and a caption:

| key | value |
|---|---|
| `recording.started` | wall-clock ms when the first segment's audio began (`MediaRecorder` `onstart`), the clock `t0` is read from |
| `recording.segments[]` | `{url, started, dur, bytes, mime}` in order: the file (`/api/uploads/<hash>.webm` or `.m4a`), the wall-clock ms its audio began, its measured duration in ms, its size and container type |
| `recording.state` | `recording`, `paused` or `done`; the first two only while a tab holds the microphone, with `recording.by` naming the tab (lease, below) |
| `recording.dur` | the total, the sum of `dur`, kept for the card |
| content | the title, "Recording · 14 Oct 2026 10:03" by default, editable like an ink group's caption; children allowed, and the transcript lives there |

A recording's files are named in its properties, so the orphan collector,
the share scoping, the mirror, the backup and the quota all handle them
with no new code. The SQL kind rule gets a `WHEN` on
`json_type(properties, '$.recording') = 'object'` before the note
fallback, and `block_kind` the same. It is a block, so it syncs over the
page socket, merges by last writer per key, exports, and can be read by
the agent's `read_block` ("recording, 12:34, 3 segments" before the
caption).

Several recordings per page are several blocks. They never overlap in
time on one device, so a wall-clock instant belongs to at most one of
them; a stroke is looked up in the recording whose span contains it.

### Stamping what is written during a recording

Strokes need nothing. For the rest, the client adds `t0` (the stroke's
name for the same thing: wall-clock ms of the first keystroke or tap) to
the properties of **every block it creates**: a note block, a text box, a
highlight, a sheet. Always, not only while recording: the server's
`created_at` is another clock (the server's, set once per batch) and
cannot be compared with a stroke's `t0`, the stamp costs thirteen bytes,
and with it a page has one chronology of everything written on it. That
chronology is what lets a recording imported later line up with notes
typed before it existed, and what a replay of the whole page's writing
without any audio would read (the same clock, a compressed map over the
page's span). During playback the stamp is the recording's virtual
instant instead (below). The server does not validate `t0` beyond its
being an integer; the kind rule ignores it. Granularity is the block, not the word: a paragraph is
shaded when the recording reaches the moment it was begun. Per-word
reveal is a follow-up (an optional `t_edits: [[ms, length], …]` the
client appends on each content change while recording, capped), not a
v1 need.

### Capturing audio

`recording/recorder.js`, one object per tab:

- `getUserMedia({audio: {echoCancellation: false, noiseSuppression:
  true, autoGainControl: true}})` — a lecture, not a call — and a
  `MediaRecorder` at 48 kbps (`audioBitsPerSecond`), mono. The container is
  what the browser gives: Opus in WebM on Chrome, Edge and Firefox,
  AAC in MP4 on Safari (and WebM Opus on Safari 18.4+ when
  `isTypeSupported` says so). Read `recorder.mimeType` and name the file
  `.webm` or `.m4a` from it, as dictation does.
- **Segments.** Every five minutes (upstream's figure; one hour of
  lecture is twelve files of under 2 MB) the recorder is stopped and a
  new one started on the same stream, so each segment is a standalone
  playable file with its own header, and a crash loses at most five
  minutes. The gap between stop and start is a few ms and lands in the
  next segment's `started`. Pause stops the recorder and finalises the
  segment; resume starts a new one.
- **Durability.** `start(1000)` delivers a chunk a second; chunks go to
  IndexedDB (`gamma-recording`, keyed by recording id, segment index,
  chunk index) as they arrive and are deleted once the segment is
  uploaded and the block updated. On any later app start, un-uploaded
  chunks make a *Recover recording* notice on the status pill: the chunks
  of a segment concatenate into a playable file (the first chunk carries
  the header), and the segment's `dur` comes from decoding it once in an
  `<audio>` element. A WebM from `MediaRecorder` lacks a duration header
  and seek cues; the client patches the EBML `Duration` before upload
  (a small library or forty lines of our own) and the player never
  trusts `audio.duration`, only `dur`. Five-minute files seek acceptably
  without cues.
- **Upload.** A finalised segment goes up through `POST /api/upload-file`
  as "this tab's own work" in the tasks tray, retried like `flushInk`
  on failure; success PATCHes the block with the segment appended
  (`PUT /api/blocks/{id}`), so the recording grows in every tab and
  device as it is made. The server adds `.webm` → `audio/webm`, `.m4a` →
  `audio/mp4`, `.ogg`/`.opus` → `audio/ogg` to `FILE_MEDIA_TYPES`, puts the
  audio types in `INLINE_EXTENSIONS` so an `<audio>` element may play
  them, and checks the container's magic bytes (EBML `1A 45 DF A3`,
  `ftyp`) on `.webm`/`.m4a` uploads the way `.ink` is validated, so the
  route stores only what it claims.
- **The pill** while recording: timer, a level meter from the same
  analyser dictation uses, pause, stop, and *Bookmark*, which inserts an
  empty stamped note ("★ 12:34") at the notes' caret with the lead-in
  subtracted (Noted's Time Offset), so a star drawn in the margin is no
  longer the way to mark a moment.
- **The lease.** The block's `state: "recording"` with `by: <tab id>` and
  a heartbeat `at` tells other tabs and devices to show "Recording on
  another device" and not start a second one on the page; a lease older
  than a minute is stale and may be taken. One tab records one page at a
  time.
- **Constraints to say out loud.** The microphone needs a secure
  context: HTTPS, `localhost`, or the desktop app (it loads `127.0.0.1`).
  A self-hosted server reached over plain HTTP on a LAN cannot record;
  the status pill already says so for dictation and says the same here.
  iPad Safari suspends capture when the tab is hidden or the screen
  locks; recording there is a foreground activity until the native app
  records with `AVAudioRecorder` (not in this design; it would write the
  same block and files). Chrome keeps recording in a background tab.

### Playback

`recording/RecordingCard.jsx` is the block's row in the notes: title,
date, duration, a play button and ⋮⋮ with Rename, Download, Transcribe
and Delete. Play opens the **player bar** over the viewer's top edge
where the markup strip docks (and over the notes when there is no
viewer), and puts the page in **playback mode**:

- Controls: play/pause, a scrubber over the total duration with the
  segment joins marked, −10 s / +10 s, speed (0.7, 1, 1.25, 1.5, 2 ×
  through `playbackRate`, remembered per browser), the page's other
  recordings in a menu, Done. Keys while it is open: space, ←/→ for 10 s,
  Esc. Two `<audio>` elements alternate so the next segment is preloaded
  and the join is seamless; a seek picks the segment by cumulative
  duration and sets `currentTime` within it.
- **The clock.** `recording/recordingClock.js` (pure, tested): `toWall(rec,
  playheadMs)` and `toPlayhead(rec, wallMs)`, the two mappings over the
  segments, the second returning the next segment's start for an instant
  in a pause and `null` outside the recording. It is the recording's
  clock for the one playback store ("One clock for every replay"), which
  publishes the current wall-clock instant on animation frames.
- **Ink follows** through `inkUntil` and the layer's `frame`, under the
  display mode the player shows: Spotlight by default (unreached strokes
  at 15 % opacity; faded rather than hidden because a PDF's other
  annotations should not vanish), Real-time reveal, or Static. Untimed
  strokes and strokes outside the recording's span stay as they are.
  `InkCard` draws the same instant in the notes.
- **Notes follow.** A row whose block has `t0` is shaded while the
  playhead has not reached it and shown plainly after, like Notability's
  and Samsung's typed text; text boxes likewise on the page. Blocks
  without `t0` are left alone. Outside playback mode a stamped row shows
  OneNote's margin glyph on hover (a small play button with the time),
  which starts playback from that moment.
- **The scrubber** carries ticks where writing happened, and *Previous* /
  *Next writing* buttons jump between them, skipping the silence.
- **The view follows the writing.** When the stroke or block being
  written is off-screen, the viewer scrolls to its page and the notes to
  its row, as the replay button does today, once per block so a reader's
  own scroll is not fought; a *Follow* toggle on the bar turns it off.
- **Tap to seek.** In playback mode a tap on ink resolves the stroke
  (`nearestInkStroke`) and seeks to `toPlayhead(stroke start)` less the
  **lead-in**, a preference (3 s by default; upstream chose 2, OneNote
  5); a click on a shaded row, a text box or a
  highlight with `t0` seeks the same way; the ink and text tools are
  disarmed so the tap is not a stroke. Outside playback mode a tap on ink
  does what it does now.
- **Writing during playback** stamps new blocks with the recording's
  virtual instant (`toWall(rec, playhead)`), so review notes attach to the
  moment being heard, as in Notability. Strokes are the exception: their
  `t0` is written by `InkLayer` and means "when it was really drawn", and
  the replay's own pacing depends on it. Leave strokes drawn during
  playback unlinked in v1 and decide later whether a `t0` of the
  recording's instant is acceptable (it keeps pacing within the stroke,
  only moves it in time).
- Read-only viewers and view shares play; writers also record. The
  recording's files reach a share visitor because the block names them.

### Transcription

A server job, `transcribe`, started from the card's menu or, when the
account's **Transcribe recordings** preference is on, by the client when
a recording ends:

- Per segment, under the 25 MB cap: the job reads the file from storage
  and calls the account's OpenAI-protocol connection as `/ai/transcribe`
  does, asking `whisper-1` for `verbose_json` with segment timestamps
  (`gpt-4o-transcribe` returns no timestamps; the diarizing variant does,
  and the protocol can prefer it when the catalog lists it). The
  protocol gains `transcription_segments(data) → [{start, end, text}]`
  beside `transcript`.
- The result is **child blocks of the recording**, one per paragraph
  (segments joined on sentence ends up to ~400 characters), each with
  `t0 = started + start` on the recording's clock and `transcript: true`
  so a re-run can replace them. A transcript is then searchable by the
  existing index, exportable as text, shaded and clickable in playback
  mode like any stamped block, and readable by the agent as part of the
  page; "Summarize this recording" is the chat with the block attached,
  no new tool. Notability's Summary panel is the recording's own card
  expanded.
- The job reports `phase: "transcribing"`, `unit: "segments"`, and takes a
  `key` of the block id so one runs per recording. It is not metered
  (dictation's rule) but checked against a shared entry's allowance.
- Live transcription is not in the design. Notability's method (3 to 15 s
  overlapping slices, a verified-up-to index, re-slicing the tail) is
  recorded above for when it is.

### Export, import and the rest

- Markdown export: a recording renders as a bold "Recording · 12:34"
  line with one link per segment to `assets/<hash>.webm` and the
  transcript children as text, so `collect_and_rewrite` bundles the files; the Obsidian vault
  writes `![[<hash>.webm]]` embeds; the notes PDF a line with title,
  duration and the transcript; the annotated PDF ignores it, as
  Notability's plain PDF does. The Gamma backup needs nothing.
- Import: a dropped audio file (`.mp3`, `.m4a`, `.webm`, `.ogg`) on a page
  becomes a recording block with one segment, `started` at the drop and
  `dur` from decoding it once; what is written while it plays stamps to
  its virtual clock, which is Notability's imported-audio rule for free.
  A `.zip` of PDF + recordings from Notability is not worth a reader.
- Trim is metadata: `in`/`out` offsets on a segment the player honours;
  no re-encoding. Split and merge need re-encoding or container surgery
  and are left out, with the recordings list standing in.
- Deleting the block deletes the recording; the files go when the orphan
  collector's 30 days pass, so undo brings it back whole.

### Work list

1. **Server, small:** audio media types, inline serving and magic-byte
   checks in `storage.py` / `routers/uploads.py`; the `recording` kind in
   `db.py` and `blocks_store.block_kind` (a migration step rebuilds the
   generated column); `read_block`'s label; tests in `test_server_limits`
   and `test_blocks`.
2. **Client, the one clock:** `replay/clock.js`, `replay/playback.js`
   and `replay/useFrame.js` in place of `ink/inkReplay.js` and
   `useReplayOf`; `inkUntil` in `ink.js` with `inkAtTime` as its wrapper
   (the replay's tests pinned); `InkLayer`'s `frame` and display modes;
   `MarkupLayers` without the `replay` swap; NoteSheet and InkCard on the
   store. Behaviour-preserving for the group and sheet replays, checked
   by the existing `ink.test.mjs` and `notebooks.mjs` scenarios.
3. **Client, the recording:** `recording/recorder.js` (segments,
   IndexedDB spool, upload, recovery), `recordingClock.js` (pure, with
   `tests/shared/recording-clock.json` so the iPad can share the cases),
   the mic button in the viewer's zoom column beside the pen and on a
   sheet's tool row, the recording pill (timer, level), the lease, the
   `t0` stamp on new blocks, `RecordingCard`. Node tests for the clock
   and the chunk assembly; an e2e scenario with a fake `MediaRecorder`
   and a fixture `.webm`.
4. **Client, playback:** the recording clock on the store, the player
   bar (modes, scrubber ticks, lead-in, writing skips), `BlockTree` /
   `TextBoxLayer` shading and the margin glyph, tap-to-seek, follow.
   E2e: seek by tapping a stroke, shading of a stamped row, the join
   between two segments.
5. **Transcription job:** the protocol hook, `jobs` kind, the child
   blocks, the preference and the card's menu; `TASK_KINDS` row.
6. **Exports and docs:** the four renderers, `dev/recording.md` (new
   topic doc; the replay section of `dev/handwriting.md` moves its store
   description there and points to it), the user guide, i18n catalogs.

Stage 2 is a refactor that ships on its own with no visible change;
1 to 4 are the feature; 5 and 6 can follow in their own changes.

### Decisions this leaves to take

- **The default display mode.** Spotlight (faded) is recommended, as
  Goodnotes chose; Notability's hide is the Real-time reveal mode.
- **Stamp `t0` on every new block**, as recommended above, or only while
  a recording runs. The first draft of this note recommended the second
  so that the property meant "written to this recording"; the one-clock
  design makes the first the better choice, and the recording's span
  says which blocks belong to it.
- **Strokes drawn during playback:** unlinked (recommended for v1) or
  stamped with the recording's instant.
- **Where the record button lives:** the zoom column beside the pen
  (recommended: it is where the page's tools are) or the topbar.
- **Codec normalisation.** Nothing in the design re-encodes. If Opus in
  WebM must play on an old Safari, or AAC on a codec-less Chromium, the
  answer is a server job with `ffmpeg` present or a WebCodecs transcode
  in the browser; both are deferred until a real device shows the gap.
- **The iPad's native capture** (`AVAudioRecorder`, the same block, the
  replica pushing the segments) when the Pencil app should record.

[n-rec]: https://support.gingerlabs.com/hc/en-us/articles/206060617-Recording-and-Playing-Audio
[n-import]: https://support.gingerlabs.com/hc/en-us/articles/360044640052-Importing-Audio
[n-transcripts]: https://support.gingerlabs.com/hc/en-us/articles/6059035461146-Audio-Transcripts
[n-live]: https://blog.notability.com/post/how-we-built-live-transcription
[n-pro]: https://blog.notability.com/post/become-an-audio-pro-how-to-record-lectures-in-notability
[n-calgary]: https://ucalgary.ca/student-services/access/current-students/resources-and-supports/assistive-software-how-videos/ipad-audio
[n-cc]: https://www.coloradocollege.edu/offices/accessibilityresources/images/quickstartguides/Notability%20Quick%20Start%20Guide.pdf
[g-audio]: https://support.goodnotes.com/hc/en-us/articles/7352688559631-Add-Audio-Recordings-to-your-documents
[o-rec]: https://support.microsoft.com/en-us/office/record-audio-or-video-notes-b90fa4a2-253b-47ec-99bd-c9b368268465
[s-rec]: https://www.samsung.com/us/support/answer/ANS10001577/
[noted]: https://www.notedapp.io/faq/how-to-use-timetag/
[a-rec]: https://support.apple.com/guide/ipad/record-and-transcribe-audio-ipadd0bde806/ipados
[g-feature]: https://www.goodnotes.com/features/audio-recording
