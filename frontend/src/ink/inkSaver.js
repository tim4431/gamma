// The handwriting's save (docs/dev/handwriting.md "Fences" and "Client"):
// the drafts inkStore keeps go to the server as an upload and a block
// PUT, 700 ms after the last edit and at most 5 s after the first unsaved
// one, and again after a failure a retry may get past. One save of a group
// is on its way at a time (a LANE): a save sent before the previous one is
// answered would name the base from before it, and the server would merge
// the tab's own unanswered save as someone else's, bringing back what was
// erased in between. A factory, handed the draft store, the API and the
// keepalive room rather than importing them, so a node test drives it with
// fakes and mock timers (frontend/tests/inkSaver.test.mjs, and
// inkSync.test.mjs over the real store); useInkSave.js keeps one for the
// page's life.
import { retryableStatus } from "../collaboration/collabSession.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { KEEPALIVE_CAP, bodyBytes } from "../shared/lib/keepalive.js";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { inkProps, serializeInk } from "./ink.js";
import { planFlush } from "./inkFlush.js";

// A request of a save that has not answered in this long is given up (a
// half-open connection after a phone wakes never answers), so the group's
// lane is free again. A PUT given up may have landed: it is owed (below).
export const SAVE_TIMEOUT_MS = 60000;

// What the server's merge dropped or brought back, as the pill says it
// (the PUT answer's `ink_conflict`, "Two writers, one group"): the
// strokes of ours it did not apply, else the strokes someone erased that
// came back, else that it merged; null for a clean save or none.
export function conflictMessage(conflict) {
  if (!conflict || conflict.clean) return null;
  const lost = conflict.lost_ours?.length || 0, back = conflict.returned?.length || 0;
  if (lost) return tn("Your change to {n} stroke was not applied: someone changed it since",
    "Your change to {n} strokes was not applied: someone changed them since", lost);
  if (back) return tn("{n} stroke someone erased came back", "{n} strokes someone erased came back", back);
  return t("Merged with changes made meanwhile");
}

// The batch a PUT's answer names, which the draft stays on screen until the
// page's tree has applied (inkStore.markSaved `at`); null from a server
// that does not say.
const answeredAt = (saved) => (saved?.page_id && Number.isFinite(saved?.seq) ? { page: saved.page_id, seq: saved.seq } : null);
// A failure after which the PUT may have landed: no answer (a network error,
// the timeout), a server error, or a timeout or rate limit on the way.
const mayHaveLanded = (status) => retryableStatus(status);

