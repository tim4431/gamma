// What the app says about the PDF translation jobs pdf/translateState.js
// runs (docs/dev/translation.md): a background-tasks row per job, the pill
// while the paper on screen is being read for its brief, how a job ended,
// and once a session that a faster model can be picked. It follows every
// document's job, not only the one on screen: a job runs on when the reader
// leaves its PDF, and its row keeps counting and can still stop it. Free of
// React, so it runs in Node under the tests; app/translateClient.js's
// useTranslateJobs drives it.
import { t, tn } from "../shared/i18n/i18n.js";
import * as translateState from "../pdf/translateState.js";

// A median request slower than this, while translation follows the chat
// model, is worth a hint that a faster model can be picked.
export const SLOW_MS = 20000;

// `host()` is App's side as it is now: `screenUrl`, the PDF on screen, whose
// job alone may put up the reading pill (a paper's first requests wait
// while the server reads it for its brief: the pill says why nothing lands
// yet); `langLabel` names the language in a job's row; `postPill`,
// `addTask`, `updateTask` and `setStatus` are App's; `retry()` asks the
// viewer again for the paragraphs the server could not translate in the
// document on screen; `followsChat`: "Translate with" follows the chat
// model; `openSettings()` opens Settings → Language and Translation.
// `source` is where the jobs are (translateState's `statuses` and
// `snapshot`). Returns `sync()`, to call at every change of the jobs and of
// the PDF on screen.
export function createJobFollower(host, source = translateState) {
  const rows = new Map(); // url → {status: last seen, task: its row while the job runs}
  let reading = false; // the "Reading the paper" pill is up
  const hint = { judged: false, owed: false }; // the model hint, once a session
  return function sync() {
    const h = host();
    const kept = new Set();
    for (const [url, st] of source.statuses()) {
      kept.add(url);
      let row = rows.get(url);
      if (!row) rows.set(url, (row = { status: null, task: null }));
      if (row.status === st) continue;
      row.status = st;
      follow(host, url, st, row, hint);
    }
    // A document dropped while its job ran (translateState.reset, at the
    // session's end): its job was halted, and its row closes as stopped.
    for (const [url, row] of rows) {
      if (kept.has(url)) continue;
      if (row.task) h.updateTask(row.task, { state: "done", info: stoppedAt(row.status) });
      rows.delete(url);
    }
    const st = source.snapshot(h.screenUrl).status;
    const now = !!(st.running && st.reading);
    if (now !== reading) {
      reading = now;
      h.postPill("translate", now
        ? { msg: t("Reading the paper first, so its terms stay the same throughout…"), spinner: true }
        : null);
    }
  };
}

const stoppedAt = (st) => t("stopped at {progress}%", { progress: Math.round((st?.progress || 0) * 100) });

// A document's job, at each new status: its row while it runs, then how it
// ended.
function follow(host, url, st, row, hint) {
  const h = host();
  if (st.running && !row.task) {
    row.task = h.addTask({
      name: `Translate ${st.label} → ${h.langLabel}`, kind: "ai", info: "0%", fraction: 0,
      cancel: () => translateState.halt(url),
    });
  }
  if (!row.task) return;
  if (st.running) {
    h.updateTask(row.task, {
      info: st.reading ? t("reading the paper…") : `${Math.round(st.progress * 100)}%`, fraction: st.progress,
    });
    return;
  }
  // The job's end: stopped by requests that kept failing (the last one's
  // reason), or done with paragraphs left in the original, which the next
  // job asks for again: those the model could not translate, or those whose
  // request failed (said so: it was the connection to the provider, not
  // the text). "Try again" runs that job in the viewer, so it is offered
  // while the document is on screen.
  const left = st.error == null ? st.untranslated || 0 : 0;
  h.updateTask(row.task, { state: "done", info: st.error != null ? stoppedAt(st)
    : left ? tn("{n} paragraph untranslated", "{n} paragraphs untranslated", left)
      : st.progress >= 0.999 ? "100%" : stoppedAt(st) });
  row.task = null;
  if (st.error != null) {
    h.setStatus(st.error ? t("Translation failed: {message}", { message: st.error })
      : t("Translation failed — click the button to retry."));
  } else if (left) {
    const onScreen = () => host().screenUrl === url;
    h.setStatus(st.unanswered
      ? tn("{n} paragraph could not be translated (the connection to the AI provider failed).",
        "{n} paragraphs could not be translated (the connection to the AI provider failed).", left)
      : tn("{n} paragraph could not be translated and still shows the original.",
        "{n} paragraphs could not be translated and still show the original.", left),
    onScreen() ? { label: t("Try again"), run: () => { if (onScreen()) host().retry(); } } : undefined);
  }
  // The session's first job that reached the model decides whether the
  // model hint is owed; it is said at the end of a job with nothing else to
  // say, once.
  if (!hint.judged && st.medianMs != null) {
    hint.judged = true;
    hint.owed = !!h.followsChat && st.medianMs > SLOW_MS;
  }
  if (hint.owed && st.error == null && !left) {
    hint.owed = false;
    h.setStatus(t("Translation follows the chat model; a faster model can be picked in Settings → Language and Translation."),
      { label: t("Open settings"), run: () => host().openSettings() });
  }
}
