// Settings → Workspaces → Clones and Zotero links → "Link a Zotero
// library…" (docs/dev/zotero_sync.md, POST /api/zotero/links), and a
// link's own settings from its row. The dialog connects first when the
// account has no Zotero key yet, in place (the sign-in at zotero.org or a
// pasted key, SettingsZotero.jsx's pieces; it asks the account again every
// few seconds, so a connection made elsewhere carries it on too), then
// lists the libraries the key reaches (/api/zotero/libraries) and counts
// what a link would move (/api/zotero/links/preview, zoteroPreview.js):
// the PDFs in Zotero Storage and their size, the linked files Gamma cannot
// download ("at least" for a library larger than the server reads), and,
// into an existing workspace, the papers that match, what would go up and
// what else the first rounds send to Zotero (folders, labels as tags,
// pages' notes, highlights: a new paper's only with Send PDFs to Zotero
// on, since they follow its PDF); a count Zotero or the server keeps busy
// is asked again a few times, then Try again; and what the key may not do
// there (store or read files, read notes). Then: into a new workspace
// (named "Zotero" for My Library, a group after the group, as the server
// names it) or an existing one (and which side wins where a page
// differs, syncLinks.js zoteroAdoptHint), the direction (receive only when
// the key cannot write), which PDFs come ("When opened" by default above
// 1,000 PDFs, for a library too large to count whole, and on the iPad's
// own server), whether PDFs go to Zotero, a paper's first one and one
// replaced here alike, never over a linked file or one Zotero Storage
// does not hold (off by default for an existing workspace), Zotero's
// automatic tags, PDF pages without a record.
import React from "react";
import { Empty, Field, IconChoices, Row, Segmented, Step, SubDialog, Toggle, DialogButtons } from "./SettingsKit";
import { MenuSelect } from "../shared/ui/Menus";
import { MorphText } from "../shared/ui/MotionWidgets";
import { ArrowDownIcon, ArrowUpDownIcon, BookIcon, ClockIcon, FileIcon, LabelIcon, PenIcon, UploadIcon } from "../shared/ui/Icons";
import { nativeHost } from "../shared/lib/host.js";
import { SignInWaiting, ZoteroKeyDialog, useZoteroAccount, useZoteroSignIn } from "./SettingsZotero";
import { getZoteroLibraries, needsZoteroConnect, previewZoteroLink } from "../transfers/zoteroApi";
import { ZOTERO_CADENCE, ZOTERO_DIRECTION, ZOTERO_DOWNLOAD, ZOTERO_UNRECORDED, ZoteroLinkSettings } from "../collaboration/MirrorPopover";
import {
  ZOTERO_CADENCE_HINT, ZOTERO_CADENCE_TITLE, ZOTERO_LIVE_S, ZOTERO_UPLOAD_HINT, ZOTERO_UPLOAD_TITLE, onChangeHint, pullByKey,
  zoteroAdoptHint, zoteroLibraryLabel, zoteroWorkspaceName,
} from "../collaboration/syncLinks.js";
import { askPreview, intoLines, libraryLine, previewFailure, suggestedDownload } from "./zoteroPreview.js";
import { T, t } from "../shared/i18n/i18n.js";

const DIRECTION_TILES = [
  { value: "two-way", label: T("Two-way"), hint: T("your edits go to Zotero too"), Icon: ArrowUpDownIcon },
  { value: "pull", label: T("Receive only"), hint: T("Zotero's edits arrive here"), Icon: ArrowDownIcon },
];
const ACCOUNT_POLL_MS = 4000;

// The iPad's own server ("This iPad"): the app's web view on loopback. It
// runs only while the app is on screen and fills the device, so its
// links take PDFs as their pages open.
const onIpadServer = () => nativeHost()?.platform === "ipad"
  && ["127.0.0.1", "localhost", "[::1]", "::1"].includes(globalThis.location?.hostname);

