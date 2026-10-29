# What the film pipeline does that the README demos do not

Surveyed 2026-10-26. Question: the introductory film (the `gamma-advertising`
repo, checked out beside this one) records the same app with the same capture
runtime as the README demos, but edits it in Remotion. Which of its methods
would improve the README animations made by `tools/readme-media`, and which
belong to a film only?

## The two pipelines side by side

Both start from `tools/readme-media/runtime.mjs`: the film's recorders import
`launchRetina`, `startCapture` and `addCursor` by relative path, and the e2e
harness's `Server` and `Account` for an isolated backend. From there they part.

| Step | README demos (`tools/readme-media`, as surveyed on 2026-10-26; see "What was built") | Film (`gamma-advertising/videos/intro`) |
|---|---|---|
| Recorder | One script per slot; marks taken on the recorder's clock; framing boxes for the camera; verification through the API; manifests named per recorder (`ink-timeline.json`, `agentic-timeline.json`, `collab_marks.json`) | One script per shot, `footage/<shot>/timing.json` written last, with `marks`, `framing` for every element a camera might want, `verified`, a `frames` summary, and `contact.png`; a page-side watcher (a 16 ms `setInterval` in the page) records when the viewer *sees* a change (PDF painted, title filled, toast shown), not when the recorder noticed; a pre-warm pass runs the same network action once off camera on a separate throwaway server so the on-camera download is not a cold fetch; `--inspect` and `--rehearse` modes run the story without capture or without a model |
| AI | Real model, fresh workspace each take; model waits cut in the edit | `--mock`: an OpenAI-compatible scripted model on a local port, saved as a connection on the throwaway account. Gamma's tools, approval cards and edits run for real; only the words are scripted, and the mock checks each claim against the text Gamma fetched before answering, so the recorder can refuse a take (`verified.model = "scripted"`). Real-model runs through `GAMMA_AI_KIND` / `GAMMA_AI_KEY`, read once and removed from the environment |
| Edit | `compose.py`: a list of capture-time `segments` with dissolves between them, `quiet()` auto-trims still stretches longer than 2.5 s, a keyframed `Camera` with a light motion blur, the paper card frame, a loop dissolve | An anchor list per shot, `{at, src, hold?, rate?}`: at film time `at` show capture time `src`; the capture plays at 1× (or `rate`) until the next anchor's `src`, holds on that frame if it gets there early, cuts forward if late. Camera keys with `jump` cuts, `contain` (no ground beside the card beyond 40 px) and `clearCaption`; every cut a 0.5 s crossfade where the outgoing shot keeps playing; click ripples found after the fact by template-matching the drawn cursor at each click mark (`tools/find-clicks.py`); captions; the whole thing scrubbable in Remotion Studio |
| Output | Tile-encoded animated WebP under 5 MiB, 1600 × 900, checked by decoding every frame in Chromium | H.264 MP4 at 1920 × 1080 with narration and music, mastered to −14 LUFS |

The film took six takes on 2026-10-05 against `dev` at 3894fcb3, five days
after the README's captures (2026-09-30). The interface in both is the current
one in outline (top bar, zoom column, Notes dock); what has moved since are
details such as the top bar setting and the comments margin, so both are
equally stale and neither needs re-recording for the UI alone.

## What is worth taking

Ranked by how much it would change the README animations against what it costs.

1. **The recorder discipline.** The page-side watcher puts a mark where the
   viewer sees the change, which is what an edit cuts on; the README's
   recorder-side marks are late by a wait-for and early by a paint. The
   pre-warm pass makes a download or a registry lookup land at a believable
   speed without caching anything inside the recording server (the
   "metadata must resolve on camera" rule holds). One `timing.json` per shot
   written last, with the same four keys, would let every renderer refuse an
   unverified take the same way instead of each reading its own file name.
   Cost: edits to the recorders, no new tooling.
2. **The anchor edit list.** `srcAt` is ten lines, and it says in one list
   what `segments` plus `quiet()` plus hand-tuned `max(...)` expressions say
   in `render_feature_demos.py`: where to cut a wait, where to hold for a
   beat, and from which capture moment each piece plays. It is also what a
   reviewer reads to understand an edit. Port it into `compose.py` as an
   alternative to `segments`; dissolves stay where a cut moves `src`
   backwards or forward by more than a frame. (This note first said to keep
   the README at 1×; the developer chose the film's speed-ups instead, see
   "What was built".)
