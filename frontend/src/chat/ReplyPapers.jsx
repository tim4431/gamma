// "Save to library" under a reply: the papers it read or named
// (chat/chatPapers.js), each with a checkbox, saved together into the
// folder the chat is about through POST /api/clip — the ingest Gamma
// Connector's Save uses, so the page gets the PDF, its metadata and the
// usual dedup. A PDF only the user's browser could get (a handoff's) is
// stored from the server's copy first (POST /api/ai/handoffs/<id>/store).
// Collapsed to one pill until opened; opening it asks the library which
// papers it holds already and the registry for titles the reply left out.
import React, { useEffect, useMemo, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { CheckIcon, ChevronDownIcon, ChevronUpIcon, FilePlusIcon, PaperIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";
import { clipPayload, lookupQuery, replyPapers } from "./chatPapers.js";

const MAX_LOOKED_UP = 30;

export default function ReplyPapers({ actions, text, folder = "", options = {}, onOpenPage, onLibraryChange }) {
  const papers = useMemo(() => replyPapers(actions, text), [actions, text]);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState(() => new Set());
  // key → {state: "in" | "saving" | "saved" | "error", page, note, pdf, error}
  const [status, setStatus] = useState({});
  const [titles, setTitles] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    for (const p of papers.slice(0, MAX_LOOKED_UP)) {
      apiJson(`${API}/library/lookup?${lookupQuery(p)}`)
        .then((hit) => {
          if (!live || !hit?.block_id) return;
          setStatus((s) => (s[p.key] ? s : { ...s, [p.key]: { state: "in", page: hit.block_id } }));
        })
        .catch(() => {});
      if (!p.title && (p.doi || p.arxiv)) {
        const q = new URLSearchParams({ ...(p.doi ? { doi: p.doi } : {}), ...(p.arxiv ? { arxiv_id: p.arxiv } : {}) });
        apiJson(`${API}/library/preview?${q}`)
          .then((rec) => { if (live && rec?.title) setTitles((m) => ({ ...m, [p.key]: rec.title })); })
          .catch(() => {});
      }
    }
    return () => { live = false; };
  }, [open, papers]);

  if (!papers.length) return null;

  const saved = (p) => ["in", "saved"].includes(status[p.key]?.state);
  const choosable = papers.filter((p) => !saved(p) && status[p.key]?.state !== "saving");
  const chosen = papers.filter((p) => picked.has(p.key) && !saved(p));
  const toggle = (key) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  async function save() {
    setBusy(true);
    for (const p of chosen) {
      setStatus((s) => ({ ...s, [p.key]: { state: "saving" } }));
      try {
        let docId = "";
        if (p.request) {
          try {
            docId = (await apiJson(`${API}/ai/handoffs/${encodeURIComponent(p.request)}/store`, { method: "POST" })).doc_id || "";
          } catch {} // not delivered, or no longer held: the server fetches it itself
        }
        const out = await apiJson(`${API}/clip`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(clipPayload({ ...p, title: p.title || titles[p.key] || "" }, { ...options, folder, docId })),
        });
        setStatus((s) => ({ ...s, [p.key]: { state: "saved", page: out.block_id, pdf: !!out.doc_id,
                                             existed: !!out.existed, note: out.note || "" } }));
      } catch (err) {
        setStatus((s) => ({ ...s, [p.key]: { state: "error", error: err.message || t("Save failed") } }));
      }
    }
    setPicked(new Set());
    setBusy(false);
    onLibraryChange?.();
  }

  const where = folder ? folder.split("/").pop() : "";
  return (
    <div className="chatPapersWrap">
      <button type="button" className="chatPill chatPapersPill" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        title={open ? t("Hide the papers") : t("Choose papers from this reply to save to your library")}>
        <PaperIcon size={14} />
        <span className="chatPillText">
          {tn("{n} paper in this reply", "{n} papers in this reply", papers.length)} · {t("Save to library")}
        </span>
        {open ? <ChevronUpIcon size={14} /> : <ChevronDownIcon size={14} />}
      </button>
      {open ? (
        <div className="chatPapers" role="group" aria-label={t("Papers in this reply")}>
          {papers.map((p) => {
            const st = status[p.key] || {};
            const title = p.title || titles[p.key] || p.source;
            const kind = p.read ? (p.pdf ? t("PDF read") : t("Page read"))
              : p.blocked ? t("Needed your browser") : t("Linked");
            return (
              <div key={p.key} className={`chatPaper${saved(p) ? " saved" : ""}`}>
                <input type="checkbox" className="chatPaperCheck" aria-label={title}
                  checked={saved(p) || picked.has(p.key)} disabled={saved(p) || busy}
                  onChange={() => toggle(p.key)} />
                <span className="chatPaperText">
                  <span className="chatPaperTitle" title={title}>{title}</span>
                  <span className="chatPaperMeta" title={p.source}>{kind} · {p.source}</span>
                  {st.state === "saved" && st.note ? <span className="chatPaperNote">{st.note}</span> : null}
                  {st.state === "error" ? <span className="chatPaperError" role="alert">{st.error}</span> : null}
                </span>
                <span className="chatPaperState">
                  {st.state === "saving" ? <span className="transferSpin inline" aria-label={t("Saving…")} /> : null}
                  {saved(p) ? (
                    <button type="button" className="chatChangeLink" onClick={() => onOpenPage?.(st.page)}
                      title={t("Open its page")}>
                      <CheckIcon size={12} />
                      {st.state === "in" || st.existed ? t("In library") : st.pdf ? t("Saved") : t("Saved without PDF")}
                    </button>
                  ) : null}
                </span>
              </div>
            );
          })}
          <div className="chatPapersFoot">
            <button type="button" className="uiBtn sm ghost" disabled={busy || !choosable.length}
              onClick={() => setPicked(chosen.length === choosable.length ? new Set() : new Set(choosable.map((p) => p.key)))}>
              {chosen.length === choosable.length && choosable.length ? t("Select none") : t("Select all")}
            </button>
            <button type="button" className="uiBtn sm primary" disabled={busy || !chosen.length} onClick={save}
              title={folder ? t("Saved into {folder}", { folder }) : t("Saved into your library")}>
              <FilePlusIcon size={14} />
              {where
                ? tn("Save {n} paper to {folder}", "Save {n} papers to {folder}", chosen.length, { folder: where })
                : tn("Save {n} paper to library", "Save {n} papers to library", chosen.length)}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
