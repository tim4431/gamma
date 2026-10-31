// App's side of the PDF translated view (docs/dev/translation.md): the
// requests a job goes out as (each chunk, the paper's brief), forgetting a
// document's translation, and following the jobs pdf/translateState.js runs
// (app/translateFollow.js says what of them: a row per job, the reading
// pill, how a job ended, the model hint).
import { useEffect, useRef, useState } from "react";
import { API, apiJson, readNdjson } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { requestFields } from "../pdf/translatePlan.js";
import * as translateState from "../pdf/translateState.js";
import { createJobFollower } from "./translateFollow.js";

// POST a translation request; throws on failure with the server's reason (a
// 429 of the shared AI allowance says what to do), its HTTP `status` and
// `retryAfter` (seconds: the account's AI calls are all in use), which
// translatePlan.requestChunk reads to ask again.
async function post(path, body, signal) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    signal,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw Object.assign(new Error(detail), { status: res.status, retryAfter: Number(res.headers.get("Retry-After")) || 0 });
  }
  return res;
}

// The translation settings every request sends. The settings a request is
// made with are {lang, model, effort, speed, keep}: `keep` lets the server
// keep its translations and the paper's brief in the workspace (Settings →
// Language and Translation › Keep translations).
const modelFields = ({ lang, model, effort, speed }) => ({ lang, model: model || "", effort: effort || "", speed: speed || "" });

// The request for one chunk of paragraphs under the translation settings,
// resolving to the server's final reply ({translations, failed, brief, …}):
// a single provider call. The chunking, queueing and parallelism are the
// job's (pdf/translateJob.js); a job keeps the requests it started with, so
// its replies match the language and model its entries are kept under.
// When it throws (`post`), the job asks again (translatePlan.requestChunk)
// and reports a request that still fails; the selection popup shows the
// reason. `signal` is the job's AbortController: halting cancels the
// requests still in flight.
// The server streams NDJSON: `{i: [indices], text}` as the model writes
// each paragraph (→ onPartial, the viewer types it onto the page), then the
// final `{translations}` object.
// `context` is the viewer's for this request (pdf/translatePlan.js
// requestFields): the paper (its page, or the document's opening text when
// there is no page), whether to wait for the paper's brief while it is
// being made, and the text around the chunk. `onStage("brief")` while the
// request waits for that brief, then `onStage("translate")`.
export function chunkRequest(settings) {
  return async function translateChunk(texts, signal, onPartial, context, onStage) {
    const res = await post("/ai/translate", {
      texts, ...modelFields(settings), stream: true, ...requestFields({ ...context, keep: settings.keep }),
    }, signal);
    let final = null;
    await readNdjson(res, (events) => {
      for (const ev of events) {
        if (ev.error) throw new Error(ev.error);
        else if (ev.translations) final = ev;
        else if (ev.stage) onStage?.(ev.stage);
        else if (ev.i && onPartial) for (const i of ev.i) onPartial(i, ev.text);
      }
    });
    return final;
  };
}

// The request for the paper's brief (POST /api/ai/translate/brief), which a
// job sends as it starts and does not wait for: the server makes the brief,
// finds it kept, or waits for the request already making it. `paper` is
// {pageId, head}: the page, and the document's opening text when the server
// has not stored its PDF. `onStage` hears the stream's "stage" lines (the
// first, `brief`, while it is being made). Resolves to {ready, error?};
// throws as a chunk's request does.
export function briefRequest(settings) {
  return async function translateBrief({ pageId = "", head = "" }, signal, onStage) {
    const res = await post("/ai/translate/brief", {
      ...(pageId ? { page_id: pageId } : {}), ...(head ? { head } : {}), ...modelFields(settings), keep: settings.keep !== false,
    }, signal);
    let final = null;
    await readNdjson(res, (events) => {
      for (const ev of events) {
        if (ev.stage) onStage?.(ev.stage);
        else if ("ready" in ev) final = ev;
      }
    });
    return final;
  };
}

// "Forget this translation" (the translate button's option menu) for the
// document at `url`, whose stored PDF is `docId`, shown on page `pageId`:
// the server forgets its kept translations and brief in every language and
// model, and what it still had in memory (under the page, for a PDF opened
// from its link without a stored copy); then the viewer drops the
// document's entries, so the next click translates afresh.
export async function forgetTranslation(url, docId, pageId, setStatus) {
  const page = pageId ? `?page=${encodeURIComponent(pageId)}` : "";
  try {
    await apiJson(`${API}/translations/${encodeURIComponent(docId)}${page}`, { method: "DELETE" });
  } catch (err) {
    setStatus(t("Couldn't forget the translation: {message}", { message: err.message }));
    return;
  }
  translateState.forget(url);
  setStatus(t("Translation forgotten"));
}

// Settings › Clear kept translations: the server forgets every translation
// and brief the workspace keeps, and what it still had in memory; then the
// viewer drops every document's entries, as "Forget this translation" drops
// one's, so the next click translates afresh. Resolves to the server's
// {removed}; throws as apiJson does, leaving the viewer as it was.
export async function clearTranslations() {
  const res = await apiJson(`${API}/translations`, { method: "DELETE" });
  translateState.forgetAll();
  return res;
}

// The session's end (logout, another account or workspace): the jobs stop,
// their requests in flight aborted, and every document's translation goes
// (translateState.reset). A job outlives its viewer, so leaving the PDF does
// not stop it; leaving the session must, or its requests would go on under
// the next account's cookie and workspace.
export function endTranslations() {
  translateState.reset();
}

// Follow the jobs of every document (app/translateFollow.js): `props` is
// App's side at each render, which the follower reads when a job changes —
// `screenUrl` (the PDF on screen), `langLabel`, `postPill`, `addTask`,
// `updateTask`, `setStatus`, `retry`, `followsChat` and `openSettings`, as
// createJobFollower takes them.
export function useTranslateJobs(props) {
  const host = useRef(null);
  host.current = props;
  const [sync] = useState(() => createJobFollower(() => host.current));
  useEffect(() => {
    sync();
    return translateState.subscribe(sync);
  }, [sync]);
  // The pill follows the PDF on screen.
  useEffect(() => sync(), [props.screenUrl, sync]);
}
