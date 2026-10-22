// The handwriting's save (docs/dev/handwriting.md "Fences" and "Client"):
// the drafts inkStore keeps go to the server as an upload and a block
// PUT, 700 ms after the last edit and at most 5 s after the first unsaved
// one, and again after a failure a retry may get past. A factory, handed
// the draft store, the API and the keepalive room rather than importing
// them, so a node test drives it with fakes and mock timers
// (frontend/tests/inkSaver.test.mjs); useInkSave.js keeps one for the
// page's life.
import { retryableStatus } from "../collaboration/collabSession.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { KEEPALIVE_CAP, bodyBytes } from "../shared/lib/keepalive.js";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { inkProps, serializeInk } from "./ink.js";
import { planFlush } from "./inkFlush.js";

// blocksRef: the page's block tree, read when a flush runs. setStatus:
// the status pill. liveSaved(ink): live co-drawing's ledger
// (useInkLive.js), told what a saved file holds. inkStore: the drafts
// (inkStore.js). apiJson, API: the API's fetch and its root
// (shared/lib/utils). keepaliveRoom(priority): the room the page's
// keepalive ledger leaves (shared/lib/keepalive.js available).
// → {flushInk, scheduleInk, pending}: pending() is whether a save waits
// on its timer.
export function createInkSaver({ blocksRef, setStatus, liveSaved, inkStore, apiJson, API, keepaliveRoom }) {
  // The pending-upload timer, 0 when none.
  const inkTimerRef = { current: 0 };
  // What the server's merge dropped or brought back (the PUT answer's
  // `ink_conflict`, "Two writers, one group"), on the pill.
  function reportInkConflict(conflict) {
    if (!conflict || conflict.clean) return;
    const lost = conflict.lost_ours?.length || 0, back = conflict.returned?.length || 0;
    if (lost) setStatus(tn("Your change to {n} stroke was not applied: someone changed it since",
      "Your change to {n} strokes was not applied: someone changed them since", lost));
    else if (back) setStatus(tn("{n} stroke someone erased came back", "{n} strokes someone erased came back", back));
    else setStatus(t("Merged with changes made meanwhile"));
  }
  // The flush's keepalive requests on their way, by drawing: the upload,
  // the PUT and an empty group's DELETE, each until it settles, so a later
  // flush of the same drawing (pagehide after the flush on going hidden)
  // sends only what is not on its way already (ink/inkFlush.js planFlush).
  // Their bytes are the page's keepalive ledger's (shared/lib/keepalive.js).
  const inkFlightRef = { current: { uploads: new Map(), puts: new Map(), deletes: new Map() } };
  // Every dirty group's requests start at once, none waiting for another
  // group's: nothing is sure to run once a closing page is gone, so a loop
  // waiting for each group would send the first alone. `keepalive`: the
  // page may be going away (hidden, or pagehide), so the requests outlive
  // it — those of the groups planFlush fits, group by group in order, into
  // the page's keepalive room at ink's priority, the lowest (64 KiB of
  // keepalive bodies in flight at most, or the browser refuses the fetch;
  // the page's edits and its small requests come first); the rest go as
  // plain fetches, which a closing page may cut off, and the draft journal
  // (inkStore) brings what they carried back on the next visit.
  // `leaving`: the page is going away (pagehide) and nothing is sure to run
  // once it is gone, so the block's PUT goes beside the upload, naming the
  // file by its hash (inkStore names a draft's upload ahead), unless the
  // server will have to merge it (the block names another file than the
  // draft's base): then the upload must be there first. Only then: a peer
  // the PUT's op reaches before the upload has landed finds no file. Every
  // PUT is told to the journal before it goes (inkStore.markSending), since
  // its answer may never come.
  const flushInk = async ({ keepalive = false, leaving = false } = {}) => {
    clearTimeout(inkTimerRef.current);
    inkTimerRef.current = 0;
    inkDirtySinceRef.current = 0;
    const json = { "Content-Type": "application/json" };
    const flight = inkFlightRef.current;
    const flat = flattenBlocks(blocksRef.current);
    const groups = inkStore.dirtyDrafts().map((d) => {
      const block = flat.find((b) => b.id === d.id);
      // A group erased empty goes, unless its block holds a caption or
      // notes: those stay, with an empty drawing an undo can refill.
      if (!d.ink.strokes.length && !(block?.content || "").trim() && !block?.children?.length) {
        return { ...d, block, remove: true, inFlight: flight.deletes.has(d.ink) ? "delete" : null };
      }
      const body = d.upload?.body || serializeInk(d.ink);
      const putBody = (url) => JSON.stringify({ properties: inkProps(d.ink, url), base_properties: { ink_url: d.base || "" }, base_ids: d.baseIds || [] });
      return { ...d, block, body, putBody,
        uploadBytes: bodyBytes(body, KEEPALIVE_CAP),
        // sized with the upload's name, or with one as long when it is not named yet
        putBytes: bodyBytes(putBody(d.upload?.url || ""), KEEPALIVE_CAP) + (d.upload ? 0 : inkStore.UPLOAD_NAME_CHARS),
        beside: leaving && !!d.upload && (block?.properties?.ink_url || "") === (d.base || ""),
        inFlight: flight.puts.has(d.ink) ? "put" : flight.uploads.has(d.ink) ? "upload" : null };
    });
    // A request: through the page's keepalive ledger when the plan gave
    // its group keepalive (`ka`) and the ledger still has room for its body
    // at ink's priority — a PUT that waited for its upload's answer is
    // asked again then — and `track` (flight.uploads, .puts or .deletes)
    // holds it by drawing until it settles; else a plain fetch.
    const go = (ka, track, ink, bytes, request) => {
      if (!ka || bytes > keepaliveRoom("low")) return request();
      const sent = request({ keepalive: "low" });
      track.set(ink, sent);
      return sent.finally(() => { if (track.get(ink) === sent) track.delete(ink); });
    };
    const save = async ({ id, ink, block, body, putBody, uploadBytes, upload, beside }, { send, keepalive: ka }) => {
      if (!send) return;
      try {
        if (send === "delete") {
          await go(ka, flight.deletes, ink, 0, (o) => apiJson(`${API}/blocks/${id}`, { method: "DELETE" }, o));
          // Keep the empty draft until the tree observes the deletion; the
          // HTTP response can arrive before the corresponding socket op.
          inkStore.markDeleted(id, ink, block?.properties?.ink_url || "");
          return;
        }
        const post = () => go(ka, flight.uploads, ink, uploadBytes,
          (o) => apiJson(`${API}/upload-ink`, { method: "POST", headers: json, body }, o));
        const put = (url) => {
          const sent = putBody(url);
          return go(ka, flight.puts, ink, bodyBytes(sent),
            (o) => apiJson(`${API}/blocks/${id}`, { method: "PUT", headers: json, body: sent }, o));
        };
        // a PUT answered with an error did not land: the journal forgets it
        const sendPut = (url) => put(url).catch((err) => { if (err?.status) inkStore.markUnsent(id, url); throw err; });
        let url, saved;
        if (send === "put" || beside) {
          // the PUT beside the upload: this flush's, or the one on its way
          url = upload.url;
          inkStore.markSending(id, url);
          [, saved] = await Promise.all([send === "put" ? flight.uploads.get(ink) : post(), sendPut(url)]);
        } else {
          url = (await post()).url;
          await inkStore.markSending(id, url);
          saved = await sendPut(url);
        }
        inkStore.markSaved(id, ink, url, saved?.properties?.ink_url || url);
        liveSaved(ink);
        reportInkConflict(saved?.ink_conflict);
      } catch (err) {
        const status = err?.status || 0;
        // A 5xx or a network error, or the block's insert still queued
        // (404): try again shortly. Any other 4xx will not change on a
        // retry: the draft stays, behind a pill that stays too.
        if (retryableStatus(status) || status === 404) {
          setStatus(t("Handwriting not saved yet: {err}", { err: err.message || err }));
          if (!inkTimerRef.current) inkTimerRef.current = setTimeout(flushInk, 2000);
        } else {
          setStatus(t("Handwriting not saved: {err}", { err: err.message || err }), null, { sticky: true });
        }
      }
    };
    // a flush the page will live past asks for no keepalive room
    const plan = planFlush(groups, keepalive ? keepaliveRoom("low") : -Infinity);
    await Promise.all(groups.map((g, i) => save(g, plan[i])));
  };
  // 700 ms after the last edit, but never more than 5 s after the first
  // unsaved one, so files land while writing goes on.
  const inkDirtySinceRef = { current: 0 };
  function scheduleInk() {
    clearTimeout(inkTimerRef.current);
    const now = Date.now();
    if (!inkDirtySinceRef.current) inkDirtySinceRef.current = now;
    inkTimerRef.current = setTimeout(flushInk, Math.max(0, Math.min(700, inkDirtySinceRef.current + 5000 - now)));
  }
  const pending = () => !!inkTimerRef.current;
  return { flushInk, scheduleInk, pending };
}
