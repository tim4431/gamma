// A recording as the replay store's time source (replay/playback.js): the
// segments play through two <audio> elements that take turns, so the next
// one is loaded before the join. Time is the recording clock's: the
// segment's start on it plus the element's position. A seek picks the
// segment and the offset (clock.segmentAt); the rate is the elements'
// playbackRate, pitch kept.
import { assetUrl } from "../shared/lib/utils.js";

export function audioSource(clock, { onError } = {}) {
  const { segments } = clock;
  const players = [new Audio(), new Audio()];
  for (const a of players) { a.preload = "auto"; a.preservesPitch = true; }
  let index = -1;        // the segment playing
  let active = 0;        // which player has it
  let rate = 1;
  let playing = false;
  let failed = false;

  const player = () => players[active];
  const src = (i) => assetUrl(segments[i].url);

  function load(a, i) {
    if (!segments[i]) return;
    const url = src(i);
    if (a.dataset.seg !== String(i)) { a.src = url; a.dataset.seg = String(i); a.playbackRate = rate; }
  }

  // Put the playhead at t: the segment it falls in, loaded in the active
  // player, the next one warming in the other.
  function place(t) {
    const at = clock.segmentAt(t);
    if (!at) return;
    if (at.index !== index) {
      index = at.index;
      // The other player may already hold this segment (it was warming).
      const other = players[1 - active];
      if (other.dataset.seg === String(index)) active = 1 - active;
      load(player(), index);
      if (segments[index + 1]) load(players[1 - active], index + 1);
    }
    const a = player();
    const want = at.offset / 1000;
    if (Math.abs((a.currentTime || 0) - want) > 0.05) {
      try { a.currentTime = want; } catch { /* not seekable yet: plays from the start */ }
    }
  }

  function onEnded() {
    if (!playing || index + 1 >= segments.length) return;
    place(segments[index + 1].start);
    player().play().catch(fail);
  }
  for (const a of players) {
    a.addEventListener("ended", () => { if (a === player()) onEnded(); });
    a.addEventListener("error", () => { if (a === player()) fail(a.error); });
  }

  function fail(error) {
    if (failed) return;
    failed = true;
    playing = false;
    onError?.(error);
  }

  return {
    start(t, r) { rate = r; place(t); playing = true; player().playbackRate = rate; player().play().catch(fail); },
    pause() { playing = false; player().pause(); },
    seek(t) { place(t); if (playing) player().play().catch(fail); },
    setRate(r) { rate = r; for (const a of players) a.playbackRate = r; },
    now() {
      if (index < 0) return 0;
      const seg = segments[index];
      return seg.start + Math.min(seg.dur, (player().currentTime || 0) * 1000);
    },
    close() {
      playing = false;
      for (const a of players) { a.pause(); a.removeAttribute("src"); a.load(); }
    },
    get failed() { return failed; },
  };
}
