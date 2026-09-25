import React from "react";
import { API, apiJson, fmtBytes, isUnverifiedPaperMeta, metaSourceInfo, getCurrentWorkspace } from "../shared/lib/utils";
import { MenuSelect } from "../shared/ui/Menus";
import { T, t, tn } from "../shared/i18n/i18n.js";
import {
  PaneHead, Section, Row, Toggle, Segmented, ToggleGroup, IconChoices, UnitInput, CharSlider, approxPages,
  Stat, Empty, QuotaMeter, LogBox, SettingsDraftContext, SettingsSyncContext, useSettingsDraft,
} from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { LibraryDisplaySettings } from "./SettingsLibraryDisplay";
import { AppearanceSettings } from "./SettingsAppearance";
import { KeyboardSettings } from "./SettingsKeyboard";
import { AiSettings } from "./SettingsAi";
import { IntegrationSettings } from "./SettingsIntegrations";
import { UsersSettings } from "./SettingsUsers";
import { WorkspacesSettings } from "./SettingsWorkspace";
import { SyncSettings } from "./SettingsSync";
import { WorkspaceBackups } from "./SettingsBackups";
import { ServerSettings } from "./SettingsServer";
import { dotTone } from "../app/notices";
import { resolveSettingsPane, searchSettings } from "./settingsNavigation";
import { TranslationSettings } from "./SettingsTranslation";
import {
  ActivityIcon,
  BookIcon,
  BugIcon,
  CloudDownloadIcon,
  EyeIcon,
  ContrastIcon,
  DatabaseIcon,
  CornerDownLeftIcon,
  FileTextIcon,
  FolderIcon,
  GlobeIcon,
  HandIcon,
  LinkIcon,
  HardDriveIcon,
  HighlightIcon,
  HomeIcon,
  ListIcon,
  MessageSquareIcon,
  OutlineIcon,
  PaperIcon,
  PenIcon,
  PencilIcon,
  RectSelectIcon,
  RefreshIcon,
  SearchIcon,
  ServerIcon,
  SparklesIcon,
  TerminalIcon,
  TypeIcon,
  UserIcon,
  UsersIcon,
  KeyboardIcon,
} from "../shared/ui/Icons";

// One sidebar, three groups: everyday preferences, AI, management. Every
// pane is one click from any other; nothing opens a second dialog.
const PREFERENCE_NAV = [
  ["appearance", T("Appearance"), ContrastIcon],
  ["reading", T("Reading & editing"), BookIcon],
  ["keyboard", T("Keyboard"), KeyboardIcon],
  ["library", T("Library"), ListIcon],
  ["account", T("Account"), UserIcon],
];
const AI_NAV = [
  ["ai", T("Connections"), SparklesIcon],
  ["assistant", T("Chat"), MessageSquareIcon],
  ["ai-advanced", T("Advanced"), ActivityIcon],
  ["prompts", T("Prompts"), TypeIcon],
  ["integrations", T("Integrations"), LinkIcon],
];
const MANAGEMENT_NAV = [
  ["workspaces", T("Workspaces"), UsersIcon],
  ["sync", T("Sync"), RefreshIcon],
  ["backups", T("Backups"), DatabaseIcon],
  ["maintenance", T("Library maintenance"), HardDriveIcon],
  ["users", T("Users"), UsersIcon],
  ["server", T("Server"), ServerIcon],
  ["diagnostics", T("Diagnostics"), ActivityIcon],
];

// --- Editor: notes + search + PDF viewer -----------------------------------

// What draws on the page with the handwriting tools open: the stored
// preference is "fingers never draw" (inkPenOnly), pictured as two tiles.
const DRAW_WITH = [
  { value: "pen", label: T("Pen only"), hint: T("fingers scroll and zoom"), Icon: PenIcon },
  { value: "any", label: T("Pen and finger"), hint: T("for screens without a stylus"), Icon: HandIcon },
];

function ViewerSettings({ value }) {
  return (
    <>

      <Section title={t("PDF viewer")} scope="account" prefs={SECTION_PREFS.reading["PDF viewer"]}>
        <Row
          icon={HighlightIcon}
          label={t("Imported annotations")}
          hint={t("Annotations saved inside a PDF become highlights")}
          title={t("Highlights, notes and rectangles saved inside a PDF file (a Gamma export, SumatraPDF, Acrobat…) are imported as regular highlights. Keep originals leaves the file untouched (the viewer hides them); Remove originals rewrites the stored PDF without them.")}
        >
          <Segmented value={value.embAnnots} onChange={value.setEmbAnnots}
            options={[["hide", t("Keep originals")], ["strip", t("Remove originals")]]} />
        </Row>
      </Section>
      <Section title={t("Handwriting")} scope="browser">
        <div data-setting="Draws with">
          <IconChoices label={t("Draws with")} value={value.inkPenOnly ? "pen" : "any"}
            onChange={(choice) => value.setInkPenOnly(choice === "pen")} options={DRAW_WITH} />
        </div>
        <Toggle
          icon={PenIcon}
          label={t("Stylus draws right away")}
          hint={t("Without opening the tools first")}
          title={t("With a stylus (Apple Pencil, Surface Pen, Wacom…), touching the page draws with the pen tool even when the handwriting tools are closed. Fingers and the mouse still select text. Turn off if your stylus keeps leaving marks while you navigate.")}
          checked={value.inkAutoPen}
          onChange={value.setInkAutoPen}
        />
        <Toggle
          icon={ActivityIcon}
          label={t("Pressure-sensitive strokes")}
          hint={t("Pen strokes thicken with pressure")}
          title={t("Use the stylus pressure for stroke width, like ink on paper. Off gives even strokes. Mouse and finger strokes are always even.")}
          checked={value.inkPressure}
          onChange={value.setInkPressure}
        />
      </Section>
    </>
  );
}

// AI › Advanced › Translation performance. Effort means nothing to a
// translation service ("engine:<id>"), so its row hides while one is picked.
function TranslationPerformance({ value }) {
  const engine = (value.translateEngines || []).some((e) => e.id === value.translateModel);
  return <>
        {!engine ? <Row
          icon={ActivityIcon}
          label={t("Translation effort")}
          hint={t("Low makes reasoning models translate much faster")}
          title={t("Reasoning effort sent with translation calls. Reasoning models spend their thinking budget before writing any output, which is wasted on translation — Low or Minimal typically cuts a page from ~20s to a few seconds. Default omits the parameter (some models reject it).")}
        >
          <MenuSelect
            label={t("Translation effort")}
            value={value.translateEffort}
            onChange={value.setTranslateEffort}
            options={[["", t("Default")], ["minimal", t("Minimal")], ["low", t("Low")], ["medium", t("Medium")], ["high", t("High")]]}
          />
        </Row> : null}
        <Row
          icon={RefreshIcon}
          label={t("Parallel requests")}
          hint={t("Translation calls in flight at once (1–32)")}
          title={t("A page is translated in small chunks, this many at a time; a whole-document job streams chunks across pages and never exceeds it. Higher is faster until your provider's rate limit pushes back.")}
        >
          <UnitInput value={value.translateParallel} unit="calls" min={1}
            onCommit={(raw) => {
              const n = Number.parseInt(raw, 10);
              if (Number.isFinite(n)) value.setTranslateParallel(Math.max(1, Math.min(32, n)));
            }} />
        </Row>
  </>;
}

