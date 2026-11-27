// Settings → Server → Text recognition (admins): the server options of the
// text recognition of scanned PDFs (gamma/server_options.py; docs/dev/
// documents.md "Recognized text"), saved through useServerOptions:
// - `ocr`, whether this server recognizes text at all, on the server or in
//   the window that asks. GAMMA_OCR overrides it (`ocr_env`: the row is then
//   read-only), and a hosted container or a demo server recognizes none
//   unless that says on, so the row is not there either.
// - `ocr_auto`, whether the server reads by itself the words of a PDF
//   uploaded with no text at all, a scan whose pages are only pictures
//   (gamma/ocr_auto.py), and `ocr_workers`, how many files it recognizes at
//   once: rows only where the server recognizes text itself, not where the
//   window that asks reads the pages (the apps, macOS before 15).
// Which rows apply is `options.text_recognition` (server_options
// ``ocr_rows``); none, no section. Read once for all the Server pane's
// sections over /api/admin/settings (settingsReads.js).
import React from "react";
import { Row, Section, Toggle, UnitInput } from "./SettingsKit";
import { useServerOptions } from "./serverOptions.js";
import { ScanTextIcon, ServerIcon, SparklesIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

const envHint = (name) => t("Set by {name} in the server's environment", { name });

export function TextRecognitionSettings({ setStatus }) {
  const { options, error, save, saveNumber } = useServerOptions({ setStatus });
  // Until it is read (or when the read failed: the pane's other sections
  // over the same settings say so), nothing: the section may not be there.
  const rows = options?.text_recognition;
  if (!rows?.switch && !rows?.server) return null;
  const managed = !!options.ocr_env;
  const on = !!options.ocr;
  const [lo, hi] = options.ranges.ocr_workers;
  const commitWorkers = (raw) => saveNumber("ocr_workers", raw, (n) => t("Up to {n} files are recognized at once.", { n }));
  return <Section title={t("Text recognition")}>
    {rows.switch ? (
      <Toggle icon={ScanTextIcon} label={t("Recognize text in scanned PDFs")} checked={on} disabled={managed}
        hint={managed ? envHint("GAMMA_OCR") : on ? t("The Document menu offers Recognize text for a scan")
          : t("No scan's words are read from its page pictures")}
        title={t("A scanned PDF's pages are pictures: their words are read from them, on this server or in the browser of whoever asks, so that selection, search, the AI and translation read them. Off, nobody can start a recognition here; the words already recognized stay.")}
        onChange={(value) => save({ ocr: value }, value ? t("Text recognition on.") : t("Text recognition off."))} />
    ) : null}
    {rows.server ? <>
      <Toggle icon={SparklesIcon} label={t("Recognize scans automatically")} checked={!!options.ocr_auto}
        hint={options.ocr_auto ? t("A PDF uploaded with no text is read from its page pictures")
          : t("Scans are recognized when someone asks, from the Document menu")}
        title={t("When someone uploads a PDF whose pages are all pictures, with no text to select or search, the server reads its words in the background, one file at a time — seconds a page, an hour or more for a long book — and a file someone asks to recognize goes first. Off, nothing new starts: a run under way finishes, and the files waiting are not started.")}
        onChange={(value) => save({ ocr_auto: value }, value ? t("Automatic text recognition on.") : t("Automatic text recognition off."))} />
      <Row icon={ServerIcon} label={t("Files recognized at once")}
        hint={t("Each keeps a core busy and about 50 MB of memory while it runs")}
        title={t("How many files this server recognizes at the same time, whoever asked; the others wait their turn. A page takes a core for seconds, so more help a server with cores to spare.")}>
        <UnitInput min={lo} max={hi} label={t("Files recognized at once")} value={String(options.ocr_workers)} onCommit={commitWorkers} />
      </Row>
    </> : null}
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </Section>;
}
