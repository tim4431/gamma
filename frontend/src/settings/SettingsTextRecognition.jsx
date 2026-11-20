// Settings → Server → Text recognition (admins): whether the server reads by
// itself the words of a PDF uploaded with no text at all, a scan whose pages
// are only pictures (gamma/ocr_auto.py; docs/dev/documents.md "Recognized
// text"). It lives in the server `settings` table through
// /api/admin/settings (`ocr_auto`); GAMMA_OCR_AUTO overrides the saved value,
// and the row is then read-only. A server that does not recognize text
// itself (turned off, a hosted container, the demo, or one whose pages are
// read in the window that asks: the apps, macOS before 15) has no section
// (`ocr_auto_available`): nothing would run. The setting is read once for
// all the Server pane's sections over /api/admin/settings (settingsReads.js).
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Section, Toggle } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { ScanTextIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

const envHint = (name) => t("Set by {name} in the server's environment", { name });

export function TextRecognitionSettings({ setStatus }) {
  const { data: saved, set: setSaved } = useSettingsRead("/admin/settings");
  const [error, setError] = React.useState("");
  // The switch shows its new state at once and flips back if the save
  // fails; only its own field goes, so another section's save meanwhile
  // keeps its value.
  async function save(on) {
    setError("");
    const before = saved.ocr_auto;
    setSaved((prev) => ({ ...prev, ocr_auto: on }));
    try {
      setSaved(await apiJson(`${API}/admin/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ocr_auto: on }),
      }));
      setStatus?.(on ? t("Automatic text recognition on.") : t("Automatic text recognition off."));
    } catch (err) {
      setSaved((prev) => ({ ...prev, ocr_auto: before }));
      setError(t("Could not save: {message}", { message: err.message }));
    }
  }
  // Until it is read (or when the read failed: the pane's other sections
  // over the same settings say so), nothing: the section may not be there.
  if (!saved?.ocr_auto_available) return null;
  const managed = saved.ocr_auto_source === "environment";
  const on = !!saved.ocr_auto;
  return <Section title={t("Text recognition")}>
    <Toggle icon={ScanTextIcon} label={t("Recognize scans automatically")} checked={on} disabled={managed}
      hint={managed ? envHint("GAMMA_OCR_AUTO") : on ? t("A PDF uploaded with no text is read from its page pictures")
        : t("Scans are recognized when someone asks, from the Document menu")}
      title={t("When someone uploads a PDF whose pages are all pictures, with no text to select or search, the server reads its words in the background, one file at a time — seconds a page, an hour or more for a long book — and a file someone asks to recognize goes first. Off, nothing new starts: a run under way finishes, and the files waiting are not started.")}
      onChange={save} />
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </Section>;
}