const SEARCH_SHAPES = [["panel", t("Full panel")], ["bar", t("Find bar")]];

function SearchSettings({ value }) {
  return (
    <>

      <Section title={t("Search opens as")} scope="account" prefs={SECTION_PREFS.reading["Search opens as"]}>
        <Row icon={HomeIcon} label={t("On the home page")} hint={t("Full panel: grouped result lists")}
          title={t("With no PDF open the compact find bar has nothing to show, so the home page defaults to the full panel.")}>
          <Segmented value={value.searchDetailsHome ? "panel" : "bar"} onChange={(v) => value.setSearchDetailsHome(v === "panel")}
            options={SEARCH_SHAPES} />
        </Row>
        <Row icon={PaperIcon} label={t("On a page")} hint={t("Find bar: match counter and next / previous")}
          title={t("On a page, Ctrl+F defaults to the compact browser-style find bar; the full panel adds the grouped result lists.")}>
          <Segmented value={value.searchDetailsPaper ? "panel" : "bar"} onChange={(v) => value.setSearchDetailsPaper(v === "panel")}
            options={SEARCH_SHAPES} />
        </Row>
      </Section>
    </>
  );
}

function NotesSettings({ value }) {
  return (
    <>

      <Section title={t("Notes")} scope="account" prefs={SECTION_PREFS.reading["Notes"]}>
        <Row icon={CornerDownLeftIcon} label={t("Enter key")}
          hint={value.enterNewNote ? t("Shift+Enter inserts a new line") : t("Shift+Enter creates a new note")}>
          <Segmented value={value.enterNewNote ? "note" : "line"}
            onChange={(choice) => value.setEnterNewNote(choice === "note")}
            options={[["note", t("New note")], ["line", t("New line")]]} />
        </Row>
      </Section>
    </>
  );
}

// Kick off a full search-index rebuild (the Library pane's Index section).
async function requestReindex(setStatus, scheduledSuffix, wakeTasks) {
  try {
    const result = await apiJson(`${API}/search-reindex`, { method: "POST" });
    if (result.scheduled || result.busy) wakeTasks?.();
    setStatus(result.busy
      ? t("Indexing is already running—see the tasks popover.") : result.scheduled
        ? t("Re-indexing {scheduled} paper{_s} {scheduledSuffix}", { scheduled: result.scheduled, _s: result.scheduled === 1 ? "" : "s", scheduledSuffix })
        : t("No papers with PDFs to index."));
  } catch (err) {
    setStatus(t("Reindex failed: {message}", { message: err.message }));
  }
}

// --- Library: storage + per-paper health ------------------------------------

// Everyone sees their own usage against their effective limits (GET /api/quota).
function StorageCard() {
  const [q, setQ] = React.useState(null);
  React.useEffect(() => { apiJson(`${API}/quota`).then(setQ).catch(() => {}); }, []);
  if (!q) return null;
  return (
    <div className="setCard">
      <div className="setCardHead">
        <span className="setIcon"><HardDriveIcon size={15} /></span>
        <span className="settingText">
          <span className="settingLabel">{t("Uploaded files")}</span>
          <span className="settingDesc">{t("PDFs and images on the server · up to {max_upload_mb} MB each", { max_upload_mb: q.max_upload_mb })}</span>
        </span>
        <span className="setCardVal">
          {fmtBytes(q.used_bytes)}
          <em>{q.quota_mb ? ` / ${fmtBytes(q.quota_mb * 1024 * 1024)}` : t(" · no quota")}</em>
        </span>
      </div>
      <QuotaMeter usedBytes={q.used_bytes} quotaMb={q.quota_mb} barOnly />
    </div>
  );
}

// Whole-library import from Zotero: the user zips their File → Export Library
// → "Zotero RDF" folder (with files, notes and annotations) and uploads it.
// Collections become folders, tags labels, notes child blocks; annotations ride
// inside the exported PDFs and reuse the embedded-annotations importer (the
// strip-vs-hide choice follows the standing Settings → PDF viewer preference).
function LibrarySettings({ value }) {
  return (
    <>
      <PaneHead icon={ListIcon} title={t("Library")} />
      <Section title={t("Display")} scope="account" prefs={SECTION_PREFS.library["Display"]}>
        <LibraryDisplaySettings value={value} />
      </Section>
      <Section title={t("PDFs")} scope="account" prefs={SECTION_PREFS.library["PDFs"]}>
        <Toggle
          icon={CloudDownloadIcon}
          label={t("Open-access fallback")}
          hint={t("Fetch a free copy when a publisher blocks the PDF")}
          title={t("When a publisher PDF is paywalled or refuses to download, load a legal open-access copy instead — usually the arXiv version. A note tells you when the substitute isn't the published version.")}
          checked={value.oaFallback}
          onChange={value.setOaFallback}
        />
        <Toggle
          icon={SparklesIcon}
          label={t("Auto-fetch metadata")}
          hint={t("Title, authors and BibTeX on first open")}
          title={t("Look up title, authors, venue and BibTeX the first time a paper opens (arXiv → DOI → AI). Turn this off to fetch only via the refresh button in the metadata popover.")}
          checked={value.metaAutoFetch}
          onChange={value.setMetaAutoFetch}
        />
        <Toggle
          icon={HardDriveIcon}
          label={t("Save external PDFs")}
          hint={t("Keep a server copy of PDFs opened from a URL")}
          title={t("Keep a server copy of PDFs opened from a URL, so they load instantly next time and survive dead links.")}
          checked={value.pdfSaveLocal}
          onChange={value.setPdfSaveLocal}
        />
      </Section>
    </>
  );
}

