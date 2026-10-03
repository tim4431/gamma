// A PDF's upload: one multipart POST /api/uploads for a file of up to
// PART_BYTES, else in parts — POST /api/uploads/parts opens it, the parts
// follow one request each, finish stores the whole (gamma/upload_parts.py).
// A proxy in front of the server caps a request's body (Cloudflare at
// 100 MB) and a part stays well under that. Each part is an xhrUpload
// reporting its bytes into the whole's progress; a part the network lost
// is sent again from the byte the server says it holds (its 409 carries
// `received`), up to RETRIES times in a row, while an answer from the
// server (400, 413, 507) is final. Resolves to the same {doc_id,
// source_url, size, already_existed} as the single request; rejects like
// xhrUpload (`aborted: true` after the stop button or `signal`), having
// told the server to drop the unfinished upload. The web app's PDF ingest
// (App.jsx resolvePdfSource) goes through here; the browser extension
// speaks the protocol itself (extension/worker.js uploadBlob).
import { API, apiJson } from "./utils";
import { xhrUpload } from "./xhrUpload";

export const PART_BYTES = 32 * 1024 * 1024;
const RETRIES = 3;
const RETRY_WAIT_MS = 1500;

export function uploadPdf(file, filename, options = {}) {
  if (file.size <= PART_BYTES) {
    const form = new FormData();
    form.append("file", file, filename);
    return xhrUpload(`${API}/uploads`, form, options);
  }
  return uploadInParts(file, filename, options);
}

function stopped() {
  const err = new Error("stopped");
  err.aborted = true;
  err.name = "AbortError";
  return err;
}

const wait = (ms, signal) => new Promise((resolve, reject) => {
  const stop = () => { clearTimeout(timer); reject(stopped()); };
  const timer = setTimeout(() => { signal?.removeEventListener("abort", stop); resolve(); }, ms);
  signal?.addEventListener("abort", stop, { once: true });
});

async function uploadInParts(file, filename, { onProgress, onProcessing, onAbortable, signal } = {}) {
  if (signal?.aborted) throw stopped();
  const ctl = new AbortController();
  const stop = () => ctl.abort();
  signal?.addEventListener("abort", stop, { once: true });
  onAbortable?.(stop);
  // the small JSON calls (open, finish), stopped with the rest
  const post = (url, body) => apiJson(url, {
    method: "POST",
    signal: ctl.signal,
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  }).catch((err) => { throw err?.name === "AbortError" ? stopped() : err; });
  let token = "";
  try {
    const opened = await post(`${API}/uploads/parts`, { size: file.size, name: filename });
    token = opened.token;
    const partBytes = Math.max(1, Math.min(PART_BYTES, opened.part_bytes || PART_BYTES));
    let offset = 0;
    let failures = 0;
    while (offset < file.size) {
      const form = new FormData();
      form.append("offset", String(offset));
      form.append("part", file.slice(offset, Math.min(offset + partBytes, file.size)), "part");
      try {
        const reply = await xhrUpload(`${API}/uploads/parts/${token}`, form, {
          signal: ctl.signal,
          onProgress: (loaded) => onProgress?.(Math.min(offset + loaded, file.size), file.size),
        });
        offset = reply.received;
        failures = 0;
      } catch (err) {
        if (err.aborted || ++failures > RETRIES) throw err;
        if (err.status === 409 && typeof err.data?.received === "number") { offset = err.data.received; continue; }
        if (err.status) throw err; // the server answered: final
        await wait(RETRY_WAIT_MS, ctl.signal); // the network failed: the same part again
        continue;
      }
      onProgress?.(offset, file.size);
    }
    onProcessing?.();
    return await post(`${API}/uploads/parts/${token}/finish`);
  } catch (err) {
    if (token) fetch(`${API}/uploads/parts/${token}`, { method: "DELETE", credentials: "include", keepalive: true }).catch(() => {});
    throw err;
  } finally {
    signal?.removeEventListener("abort", stop);
  }
}
