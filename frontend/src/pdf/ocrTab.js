// The engine a window reads a scan's pages with (docs/dev/pdf_loading.md
// "Recognized words"; docs/dev/frontend-refactor.md "Lazy boundaries"):
// tesseract.js and what pdf/ocrLoop.js's run is handed in the browser. A
// chunk of its own, reached only by pdf/ocrRuns.js's import() when a run
// starts, so no other screen fetches or parses it.
//
// Every file comes from this server: the worker script and the one core
// this build ships (the single-file SIMD LSTM build, its WASM inlined, so no
// .wasm is fetched beside it) are content-hashed assets (?url, sent
// immutable like the pdf.js worker), and the models are the server's own
// (the capability's /api/ocr/models/<tag>); tesseract.js would fetch every
// one of them from a CDN otherwise. Their notices are in
// backend/tessdata/NOTICE, which ships wherever the models do.
//
// A page is drawn on a canvas of its own, as the server draws it for its
// engine: intent "print" (a display render waits for animation frames,
// which a hidden window never gets), without annotations, at ocrRaster's
// zoom, its optional content the print intent's (pdf.js refuses a render
// whose optional content was read for another intent, and the viewer sets
// none of its own); read back to a grey PGM and the canvas released before
// the engine reads it. The stored file is opened apart from the viewer's
// copy (PdfViewer's openPdfDocument, by ranges when it is large), so a run
// goes on while the reader is elsewhere and frees a page's resources as it
// goes. What Tesseract prints of its work (the diacritics it found, say)
// goes nowhere (ocrRaster's `debug_file`), not to the console as errors.

import Tesseract from "tesseract.js/dist/tesseract.esm.min.js";
import workerPath from "tesseract.js/dist/worker.min.js?url";
import corePath from "tesseract.js-core/tesseract-core-simd-lstm.wasm.js?url";
import { storedName, uploadUrl } from "../documents/pageDocument.js";
import { canvasLimits } from "../shared/lib/canvasSize.js";
import { apiJson } from "../shared/lib/utils";
import { getJob, startJob } from "../tasks/tasksApi";
import { browserEnv, runWindow } from "./ocrLoop.js";
import { hasSimd, pageDpi, toPgm, workerOptions, zoomPlan } from "./ocrRaster.js";
import { loadPdfEngine, openPdfDocument } from "./PdfViewer";

// The run pdf/ocrRuns.js starts: `runWindow`'s, its server reached through
// the tasks store (`tasks`, so the job is the tray's at once) and the OCR
// routes, its engine this browser's.
export function run({ docId, pageId, capability, client, tasks, meta, signal, report }) {
  const at = (path) => `/api/ocr/${encodeURIComponent(docId)}${path}`;
  const send = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  const api = {
    start: (body) => (tasks?.start ? tasks.start("ocr", body, meta) : startJob("ocr", body)),
    job: (id) => getJob(id),
    plan: () => apiJson(at("/plan"), { signal }),
    put: (n, body) => apiJson(at(`/pages/${n}`), send("PUT", body)),
    done: (body) => apiJson(at("/done"), send("POST", body)),
  };
  const engine = {
    supported: () => hasSimd() && typeof Worker === "function",
    open: () => openPdfDocument(uploadUrl(storedName(docId, ".pdf")), { ranged: true }),
    raster,
    worker: () => startWorker(capability.models),
  };
  return runWindow({ docId, pageId, capability, client, signal, report }, { api, engine, env: browserEnv() });
}

// Page `n` of `doc` drawn for the engine, as `render` (the plan's) says:
// {pgm, width, height, dpi}. The canvas and the page's resources go before
// it returns. `signal` calls it off (the page ran out of time, the run
// stopped): a drawing under way is cancelled and its canvas released at
// once, and one whose page came only after that draws nothing, so pages
// that waited behind a stalled one do not each hold a canvas.
async function raster(doc, n, render, signal) {
  const lib = await loadPdfEngine();
  const page = await doc.getPage(n);
  const canvas = document.createElement("canvas");
  let task = null;
  const cancel = () => task?.cancel();
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    if (signal?.aborted) throw new Error("called off");
    const own = page.getViewport({ scale: 1 });
    const plan = zoomPlan(own.width, own.height, canvasLimits(), render);
    if (!plan) throw new Error("the page has no size");
    canvas.width = plan.width;
    canvas.height = plan.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("PDF canvas allocation failed");
    const viewport = page.getViewport({ scale: plan.zoom });
    task = page.render({
      canvasContext: ctx, viewport, intent: "print", annotationMode: lib.AnnotationMode.DISABLE,
      transform: [plan.width / viewport.width, 0, 0, plan.height / viewport.height, 0, 0],
    });
    await task.promise;
    if (signal?.aborted) throw new Error("called off");
    const pgm = toPgm(plan.width, plan.height, (y, rows) => ctx.getImageData(0, y, plan.width, rows).data);
    return { pgm, width: plan.width, height: plan.height, dpi: pageDpi(plan.width, own.width, page.userUnit) };
  } finally {
    signal?.removeEventListener("abort", cancel);
    canvas.width = 0;
    canvas.height = 0;
    page.cleanup();
  }
}

// One tesseract.js engine with the server's models (`models`): {ready, a
// promise of it once its core and models are in; recognize(pgm, dpi), the
// page's TSV; terminate()}. tesseract.js leaves createWorker pending for
// ever when a model fails to load or the engine does not start, telling
// only the errorHandler, and hands its Web Worker over only once ready: the
// one it spawns is caught as it is made (createWorker spawns it before its
// first await), so a worker that never got ready can be ended too. Every
// call is raced against the errorHandler's report.
function startWorker(models) {
  const { langs, oem, options, parameters } = workerOptions({ models, workerPath, corePath });
  let broken;
  const fault = new Promise((_, reject) => { broken = reject; });
  fault.catch(() => {});
  const errorHandler = (e) => broken(e instanceof Error ? e : new Error(String(e)));
  let spawned = null, worker = null, ended = false;
  const Native = globalThis.Worker;
  let made;
  globalThis.Worker = class extends Native {
    constructor(...args) {
      super(...args);
      spawned = this;
    }
  };
  try {
    made = Tesseract.createWorker(langs, oem, { ...options, errorHandler, logger: () => {} });
  } finally {
    globalThis.Worker = Native;
  }
  const handle = {
    ready: Promise.race([made, fault]).then(async (w) => {
      worker = w;
      if (ended) {
        w.terminate();
        throw new Error("ended");
      }
      await Promise.race([w.setParameters(parameters), fault]);
      return handle;
    }),
    async recognize(pgm, dpi) {
      await Promise.race([worker.setParameters({ user_defined_dpi: String(dpi) }), fault]);
      const { data } = await Promise.race([worker.recognize(pgm, {}, { text: false, tsv: true }), fault]);
      return data?.tsv || "";
    },
    terminate() {
      ended = true;
      try {
        if (worker) worker.terminate();
        else spawned?.terminate();
      } catch { /* gone already */ }
    },
  };
  handle.ready.catch(() => {});
  return handle;
}