// blocksRef: the page's block tree, read when a flush runs. setStatus:
// the status pill. liveSaved(ink): live co-drawing's ledger
// (useInkLive.js), told what a saved file holds. inkStore: the drafts
// (inkStore.js). apiJson, API: the API's fetch and its root
// (shared/lib/utils). keepaliveRoom(priority): the room the page's
// keepalive ledger leaves (shared/lib/keepalive.js available).
// keeps(id): the group's block stays when it is erased empty, as one with
// a caption or notes does: a group with a comment thread
// (comments/useInkThreads.js), which the thread hangs on. hidden(): the
// page is in the background, so a save that follows one goes with
// keepalive. timeoutMs: SAVE_TIMEOUT_MS.
// → {flushInk, scheduleInk, pending}: pending() is whether a save waits
// on its timer, on a group's save on its way, or owes a PUT.
export function createInkSaver({ blocksRef, setStatus, liveSaved, inkStore, apiJson, API, keepaliveRoom, keeps = () => false,
  hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden", timeoutMs = SAVE_TIMEOUT_MS }) {
  // The pending-upload timer, 0 when none.
  const inkTimerRef = { current: 0 };
  // When the first edit since the last flush was made (the 5 s cap).
  const inkDirtySinceRef = { current: 0 };
  // What the server's merge dropped or brought back, on the pill.
  function reportInkConflict(conflict) {
    const said = conflictMessage(conflict);
    if (said) setStatus(said);
  }
  // The keepalive requests on their way, by drawing: the uploads, the PUTs
  // and an empty group's DELETEs, each until it settles, so a later flush
  // (pagehide after the flush on going hidden) sends only what is not on
  // its way already with keepalive. Their bytes are the page's keepalive
  // ledger's (shared/lib/keepalive.js).
  const inkFlightRef = { current: { uploads: new Map(), puts: new Map(), deletes: new Map() } };
  // Each group's save on its way, its LANE, one at a time per group: {ink,
  // phase ('upload' until its PUT is known, then 'put'; or 'delete'),
  // batch, snap (the draft it sends), put {url, body, uploaded}, ka (its
  // requests go with keepalive), superseded, paying (its PUT is an owed
  // one), again, done}, and its transmissions: `sends`, every request of
  // its PUT or DELETE on its way, its own and each one a flush sent again
  // as the page may go, all under its batch, so whichever lands first the
  // others are answered from the op log; `answer`, the first one's; `err`,
  // `statusless` and `maybe`, what the failures said. A lane settles once
  // every transmission has, and only the save that made it clears it.
  // `again` {keepalive}: a flush found a newer drawing and left it to
  // follow.
  const lanes = new Map();
  // A PUT that may have landed with its answer lost ({ink, url, body,
  // batch, uploaded}): the group's next save sends it again first, under
  // its batch id, which the server answers from its op log if it landed
  // (routers/blocks.py), and the newer drawing then goes on the base that
  // answer stored. Forgotten when the group's block starts over.
  const owed = new Map();
  inkStore.onDropDraft((id) => owed.delete(id));
  // Each group's block, and whether it keeps its block erased empty, as
  // the page's tree last showed them to a flush: a flush that runs after
  // the page changed (a follow-up, a retry) decides from them, never from
  // the next page's tree and threads.
  const seen = new Map();
  let batches = 0;
  const newBatch = () => `ink-${inkStore.journalTab()}-${++batches}`;
  // A batch id as long as one a lane names, for sizing a PUT before its
  // lane is made.
  const batchLike = `ink-${inkStore.journalTab()}-${"0".repeat(6)}`;

  // A request: through the page's keepalive ledger when `ka` and the
  // ledger still has room for its body at ink's priority — asked as the
  // request starts — and `track` (flight.uploads, .puts or .deletes) holds
  // it by drawing until it settles; else a plain fetch. Given up after
  // timeoutMs either way. `request(signal, opts)`.
  const go = (ka, track, ink, bytes, request) => {
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : 0;
    const run = (o) => request(ctl?.signal, o).finally(() => clearTimeout(timer));
    if (!ka || bytes > keepaliveRoom("low")) return run();
    const sent = run({ keepalive: "low" });
    track.set(ink, sent);
    return sent.finally(() => { if (track.get(ink) === sent) track.delete(ink); });
  };
  const json = { "Content-Type": "application/json" };
  const upload = (ka, ink, body) => go(ka, inkFlightRef.current.uploads, ink, bodyBytes(body, KEEPALIVE_CAP),
    (signal, o) => apiJson(`${API}/upload-ink`, { method: "POST", headers: json, body, signal }, o));
  const putBodyOf = (d, url, batch) => JSON.stringify({ properties: inkProps(d.ink, url), base_properties: { ink_url: d.base || "" },
    base_ids: d.baseIds || [], batch });
  // the group's dirty draft as a flush reads it (with its upload named), or null
  const draftOf = (id) => (inkStore.draft(id)?.dirty ? inkStore.dirtyDrafts().find((d) => d.id === id) || null : null);
  const inTree = (id) => flattenBlocks(blocksRef.current).some((b) => b.id === id);

  // --- a lane's transmissions ---------------------------------------------
  function track(lane, settledSend) {
    lane.sends.add(settledSend);
    settledSend.finally(() => lane.sends.delete(settledSend));
  }
  function noteFailure(lane, err) {
    lane.err ||= err;
    const status = err?.status || 0;
    if (!status) lane.statusless = true;
    if (mayHaveLanded(status)) lane.maybe = true;
  }
  // One transmission of the lane's PUT, `lane.put.body` under its batch.
  function transmitPut(lane, id, ka) {
    track(lane, go(ka, inkFlightRef.current.puts, lane.ink, bodyBytes(lane.put.body),
      (signal, o) => apiJson(`${API}/blocks/${id}`, { method: "PUT", headers: json, body: lane.put.body, signal }, o))
      .then((saved) => { lane.answer ||= saved || {}; }, (err) => noteFailure(lane, err)));
  }
  // One transmission of the lane's DELETE. A 404 for a block the page no
  // longer shows: it went, by an earlier one whose answer was lost or by
  // someone else. One still on the page may wait for its insert (queued).
  function transmitDelete(lane, id, ka) {
    track(lane, go(ka, inkFlightRef.current.deletes, lane.ink, 0, (signal, o) => apiJson(`${API}/blocks/${id}`, { method: "DELETE", signal }, o))
      .then(() => { lane.answer ||= {}; }, (err) => {
        if (err?.status === 404 && !inTree(id)) lane.answer ||= {};
        else noteFailure(lane, err);
      }));
  }
  const allSettled = async (lane) => { while (lane.sends.size) await Promise.allSettled([...lane.sends]); };
  // → the lane's answer once every transmission has settled; throws the
  // first failure when none answered.
  async function settled(lane) {
    await allSettled(lane);
    if (lane.answer) return lane.answer;
    throw lane.err || new Error("not saved");
  }
  const fresh = (lane) => Object.assign(lane, { sends: new Set(), answer: null, err: null, statusless: false, maybe: false });
  const owe = (id, lane, uploaded = lane.put.uploaded) => owed.set(id,
    { ink: lane.ink, url: lane.put.url, body: lane.put.body, batch: lane.batch, uploaded });

  // A failed save's pill, and its retry: a 5xx or a network error, or the
  // block's insert still queued (404), tries again shortly — with
  // keepalive while the page is in the background; any other 4xx will not
  // change on a retry: the draft stays, behind a pill that stays too. →
  // whether a retry is armed.
  function failed(err) {
    const status = err?.status || 0;
    if (retryableStatus(status) || status === 404) {
      setStatus(t("Handwriting not saved yet: {err}", { err: err.message || err }));
      if (!inkTimerRef.current) inkTimerRef.current = setTimeout(() => flushInk({ keepalive: hidden() }), 2000);
      return true;
    }
    setStatus(t("Handwriting not saved: {err}", { err: err.message || err }), null, { sticky: true });
    return false;
  }

  // A group's save, on a lane of its own: an empty group's DELETE, else
  // the PUT it owes and then its drawing as it is by then. When it settles
  // with the group still dirty, the group goes again — at once when a flush
  // left a newer drawing to it (`again`), else on the timer — unless a
  // retry is armed. → the save and what follows it.
  function startLane(g, { send, keepalive: ka }, { leaving }) {
    const { id } = g;
    const lane = fresh({ ink: g.ink, phase: g.remove ? "delete" : "upload", batch: newBatch(), snap: g, put: null, ka: !!ka,
      superseded: false, paying: false, again: null, done: null });
    lanes.set(id, lane);
    // The PUT it owes, sent again as it went (its upload first when that
    // was never answered: the same bytes, the same name). Its answer moves
    // the draft's base like any save's; a 404 says it never landed; any
    // other failure leaves it owed. While it is out the lane's PUT is that
    // one, so a flush as the page goes sends it again too, never a newer
    // drawing on the base from before it.
    async function payDebt(debt) {
      Object.assign(lane, { phase: "put", paying: true, ink: debt.ink, batch: debt.batch,
        put: { url: debt.url, body: debt.body, uploaded: debt.uploaded } });
      if (!debt.uploaded) {
        await upload(lane.ka, debt.ink, serializeInk(debt.ink));
        lane.put.uploaded = true;
      }
      transmitPut(lane, id, lane.ka);
      let saved;
      try {
        saved = await settled(lane);
      } catch (err) {
        if (lane.err?.status === 404 && !lane.maybe && owed.get(id) === debt) { owed.delete(id); inkStore.markUnsent(id, debt.url); }
        throw err;
      }
      if (owed.get(id) === debt) owed.delete(id);
      inkStore.markSaved(id, debt.ink, debt.url, saved.properties?.ink_url || debt.url, answeredAt(saved));
      liveSaved(debt.ink);
      reportInkConflict(saved.ink_conflict);
    }
    // The lane's PUT settled: → its answer. When none answered, the journal
    // forgets the PUT if every failure came with a status (none landed), and
    // the PUT is owed if one may have landed; then it throws.
    async function putOutcome() {
      try {
        return await settled(lane);
      } catch (err) {
        if (!lane.statusless) inkStore.markUnsent(id, lane.put.url);
        if (lane.maybe) owe(id, lane);
        throw err;
      }
    }
    // An upload that failed after its PUT went beside it (this flush's, or
    // one a flush sent as the page went): the PUT may name a file the server
    // lacks, so whatever it answered it is owed with its upload.
    async function uploadFailed(err) {
      await allSettled(lane);
      if (!lane.answer && !lane.statusless) inkStore.markUnsent(id, lane.put.url);
      owe(id, lane, false);
      throw err;
    }
    // The drawing `d` as an upload and a PUT under the lane's batch. `beside`:
    // the PUT goes beside the upload, naming it by its hash. → the PUT's
    // answer, or null when a flush superseded the lane before its PUT went.
    async function sendDrawing(d, beside) {
      Object.assign(lane, { phase: "upload", ink: d.ink, snap: d });
      const body = d.upload?.body || serializeInk(d.ink);
      if (beside) {
        const url = d.upload.url;
        Object.assign(lane, { phase: "put", put: { url, body: putBodyOf(d, url, lane.batch), uploaded: false } });
        inkStore.markSending(id, url);
        const up = upload(lane.ka, d.ink, body);
        transmitPut(lane, id, lane.ka);
        try { await up; } catch (err) { return uploadFailed(err); }
        lane.put.uploaded = true;
        return putOutcome();
      }
      let url;
      try {
        url = (await upload(lane.ka, d.ink, body)).url;
      } catch (err) {
        if (lane.put) return uploadFailed(err);   // a flush as the page went sent its PUT beside it
        throw err;
      }
      if (lane.superseded) return null;
      if (lane.put) lane.put.uploaded = true;     // that PUT names this upload already
      else {
        Object.assign(lane, { phase: "put", put: { url, body: putBodyOf(d, url, lane.batch), uploaded: true } });
        await inkStore.markSending(id, url);
        transmitPut(lane, id, lane.ka);
      }
      return putOutcome();
    }
    // → whether an answer came (the save's, or the owed PUT's)
    async function work() {
      if (send === "delete") {
        inkStore.markDeleting(id);
        transmitDelete(lane, id, lane.ka);
        await settled(lane);
        // Keep the empty draft until the tree observes the deletion; the
        // HTTP response can arrive before the corresponding socket op.
        inkStore.markDeleted(id, g.ink, g.block?.properties?.ink_url || "");
        owed.delete(id);   // a PUT it owed the block went with it
        return true;
      }
      const debt = owed.get(id);
      if (debt) {
        await payDebt(debt);
        if (leaving) return true;   // the newest drawing waits: the journal keeps it if the page goes
        Object.assign(fresh(lane), { paying: false, batch: newBatch(), put: null });
      }
      // the drawing as it is now: the debt's answer moved its base
      const d = debt ? draftOf(id) : g;
      if (!d) return !!debt;
      const saved = await sendDrawing(d, !debt && g.beside);
      if (!saved) return !!debt;   // superseded: the newest drawing has a lane of its own
      inkStore.markSaved(id, d.ink, lane.put.url, saved.properties?.ink_url || lane.put.url, answeredAt(saved));
      liveSaved(d.ink);
      reportInkConflict(saved.ink_conflict);
      return true;
    }
    lane.done = (async () => {
      let answered = false, retry = false;
      try {
        answered = await work();
      } catch (err) {
        await allSettled(lane);   // nothing of it is on its way when the group goes again
        const why = lane.err || err;
        // a DELETE refused: it did not land, and the group takes strokes again
        if (lane.phase === "delete" && why?.status && why.status !== 404 && !retryableStatus(why.status)) inkStore.unmarkDeleting(id);
        retry = failed(why);
      }
      if (lanes.get(id) === lane) lanes.delete(id);
      if (lane.superseded || retry || !inkStore.draft(id)?.dirty) return null;
      if (lane.again) return flushInk({ keepalive: lane.again.keepalive || hidden() });
      if (answered && !inkTimerRef.current) scheduleInk();
      return null;
    })();
    return lane.done;
  }
  // What a flush sends of a lane on its way as the page may go
  // (planFlush's 'both' / 'put' / 'upload' / 'delete'): its PUT or DELETE
  // again with keepalive, under its batch — or, leaving while it still
  // uploads a drawing named by its hash, its PUT beside that upload — and
  // its upload again when that is not answered yet. Each is one of the
  // lane's transmissions; the lane acts on their answers.
  function joinLane(g, { send, keepalive: ka }) {
    const { lane, id } = g;
    if (send === "delete") { transmitDelete(lane, id, ka); return; }
    if (send === "upload" || send === "both") upload(ka, lane.ink, lane.snap?.upload?.body || serializeInk(lane.ink)).catch(() => {});
    if (send !== "put" && send !== "both") return;
    if (!lane.put) {
      const url = lane.snap.upload.url;
      Object.assign(lane, { phase: "put", put: { url, body: putBodyOf(lane.snap, url, lane.batch), uploaded: false } });
      inkStore.markSending(id, url);
    }
    transmitPut(lane, id, ka);
  }

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
  // server will have to merge it (the server does not hold the draft's
  // base: the block names another file, and the tree is not merely behind
  // this tab's own last save, inkStore.treeBehind); then the upload must
  // be there first. Only then: a peer the PUT's op reaches before the
  // upload has landed finds no file. Every PUT is told to the journal
  // before it goes (inkStore.markSending), since its answer may never come.
  // A group whose save is on its way: a newer drawing waits for it
  // (`again`). As the page may go, the save on its way is made to outlive
  // it instead: one still uploading an older drawing is superseded, its
  // PUT never sent, and the newest drawing goes now; otherwise its
  // requests go again with keepalive (joinLane) and the newest drawing
  // waits (the journal keeps it). → once every save this flush started or
  // left to finish has settled, with what follows them.
  const flushInk = async ({ keepalive = false, leaving = false } = {}) => {
    clearTimeout(inkTimerRef.current);
    inkTimerRef.current = 0;
    inkDirtySinceRef.current = 0;
    const away = keepalive || leaving;
    const flight = inkFlightRef.current;
    const flat = flattenBlocks(blocksRef.current);
    const waits = [];
    // the server holds the draft's base: the block names it, or the tree is behind this tab's own save of it
    const holdsBase = (d, block) => (block?.properties?.ink_url || "") === (d.base || "") || inkStore.treeBehind(d.id);
    const groups = inkStore.dirtyDrafts().map((d) => {
      const inTreeBlock = flat.find((b) => b.id === d.id) || null;
      if (inTreeBlock) seen.set(d.id, { block: inTreeBlock, keeps: keeps(d.id) });
      // a group never in a tree a flush read: as the tree has it (no block)
      const rec = seen.get(d.id) || { block: null, keeps: keeps(d.id) };
      const block = rec.block;
      const lane = lanes.get(d.id);
      // A group erased empty goes, unless its block holds a caption or
      // notes, or a comment thread hangs on it: those stay, with an empty
      // drawing an undo can refill.
      const remove = !d.ink.strokes.length && !(block?.content || "").trim() && !block?.children?.length && !rec.keeps;
      const debt = owed.get(d.id);
      const body = d.upload?.body || serializeInk(d.ink);
      const own = { ...d, block, lane, remove, mode: "save", inFlight: null,
        uploadBytes: debt ? (debt.uploaded ? 0 : bodyBytes(serializeInk(debt.ink), KEEPALIVE_CAP)) : bodyBytes(body, KEEPALIVE_CAP),
        // sized with the upload's name, or with one as long when it is not named yet
        putBytes: debt ? bodyBytes(debt.body)
          : bodyBytes(putBodyOf(d, d.upload?.url || "", batchLike), KEEPALIVE_CAP) + (d.upload ? 0 : inkStore.UPLOAD_NAME_CHARS),
        beside: !debt && leaving && !!d.upload && holdsBase(d, inTreeBlock) };
      if (!lane) return own;
      if (away && lane.ink !== d.ink && lane.phase === "upload") {
        lane.superseded = true;   // its PUT never goes: the newest drawing goes now
        return own;
      }
      if (lane.ink !== d.ink) lane.again = { keepalive: !!lane.again?.keepalive || away };
      waits.push(lane.done);
      if (!away) return { ...own, mode: "wait", inFlight: "waits" };
      // the page may go: the lane's requests again with keepalive, what is not on its way with keepalive already
      lane.ka = true;
      const g = { ...own, mode: "join", beside: false, remove: false };
      if (lane.phase === "delete") return { ...g, remove: true, inFlight: flight.deletes.has(lane.ink) ? "waits" : null };
      const upNeeded = !lane.put?.uploaded && !flight.uploads.has(lane.ink);
      const uploadBytes = bodyBytes(lane.snap?.upload?.body || serializeInk(lane.ink), KEEPALIVE_CAP);
      if (lane.put) {
        const putBytes = bodyBytes(lane.put.body);
        if (flight.puts.has(lane.ink)) return { ...g, inFlight: upNeeded ? "reupload" : "waits", uploadBytes };
        return upNeeded ? { ...g, beside: true, uploadBytes, putBytes } : { ...g, inFlight: "resend", putBytes };
      }
      // still uploading its drawing: leaving, its PUT beside that upload when it is named and the server holds its base
      if (leaving && lane.snap?.upload && holdsBase(lane.snap, inTreeBlock)) {
        const putBytes = bodyBytes(putBodyOf(lane.snap, lane.snap.upload.url, lane.batch));
        return upNeeded ? { ...g, beside: true, uploadBytes, putBytes } : { ...g, inFlight: "resend", putBytes };
      }
      return { ...g, inFlight: upNeeded ? "reupload" : "waits", uploadBytes };
    });
    // a flush the page will live past asks for no keepalive room
    const plan = planFlush(groups, keepalive ? keepaliveRoom("low") : -Infinity);
    const started = groups.map((g, i) => {
      if (!plan[i].send) return null;
      if (g.mode === "join") { joinLane(g, plan[i]); return null; }
      return startLane(g, plan[i], { leaving });
    });
    await Promise.all([...started, ...waits]);
  };
  // 700 ms after the last edit, but never more than 5 s after the first
  // unsaved one, so files land while writing goes on.
  function scheduleInk() {
    clearTimeout(inkTimerRef.current);
    const now = Date.now();
    if (!inkDirtySinceRef.current) inkDirtySinceRef.current = now;
    inkTimerRef.current = setTimeout(flushInk, Math.max(0, Math.min(700, inkDirtySinceRef.current + 5000 - now)));
  }
  // A save waits: on its timer, behind a group's save on its way, or as a
  // PUT a group owes — so going hidden and leaving the page flush them.
  const pending = () => !!inkTimerRef.current || owed.size > 0 || [...lanes.values()].some((l) => l.again);
  return { flushInk, scheduleInk, pending };
}
