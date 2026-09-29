// A paper a reply fetched, shown under it (FetchHandoffCards.jsx lists them
// in call order, chat/chatPapers.js has the rules): a row for a document
// fetch_paper read, and the "Add to library" button that row and a
// handoff card whose PDF arrived both carry. The button first asks the
// library whether it holds the paper (GET /api/library/lookup); adding
// stores a PDF the user's browser delivered (POST /api/ai/handoffs/<id>/store)
// and saves through POST /api/clip into the folder the chat is about.
import React, { useEffect, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { CheckIcon, FileTextIcon, PlusIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";
import { clipPayload, lookupQuery } from "./chatPapers.js";

// One question per paper per page load: a long chat shows many of them.
const lookups = new Map(); // lookupQuery → Promise<{block_id} | null>
const records = new Map(); // "doi" / "arxiv_id" query → Promise<registry title>

function inLibrary(paper) {
  const q = lookupQuery(paper);
  if (!lookups.has(q)) lookups.set(q, apiJson(`${API}/library/lookup?${q}`).catch(() => null));
  return lookups.get(q);
}

// The registry's title for a paper the fetch knew only by its identifier
// (asked only while `wanted`).
export function useRecordTitle(paper, wanted = true) {
  const [title, setTitle] = useState("");
  useEffect(() => {
    if (!wanted || paper.title || !(paper.doi || paper.arxiv)) return undefined;
    const q = new URLSearchParams(paper.arxiv ? { arxiv_id: paper.arxiv } : { doi: paper.doi }).toString();
    if (!records.has(q)) records.set(q, apiJson(`${API}/library/preview?${q}`).then((r) => r?.title || "", () => ""));
    let live = true;
    records.get(q).then((found) => { if (live) setTitle(found); });
    return () => { live = false; };
  }, [wanted, paper.title, paper.doi, paper.arxiv]);
  return paper.title || title;
}

// `save`: {folder, options: {allowOa, saveCopy, fetchMetadata}, onOpenPage,
// onLibraryChange} from the chat; `title` names the paper to screen readers
// and becomes the page's title until its metadata arrives.
export function AddToLibrary({ paper, title, save }) {
  const [state, setState] = useState({ step: "" }); // "" | "saving" | "in" | "error"
  useEffect(() => {
    let live = true;
    inLibrary(paper).then((hit) => {
      if (live && hit?.block_id) setState((s) => (s.step ? s : { step: "in", page: hit.block_id }));
    });
    return () => { live = false; };
  }, [paper]);

  async function add() {
    setState({ step: "saving" });
    try {
      let docId = "";
      if (paper.request) {
        try {
          docId = (await apiJson(`${API}/ai/handoffs/${encodeURIComponent(paper.request)}/store`, { method: "POST" })).doc_id || "";
        } catch {} // not delivered, or no longer held: the server fetches it itself
      }
      const out = await apiJson(`${API}/clip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(clipPayload({ ...paper, title: paper.title || title || "" },
          { ...save.options, folder: save.folder, docId })),
      });
      lookups.set(lookupQuery(paper), Promise.resolve(out));
      setState({ step: "in", page: out.block_id, note: out.doc_id ? out.note || "" : out.note || t("Saved without its PDF.") });
      save.onLibraryChange?.();
    } catch (err) {
      setState({ step: "error", error: err.message || t("Couldn't add it") });
    }
  }

  if (state.step === "in") {
    return (
      <button type="button" className="chatAddPaper done" onClick={() => save.onOpenPage?.(state.page)}
        title={[t("In your library — open it"), state.note].filter(Boolean).join("\n")}>
        <CheckIcon size={13} />{t("In library")}
      </button>
    );
  }
  return (
    <span className="chatAddPaperWrap">
      {state.step === "error" ? <span className="chatAddPaperError" role="alert" title={state.error}>{t("Couldn't add it")}</span> : null}
      <button type="button" className="uiBtn sm iconSq ghost chatAddPaper" onClick={add} disabled={state.step === "saving"}
        title={state.step === "error" ? state.error : t("Add to library")}
        aria-label={t("Add “{title}” to your library", { title: title || paper.source })}>
        {state.step === "saving" ? <span className="transferSpin inline" aria-hidden="true" /> : <PlusIcon size={14} />}
      </button>
    </span>
  );
}

// A document fetch_paper read: its title (the registry's when the fetch had
// none), what it was, and the button. `save` is null where the reader cannot
// add to the library (a workspace viewer, a share link).
export default function FetchedPaper({ paper, save }) {
  const title = useRecordTitle(paper);
  const what = paper.pdf
    ? (paper.pages ? tn("PDF, {n} page", "PDF, {n} pages", paper.pages) : "PDF")
    : t("Web page");
  return (
    <div className="chatPaper" data-paper={paper.key}>
      <FileTextIcon size={15} />
      <div className="chatPaperText">
        <div className="chatPaperTitle" title={title || paper.source}>{title || paper.source}</div>
        <div className="chatPaperMeta" title={paper.source}>
          {[title ? paper.source : "", what, paper.request ? t("from your browser") : ""].filter(Boolean).join(" · ")}
        </div>
      </div>
      {save ? <AddToLibrary paper={paper} title={title} save={save} /> : null}
    </div>
  );
}
