// Settings → Citations › Add style… (docs/dev/citations.md): the dialog
// that adds a citation style to the account on this server
// (citations/citationApi.js), from the CSL repository or from a .csl file.
// What the pane shares with the other pickers of a style and a language is
// citations/CitationChoices.jsx.
import React from "react";
import { addStyle, searchStyles, uploadStyle } from "../citations/citationApi.js";
import { isBundledStyle } from "../citations/bundledStyles.js";
import { styleKindLabel } from "../citations/citeRules.js";
import { pickFiles } from "../shared/lib/pickFiles.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { MorphText } from "../shared/ui/MotionWidgets";
import { CheckIcon, SearchIcon, UploadIcon } from "../shared/ui/Icons";
import { DialogButtons, Empty, FormNotice, Row, SubDialog } from "./SettingsKit";
import { t } from "../shared/i18n/i18n.js";

// The most a style's file may hold (gamma/routers/citations.py
// STYLE_BYTES_MAX): a larger file is refused before it is sent.
const STYLE_BYTES = 512 * 1024;
// How long the search waits for typing to pause before it asks the server.
const SEARCH_DELAY_MS = 250;

// Add a citation style: a search of the CSL repository Zotero serves (GET
// /api/citations/repository, the closest 50 of its 10,000-odd styles by
// title, short title or id), each result's class and an Add; and a style's
// own .csl file (Add from file…), which works with downloads off as well,
// unless it is a dependent style whose parent would have to be fetched.
// `added`: the account's rows, `limit` the most it keeps, `downloads`
// whether the repository may be asked (citationApi's list). The dialog stays
// open over several adds; a file's closes it. `onAdded(row)` for each.
export function AddStyleDialog({ added, limit, downloads, onAdded, onClose }) {
  const [query, setQuery] = React.useState("");
  const [found, setFound] = React.useState({ q: "", results: [] });
  const [searching, setSearching] = React.useState(false);
  const [off, setOff] = React.useState(!downloads);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState("");
  const [just, setJust] = React.useState([]);  // ids added while the dialog is open
  const ids = new Set([...added.map((row) => row.id), ...just]);
  const full = ids.size >= limit;
  const q = query.trim();

  React.useEffect(() => {
    if (off || !q) { setSearching(false); return undefined; }
    let live = true;
    setSearching(true);
    const timer = setTimeout(() => {
      searchStyles(q).then(
        (answer) => { if (live) { setFound({ q, results: answer?.results || [] }); setSearching(false); } },
        (err) => {
          if (!live) return;
          if (err.status === 403) setOff(true);
          else setError(err.message);
          setSearching(false);
        });
    }, SEARCH_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [q, off]);

  async function add(id) {
    setBusy(id); setError("");
    try {
      const row = await addStyle(id);
      // A renamed style is kept under the id it has now.
      setJust((list) => [...list, id, row.id]);
      onAdded(row);
    } catch (err) {
      if (err.status === 403) setOff(true);
      setError(err.message);
    } finally { setBusy(""); }
  }
  const fromFile = () => pickFiles({ accept: ".csl,.xml,application/vnd.citationstyles.style+xml,text/xml,application/xml" }, async ([file]) => {
    setError("");
    if (file.size > STYLE_BYTES) {
      setError(t("{file} is over 512 KB, and a citation style is at most that.", { file: file.name }));
      return;
    }
    setBusy("file");
    try {
      onAdded(await uploadStyle(file));
      onClose();
    } catch (err) {
      setError(err.message);
      setBusy("");
    }
  });

  const shown = found.q === q ? found.results : [];
  return (
    <SubDialog title={t("Add a citation style")} onClose={onClose} className="citeStyleDialog">
      {off ? (
        <FormNotice tone="info">{t("Downloading citation styles is switched off on this server. A style's .csl file can still be added.")}</FormNotice>
      ) : <>
        <input className="aiKeyInput citeStyleSearch" type="search" autoFocus={!touchTyping()} value={query}
          aria-label={t("Search citation styles")} placeholder={t("A style, a journal or a field")} spellCheck={false}
          onChange={(event) => { setQuery(event.target.value); setError(""); }} />
        <div className="citeStyleResults" aria-busy={searching}>
          {!q ? <p className="settingsPaneHint">{t("The CSL repository holds over 10,000 styles: a journal's own (Nature Physics), a field's (Vancouver), a publisher's or a university's.")}</p>
            : searching && !shown.length ? <Empty icon={SearchIcon} loading>{t("Searching…")}</Empty>
              : !shown.length ? <Empty icon={SearchIcon}>{t("No style in the repository matches “{query}”.", { query: q })}</Empty>
                : shown.map((style) => {
                  const bundled = isBundledStyle(style.id);
                  const have = ids.has(style.id);
                  return (
                    <Row key={style.id} label={style.title} hint={style.short_title ? `${style.short_title} · ${style.id}` : style.id}
                      title={style.title}>
                      <span className="setRowControls">
                        {style.kind ? <span className="uiTag">{styleKindLabel(style.kind)}</span> : null}
                        {bundled || have ? (
                          <button type="button" className="uiBtn sm" disabled
                            title={bundled ? t("Gamma carries this style already") : undefined}>
                            <CheckIcon size={14} />{bundled ? t("Included") : t("Added")}
                          </button>
                        ) : (
                          <button type="button" className="uiBtn sm" disabled={full || !!busy}
                            aria-label={t("Add {style}", { style: style.title })} onClick={() => add(style.id)}>
                            <MorphText>{busy === style.id ? t("Adding…") : t("Add")}</MorphText>
                          </button>
                        )}
                      </span>
                    </Row>
                  );
                })}
        </div>
      </>}
      {full ? <FormNotice tone="warn">{t("You have added {n} styles, the most an account keeps: remove a style to add another.", { n: limit })}</FormNotice> : null}
      {error ? <FormNotice>{error}</FormNotice> : null}
      {/* With the repository switched off a file is the one way in, so it
          is the main button; Done once something was added, else Close. */}
      <DialogButtons>
        <button type="button" className={`uiBtn${off ? " primary" : ""}`} disabled={full || !!busy} onClick={fromFile}>
          <UploadIcon size={14} /><MorphText>{busy === "file" ? t("Adding…") : t("Add from file…")}</MorphText>
        </button>
        <button type="button" className={`uiBtn${off ? "" : " primary"}`} onClick={onClose}>{just.length ? t("Done") : t("Close")}</button>
      </DialogButtons>
    </SubDialog>
  );
}