export function ZoteroLinkDialog({ busy, error, onSubmit, onClose, candidates = [], linked = [] }) {
  const [connectError, setConnectError] = React.useState("");
  const [keyOpen, setKeyOpen] = React.useState(false);
  const [libraries, setLibraries] = React.useState(null); // null: not read yet
  const [libraryError, setLibraryError] = React.useState("");
  const [library, setLibrary] = React.useState("");
  const [into, setInto] = React.useState("");
  const [adopt, setAdopt] = React.useState("theirs");
  const [name, setName] = React.useState("");
  const [mode, setMode] = React.useState("two-way");
  const [download, setDownload] = React.useState(null); // null: the default the counts suggest
  const [upload, setUpload] = React.useState(null); // null: on for a new workspace, off for an existing one
  const [autoTags, setAutoTags] = React.useState(false);
  const [unrecorded, setUnrecorded] = React.useState(false);
  const [preview, setPreview] = React.useState(null); // null: counting; false: not counted (previewError says why)
  const [previewError, setPreviewError] = React.useState("");
  const [previewTry, setPreviewTry] = React.useState(0); // Try again asks once more
  const { account, error: accountError, set: setAccount, reload } = useZoteroAccount({ poll: 0 });
  // The stored connection says "connected" whatever Zotero thinks of its
  // key: one Zotero refused (the libraries answer 409) counts as none until
  // the account connects again (a new connection, a new id).
  const connection = account?.connected ? `${account.connected_at || ""}|${account.hint || ""}` : "";
  const [refused, setRefused] = React.useState("");
  const connected = Boolean(connection) && refused !== connection;
  // a connection made elsewhere (the Account row, another tab) carries the
  // dialog on; an account read that failed is asked again the same way
  React.useEffect(() => {
    if (connected) return undefined;
    const timer = setInterval(() => reload().catch(() => {}), ACCOUNT_POLL_MS);
    return () => clearInterval(timer);
  }, [account, connected, reload]);
  function connectedNow(next) {
    setKeyOpen(false);
    setConnectError("");
    if (next) setAccount((previous) => ({ ...previous, ...next, connected: true }));
    else reload().catch(() => {});
  }
  const signIn = useZoteroSignIn({ onConnected: connectedNow, onError: setConnectError });

  // the libraries the key reaches, once connected (never before: the
  // server refuses the read then)
  const asked = connected ? connection : "";
  React.useEffect(() => {
    if (!asked) { setLibraries(null); return undefined; }
    let live = true;
    setLibraryError("");
    getZoteroLibraries().then((list) => { if (live) setLibraries(list); }, (err) => {
      if (!live) return;
      setLibraryError(err.message);
      if (needsZoteroConnect(err)) setRefused(asked);
      else setLibraries([]);
    });
    return () => { live = false; };
  }, [asked]);
  const taken = new Set(linked);
  const choices = (libraries || []).filter((l) => l.read && !taken.has(l.id));
  const chosen = choices.find((l) => l.id === library) || choices[0] || null;
  React.useEffect(() => { if (chosen && chosen.id !== library) setLibrary(chosen.id); }, [chosen, library]);

  // what the link would move, for the counts and the defaults; a count the
  // server or Zotero keeps busy is asked again a few times (askPreview)
  React.useEffect(() => {
    setPreviewError("");
    if (!chosen) { setPreview(null); return undefined; }
    let live = true;
    setPreview(null);
    askPreview(() => previewZoteroLink(chosen.id, into), { alive: () => live }).then((p) => { if (live && p) setPreview(p); }, (err) => {
      if (!live) return;
      setPreview(false);
      setPreviewError(previewFailure(err));
    });
    return () => { live = false; };
  }, [chosen?.id, into, previewTry]); // eslint-disable-line react-hooks/exhaustive-deps -- the library's id is what matters

  // the library row's access (zotero_web.libraries): files may be read, and
  // stored where the key writes too
  const canWrite = Boolean(chosen?.write);
  const canUpload = canWrite && Boolean(chosen?.files);
  const effectiveMode = canWrite ? mode : "pull";
  const effectiveDownload = download ?? suggestedDownload(preview, { ipad: onIpadServer() });
  const effectiveUpload = canUpload && effectiveMode !== "pull" && (upload ?? !into);
  const intoCounts = into ? intoLines(preview, { mode: effectiveMode, upload: effectiveUpload, notes: chosen?.notes !== false }) : [];
  const ok = Boolean(chosen) && !busy;

  function submit() {
    onSubmit({
      library: chosen.id, workspace_id: into, name: into ? "" : name.trim(), mode: effectiveMode,
      download: effectiveDownload, upload: effectiveUpload, auto_tags: autoTags, send_unrecorded: Boolean(into) && unrecorded,
      adopt,
    });
  }

  const dialog = (
    <SubDialog title={t("Link a Zotero library")} onClose={onClose} draft={into || name || null}>
      <div className="settingsForm">
        {!account && accountError ? (
          <Empty icon={BookIcon}>
            <span role="alert">{t("Could not ask this server for your Zotero account: {message}", { message: accountError })}</span>
            <button className="uiBtn sm" onClick={() => reload().catch(() => {})}>{t("Try again")}</button>
          </Empty>
        ) : !account ? <Empty icon={BookIcon} loading>{t("Loading…")}</Empty>
          : !connected ? (
            <Step n={1} title={t("Connect your Zotero account")}
              hint={t("Sign in at zotero.org, or paste an API key you make there. The key stays on this server.")}>
              <span className="setRowControls">
                {signIn.waiting ? <SignInWaiting waiting={signIn.waiting} onCancel={signIn.cancel} /> : <>
                  {account.oauth?.available ? (
                    <button className="uiBtn sm primary" disabled={signIn.starting} onClick={signIn.connect}>{t("Connect Zotero")}</button>
                  ) : null}
                  <button className={`uiBtn sm${account.oauth?.available ? "" : " primary"}`}
                    onClick={() => { setConnectError(""); setKeyOpen(true); }}>{t("Use an API key…")}</button>
                </>}
              </span>
              {signIn.waiting ? <span className="settingDesc">{t("Finish signing in on zotero.org; this dialog goes on by itself.")}</span> : null}
              {connectError || libraryError ? <div className="settingsPaneHint aiKeysError" role="alert">{connectError || libraryError}</div> : null}
            </Step>
          ) : libraries === null ? <Empty icon={BookIcon} loading>{t("Asking Zotero for your libraries…")}</Empty>
          : !choices.length ? (
            <Empty icon={BookIcon}>
              <span>{libraryError || (libraries.length ? t("Every library this key reaches is linked already.") : t("This key reaches no library."))}</span>
            </Empty>
          ) : <>
            <Field label={t("Library")} hint={libraryLine(preview) || (preview === null ? t("Counting what it holds…") : "")}>
              <MenuSelect block value={chosen.id} onChange={(v) => { setLibrary(v); setDownload(null); }}
                options={choices.map((l) => [l.id, zoteroLibraryLabel(l)])} />
            </Field>
            {chosen.files === false || chosen.notes === false ? (
              <div className="settingsPaneHint">
                {[chosen.files === false ? t("Your Zotero key cannot read this library's files, so its PDFs stay in Zotero.") : "",
                  chosen.notes === false ? t("Your Zotero key cannot read notes in this library, so notes travel neither way.") : ""].filter(Boolean).join(" ")}
              </div>
            ) : null}
            {previewError ? (
              <div className="zoteroCountsFailed" role="status">
                <span className="settingsPaneHint">{previewError}</span>
                <button className="uiBtn sm" onClick={() => setPreviewTry((n) => n + 1)}>{t("Try again")}</button>
              </div>
            ) : null}
            {candidates.length ? (
              <Field label={t("Into")} hint={t("A new workspace, or one of yours whose papers are in this library (they are matched by Zotero key, DOI, arXiv id or PDF)")}>
                <MenuSelect block value={into} onChange={(v) => { setInto(v); setUpload(null); }}
                  options={[["", t("A new workspace")], ...candidates.map((w) => [w.id, w.name])]} />
              </Field>
            ) : null}
            {intoCounts.length ? (
              <div className="settingsPaneHint">{intoCounts.map((line) => <div key={line}>{line}</div>)}</div>
            ) : null}
            {into ? (
              <Field label={t("If a page differs")} hint={zoteroAdoptHint()}>
                <Segmented value={adopt} onChange={setAdopt} options={[["theirs", t("Take Zotero's")], ["mine", t("Keep Gamma's")]]} />
              </Field>
            ) : (
              <Field label={t("Name here")} hint={t("Optional — defaults to “{name}”", { name: zoteroWorkspaceName(chosen) })}>
                <input className="aiKeyInput" value={name} placeholder={zoteroWorkspaceName(chosen)} onChange={(e) => setName(e.target.value)} />
              </Field>
            )}
            <Field label={t("Direction")} hint={canWrite ? null : t("Your Zotero key cannot write to this library, so the link only receives.")}>
              <IconChoices label={t("Direction")} value={effectiveMode} onChange={(v) => { if (canWrite) setMode(v); }}
                options={canWrite ? DIRECTION_TILES : DIRECTION_TILES.filter((tile) => tile.value === "pull")} />
            </Field>
            <Field label={t("PDFs from Zotero")} hint={effectiveDownload === "open"
              ? t("Records, notes, folders and labels come whole; a PDF comes when you open its page, and the 50 newest at once.")
              : t("Every PDF in Zotero Storage comes here, with its annotations.")}>
              <Segmented value={effectiveDownload} onChange={setDownload} options={ZOTERO_DOWNLOAD} />
            </Field>
            <Toggle icon={UploadIcon} label={t("Send PDFs to Zotero")} checked={effectiveUpload} disabled={!canUpload || effectiveMode === "pull"}
              onChange={setUpload} title={t(ZOTERO_UPLOAD_TITLE)}
              hint={!canUpload ? t("Your Zotero key cannot store files in this library") : t(ZOTERO_UPLOAD_HINT)} />
            <Toggle icon={LabelIcon} label={t("Automatic tags as labels")} checked={autoTags} onChange={setAutoTags}
              hint={t("publishers' keywords too, not only your own tags")} />
            {into && effectiveMode !== "pull" ? (
              <Row icon={FileIcon} label={t("PDFs without a record")} hint={t("a PDF page whose paper record is not found")}>
                <Segmented value={unrecorded} onChange={setUnrecorded} options={ZOTERO_UNRECORDED} />
              </Row>
            ) : null}
          </>}
        {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
        <DialogButtons footnote={connected && chosen ? null : t("Connect Zotero and pick a library to continue.")}>
          <button className="uiBtn" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
          <button className="uiBtn primary" disabled={!ok} onClick={submit}>
            <MorphText>{busy ? t("Linking…") : t("Link")}</MorphText>
          </button>
        </DialogButtons>
      </div>
    </SubDialog>
  );
  return <>
    {dialog}
    {keyOpen ? <ZoteroKeyDialog onClose={() => setKeyOpen(false)} onConnected={connectedNow} /> : null}
  </>;
}

// A link's settings from its row in Settings (the pill's gear has the same
// rows for the open workspace's link): cadence, a round after an edit,
// direction, and what PDFs and tags travel. Each change is saved at once
// (PATCH /api/zotero/links/{ws}); `patch(body)` answers the link's info.
export function ZoteroLinkSettingsDialog({ link, patch, onClose }) {
  const [busy, setBusy] = React.useState(false);
  const [info, setInfo] = React.useState(link);
  const [error, setError] = React.useState("");
  async function save(body) {
    setBusy(true);
    setError("");
    try {
      const next = await patch(body);
      setInfo((prev) => ({ ...prev, ...body, ...(next && typeof next === "object" ? next : {}) }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <SubDialog title={t("Link settings · {name}", { name: info.name || zoteroLibraryLabel(info) })} onClose={onClose}>
      <div className="settingsForm">
        <Row icon={ClockIcon} label={t("Automatic sync")} hint={t(ZOTERO_CADENCE_HINT)} title={t(ZOTERO_CADENCE_TITLE)}>
          <Segmented value={info.poll_s ?? ZOTERO_LIVE_S} disabled={busy} onChange={(v) => save({ poll_s: v })} options={ZOTERO_CADENCE} />
        </Row>
        <Toggle icon={PenIcon} checked={Boolean(info.on_change)} disabled={busy} onChange={(v) => save({ on_change: v })}
          label={t("Sync after an edit")} hint={onChangeHint("zotero")} />
        {!info.detached ? <Row icon={ArrowUpDownIcon} label={t("Direction")} hint={pullByKey(info) ? t("receive only · your Zotero key cannot write") : undefined}>
          <Segmented value={info.mode === "pull" ? "pull" : "two-way"} disabled={busy} onChange={(v) => save({ mode: v })} options={ZOTERO_DIRECTION} />
        </Row> : null}
        {!info.detached ? <ZoteroLinkSettings info={info} busy={busy} patch={save} /> : null}
        {error ? <div className="settingsPaneHint aiKeysError" role="alert">{error}</div> : null}
        <DialogButtons>
          <button className="uiBtn primary" onClick={onClose}>{t("Done")}</button>
        </DialogButtons>
      </div>
    </SubDialog>
  );
}
