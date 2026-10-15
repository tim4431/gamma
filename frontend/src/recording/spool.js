// Where a recording's audio waits before it is uploaded: IndexedDB, one row
// per chunk the recorder delivers (about one a second), keyed by recording
// id, segment number and chunk number, with one row per segment saying
// where it belongs. A segment's rows go when it is uploaded and the block
// knows it; rows still there when the app starts are a recording a closed
// or crashed tab left behind, offered for recovery (recording/useRecorder.js).
// Everything here tolerates a browser without IndexedDB (a private window,
// an old WebKit): the recorder then keeps chunks in memory only.
const DB = "gamma-recording";
const CHUNKS = "chunks";     // {key: "<rec>/<seg>/<chunk>", rec, seg, n, blob}
const SEGMENTS = "segments"; // {key: "<rec>/<seg>", rec, seg, started, mime, ws, page, block}

let opening = null;

function open() {
  if (opening) return opening;
  opening = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") { resolve(null); return; }
    let req;
    try { req = indexedDB.open(DB, 1); } catch { resolve(null); return; }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(CHUNKS)) db.createObjectStore(CHUNKS, { keyPath: "key" }).createIndex("seg", ["rec", "seg"]);
      if (!db.objectStoreNames.contains(SEGMENTS)) db.createObjectStore(SEGMENTS, { keyPath: "key" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
  return opening;
}

function run(store, mode, fn) {
  return open().then((db) => new Promise((resolve, reject) => {
    if (!db) { resolve(undefined); return; }
    let tx;
    try { tx = db.transaction(store, mode); } catch (err) { reject(err); return; }
    const req = fn(tx.objectStore(store));
    tx.oncomplete = () => resolve(req?.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("aborted"));
  }));
}

const segKey = (rec, seg) => `${rec}/${seg}`;
const pad = (n) => String(n).padStart(6, "0");

export async function openSegment(rec, seg, meta) {
  await run(SEGMENTS, "readwrite", (s) => s.put({ key: segKey(rec, seg), rec, seg, ...meta }));
}

export async function putChunk(rec, seg, n, blob) {
  await run(CHUNKS, "readwrite", (s) => s.put({ key: `${segKey(rec, seg)}/${pad(n)}`, rec, seg, n, blob }));
}

// The chunks of a segment in order (a Blob of them is the segment's file).
export async function chunksOf(rec, seg) {
  const rows = await run(CHUNKS, "readonly", (s) => s.index("seg").getAll([rec, seg]));
  return (rows || []).sort((a, b) => a.n - b.n).map((r) => r.blob);
}

export async function dropSegment(rec, seg) {
  const rows = await run(CHUNKS, "readonly", (s) => s.index("seg").getAllKeys([rec, seg]));
  await run(CHUNKS, "readwrite", (s) => { for (const key of rows || []) s.delete(key); });
  await run(SEGMENTS, "readwrite", (s) => s.delete(segKey(rec, seg)));
}

export async function dropRecording(rec) {
  const segments = (await run(SEGMENTS, "readonly", (s) => s.getAll())) || [];
  for (const row of segments) if (row.rec === rec) await dropSegment(rec, row.seg);
}

// Every segment still spooled, oldest first: what a start of the app finds
// left behind. → [{rec, seg, started, mime, ws, page, block}]
export async function leftBehind() {
  const rows = (await run(SEGMENTS, "readonly", (s) => s.getAll())) || [];
  return rows.sort((a, b) => a.started - b.started || a.seg - b.seg);
}
