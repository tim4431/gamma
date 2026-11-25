# Handwriting that vanished or came back while writing

A user reported that "very occasionally, while writing, strokes written
earlier suddenly disappear, and strokes already erased reappear; it feels
like a sync problem". This note records what a survey of the whole
handwriting sync path found in November 2026. Seven investigators covered
these areas:

- the save pipeline;
- the screen against the block tree;
- the server's merge;
- undo and gestures;
- live co-drawing and the native pen layers;
- a device's mirror of the main server;
- the draft journal.

Their 49 findings merged into 26. It also records which ones were built
as fixes and which are still open. The mechanics as built are in
[dev/handwriting.md](../dev/handwriting.md) "Fences" and "Client"
(inkStore, the saver, the strokes, the undo).

## What explains the report

Each of these happens with one tab on one device. Each was reproduced in a
node test over the real `inkStore` and `createInkSaver`, or in a pytest
against the real `PUT /blocks/{id}`.

1. **Two saves of one group overlapped.** The flush guarded only keepalive
   requests. A flush that started while the group's previous save was still
   uploading or PUTting sent the base that existed before that save was
   answered (`markSaved` moves it). The server then merged the tab's own
   unanswered save as another writer's. The three-way merge reads a stroke
   in theirs and not in the base as "theirs added it", so:
   - a stroke drawn and erased between the two saves came back, stored;
   - an undone erasure was lost again;
   - a cut against a cut kept the old piece;
   - a move of a fresh stroke was dropped with "someone changed it since",
     with nobody else on the page.

   When the second PUT overtook the first, the older change was merged on
   top, and the out-of-order `markSaved` left the draft dirty with nothing
   scheduled.
2. **A save's answer arrived before its own echo.** The block's `PUT` is
   logged with no client, so the writer's tab gets its own op back over
   the socket. That is the only way the tree's `ink_url` moves. On
   loopback the echo trailed the answer in 35 of 60 rounds with one tab,
   and in 50 of 60 with three peers. In that gap `inkFor` dropped the clean
   draft, because the tree still named the old file, so the group showed
   that file:
   - strokes saved since vanished, and strokes erased since returned;
   - a new group showed nothing;
   - a stroke, an eraser frame or an undo in the gap built on the old file.

   The gap is milliseconds on a healthy socket, and seconds while it
   reconnects, falls back to the 2 s poll, or is half-open after a phone
   wakes (there is no client heartbeat).
3. **A PUT that landed with its answer lost** was retried with the newest
   drawing on the old base, which is cause 1 without an overlap.
4. **One failed fetch of a group's file was cached as missing for the
   tab's life.** The group vanished. The next stroke into it started from
   an empty drawing on the stored file's base, and the server, finding the
   stored file equal to the base, replaced every stroke. On an isolated
   server 30 strokes became 1, with no conflict reported.
5. **Pen-ups while a group's file loaded overwrote each other.** Every
   `groupFor` continuation read "no draft" before any of them applied.
6. **The base was labelled from the tree at apply time**, not from the
   file the strokes were read from.
7. **A stroke into a group whose empty-group `DELETE` was out** went with
   the block, then was retried as a 404 for ever.
8. **The journal's restore counted from an upload whose `PUT` never
   landed** while the block still named the entry's base, and took that
   upload's strokes for strokes the server had deleted.

### How often

Measured in process on a desktop CPU:
- a stroke of 80 samples is about 1.1 KiB;
- a page of handwriting is 1 to 2.5 MB, re-uploaded whole on every flush,
  since a group splits only at 3 MB;
- parsing a 2 MB upload took 159 ms, a plain `PUT` 6 ms, and a merging
  `PUT` 915 ms;
- serializing took 26 ms, and the awaited journal write up to 500 ms.

A NAS, or the iPad or Android app's own Python server, is 3 to 5 times
slower, so a flush on a dense page takes 1 to 2 s. The next flush comes
700 ms after the last edit. So "write, pause, erase, pause" overlaps on a
dense page, and a small group (0.1 to 0.3 s a flush) rarely does. That
matches "very occasionally". The merging `PUT` that an overlap causes is
itself slow, so the next flush tends to overlap too.

## What was built

- One save of a group at a time, its lane. A newer drawing waits and goes
  on the base the answer stored. As the page goes, the save on its way is
  made to outlive it: its requests are sent again with keepalive under its
  batch, or it is superseded while still uploading.
- `PUT /blocks/{id}` takes a `batch`. A lost answer's `PUT` is owed and
  sent again first under it, and the op log answers a landed one instead
  of applying it twice.
- A clean draft stays on screen until the page session has applied the
  save's `seq` (`collabSession` `applied`, `inkStore.setTreeApplied`).
- A failed file is fetched again with a backoff, and keeps its last
  drawing on screen. A stroke reads its group as it applies
  (`inkEdits.pickGroup`), and passes over a group whose file is not here.
- A group whose `DELETE` is out takes no strokes. An undo waits, with a
  pill, while its group's `DELETE` is out or its file is on its way
  (`inkHistory.stepWaits`).
- The restore counts from the base when the block still names it.
- Saves and file fetches time out after 60 s.

A first plan used a list of superseded urls in place of the seq and sent
nothing at a close with a save out. Review showed both wrong:
- uploads are content-addressed, so a revert to a base's bytes names the
  base again;
- the journal's restore then counted from an unlanded upload.

A second review of the build found that copies of a save, sent again as
the page goes, must be transmissions of that save under its batch, with
their answers its own. Copies built from the current draft were a second,
unsynchronised save of the group.

## Still open

- **Finger-chord undo from the writing hand.** It is on by default on
  touch devices. There is no cool-down after a pen lifts, and a pen stroke
  between two chords does not break the pair. On tablets whose palm
  rejection lets hand contacts through between strokes, it undoes the
  newest stroke, with a pill. It is not a sync problem, but it explains
  both symptoms.
- **The Pencil's double tap and a stylus button arm the eraser** by
  default, and a barrel button held at touchdown makes that stroke an
  eraser pass. This is documented behaviour.
- **Two tabs on plain http.** Web Locks need a secure context, so every
  other tab counts as gone and a second tab restores a live tab's draft.
  Separately, the workspace's journal is read once and restored minutes
  later from that snapshot.
- **The desktop clone's settle step** writes the origin's merged drawing
  without fetching its file, with two writers. The retry of a failed fetch
  removes its worst client effect.
- **A peer deleting a group** this tab has a dirty draft of still leaves
  a 404 retried for ever.
- **Pen-ups queued behind a file that fails** each start a group of
  their own. The groups are invisible and nothing is lost. Taking the
  active new group would need `applyInk` to know about an insert not
  rendered yet.
- **A restore across a byte-identical revert.** Another writer's erasure
  that gives back the base's exact bytes reads as "nothing landed", so a
  closed tab's unanswered save comes back.
- **A refetch older than the session's applied seq** can be drawn before
  the session marks the reload, which blinks a just-saved drawing out for
  one round trip.
