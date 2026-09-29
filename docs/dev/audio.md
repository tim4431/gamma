# Recording and handwriting timing

A recording is an ordinary child block, with `properties.type = "audio"`.
Audio segments are ordinary immutable uploads referenced from properties,
so serving, quotas, Mirror, orphan retention and backups use the same file
path as other assets. There is no native recording database or save API on
the server. The capturing client owns a recording block; a separate
recording starts another block rather than concurrently appending to the
same recording's property arrays.

```json
{
  "type":"audio",
  "audio_segments":[{"id":"segmentA","url":"/api/uploads/0123456789abcdef01234567.m4a","duration_ms":8000}],
  "audio_events":[
    {"kind":"page","segment_id":"segmentA","start_ms":0,"end_ms":0,"sheet_id":"sheetA"},
    {"kind":"stroke","segment_id":"segmentA","start_ms":2400,"end_ms":2800,"block_id":"groupA","stroke_id":"strokeA","sheet_id":"sheetA"}
  ]
}
```

`audio_segments` is ordered. Each finalized segment has a unique ID, an
uploaded URL and its recorded duration. Pause/resume and recovery may
produce more segments. Upload the file before committing its reference;
save a local durable recording journal while capture is active, and only
publish events backed by finalized playable segments. The server accepts
M4A, AAC, MP3, WAV, WebM, Ogg and MP4 upload references; durations and events
are integer milliseconds. Missing segments, reversed intervals and events
past their segment's duration are rejected by the normal block write path.

Event offsets use the segment's recorder clock. `t0` on a stroke is wall
time and is not a reliable audio clock. During capture, relate stroke
sample time to the active segment; the event's `start_ms` maps sample time
zero to that segment. Individual stroke sample `t` values retain their
original offsets. Page events name either a stable notebook `sheet_id` or
a 1-based `pdf_page`; stroke events also name the group block and stroke.
Trim an event at a segment boundary and create the continuation against the
next segment when needed. Playback sums segment durations for the overall
timeline while retaining segment-relative seek positions.

`gamma-ink` v2 may store `source_id` on a fragment made by partial erasure.
Replay first matches a stroke's ID, then its source ID, to the recorded
stroke event. Fragment sample times must keep the original trajectory;
re-basing them to zero would replay the fragment too early. Duplicating a
stroke creates a new identity and removes lineage, so it does not acquire
the original's recording link. Ordinary transforms and restyling preserve
identity and sample timing. Replay describes surviving handwriting, not a
complete edit-history recording of erased content.

Timing has modest linear storage cost: one millisecond delta per retained
sample, plus recording events. Gamma already stores stroke sample timing
in v1. Audio data itself is normally the larger asset. All media files
remain content-hashed and use range serving; no per-stroke bitmap or
PencilKit archive is required for browser playback.

The pure schemas and lookup helper are in `gamma/audio.py`; audio MIME
types are in `gamma/storage.py`. The normal block writer validates audio
metadata and the existing upload reference scanner sees nested URLs.
Browser and native playback both use the pure projection in
`frontend/src/ink/inkReplay.js`, including current stroke geometry and
partial-erase lineage. Platform audio players supply segment-relative
playback time; they do not implement separate stroke-timing rules.
