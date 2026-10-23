// Live co-drawing, this tab's side (docs/dev/handwriting.md "Live
// co-drawing", docs/dev/collab.md "ink-live"): the chunks of the stroke in
// progress out on the page socket, a `drop` for a stroke sent live that an
// edit took away before any save held it, and the peers' wet strokes kept
// to the peers still on the page (inkLive.js holds them; App hands the
// session's incoming chunks to it). App composes it after the page's
// collab session.
import { useCallback, useEffect, useRef } from "react";
import * as inkLive from "./inkLive.js";

// focusedBlockId: the open page; leaving it drops every wet stroke and
// forgets what was sent. readOnly, inkStale (the build fence,
// buildFence.js): nothing is sent. peers: the page session's peers.
// sendInkLive(msg): the page session's send, read when it runs.
export function useInkLive({ focusedBlockId, readOnly, inkStale, peers, sendInkLive }) {
  // The strokes this tab sent live (ink/inkLive.js liveLedger) that no
  // saved file holds yet: one an edit takes away first gets a `drop`
  // (applyInk), since no file will land it on the peers' pages.
  const inkLiveSentRef = useRef(null);
  if (!inkLiveSentRef.current) inkLiveSentRef.current = inkLive.liveLedger();
  // Live co-drawing (ink/inkLive.js): a peer's wet strokes go when the peer
  // leaves the page (or the socket closes, which empties the peers), and
  // every wet stroke goes with the page.
  useEffect(() => { inkLive.keepWetOf(peers.map((p) => p.client)); }, [peers]);
  useEffect(() => () => inkLive.clearWet(), [focusedBlockId]);
  // Leaving a page forgets what was sent live on it.
  useEffect(() => () => { inkLiveSentRef.current.clear(); }, [focusedBlockId]);
  // A chunk of the stroke in progress for the page's other peers
  // (ink/inkLive.js, docs/dev/collab.md "ink-live"), out on the page socket
  // with its surface as `page`.
  function handleInkLive(page, msg) {
    if (readOnly || inkStale || !msg?.id) return;
    sendInkLive({ ...msg, page });
    inkLiveSentRef.current.sent(msg.id, page, !!msg.drop);
  }
  // An edit's changes (applyInk): a stroke sent live and taken away before
  // any save held it gets a drop, so its wet stroke goes from the peers'
  // pages.
  function liveRemoved(changes) {
    for (const { id, page } of inkLiveSentRef.current.removed(changes)) handleInkLive(page, { id, drop: true });
  }
  // A save landed (the flush, after markSaved): what its file holds is no
  // longer only live.
  const liveSaved = useCallback((ink) => inkLiveSentRef.current.saved(ink), []);
  return { handleInkLive, liveRemoved, liveSaved };
}
