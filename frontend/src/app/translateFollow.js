// What the app says about the PDF translation jobs pdf/translateState.js
// runs (docs/dev/translation.md): a background-tasks row per job, the pill
// while the paper on screen is being read for its brief, how a job ended,
// and once a session each that a faster model can be picked and that Export
// now writes the translation into the file. It follows every document's job,
// not only the one on screen: a job runs on when the reader leaves its PDF,
// and its row keeps counting and can still stop it. Free of
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
// yet); `langLabel` names the language in a job's row and `title()` the
// page on screen, which names the paper of a job on its PDF; `postPill`,
// `addTask`, `updateTask` and `setStatus` are App's; `retry()` asks the
// viewer again for the paragraphs the server could not translate in the
// document on screen; `rerun()` runs the last job asked for there again
// (one that stopped, a paragraph's); `translateReferences()` asks it for the
// reference lists left original there; `followsChat`: "Translate with"
// follows the chat model; `openSettings()` opens Settings → Language and Translation;
// `exportsTranslation()`: the open page's annotated PDF would carry the
// translation shown (the count the Export dialog reads); `openExport()` opens
// that dialog on Annotated PDF; `statusFree()`: no error or button holds the
// status pill. `source` is where the jobs are (translateState's `statuses`,
// `snapshot` and `halt`). Returns `sync()`, to call at every change of the
// jobs and of the PDF on screen.
export function createJobFollower(host, source = translateState) {
  const rows = new Map(); // url → {status: last seen, task: its row while the job runs}
  let reading = false; // the "Reading the paper" pill is up
  // The model hint, the references hint and the export hint, each once a
  // session.
  const hint = { judged: false, owed: false, refsSaid: false, exportSaid: false };
  return function sync() {
    const h = host();
    const kept = new Set();
    for (const [url, st] of source.statuses()) {
      kept.add(url);
      let row = rows.get(url);
      if (!row) rows.set(url, (row = { status: null, task: null }));
      if (row.status === st) continue;
      row.status = st;
      follow(host, source, url, st, row, hint);
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

// A job's row: what it translates (a paragraph, a page or the document), of
// which paper and into what. Jobs of several documents run side by side, so
// the row names the paper; a job starts on the PDF on screen, whose page
// names it.
export function jobName(st, url, h) {
  const title = url === h.screenUrl ? h.title?.() || "" : "";
  const vars = { page: /^p\.(\d+)$/.exec(st.label || "")?.[1], title, language: h.langLabel };
  if (vars.page && st.focus) {
    return title ? t("Translate a paragraph on p. {page} of “{title}” into {language}", vars)
      : t("Translate a paragraph on p. {page} into {language}", vars);
  }
  if (vars.page) {
    return title ? t("Translate p. {page} of “{title}” into {language}", vars) : t("Translate p. {page} into {language}", vars);
  }
  return title ? t("Translate “{title}” into {language}", vars) : t("Translate the document into {language}", vars);
}

// A document's job, at each new status: its row while it runs, then how it
// ended.
function follow(host, source, url, st, row, hint) {
  const h = host();
  if (st.running && !row.task) {
    row.task = h.addTask({
      name: jobName(st, url, h), kind: "ai", info: "0%", fraction: 0,
      cancel: () => source.halt(url),
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
  // request failed (said so: it was the request, not the text). A paragraph
  // asked for again (its menu's Translate again) whose new text did not come
  // keeps the one it had, and says so. "Try again" runs that job in the
  // viewer, so it is offered while the document is on screen; a stop because
  // there is no AI provider (503) offers the settings instead.
  const left = st.error == null ? st.untranslated || 0 : 0;
  const unchanged = st.error == null ? st.unchanged || 0 : 0;
  const finished = st.progress >= 0.999;
  const onScreen = () => host().screenUrl === url;
  const again = (run) => (onScreen() ? { label: t("Try again"), run: () => { if (onScreen()) run(); } } : undefined);
  // A job that did not do what it was asked ends as failed in the list, not
  // with a success's check: one that stopped, and one on a paragraph whose new
  // text did not come.
  const failed = st.error != null || unchanged > 0 || (left > 0 && !!st.focus);
  h.updateTask(row.task, { state: failed ? "failed" : "done", info: st.error != null ? stoppedAt(st)
    : left ? tn("{n} paragraph untranslated", "{n} paragraphs untranslated", left)
      : unchanged ? t("earlier translation kept")
        : finished ? t("Done") : stoppedAt(st) });
  row.task = null;
  if (st.error != null) {
    h.setStatus(st.error ? t("Translation failed: {message}", { message: st.error }) : t("Translation failed."),
      st.refused === 503 ? { label: t("Open settings"), run: () => host().openSettings() } : again(() => host().rerun()));
  } else if (left && st.focus) {
    h.setStatus(t("This paragraph could not be translated."), again(() => host().rerun()));
  } else if (left) {
    h.setStatus(st.unanswered
      ? tn("{n} paragraph could not be translated (the translation request failed).",
        "{n} paragraphs could not be translated (the translation request failed).", left)
      : tn("{n} paragraph could not be translated and still shows the original.",
        "{n} paragraphs could not be translated and still show the original.", left),
    again(() => host().retry()));
  } else if (unchanged) {
    h.setStatus(t("This paragraph could not be translated again; it keeps its earlier translation."), again(() => host().rerun()));
  }
  // The session's first job that reached the model decides whether the
  // model hint is owed; it is said at the end of a job with nothing else to
  // say, once.
  if (!hint.judged && st.medianMs != null) {
    hint.judged = true;
    hint.owed = !!h.followsChat && st.medianMs > SLOW_MS;
  }
  // The reference lists stay original, which the reader can ask to change.
  // A job that found nothing else to do says so every time: the click would
  // look dead otherwise. Any other job says so once a session, on the PDF on
  // screen, unless the pill holds an error or a button.
  const refsLeft = st.error == null && !left && st.refsLeft > 0 && onScreen();
  const sayRefs = (msg) => {
    hint.refsSaid = true;
    h.setStatus(msg, { label: t("Translate references too"), run: () => { if (onScreen()) host().translateReferences(); } });
  };
  if (refsLeft && st.refsOnly) {
    sayRefs(t("Nothing else to translate here: the references stay in the original."));
  } else if (hint.owed && st.error == null && !left && !unchanged) {
    hint.owed = false;
    h.setStatus(t("Translation follows the chat model; a faster model can be picked in Settings → Language and Translation."),
      { label: t("Open settings"), run: () => host().openSettings() });
  } else if (refsLeft && !hint.refsSaid && h.statusFree()) {
    sayRefs(t("The references stay in the original."));
  } else if (!hint.exportSaid && st.error == null && !left && finished && onScreen()
    && h.statusFree() && h.exportsTranslation()) {
    // Export writes the open page, so this waits for a job that finished on
    // the PDF on screen. A job with something else to say, or a pill already
    // holding an error or a button, leaves it to a later one.
    hint.exportSaid = true;
    h.setStatus(t("Translation finished. Export now to get a PDF with the translation in it."),
      { label: t("Export…"), run: () => { if (onScreen()) host().openExport(); } });
  }
}