3. **The film's recorders themselves.** Seven of them cover README stories on
   the newer UI and already import this repo's runtime and harness:
   `f1-highlight` (annotate and ink's first half), `f12-notes-flow` (notes
   and the AI-in-notes story in one take), `f11-ai-demo` (the agentic chat,
   with a scripted or a real model), `f8-collab-picture` and `f15-collab-ipad`
   (collaboration), `f13-paste-nature` (a paper added by its link, with the
   DOI lookup filling the record), `f5-picture` (a pasted figure sized by its
   grip). Two show features the README has no demo for: `f7-translate` (a
   page redrawn in Chinese in place) and `f14-zotero-import` (a Zotero RDF
   export with its highlights imported, three folders and labels landing).
   Moving a recorder here is a path fix; its `timing.json` already has
   what `compose.py` needs.
4. **A preview before the WebP.** The one thing Remotion Studio gives that
   the Python pipeline lacks is scrubbing an edit before a render. The
   cheap version is for `publish_demo` to write a small H.264 preview beside
   the lossless master, viewed in the editor, before the WebP encode and the
   Chromium decode check. The anchor list makes this worth having: an edit
   becomes a few numbers to adjust and re-preview.

## What to leave in the film

- **Remotion as the compositor.** The README's constraints are the film's
  inverted: an animated WebP has no motion compensation, so a moving camera
  costs up to a megabyte per move and the still framing is the default,
  while the film moves its camera at every beat and crossfades every cut.
  `compose.py` and `encode_master` encode those costs (the move budget, the
  motion sidecar that lowers quality on moving frames, the tile encoder
  that avoids libwebp's ghosts); Remotion would render frames that the same
  encoder then has to take in, through an image sequence, with the motion
  measured from pixel differences instead of the camera. That is a second
  Node project (the runtime deliberately resolves Playwright through
  `frontend/`), a React port of the frame and camera, and a licence to
  check, for a scrubber. The preview above is the cheaper answer.
- **Click ripples.** The film finds clicks after the fact because the
  in-page press ring is small at a 1920-wide stage with the card at 80 %.
  The README frames the window at 1600 px, where the ring the cursor draws
  reads fine; the ripple would be a second ring.
- **The scripted model for the README** (superseded: the developer chose
  the scripted model for its stability, see "What was built"). The README is
  where a visitor decides whether the product is real, and the current rule
  (real model, fresh workspace, every claim verified through the API) is the
  right one there. The mock earns its place in the film, where words must land on a
  beat. What is worth borrowing is the mock's grounding check as an idea:
  the recorder refusing a take whose answer the fetched papers do not
  support, which a real-model recorder could apply to its own citations
  through Gamma's citation matcher, as `record-native-agentic.mjs` partly
  does with its exact-match check.
- **Crossfades on every cut.** The README dissolves only where it cuts a
  wait, since a dissolve re-encodes every pixel of the frame for its length.

## What was built (2026-10-29)

The developer took the recorder discipline and the film's storytelling for
five README clips, and decided two points against this note's first advice:
the edits speed up pointer travel (to 1.3×), drawing (1.5×) and streamed AI
text (2×), and the AI demos use a scripted model, which plays the same way
every take, instead of a real one.

- **Recorder discipline** became `tools/readme-media/take.mjs`: an isolated
  library, page-side watched marks, framing boxes, checks, and a
  `timing.json` written only when every check passed, which the renderer
  requires. Its scripted model, `demo-model.mjs` with one script per story
  under `models/`, speaks the OpenAI wire to Gamma, so Gamma's own tools,
  approval cards and edits run; the reading script checks every fact against
  the paper text Gamma fetched before it states it. The code was written
  fresh, not copied from the film.
- **The anchor list** did not become a second edit format: `compose.py`
  segments gained a playback rate (0 holds a frame) and an optional per-cut
  fade, and a segment that starts where the last one ended continues it
  without a dissolve. That is the anchor list's meaning in the existing form.
- **The preview** is a small H.264 copy of each master in scratch, which the
  reviews of every clip read frame by frame.
- **The clips**: the AI reading demo (20 s, against 41 s before) now leads the
  README; AI in notes, collaboration and the Connector were recut or
  re-recorded at 10–12 s; the annotate-and-ink slot became a Notability note
  imported and then drawn on with Gamma's pen (15 s). The note is generated
  (`make-notability-note.py`) because the repository's real samples are not
  cleared for publication.
- **Left in the film**, as above: Remotion, the click ripples, crossfades on
  every cut.

## Open

- Whether the README wants slots for translation and the Zotero import. The
  film's recorders exist; the README's sections do not yet mention either
  story beside a picture.
- The library, search, metadata and reference-links clips still use the old
  recorders and the demo account; moving them to `take.mjs` is not done.
- Two app behaviours the reviews of the clips noticed and left alone: an ink
  card with no text reads "(empty)" in Notes (every imported Notability page
  shows it), and a sent PDF passage keeps the PDF's line breaks in the chat
  bubble.