// Library-wide health: per paper, whether metadata resolved, whether the PDF
// yielded extractable text, and whether the search index covers it — with
// batch retry for the metadata lookups. Text and index state come from the FTS
// index, so "unknown" means not visited yet, not broken; Reindex fills it in.
function MaintenanceSettings({ value }) {
  return <>
    <PaneHead icon={HardDriveIcon} title={t("Library maintenance")} />
      <Section title={t("Storage")}>
        <StorageCard />
      </Section>
      <Section title={t("Index")}>
        <Row
          icon={RefreshIcon}
          label={t("PDF text index")}
          hint={value.indexTask?.active ? t("Rebuilding — progress in the tasks popover") : t("Re-extract every paper if results look stale")}
          title={t("Full-text search reads a per-user index built from the extracted PDF text. Rebuild it when library-wide results look stale or incomplete.")}
        >
          <button className="uiBtn sm" disabled={value.indexTask?.active} onClick={() => requestReindex(value.setStatus, t("in the background."), value.wakeTasks)}>
            {value.indexTask?.active ? t("Indexing…") : t("Rebuild")}
          </button>
        </Row>
      </Section>
      <MetaStatusSection value={value} />
  </>;
}

function MetaStatusSection({ value }) {
  const [papers, setPapers] = React.useState(null); // null = loading
  const [error, setError] = React.useState("");
  const [selected, setSelected] = React.useState(() => new Set());
  const [busy, setBusy] = React.useState(null); // {done, total, title} during a batch run
  const [sortMode, setSortMode] = React.useState("meta"); // meta | text | updated
  const [filterMode, setFilterMode] = React.useState("all"); // all | attention | unverified | missing | notext
  const stopRef = React.useRef(false);

  async function refresh() {
    try {
      const data = await apiJson(`${API}/metadata/status`);
      setPapers(data.papers || []);
      setError("");
      setSelected((prev) => new Set([...prev].filter((id) => (data.papers || []).some((p) => p.id === id))));
    } catch (err) {
      setError(err.message);
      setPapers((prev) => prev || []);
    }
  }
  React.useEffect(() => { refresh(); }, []);

  // Indexing runs in a background thread server-side, so after scheduling we
  // re-poll the table a few times to let the dots fill in. A newer poll (or
  // unmount) cancels the older one.
  const pollRef = React.useRef(0);
  React.useEffect(() => () => { pollRef.current++; }, []);
  function pollRefresh() {
    const id = ++pollRef.current;
    [1500, 4000, 8000, 15000].forEach((ms) =>
      setTimeout(() => { if (pollRef.current === id) refresh(); }, ms));
  }

  // Index specific papers (the per-row button) without touching the rest.
  async function indexDocs(docIds) {
    try {
      const r = await apiJson(`${API}/search-reindex`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: docIds }),
      });
      value.setStatus(r.busy
        ? t("Indexing is already running—try again when it finishes.")
        : t("Indexing {papers} in the background.", { papers: docIds.length === 1 ? "1 paper" : `${docIds.length} papers` }));
      value.wakeTasks?.();
      pollRefresh();
    } catch (err) {
      value.setStatus(t("Indexing failed: {message}", { message: err.message }));
    }
  }

  const list = papers || [];
  const textOk = (p) => (p.text_chars ?? 0) >= 50; // same threshold as /api/pdf-text-status
  // "Unverified": records nothing tied to their document (AI-extracted
  // papers, unconfirmed identifiers) — the same ones the red "!" flags on the
  // metadata button. Missing metadata and unverified records are what a
  // batch (re)fetch can actually fix.
  const unverifiedPaper = (p) => p.has_meta && isUnverifiedPaperMeta(p.meta_source, p.meta_kind, p.meta_unverified);
  const fetchable = (p) => !p.has_meta || unverifiedPaper(p);
  const noText = (p) => p.text_chars !== null && !textOk(p);
  // Index work: papers with a file the search index doesn't cover yet, holds
  // at an older extractor version, or hasn't visited (text unknown) — the same
  // rule as the per-row index button.
  const needsIndex = (p) => p.doc_id && p.has_file && (!p.indexed || p.index_stale || p.text_chars === null);
  const missing = list.filter((p) => !p.has_meta);
  const unverified = list.filter(unverifiedPaper);
  const needsWork = list.filter(fetchable);
  const toIndex = list.filter(needsIndex);
  const counts = {
    verified: list.filter((p) => p.has_meta && !unverifiedPaper(p)).length,
    text: list.filter(textOk).length,
    indexed: list.filter((p) => p.indexed).length,
  };
  const FILTERS = {
    all: () => true,
    attention: (p) => fetchable(p) || noText(p),
    unverified: unverifiedPaper,
    missing: (p) => !p.has_meta,
    notext: noText,
  };
  // ISO timestamps compare lexicographically; unfinished-first sorts fall back
  // to recency inside each group.
  const shown = React.useMemo(() => {
    const byTime = (a, b) => (b.updated_at || "").localeCompare(a.updated_at || "");
    const arr = list.filter(FILTERS[filterMode] || FILTERS.all);
    if (sortMode === "meta") arr.sort((a, b) => (fetchable(b) ? 1 : 0) - (fetchable(a) ? 1 : 0) || byTime(a, b));
    else if (sortMode === "text") arr.sort((a, b) => (textOk(a) ? 1 : 0) - (textOk(b) ? 1 : 0) || byTime(a, b));
    else arr.sort(byTime);
    return arr;
  }, [list, sortMode, filterMode]);

  async function retry(targets) {
    if (!targets.length || busy) return;
    stopRef.current = false;
    let ok = 0, failed = 0;
    for (let i = 0; i < targets.length; i++) {
      if (stopRef.current) break;
      const paper = targets[i];
      setBusy({ done: i, total: targets.length, title: paper.title });
      try {
        await apiJson(`${API}/metadata/fetch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            block_id: paper.id,
            force: true,
            prompt: value.metaPrompt || "",
            model: value.metaFetchModel || "",
            context_char_limit: value.metaContextChars || 6000,
          }),
        });
        ok++;
      } catch {
        failed++;
      }
    }
    setBusy(null);
    value.setStatus(t("Metadata: {ok} fetched{failed}{stopped}.", { ok, failed: failed ? `, ${failed} failed` : "", stopped: stopRef.current ? " (stopped)" : "" }));
    refresh();
  }

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  // Select-all works on the filtered view, so "filter to unverified → select
  // all → fetch" is three clicks.
  const allSelected = shown.length > 0 && shown.every((p) => selected.has(p.id));
  // One adaptive primary action instead of three near-identical buttons: a
  // selection wins, otherwise it offers exactly the papers a fetch can fix
  // (missing metadata + unverified AI records).
  const targets = selected.size ? list.filter((p) => selected.has(p.id)) : needsWork;
  // Same rule for the index button: the selection's indexable papers, else
  // everything the index is missing or holds stale.
  const indexTargets = selected.size ? list.filter((p) => selected.has(p.id) && p.doc_id && p.has_file) : toIndex;
  const indexing = !!value.indexTask?.active;

  const cell = (tone, text, title) => (
    <span className={`metaCell ${tone}`} title={title}><i className="setDot" />{text}</span>
  );
  const metaCell = (p) => {
    if (!p.has_meta) {
      return p.meta_error ? cell("bad", "failed", p.meta_error) : cell("muted", "none", t("No metadata yet"));
    }
    const src = metaSourceInfo({ source: p.meta_source, kind: p.meta_kind, unverified: p.meta_unverified });
    if (!src) return cell("ok", "yes", t("Metadata resolved"));
    return cell(src.warn ? "bad" : p.meta_source === "ai" ? "muted" : "ok", src.short, src.hint);
  };
  // Text and index are separate columns: extraction state is only known once
  // the indexer has visited the doc, so an unindexed paper shows "unknown".
  const textCell = (p) => (
    p.text_chars === null
      ? cell("muted", "unknown", t("Unknown until the paper is indexed — Reindex to find out"))
      : textOk(p)
        ? cell("ok", p.text_chars >= 1000 ? `${Math.round(p.text_chars / 1000)}k` : String(p.text_chars),
          t("{text_chars} characters extracted", { text_chars: p.text_chars.toLocaleString() }))
        : cell("bad", t("no text"), p.has_file ? t("No text layer — scanned or image-only?") : t("PDF file not on the server"))
  );
  const indexCell = (p) => (
    p.indexed
      ? cell("ok", "indexed", t("In the search index"))
      : p.index_stale
        ? cell("muted", "stale", t("Indexed with an older extractor version — Reindex refreshes it"))
        : cell("muted", "—", t("Not in the search index yet"))
  );

  return (
    <Section
      title={t("Papers")}
      action={
        <span className="metaStatActions">
          <MenuSelect
            label={t("Show papers")} value={filterMode} onChange={setFilterMode}
            options={[
              ["all", `All (${list.length})`],
              ["attention", t("Needs attention")],
              ["unverified", t("Unverified AI ({n})", { n: unverified.length })],
              ["missing", t("Missing metadata ({n})", { n: missing.length })],
              ["notext", t("No text layer")],
            ]}
          />
          <MenuSelect
            label={t("Sort papers")} value={sortMode} onChange={setSortMode}
            options={[
              ["meta", t("Needs work first")],
              ["text", t("Missing text first")],
              ["updated", t("Recently modified")],
            ]}
          />
          <button className="uiBtn sm iconSq" aria-label={t("Reindex")}
            title={t("Re-extract every paper into the search index (also fills in the text column)")}
            onClick={() => { requestReindex(value.setStatus, t("— text status fills in as it runs."), value.wakeTasks); pollRefresh(); }}>
            <RefreshIcon size={13} />
          </button>
          <button className="uiBtn sm iconSq" onClick={refresh} disabled={!!busy} title={t("Reload this table")} aria-label={t("Reload")}>
            <ActivityIcon size={13} />
          </button>
        </span>
      }
    >
      {papers === null ? <Empty icon={ListIcon}>{t("Loading…")}</Empty> : null}
      {error ? <Empty icon={ActivityIcon}>{t("Status unavailable — {error}", { error: error })}</Empty> : null}
      {papers !== null && !list.length && !error ? <Empty icon={PaperIcon}>{t("No papers yet — open a PDF first.")}</Empty> : null}
      {list.length ? (
        <>
          <div className="setStats">
            <Stat icon={PaperIcon} label="verified" value={counts.verified} total={list.length}
              title={t("Metadata from a registry (arXiv/DOI/Crossref), edited by hand, or a non-paper document — nothing left to verify. Missing and unverified AI records count against this.")} />
            <Stat icon={FileTextIcon} label={t("text layer")} value={counts.text} total={list.length}
              title={t("Papers whose PDF yielded extractable text")} />
            <Stat icon={SearchIcon} label="indexed" value={counts.indexed} total={list.length}
              title={t("Papers covered by the full-text search index")} />
          </div>
          <div className="metaStatTable">
            <div className="metaStatRow metaStatHeader">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => setSelected((prev) => {
                  const next = new Set(prev);
                  shown.forEach((p) => (allSelected ? next.delete(p.id) : next.add(p.id)));
                  return next;
                })}
                title={allSelected ? t("Clear the shown papers from the selection") : t("Select all shown papers")}
              />
              <span>{t("Paper")}</span>
              <span>{t("Metadata")}</span>
              <span>{t("Text")}</span>
              <span>{t("Index")}</span>
              <span />
            </div>
            {!shown.length ? (
              <div className="metaStatRow" style={{ cursor: "default" }}>
                <span /><span className="metaStatTitle" style={{ color: "var(--text-dim)" }}>{t("Nothing matches this filter.")}</span>
              </div>
            ) : null}
            {shown.map((p) => (
              <label key={p.id} className="metaStatRow">
                <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                <span
                  className="metaStatTitle" title={t("{title} — click to open", { title: p.title })}
                  onClick={(e) => {
                    if (!value.openPaper) return;
                    e.preventDefault(); e.stopPropagation();
                    value.openPaper(p.id);
                  }}
                >{p.title}</span>
                {metaCell(p)}
                {textCell(p)}
                {indexCell(p)}
                {needsIndex(p) ? (
                  <button
                    className="searchToggle" aria-label={t("Index {title}", { title: p.title })}
                    title={t("Extract this paper's text into the search index now")}
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); indexDocs([p.doc_id]); }}
                  >
                    <RefreshIcon size={13} />
                  </button>
                ) : <span />}
              </label>
            ))}
          </div>
          {busy ? (
            <div className="metaStatBatchRow">
              <span className="metaStatProgress">{t("Fetching {done}/{total} — {title}", { done: busy.done + 1, total: busy.total, title: busy.title })}</span>
              <button className="uiBtn sm" onClick={() => { stopRef.current = true; }}>{t("Stop")}</button>
            </div>
          ) : (
            <div className="metaStatBatchRow">
              <span className="metaStatProgress">
                {selected.size
                  ? `${selected.size} selected`
                  : needsWork.length || toIndex.length
                    ? [missing.length && `${missing.length} missing metadata`,
                       unverified.length && `${unverified.length} unverified (AI)`,
                       toIndex.length && `${toIndex.length} to index`]
                        .filter(Boolean).join(" · ")
                    : t("Everything is verified and indexed")}
              </span>
              <button className="uiBtn sm primary" disabled={!targets.length} onClick={() => retry(targets)}
                title={selected.size ? t("Fetch metadata for the selected papers") : t("Fetch metadata for papers that are missing it or have an unverified AI record")}>
                <SparklesIcon size={13} />{selected.size ? t("Fetch selected") : t("Fetch needed")}
              </button>
              <button className="uiBtn sm" disabled={!shown.length} onClick={() => retry(shown)}
                title={filterMode === "all"
                  ? t("Re-fetch metadata for every paper, including ones that already have it") : t("Re-fetch metadata for every paper the current filter shows")}>
                {filterMode === "all" ? t("Refetch all") : t("Refetch shown")}
              </button>
              <button className="uiBtn sm" disabled={!indexTargets.length || indexing}
                onClick={() => indexDocs(indexTargets.map((p) => p.doc_id))}
                title={indexing ? t("Indexing is already running — progress in the tasks popover") : selected.size ? t("Extract the selected papers' text into the search index") : t("Extract only the papers the search index is missing or holds at an older extractor version")}>
                <RefreshIcon size={13} />{indexing ? t("Indexing…") : selected.size ? t("Reindex selected") : t("Reindex needed")}
              </button>
            </div>
          )}
        </>
      ) : null}
    </Section>
  );
}

// --- Assistant: agent, chat, context budgets, prompts -----------------------

// One accordion instead of three stacked textareas: only the prompt being
// edited takes up space, and Restore default lights up only when it would
// change something. Everything starts collapsed — the section reads as a
// category list until a prompt is opened.
function PromptAccordion({ items }) {
  const [open, setOpen] = React.useState(null);
  return (
    <div className="setAccList">
      {items.map(({ key, label, icon: Icon, draft, setDraft, defaultValue, custom }) => {
        const isOpen = open === key;
        const modified = (draft || "").trim() !== (defaultValue || "").trim();
        return (
          <div key={key} className={`setAcc ${isOpen ? "open" : ""}`}>
            <button className="setAccHead" onClick={() => setOpen(isOpen ? null : key)}>
              <span className="setIcon"><Icon size={14} /></span>
              <span className="setAccName">{t(label)}</span>
              {custom ? <span className="uiTag">{t("custom")}</span> : null}
              <span className="setAccChevron">{isOpen ? "▾" : "▸"}</span>
            </button>
            {isOpen ? (
              <div className="setAccBody">
                <textarea className="promptTextarea" value={draft} rows={6}
                  onChange={(event) => setDraft(event.target.value)} />
                <div className="reportModalBtns settingsAlignStart">
                  <button className="uiBtn sm" disabled={!modified} onClick={() => setDraft(defaultValue || "")}>
                    {t("Restore default")}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// Prompts pane: the three editable prompts as a collapsed accordion, with one
// Save button for all of them.
function PromptsSettings({ value }) {
  const prompts = [
    { key: "chat", label: T("Chat system prompt"), icon: MessageSquareIcon,
      draft: value.promptDraft, setDraft: value.setPromptDraft,
      defaultValue: value.aiInfo?.default_prompt, custom: !!value.chatSystem, saved: value.chatSystem },
    { key: "meta", label: T("Metadata extraction"), icon: PaperIcon,
      draft: value.metaPromptDraft, setDraft: value.setMetaPromptDraft,
      defaultValue: value.aiInfo?.metadata_prompt, custom: !!value.metaPrompt, saved: value.metaPrompt },
    { key: "cite", label: T("PPT citation"), icon: TypeIcon,
      draft: value.citePromptDraft, setDraft: value.setCitePromptDraft,
      defaultValue: value.aiInfo?.cite_prompt, custom: !!value.citePrompt, saved: value.citePrompt },
    { key: "agent", label: T("Library agent"), icon: SparklesIcon,
      draft: value.agentPromptDraft, setDraft: value.setAgentPromptDraft,
      defaultValue: value.aiInfo?.agent_prompt, custom: !!value.agentSystem, saved: value.agentSystem },
  ];
  // A stored "" means "use the default", so the effective saved text is the
  // custom one or the default — that is what a draft is dirty against.
  const dirty = prompts.some((p) => (p.draft || "").trim() !== (p.saved || p.defaultValue || "").trim());
  const discard = () => prompts.forEach((p) => p.setDraft(p.saved || p.defaultValue || ""));
  useSettingsDraft("prompts", dirty, discard);
  return (
    <>
      <PaneHead icon={TypeIcon} title={t("Custom prompts")} />
      <Section
        title={t("Prompts")}
        scope="account" prefs={SECTION_PREFS.prompts["Prompts"]}
        action={
          <span className="setControlGroup">
            <button className="uiBtn sm" disabled={!dirty} onClick={discard}>{t("Cancel")}</button>
            <button className="uiBtn sm primary" disabled={!dirty} onClick={value.savePrompts}>{t("Save")}</button>
          </span>
        }
      >
        <PromptAccordion items={prompts} />
      </Section>
    </>
  );
}

// The agent's capabilities, one chip per permission (see docs/dev/ai_tools.md).
// [key, icon, label, hint, scopes, short] — scopes says which agent scopes
// offer it ("folder" = library/folder chat, "page" = page chat), short is
// the chip name in the ToggleGroup.
const AGENT_PERM_ROWS = [
  ["list", ListIcon, t("List pages"),
   t("See the folder's page titles, labels and metadata"), ["folder"], t("List")],
  ["read", BookIcon, t("Read pages"),
   t("Read a page's PDF text plus your highlights and notes"), ["folder", "page"], t("Read")],
  ["block_read", OutlineIcon, t("Read note blocks"),
   t("Read a page's note outline with block ids"), ["folder", "page"], t("Blocks")],
  ["view", EyeIcon, t("View PDF pages"),
   t("Show the model a picture of a PDF page (scans without a text layer, figures)"), ["folder", "page"], t("View")],
  ["search", SearchIcon, t("Search"),
   t("Full-text search across the folder's notes and PDFs"), ["folder", "page"], t("Search")],
  ["web_search", GlobeIcon, t("Search papers online"),
   t("Look papers up on Crossref and arXiv (e.g. a reference a paper cites)"), ["folder", "page"], t("Papers")],
  ["web_read", CloudDownloadIcon, t("Fetch documents"),
   t("Read a paper or web page by DOI, arXiv id or URL without adding it to the library"), ["folder", "page"], t("Fetch")],
  ["rename", PenIcon, t("Rename pages"), t("Change page titles on request"), ["folder"], t("Rename")],
  ["move", FolderIcon, t("Move pages"),
   t("File pages into folders (a new path creates the folder)"), ["folder"], t("Move")],
  ["block_edit", PencilIcon, t("Edit note blocks"),
   t("Edit, create and move note blocks on request (never deletes)"), ["folder", "page"], t("Edit")],
];

// The three chat kinds, each with its own permission map (prefs.js
// CHAT_KINDS): [kind, icon, label, hint, agent scope].
export const CHAT_KIND_ROWS = [
  ["folder", FolderIcon, t("Folder chat"), t("Home and folder views"), "folder"],
  ["pdf", FileTextIcon, t("PDF chat"), t("Pages with a PDF"), "page"],
  ["notes", OutlineIcon, t("Notes chat"), t("Note pages"), "page"],
];

// One chat kind's tool chips (a ToggleGroup) bound to the stored permission
// map — the Settings pane and the chat header's ⚙ popover render this same
// control over the same map, so a change in one is the change in the other.
export function AgentToolPicker({ kind, perms, setPerms, disabled }) {
  const scope = CHAT_KIND_ROWS.find((r) => r[0] === kind)?.[4] || "page";
  const map = perms?.[kind] || {};
  const rows = AGENT_PERM_ROWS.filter((r) => r[4].includes(scope));
  return (
    <ToggleGroup
      disabled={disabled}
      options={rows.map(([key, Icon, label, hint, , short]) => [key, short, Icon, `${label} — ${hint}`])}
      selected={rows.filter(([key]) => map[key] !== false).map(([key]) => key)}
      onToggle={(key, on) => setPerms((p) => ({ ...p, [kind]: { ...(p?.[kind] || {}), [key]: on } }))}
    />
  );
}

// Chat: the tools each chat kind may use, one chip row per kind — the same
// chips the chat header's settings popover shows for the open chat.
function AssistantSettings({ value }) {
  return (
    <Section title={t("Tools")} scope="account" prefs={SECTION_PREFS.assistant["Tools"]}>
      <Toggle icon={SparklesIcon} label={t("Assistant tools")}
        hint={t("Let chats read, search and edit your library")}
        title={t("The master switch for tools in every chat. Off keeps your per-chat choices below for when you turn it on again.")}
        checked={value.agentEnabled} onChange={value.setAgentEnabled} />
      {CHAT_KIND_ROWS.map(([kind, icon, label, hint]) => (
        <Row key={kind} icon={icon} label={label} hint={hint}>
          <AgentToolPicker kind={kind} perms={value.agentPerms} setPerms={value.setAgentPerms} disabled={!value.agentEnabled} />
        </Row>
      ))}
    </Section>
  );
}

function AdvancedAiSettings({ value, ai, papers }) {
  const budgets = [value.chatContextChars, value.metaContextChars, value.multiContextChars];
  const contextPreset = budgets.every((n, i) => n === [60000, 6000, 120000][i]) ? "standard" : budgets.every((n, i) => n === [120000, 12000, 240000][i]) ? "larger" : "custom";
  const shared = t("Extracted PDF text is measured in characters. Larger budgets can improve answers but cost more tokens.");
  const limits = [
    [FileTextIcon, t("Single paper"), t("Read from the open paper for one chat message"),
      value.chatContextChars, value.setChatContextChars,
      t("{shared} When you ask about selected passages, this budget is spent around them (a grounding slice from the start plus text around each selection's page) instead of only the start of the paper.", { shared })],
    [PaperIcon, t("Metadata extraction"), t("Read while detecting identifiers and extracting fields"),
      value.metaContextChars, value.setMetaContextChars, shared],
    [BookIcon, t("Multi-paper total"), t("Shared evenly by every selected paper"),
      value.multiContextChars, value.setMultiContextChars, shared],
  ];

  return <>

        <Row icon={ActivityIcon} label={t("Default reasoning effort")} hint={t("Leave Default unless your model supports it")}>
          <MenuSelect label={t("Default reasoning effort")} value={ai.chatEffort} onChange={ai.setChatEffort}
            options={[["", t("Default")], ...(ai.aiInfo?.efforts || ["low", "medium", "high"]).map((v) => [v, v])]} />
        </Row>
        <Section title={t("Tool limits")} scope="account" prefs={SECTION_PREFS.advanced["Tool limits"]}>
        <Row icon={RefreshIcon} label={t("Tool rounds")}
          hint={t("AI ↔ tool round-trips per message")}
          title={t("Each round-trip lets the model issue more tool calls. This is a runaway guard — actual work is separately capped at 200 changes per message.")}>
          <UnitInput value={value.toolRounds} unit="rounds" min={1}
            onCommit={(raw) => {
              const n = Number.parseInt(raw, 10);
              if (Number.isFinite(n)) value.setToolRounds(Math.max(1, Math.min(100, n)));
            }} />
        </Row>
        <Row icon={BookIcon} label={t("Read window")}
          hint={t("Document text per read tool call · {pages}", { pages: approxPages(value.agentReadChars) })}
          title={t("The most extracted PDF text one read_page tool call may return. The agent reads a long paper in windows of this size, continuing where the last call stopped — a larger window means fewer calls but more tokens per message.")}>
          <CharSlider value={value.agentReadChars} onChange={value.setAgentReadChars} />
        </Row>
        </Section>
      <Section
        title={t("Context size")}
        scope="account" prefs={SECTION_PREFS.advanced["Context size"]}
        action={
          <MenuSelect label={t("Context budget")} value={contextPreset}
            onChange={(preset) => {
              if (preset === "custom") return; // the sliders below are the custom values
              const factor = preset === "larger" ? 2 : 1;
              value.setChatContextChars(60000 * factor);
              value.setMetaContextChars(6000 * factor);
              value.setMultiContextChars(120000 * factor);
            }} options={[["standard", t("Standard")], ["larger", t("Larger")], ["custom", t("Custom")]]} />
        }
      >
        {limits.map(([icon, label, hint, current, setCurrent, title]) => (
          <Row key={label} icon={icon} label={label} hint={t("{hint} · {current}", { hint: hint, current: approxPages(current) })}
            title={title}>
            <CharSlider value={current} onChange={setCurrent} />
          </Row>
        ))}
      </Section>
        <Section title={t("Translation performance")} scope="account" prefs={SECTION_PREFS.advanced["Translation performance"]}><TranslationPerformance value={papers} /></Section>
        <Section title={t("Chat")} scope="account" prefs={SECTION_PREFS.advanced["Chat"]}>
          <Toggle
            icon={RectSelectIcon}
            label={t("Clear snapshots on click")}
            hint={t("A plain click in the PDF also drops pending snapshots")}
            title={t("A plain click in the PDF clears the quoted text selections under the chat. Turn this on to also drop pending rectangle snapshots with that click — images pasted into the chat are never touched.")}
            checked={value.chatImgAutoClear}
            onChange={value.setChatImgAutoClear}
          />
        </Section>
  </>;
}


// --- Advanced: logs ---------------------------------------------------------

function AdvancedSettings({ value }) {
  const [level, setLevel] = React.useState("all");
  const entries = value.sysLog
    .map((entry, index) => ({ key: index, timeMs: entry.t, text: entry.msg, tone: entry.tone }))
    .filter((entry) => level === "all" || (level === "warn" ? !!entry.tone : entry.tone === "error"));
  return (
    <>
      <PaneHead icon={ActivityIcon} title={t("Diagnostics")} />
      <Section title={t("Tracing")}>
        <Toggle
          icon={BugIcon}
          label={t("Debug logging")}
          hint={t("Trace reading-position, restore and sync events")}
          title={t("Trace reading-position tracking, restore and sync events into the system log below (and the browser console).")}
          checked={value.debugLog}
          onChange={value.setDebugLog}
        />
      </Section>
      <Section title={t("Logs")}>
        <LogBox
          icon={TerminalIcon}
          label={t("System log")}
          description={t("Application events from this browser session")}
          entries={entries}
          emptyText={level === "all" ? t("Nothing logged yet this session.")
            : t("No {errors} logged this session.", { errors: level === "warn" ? t("warnings or errors") : t("errors") })}
          copyStatus={t("Log copied.")}
          setStatus={value.setStatus}
          extra={<Segmented value={level} onChange={setLevel}
            options={[["all", t("All")], ["warn", t("Warnings"), null, t("Warnings and errors")], ["error", t("Errors")]]} />}
        />
      </Section>
      <Section title={t("Help")}>
        <Row icon={BugIcon} label={t("Report a problem")} hint={t("A GitHub issue prefilled with this log and the build")}
          title={t("Describe what went wrong; Gamma adds its build, your browser and the recent lines of this log and opens the bug form on GitHub for you to review before posting.")}>
          <button type="button" className="uiBtn sm" onClick={value.openReport}>{t("Report…")}</button>
        </Row>
      </Section>
    </>
  );
}

// --- the dialog -------------------------------------------------------------

const SYNC_POLL_MS = 15000;
const SYNC_SOON_MS = 6000; // the server pushes to Gamma Cloud 5 s after a change lands

// The account profile's sync state for the section tags while the dialog is
// open: this browser's (useProfileSync, `local`, per preference name) plus
// this server's with Gamma Cloud (GET /api/auth/cloud/sync-status, `cloud`,
// account-wide), polled every 15 s, again as soon as a local change has been
// saved, and sooner while a push waits. Every answer also goes to the local
// hook's noteCloud, which then knows when its last push reached the cloud.
function useCloudSyncStatus(open, local) {
  const [cloud, setCloud] = React.useState(null);
  const signedIn = !!local && local.state !== "signed-out";
  const saved = local?.state === "loaded";
  const noteCloud = local?.noteCloud;
  React.useEffect(() => {
    if (!open || !signedIn) { setCloud(null); return undefined; }
    let stopped = false;
    let timer = null;
    const poll = () => {
      apiJson(`${API}/auth/cloud/sync-status`).catch(() => null).then((d) => {
        if (stopped) return;
        if (d) { setCloud(d); noteCloud?.(d.profile); }
        const soon = d?.profile?.state === "pending" && !d.profile.error;
        timer = setTimeout(poll, soon ? SYNC_SOON_MS : SYNC_POLL_MS);
      });
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [open, signedIn, saved, noteCloud]);
  return React.useMemo(() => ({ local, cloud }), [local, cloud]);
}

export default function SettingsDialog({
  activePane, onPaneChange, onClose, papers, notes, keyboard, library, ai, prompts,
  context, search, users, workspace, backups, server, diagnostics, profileSync, notices,
}) {
  const syncState = useCloudSyncStatus(!!activePane, profileSync);
  const [query, setQuery] = React.useState("");
  const [mobileIndex, setMobileIndex] = React.useState(false);
  const [jump, setJump] = React.useState(null);
  const [pending, setPending] = React.useState(null);
  const paneRef = React.useRef(null);
  const modalRef = React.useRef(null);
  const drafts = React.useRef(new Map());
  const available = (id) => {
    if (id === "integrations") return !!users && !users.isGuest;
    if (["account", "users"].includes(id)) return !!users && (id !== "users" || users.isAdmin);
    if (["workspaces", "sync", "backups"].includes(id)) return !!workspace;
    if (id === "server") return !!server;
    return true;
  };
  const allNav = [...PREFERENCE_NAV, ...AI_NAV, ...MANAGEMENT_NAV];
  const allowed = allNav.filter(([id]) => available(id));
  const requested = resolveSettingsPane(activePane);
  const pane = allowed.some(([id]) => id === requested) ? requested : "appearance";
  const guard = (action) => {
    if (drafts.current.size) setPending(() => action);
    else action();
  };
  const navigate = (id, label) => guard(() => {
    setQuery(""); setMobileIndex(false); onPaneChange(id);
    setJump(label ? { label } : null);
    paneRef.current?.scrollTo(0, 0);
  });
  React.useEffect(() => {
    if (!activePane) { setQuery(""); setPending(null); setMobileIndex(false); return; }
    const legacyTarget = { notes: t("Enter key"), search: t("On the home page"), viewer: t("Imported annotations"),
      context: t("Single paper") }[activePane];
    if (legacyTarget) setJump({ label: legacyTarget });
  }, [activePane]);
  // Looking at a pane resolves the notices pointing at it (app/useNotices.js).
  React.useEffect(() => {
    if (activePane && !query) notices?.markSeen(pane);
  }, [activePane, pane, query, notices]);
  React.useEffect(() => {
    if (!jump || query || !activePane) return;
    const target = [...(paneRef.current?.querySelectorAll("[data-setting]") || [])]
      .find((element) => element.dataset.setting === jump.label);
    if (!target) return;
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "center", behavior: "instant" });
  }, [jump, pane, query, activePane]);
  // Keep keyboard navigation inside the settings surface; Escape uses the same
  // draft guard as Close and clicking the backdrop.
  React.useEffect(() => {
    if (!activePane) return;
    const previous = document.activeElement;
    modalRef.current?.focus();
    return () => previous?.focus?.();
  }, [!!activePane]);
  if (!activePane) return null;
  const results = searchSettings(query, allowed.map(([id]) => id));
  const aiValue = { ...ai, aiInfo: prompts.aiInfo };
  const paperValue = { ...papers, chatModelName: (ai.aiModels || []).find((m) => m.id === ai.chatModel)?.model };
  const navButton = ([id, label, Icon]) => <button key={id} type="button"
    className={`settingsNavBtn ${pane === id && !query ? "active" : ""}`}
    aria-current={pane === id && !query ? "page" : undefined} onClick={() => navigate(id)}>
    <Icon size={17} /><span>{t(label)}</span>
    {notices?.panes?.[id] ? <i className={`noticeDot inline ${dotTone(notices.panes[id])}`} data-tone={notices.panes[id]} aria-hidden="true" /> : null}
  </button>;
  return (
    <SettingsDraftContext.Provider value={drafts}>
      <SettingsSyncContext.Provider value={syncState}>
      <div className="reportOverlay" onClick={() => guard(onClose)}>
        <div className={`settingsModal ${mobileIndex ? "settingsIndexOpen" : ""}`}
          role="dialog" aria-modal="true" aria-label={t("Settings")}
          tabIndex={-1} ref={modalRef} onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation(); event.preventDefault();
              if (pending) setPending(null);
              else if (query) setQuery("");
              else guard(onClose);
            }
            if (event.key === "Tab") {
              const targets = [...modalRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')]
                .filter((el) => el.getClientRects().length && !el.closest("[inert]"));
              const first = targets[0], last = targets.at(-1);
              if (event.shiftKey && (document.activeElement === first || document.activeElement === modalRef.current)) { event.preventDefault(); last?.focus(); }
              else if (!event.shiftKey && (document.activeElement === last || document.activeElement === modalRef.current)) { event.preventDefault(); first?.focus(); }
            }
          }}>
          <div className="settingsTopbar">
            <button className="uiBtn sm settingsMobileBack" onClick={() => guard(() => { setMobileIndex(true); setQuery(""); })}>{t("Back")}</button>
            <span className="settingsTopTitle">{t("Settings")}</span>
            <div className="settingsSearch">
              <SearchIcon size={16} />
              <input className="aiKeyInput" type="search" aria-label={t("Search settings")} placeholder={t("Search settings...")} value={query}
                onChange={(event) => {
                  const next = event.target.value;
                  guard(() => { setQuery(next); setMobileIndex(false); });
                }} />
              {query ? <button className="uiClose uiCloseSm" aria-label={t("Clear search")} onClick={() => setQuery("")}>×</button> : null}
            </div>
            <button className="uiClose uiCloseLg" onClick={() => guard(onClose)} aria-label={t("Close settings")}>×</button>
          </div>
          <div className="settingsBody" inert={pending ? "" : undefined}>
            <nav className="settingsSidebar" aria-label={t("Settings categories")}>
              {PREFERENCE_NAV.filter(([id]) => available(id)).map(navButton)}
              <div className="settingsNavGroup">{t("AI")}</div>
              {AI_NAV.filter(([id]) => available(id)).map(navButton)}
              <div className="settingsNavGroup">{t("Manage")}</div>
              {MANAGEMENT_NAV.filter(([id]) => available(id)).map(navButton)}
            </nav>
            <main className="settingsPane" ref={paneRef} key={pane}>
              {query.trim() ? <>
                <PaneHead icon={SearchIcon} title={t("Search settings")}>{tn("{n} matching setting", "{n} matching settings", results.length)}</PaneHead>
                {results.length ? results.map(({ pane: id, label }) => <button key={`${id}:${label}`} className="uiBtn settingsSearchResult"
                  onClick={() => navigate(id, label)}><span>{t(label)}</span><small>{t(allNav.find(([key]) => key === id)?.[1] || "")}</small></button>)
                  : <Empty icon={SearchIcon}>{t('No settings found. Try "model", "PDF", or "storage".')}</Empty>}
              </> : <>
                {pane === "appearance" ? <AppearanceSettings value={papers} diagnostics={diagnostics} /> : null}
                {pane === "reading" ? <>
                  <PaneHead icon={BookIcon} title={t("Reading & editing")} />
                  <ViewerSettings value={papers} />
                  <TranslationSettings value={paperValue} onSpeed={() => navigate("ai-advanced", t("Parallel requests"))} />
                  <NotesSettings value={notes} /><SearchSettings value={search} />
                </> : null}
                {pane === "keyboard" && keyboard ? <KeyboardSettings value={keyboard} /> : null}
                {pane === "library" ? <LibrarySettings value={{ ...papers, ...library }} /> : null}
                {pane === "maintenance" ? <MaintenanceSettings value={library} /> : null}
                {pane === "ai" ? <>
                  <PaneHead icon={SparklesIcon} title={t("Connections")} />
                  <AiSettings value={aiValue}
                    confirm={workspace?.confirm} setStatus={workspace?.setStatus} />
                </> : null}
                {pane === "assistant" ? <>
                  <PaneHead icon={MessageSquareIcon} title={t("Chat")} />
                  <AssistantSettings value={context} />
                </> : null}
                {pane === "ai-advanced" ? <>
                  <PaneHead icon={ActivityIcon} title={t("Advanced")} />
                  <AdvancedAiSettings value={context} ai={aiValue} papers={paperValue} />
                </> : null}
                {pane === "prompts" ? <PromptsSettings value={prompts} /> : null}
                {pane === "integrations" ? <IntegrationSettings key={getCurrentWorkspace()} workspaceId={getCurrentWorkspace()} /> : null}
                {pane === "account" ? <UsersSettings value={users} selfOnly /> : null}
                {pane === "users" ? <UsersSettings value={users} /> : null}
                {pane === "workspaces" ? <WorkspacesSettings value={workspace} onServer={available("server") ? () => navigate("server", t("Shared workspaces")) : null} /> : null}
                {pane === "sync" ? <SyncSettings value={workspace} papers={papers} /> : null}
                {pane === "backups" && backups ? <WorkspaceBackups value={backups} /> : null}
                {pane === "server" ? <ServerSettings value={server} /> : null}
                {pane === "diagnostics" ? <AdvancedSettings value={diagnostics} /> : null}
              </>}
            </main>
          </div>
          {pending ? <div className="settingsUnsaved" role="alertdialog" aria-label={t("Unsaved changes")}>
            <span>{t("You have unsaved edits. Keep editing or discard them to continue.")}</span>
            <button className="uiBtn" autoFocus onClick={() => setPending(null)}>{t("Keep editing")}</button>
            <button className="uiBtn danger" onClick={() => {
              const action = pending;
              drafts.current.forEach((discard) => discard()); drafts.current.clear();
              setPending(null); action();
            }}>{t("Discard changes")}</button>
          </div> : null}
        </div>
      </div>
      </SettingsSyncContext.Provider>
    </SettingsDraftContext.Provider>
  );
}
